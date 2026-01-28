# app/services/notification_service.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.repositories.notification_repository import NotificationRepository
from app.schemas.notifications import (
    ChannelStatus,
    DeliveryStatus,
    NotificationCreateRequest,
    NotificationCreateResponse,
    NotificationReadRequest,
    NotificationStatusResponse,
)
from app.utils import get_cache


class NotificationService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self._repo = NotificationRepository(db=db)
        self._cache = get_cache()

    async def create_notification(self, payload: NotificationCreateRequest) -> NotificationCreateResponse:
        # Ensure indexes exist (safe to call; also bootstrapped at startup)
        await self._repo.create_indexes()

        # Validate referenced entities (no sample data creation)
        if not await self._cached_exists(kind="user", object_id=payload.user_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        if not await self._cached_exists(kind="template", object_id=payload.template_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

        now = datetime.now(timezone.utc)

        channels: List[Dict[str, Any]] = [
            {
                "channel": ch.value,
                "status": DeliveryStatus.QUEUED.value,
                "attempt_count": 0,
                "last_error": None,
                "next_attempt_at": now,
                "created_at": now,
                "updated_at": now,
            }
            for ch in payload.channels
        ]

        doc: Dict[str, Any] = {
            "idempotency_key": str(payload.idempotency_key),
            "user_id": payload.user_id,
            "template_id": payload.template_id,
            "template_params": payload.template_params,
            "priority": payload.priority.value,
            "channels": channels,
            "created_at": now,
            "updated_at": now,
        }

        try:
            notification_id = await self._repo.insert_notification(doc)
            return NotificationCreateResponse(notification_id=notification_id)
        except DuplicateKeyError:
            existing = await self._repo.find_by_user_and_idempotency(
                user_id=payload.user_id,
                idempotency_key=str(payload.idempotency_key),
            )
            if not existing or "_id" not in existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency conflict but existing record not found",
                )
            return NotificationCreateResponse(notification_id=str(existing["_id"]))

    async def get_notification_status(self, notification_id: str) -> NotificationStatusResponse:
        """
        GET /api/notifications/{notification_id}
        Delivery tracking includes:
          - overall derived status
          - per-channel status, attempt_count, last_error
          - scheduling/last update timestamps
        """
        doc = await self._repo.find_by_id(notification_id)
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

        channel_statuses = [
            ChannelStatus(
                channel=item["channel"],
                status=item.get("status", DeliveryStatus.QUEUED.value),
                attempt_count=int(item.get("attempt_count", 0)),
                last_error=item.get("last_error"),
                next_attempt_at=item.get("next_attempt_at"),
                updated_at=item.get("updated_at"),
            )
            for item in doc.get("channels", [])
        ]

        overall = self._derive_overall_status(channel_statuses)

        return NotificationStatusResponse(
            notification_id=str(doc["_id"]),
            user_id=doc["user_id"],
            template_id=doc["template_id"],
            priority=doc.get("priority", "NORMAL"),
            overall_status=overall,
            channels=channel_statuses,
            created_at=doc.get("created_at"),
            updated_at=doc.get("updated_at"),
        )

    async def mark_read(self, notification_id: str, payload: NotificationReadRequest) -> NotificationStatusResponse:
        updated = await self._repo.set_channel_read(
            notification_id=notification_id,
            channel=payload.channel.value if payload.channel else None,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
        return await self.get_notification_status(notification_id)

    async def _cached_exists(self, kind: str, object_id: str) -> bool:
        """
        Minimal cache wrapper:
          - LRU stores python objects directly
          - Memcache stores bytes; we store b"1"/b"0"
        """
        key = f"exists:{kind}:{object_id}"
        cached = await self._cache.get(key)
        if cached is not None:
            if isinstance(cached, (bytes, bytearray)):
                return cached == b"1"
            if isinstance(cached, str):
                return cached == "1"
            if isinstance(cached, bool):
                return cached
            return bool(cached)

        if kind == "user":
            exists = await self._repo.user_exists(object_id)
        elif kind == "template":
            exists = await self._repo.template_exists(object_id)
        else:
            exists = False

        val = b"1" if exists else b"0"
        await self._cache.set(key, val, ttl_seconds=settings.CACHE_TTL_SECONDS)
        return exists

    def _derive_overall_status(self, channels: List[ChannelStatus]) -> DeliveryStatus:
        if not channels:
            return DeliveryStatus.QUEUED

        statuses = {c.status for c in channels}

        if DeliveryStatus.FAILED in statuses:
            return DeliveryStatus.FAILED
        if statuses == {DeliveryStatus.READ}:
            return DeliveryStatus.READ
        if statuses.issubset({DeliveryStatus.DELIVERED, DeliveryStatus.READ}):
            return DeliveryStatus.DELIVERED
        if statuses.issubset({DeliveryStatus.SENT, DeliveryStatus.DELIVERED, DeliveryStatus.READ}):
            return DeliveryStatus.SENT
        if DeliveryStatus.SENDING in statuses:
            return DeliveryStatus.SENDING
        if DeliveryStatus.RETRY_DUE in statuses:
            return DeliveryStatus.RETRY_DUE
        return DeliveryStatus.QUEUED
