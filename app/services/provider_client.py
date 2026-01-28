# app/services/provider_client.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import httpx

from app.core.config import settings


@dataclass(frozen=True)
class ProviderResult:
    ok: bool
    status_code: Optional[int]
    response_json: Optional[Dict[str, Any]]
    error: Optional[str]


class ProviderClient:
    """
    Generic provider caller.
    - Channel-specific routing is ONLY base URL + API key from configuration.
    - No mock logic in code.
    """

    def __init__(self) -> None:
        timeout = max(1, int(settings.PROVIDER_TIMEOUT_MS)) / 1000.0
        self._timeout = httpx.Timeout(timeout)

    def _provider_config(self, channel: str) -> Tuple[str, str]:
        if channel == "EMAIL":
            return settings.EMAIL_PROVIDER_BASE_URL, settings.EMAIL_PROVIDER_API_KEY
        if channel == "SMS":
            return settings.SMS_PROVIDER_BASE_URL, settings.SMS_PROVIDER_API_KEY
        if channel == "PUSH":
            return settings.PUSH_PROVIDER_BASE_URL, settings.PUSH_PROVIDER_API_KEY
        return "", ""

    async def send(self, channel: str, payload: Dict[str, Any]) -> ProviderResult:
        base_url, api_key = self._provider_config(channel)
        if not base_url:
            return ProviderResult(ok=False, status_code=None, response_json=None, error="Provider base URL not configured")

        url = f"{base_url.rstrip('/')}/send"
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
        except Exception as e:
            return ProviderResult(ok=False, status_code=None, response_json=None, error=str(e))

        status_code = resp.status_code
        try:
            body = resp.json()
        except Exception:
            body = None

        if 200 <= status_code < 300:
            return ProviderResult(ok=True, status_code=status_code, response_json=body, error=None)

        return ProviderResult(ok=False, status_code=status_code, response_json=body, error="Non-2xx provider response")
