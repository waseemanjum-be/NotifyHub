# app/services/notification_service.py

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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
    ProviderReceiptRequest,
)
from app.utils import get_cache

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self._repo = NotificationRepository(db=db)
        self._cache = get_cache()

    async def create_notification(self, payload: NotificationCreateRequest) -> NotificationCreateResponse:
        await self._repo.create_indexes()

        user_contact = await self._cached_user_contact(payload.user_id)
        if user_contact is None:
            logger.info(
                "User not found for notification create",
                extra={"user_id": payload.user_id, "template_id": payload.template_id},
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        template_content = await self._cached_template_content(payload.template_id)
        if template_content is None:
            logger.info(
                "Template not found for notification create",
                extra={"user_id": payload.user_id, "template_id": payload.template_id},
            )
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
            logger.info(
                "Notification accepted",
                extra={
                    "notification_id": notification_id,
                    "idempotency_key": str(payload.idempotency_key),
                    "user_id": payload.user_id,
                    "template_id": payload.template_id,
                    "priority": payload.priority.value,
                    "channels": [c.value for c in payload.channels],
                },
            )
            await self._repo.append_event(
                notification_id=notification_id,
                event={
                    "type": "ACCEPTED",
                    "at": now,
                    "idempotency_key": str(payload.idempotency_key),
                    "user_id": payload.user_id,
                    "template_id": payload.template_id,
                    "priority": payload.priority.value,
                    "channels": [c.value for c in payload.channels],
                },
            )
            return NotificationCreateResponse(notification_id=notification_id)
        except DuplicateKeyError:
            existing = await self._repo.find_by_user_and_idempotency(
                user_id=payload.user_id,
                idempotency_key=str(payload.idempotency_key),
            )
            if not existing or "_id" not in existing:
                logger.warning(
                    "Idempotency conflict but existing record not found",
                    extra={"user_id": payload.user_id, "idempotency_key": str(payload.idempotency_key)},
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency conflict but existing record not found",
                )

            existing_id = str(existing["_id"])
            logger.info(
                "Idempotency hit",
                extra={
                    "notification_id": existing_id,
                    "idempotency_key": str(payload.idempotency_key),
                    "user_id": payload.user_id,
                },
            )
            await self._repo.append_event(
                notification_id=existing_id,
                event={
                    "type": "IDEMPOTENCY_HIT",
                    "at": now,
                    "idempotency_key": str(payload.idempotency_key),
                    "user_id": payload.user_id,
                },
            )
            return NotificationCreateResponse(notification_id=existing_id)

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
        now = datetime.now(timezone.utc)
        updated = await self._repo.set_channel_read(
            notification_id=notification_id,
            channel=payload.channel.value if payload.channel else None,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

        logger.info(
            "Marked READ",
            extra={
                "notification_id": notification_id,
                "channel": payload.channel.value if payload.channel else "ALL",
            },
        )
        await self._repo.append_event(
            notification_id=notification_id,
            event={
                "type": "READ_MARKED",
                "at": now,
                "channel": payload.channel.value if payload.channel else "ALL",
            },
        )
        return await self.get_notification_status(notification_id)

    async def apply_receipt(self, notification_id: str, payload: ProviderReceiptRequest) -> NotificationStatusResponse:
        now = datetime.now(timezone.utc)
        new_status = payload.event.value

        ok = await self._repo.apply_provider_receipt(
            notification_id=notification_id,
            channel=payload.channel.value,
            new_status=new_status,
            now=now,
        )
        if not ok:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification or channel not found")

        logger.info(
            "Provider receipt applied",
            extra={
                "notification_id": notification_id,
                "channel": payload.channel.value,
                "event": new_status,
                "provider_message_id": payload.provider_message_id,
            },
        )
        await self._repo.append_event(
            notification_id=notification_id,
            event={
                "type": "PROVIDER_RECEIPT",
                "at": now,
                "channel": payload.channel.value,
                "event": new_status,
                "provider_message_id": payload.provider_message_id,
                "occurred_at": payload.occurred_at,
            },
        )
        return await self.get_notification_status(notification_id)

    async def _cached_user_contact(self, user_id: str) -> Optional[Dict[str, Any]]:
        key = f"user:contact:{user_id}"
        cached = await self._cache.get(key)
        if cached is not None:
            if isinstance(cached, (bytes, bytearray)):
                return json.loads(cached.decode("utf-8"))
            if isinstance(cached, str):
                return json.loads(cached)
            if isinstance(cached, dict):
                return cached
            return None

        doc = await self._repo.get_user_contact(user_id)
        if doc is None:
            return None

        ttl = settings.CACHE_TTL_SECONDS
        if settings.CACHE_BACKEND.strip().lower() == "memcache":
            await self._cache.set(key, json.dumps(doc).encode("utf-8"), ttl_seconds=ttl)
        else:
            await self._cache.set(key, doc, ttl_seconds=ttl)
        return doc

    async def _cached_template_content(self, template_id: str) -> Optional[Dict[str, Any]]:
        key = f"template:content:{template_id}"
        cached = await self._cache.get(key)
        if cached is not None:
            if isinstance(cached, (bytes, bytearray)):
                return json.loads(cached.decode("utf-8"))
            if isinstance(cached, str):
                return json.loads(cached)
            if isinstance(cached, dict):
                return cached
            return None

        doc = await self._repo.get_template_content(template_id)
        if doc is None:
            return None

        ttl = settings.CACHE_TTL_SECONDS
        if settings.CACHE_BACKEND.strip().lower() == "memcache":
            await self._cache.set(key, json.dumps(doc).encode("utf-8"), ttl_seconds=ttl)
        else:
            await self._cache.set(key, doc, ttl_seconds=ttl)
        return doc

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
