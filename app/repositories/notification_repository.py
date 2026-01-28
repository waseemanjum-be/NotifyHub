# app/repositories/notification_repository.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError


class NotificationRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db
        self._notifications = db["notifications"]
        self._users = db["users"]
        self._templates = db["notification_templates"]

    async def create_indexes(self) -> None:
        """
        Mongo equivalent of migrations for these collections.
        Safe to call on every startup (idempotent).
        """
        # Notifications: idempotency enforcement under concurrency
        await self._notifications.create_index(
            [("user_id", 1), ("idempotency_key", 1)],
            unique=True,
            name="uniq_user_idempotency_key",
        )

        # Notifications: worker scanning (multi-key indexes on array fields)
        await self._notifications.create_index(
            [("channels.status", 1), ("channels.next_attempt_at", 1), ("priority", 1)],
            name="idx_channels_status_next_attempt_priority",
        )

        # Useful operational indexes
        await self._notifications.create_index(
            [("user_id", 1), ("created_at", -1)],
            name="idx_user_created_at",
        )
        await self._notifications.create_index(
            [("template_id", 1), ("created_at", -1)],
            name="idx_template_created_at",
        )

        # Users/Templates indexes (minimal)
        await self._templates.create_index([("name", 1)], unique=True, name="uniq_template_name")

    async def insert_notification(self, doc: Dict[str, Any]) -> str:
        try:
            res = await self._notifications.insert_one(doc)
            return str(res.inserted_id)
        except DuplicateKeyError as e:
            raise e

    async def find_by_user_and_idempotency(
        self, user_id: str, idempotency_key: str
    ) -> Optional[Dict[str, Any]]:
        return await self._notifications.find_one({"user_id": user_id, "idempotency_key": idempotency_key})

    async def find_by_id(self, notification_id: str) -> Optional[Dict[str, Any]]:
        if not ObjectId.is_valid(notification_id):
            return None
        return await self._notifications.find_one({"_id": ObjectId(notification_id)})

    async def set_channel_read(self, notification_id: str, channel: Optional[str]) -> bool:
        if not ObjectId.is_valid(notification_id):
            return False

        now = datetime.now(timezone.utc)

        if channel is None:
            res = await self._notifications.update_one(
                {"_id": ObjectId(notification_id)},
                {
                    "$set": {
                        "channels.$[].status": "READ",
                        "channels.$[].updated_at": now,
                        "updated_at": now,
                    }
                },
            )
            return res.matched_count == 1

        res = await self._notifications.update_one(
            {"_id": ObjectId(notification_id), "channels.channel": channel},
            {
                "$set": {
                    "channels.$.status": "READ",
                    "channels.$.updated_at": now,
                    "updated_at": now,
                }
            },
        )
        return res.matched_count == 1

    async def user_exists(self, user_id: str) -> bool:
        """
        Supports both:
          - Mongo ObjectId in string form (preferred)
          - Custom string ids stored in a field `id`
        """
        if ObjectId.is_valid(user_id):
            doc = await self._users.find_one({"_id": ObjectId(user_id)}, {"_id": 1})
            return doc is not None

        doc = await self._users.find_one({"id": user_id}, {"_id": 1})
        return doc is not None

    async def template_exists(self, template_id: str) -> bool:
        """
        Supports both:
          - Mongo ObjectId in string form (preferred)
          - Custom string ids stored in a field `id`
        """
        if ObjectId.is_valid(template_id):
            doc = await self._templates.find_one({"_id": ObjectId(template_id)}, {"_id": 1})
            return doc is not None

        doc = await self._templates.find_one({"id": template_id}, {"_id": 1})
        return doc is not None
