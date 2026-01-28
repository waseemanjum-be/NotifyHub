# app/repositories/notification_repository.py

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorClientSession
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


class NotificationRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db
        self._notifications = db["notifications"]
        self._users = db["users"]
        self._templates = db["notification_templates"]
        self._attempts = db["delivery_attempts"]

    async def create_indexes(self) -> None:
        """
        Mongo equivalent of migrations for these collections.
        Safe to call on every startup (idempotent).
        """
        await self._notifications.create_index(
            [("user_id", 1), ("idempotency_key", 1)],
            unique=True,
            name="uniq_user_idempotency_key",
        )

        await self._notifications.create_index(
            [("channels.status", 1), ("channels.next_attempt_at", 1), ("priority", 1)],
            name="idx_channels_status_next_attempt_priority",
        )

        await self._notifications.create_index(
            [("user_id", 1), ("created_at", -1)],
            name="idx_user_created_at",
        )
        await self._notifications.create_index(
            [("template_id", 1), ("created_at", -1)],
            name="idx_template_created_at",
        )

        await self._templates.create_index([("name", 1)], unique=True, name="uniq_template_name")
        await self._users.create_index([("email", 1)], unique=True, name="uniq_user_email")

        # delivery_attempts indexes (operational)
        await self._attempts.create_index([("notification_id", 1), ("channel", 1), ("attempt_no", 1)], name="idx_attempts_lookup")
        await self._attempts.create_index([("created_at", -1)], name="idx_attempts_created_at")

    # ----------- API read/write (used by Step 6/7) -----------

    async def insert_notification(self, doc: Dict[str, Any]) -> str:
        try:
            res = await self._notifications.insert_one(doc)
            return str(res.inserted_id)
        except DuplicateKeyError as e:
            raise e

    async def find_by_user_and_idempotency(self, user_id: str, idempotency_key: str) -> Optional[Dict[str, Any]]:
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

    async def get_user_contact(self, user_id: str) -> Optional[Dict[str, Any]]:
        projection = {"_id": 1, "id": 1, "email": 1, "phone_number": 1, "name": 1}

        if ObjectId.is_valid(user_id):
            doc = await self._users.find_one({"_id": ObjectId(user_id)}, projection)
            if doc:
                return {
                    "user_id": str(doc.get("_id")),
                    "email": doc.get("email"),
                    "phone_number": doc.get("phone_number"),
                    "name": doc.get("name"),
                }
            return None

        doc = await self._users.find_one({"id": user_id}, projection)
        if not doc:
            return None
        return {
            "user_id": user_id,
            "email": doc.get("email"),
            "phone_number": doc.get("phone_number"),
            "name": doc.get("name"),
        }

    async def get_template_content(self, template_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch minimal template content for caching/lookup:
          - Supports Mongo ObjectId string in `_id`
          - Supports custom string ids stored in field `id`
        """
        projection = {"_id": 1, "id": 1, "name": 1, "subject": 1, "body": 1}

        if ObjectId.is_valid(template_id):
            doc = await self._templates.find_one({"_id": ObjectId(template_id)}, projection)
            if doc:
                return {
                    "template_id": str(doc.get("_id")),
                    "name": doc.get("name"),
                    "subject": doc.get("subject"),
                    "body": doc.get("body"),
                }
            return None

        doc = await self._templates.find_one({"id": template_id}, projection)
        if not doc:
            return None
        return {
            "template_id": template_id,
            "name": doc.get("name"),
            "subject": doc.get("subject"),
            "body": doc.get("body"),
        }

    # ----------- Worker primitives (Step 8) -----------

    async def claim_due_channel(self, now: datetime) -> Optional[Dict[str, Any]]:
        """
        Atomically claims ONE due channel by setting it to SENDING.
        Prioritizes HIGH first, then NORMAL, then LOW.
        Returns a minimal payload needed for provider call and updates.
        """
        query = {
            "channels": {
                "$elemMatch": {
                    "status": {"$in": ["QUEUED", "RETRY_DUE"]},
                    "next_attempt_at": {"$lte": now},
                }
            }
        }

        # Prefer HIGH first by sorting on priority
        sort = [("priority", 1), ("created_at", 1)]
        # priority stored as string: HIGH/NORMAL/LOW. We want HIGH first.
        # We'll map via query order using $addFields usually, but keep minimal:
        # Use a consistent priority rank field at write-time in next steps; for now, simple lexical isn't enough.
        # Minimal approach: attempt in HIGH/NORMAL/LOW order.
        for pr in ["HIGH", "NORMAL", "LOW"]:
            doc = await self._notifications.find_one_and_update(
                {
                    **query,
                    "priority": pr,
                },
                {
                    "$set": {
                        "channels.$[c].status": "SENDING",
                        "channels.$[c].updated_at": now,
                        "updated_at": now,
                    }
                },
                array_filters=[{"c.status": {"$in": ["QUEUED", "RETRY_DUE"]}, "c.next_attempt_at": {"$lte": now}}],
                return_document=ReturnDocument.AFTER,
            )
            if doc:
                # identify the claimed channel from channels array (SENDING with updated_at==now)
                claimed = None
                for ch in doc.get("channels", []):
                    if ch.get("status") == "SENDING" and ch.get("updated_at") == now:
                        claimed = ch
                        break
                if not claimed:
                    # fallback: pick first SENDING that is due
                    for ch in doc.get("channels", []):
                        if ch.get("status") == "SENDING":
                            claimed = ch
                            break

                if not claimed:
                    return None

                return {
                    "notification_id": str(doc["_id"]),
                    "user_id": doc["user_id"],
                    "template_id": doc["template_id"],
                    "template_params": doc.get("template_params", {}),
                    "priority": doc.get("priority", "NORMAL"),
                    "channel": claimed["channel"],
                    "attempt_count": int(claimed.get("attempt_count", 0)),
                }

        return None

    async def record_delivery_attempt(
        self,
        notification_id: str,
        channel: str,
        attempt_no: int,
        outcome: str,
        provider_status_code: Optional[int],
        provider_response: Optional[Dict[str, Any]],
        error: Optional[str],
        now: datetime,
    ) -> None:
        doc: Dict[str, Any] = {
            "notification_id": notification_id,
            "channel": channel,
            "attempt_no": attempt_no,
            "outcome": outcome,  # SUCCESS | FAILURE
            "provider_status_code": provider_status_code,
            "provider_response": provider_response,
            "error": error,
            "created_at": now,
        }
        await self._attempts.insert_one(doc)

    async def update_channel_after_attempt(
        self,
        notification_id: str,
        channel: str,
        new_status: str,
        attempt_count: int,
        next_attempt_at: Optional[datetime],
        last_error: Optional[str],
        now: datetime,
    ) -> None:
        """
        Updates per-channel fields after provider attempt.
        """
        update: Dict[str, Any] = {
            "channels.$.status": new_status,
            "channels.$.attempt_count": attempt_count,
            "channels.$.last_error": last_error,
            "channels.$.updated_at": now,
            "updated_at": now,
        }
        if next_attempt_at is not None:
            update["channels.$.next_attempt_at"] = next_attempt_at

        await self._notifications.update_one(
            {"_id": ObjectId(notification_id), "channels.channel": channel},
            {"$set": update},
        )
