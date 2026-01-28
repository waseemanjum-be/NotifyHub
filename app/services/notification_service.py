# app/services/notification_service.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.repositories.notification_repository import NotificationRepository
from app.schemas.notifications import (
    ChannelStatus,
    DeliveryStatus,
    NotificationCreateRequest,
    NotificationCreateResponse,
    NotificationReadRequest,
    NotificationStatusResponse,
)


class NotificationService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self._repo = NotificationRepository(db=db)

    async def create_notification(self, payload: NotificationCreateRequest) -> NotificationCreateResponse:
        now = datetime.now(timezone.utc)

        channels: List[Dict[str, Any]] = [
            {
                "channel": ch.value,
                "status": DeliveryStatus.QUEUED.value,
                "attempt_count": 0,
                "last_error": None,
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
        doc = await self._repo.find_by_id(notification_id)
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

        channel_statuses = [
            ChannelStatus(
                channel=item["channel"],
                status=item.get("status", DeliveryStatus.QUEUED.value),
                attempt_count=int(item.get("attempt_count", 0)),
                last_error=item.get("last_error"),
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
        )

    async def mark_read(
        self, notification_id: str, payload: NotificationReadRequest
    ) -> NotificationStatusResponse:
        updated = await self._repo.set_channel_read(
            notification_id=notification_id,
            channel=payload.channel.value if payload.channel else None,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
        return await self.get_notification_status(notification_id)

    def _derive_overall_status(self, channels: List[ChannelStatus]) -> str:
        if not channels:
            return DeliveryStatus.QUEUED.value

        statuses = {c.status.value if hasattr(c.status, "value") else str(c.status) for c in channels}

        if DeliveryStatus.FAILED.value in statuses:
            return DeliveryStatus.FAILED.value
        if statuses == {DeliveryStatus.READ.value}:
            return DeliveryStatus.READ.value
        if statuses.issubset({DeliveryStatus.DELIVERED.value, DeliveryStatus.READ.value}):
            return DeliveryStatus.DELIVERED.value
        if statuses.issubset({DeliveryStatus.SENT.value, DeliveryStatus.DELIVERED.value, DeliveryStatus.READ.value}):
            return DeliveryStatus.SENT.value
        if DeliveryStatus.SENDING.value in statuses:
            return DeliveryStatus.SENDING.value
        if DeliveryStatus.RETRY_DUE.value in statuses:
            return DeliveryStatus.RETRY_DUE.value
        return DeliveryStatus.QUEUED.value
