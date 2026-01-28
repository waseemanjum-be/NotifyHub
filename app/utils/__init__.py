# app/utils/__init__.py

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

from app.core.config import settings


class Cache:
    async def get(self, key: str) -> Optional[Any]:
        raise NotImplementedError

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        raise NotImplementedError

    async def delete(self, key: str) -> None:
        raise NotImplementedError


@dataclass
class _Entry:
    value: Any
    expires_at: float


class LRUCache(Cache):
    """
    Simple in-process LRU with TTL.
    Suitable for local/dev or single-process usage.
    """

    def __init__(self, maxsize: int = 2048):
        self._maxsize = maxsize
        self._data: "OrderedDict[str, _Entry]" = OrderedDict()

    async def get(self, key: str) -> Optional[Any]:
        now = time.time()
        entry = self._data.get(key)
        if not entry:
            return None
        if entry.expires_at <= now:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key, last=True)
        return entry.value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        expires_at = time.time() + max(0, int(ttl_seconds))
        self._data[key] = _Entry(value=value, expires_at=expires_at)
        self._data.move_to_end(key, last=True)

        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)


class MemcacheCache(Cache):
    """
    Memcached-backed cache (shared across workers).
    Uses pymemcache; calls are executed in a thread to avoid blocking the event loop.
    """

    def __init__(self, host: str, port: int, timeout_ms: int = 200):
        from pymemcache.client.base import Client  # dependency in requirements.txt

        self._client = Client((host, port), connect_timeout=timeout_ms / 1000.0, timeout=timeout_ms / 1000.0)

    async def get(self, key: str) -> Optional[Any]:
        def _get_sync():
            return self._client.get(key)

        raw = await asyncio.to_thread(_get_sync)
        if raw is None:
            return None
        # Store as bytes; caller can store bytes/str/json as they prefer later
        return raw

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        def _set_sync():
            self._client.set(key, value, expire=int(ttl_seconds))

        await asyncio.to_thread(_set_sync)

    async def delete(self, key: str) -> None:
        def _del_sync():
            self._client.delete(key)

        await asyncio.to_thread(_del_sync)


_cache_singleton: Optional[Cache] = None


def get_cache() -> Cache:
    """
    Config-driven cache selection:
      - none     => no-op cache
      - lru      => in-process LRU+TTL
      - memcache => shared cache
    """
    global _cache_singleton
    if _cache_singleton is not None:
        return _cache_singleton

    backend = (settings.CACHE_BACKEND or "none").strip().lower()

    if backend == "lru":
        _cache_singleton = LRUCache()
        return _cache_singleton

    if backend == "memcache":
        _cache_singleton = MemcacheCache(
            host=settings.MEMCACHE_HOST,
            port=settings.MEMCACHE_PORT,
            timeout_ms=settings.MEMCACHE_TIMEOUT_MS,
        )
        return _cache_singleton

    class _NoCache(Cache):
        async def get(self, key: str) -> Optional[Any]:
            return None

        async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
            return None

        async def delete(self, key: str) -> None:
            return None

    _cache_singleton = _NoCache()
    return _cache_singleton
