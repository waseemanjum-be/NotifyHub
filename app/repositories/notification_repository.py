# app/repositories/notification_repository.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
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
        await self._notifications.create_index(
            [("user_id", 1), ("idempotency_key", 1)],
            unique=True,
            name="uniq_user_idempotency_key",
        )
        await self._notifications.create_index(
            [("channels.status", 1), ("channels.next_attempt_at", 1), ("priority", 1)],
            name="idx_channels_status_next_attempt_priority",
        )
        await self._notifications.create_index([("user_id", 1), ("created_at", -1)], name="idx_user_created_at")
        await self._notifications.create_index([("template_id", 1), ("created_at", -1)], name="idx_template_created_at")
        await self._templates.create_index([("name", 1)], unique=True, name="uniq_template_name")
        await self._users.create_index([("email", 1)], unique=True, name="uniq_user_email")
        await self._attempts.create_index([("notification_id", 1), ("channel", 1), ("attempt_no", 1)], name="idx_attempts_lookup")
        await self._attempts.create_index([("created_at", -1)], name="idx_attempts_created_at")

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
                {"$set": {"channels.$[].status": "READ", "channels.$[].updated_at": now, "updated_at": now}},
            )
            return res.matched_count == 1

        res = await self._notifications.update_one(
            {"_id": ObjectId(notification_id), "channels.channel": channel},
            {"$set": {"channels.$.status": "READ", "channels.$.updated_at": now, "updated_at": now}},
        )
        return res.matched_count == 1

    async def get_user_contact(self, user_id: str) -> Optional[Dict[str, Any]]:
        projection = {"_id": 1, "id": 1, "email": 1, "phone_number": 1, "name": 1}
        if ObjectId.is_valid(user_id):
            doc = await self._users.find_one({"_id": ObjectId(user_id)}, projection)
            if doc:
                return {"user_id": str(doc.get("_id")), "email": doc.get("email"), "phone_number": doc.get("phone_number"), "name": doc.get("name")}
            return None
        doc = await self._users.find_one({"id": user_id}, projection)
        if not doc:
            return None
        return {"user_id": user_id, "email": doc.get("email"), "phone_number": doc.get("phone_number"), "name": doc.get("name")}

    async def get_template_content(self, template_id: str) -> Optional[Dict[str, Any]]:
        projection = {"_id": 1, "id": 1, "name": 1, "subject": 1, "body": 1}
        if ObjectId.is_valid(template_id):
            doc = await self._templates.find_one({"_id": ObjectId(template_id)}, projection)
            if doc:
                return {"template_id": str(doc.get("_id")), "name": doc.get("name"), "subject": doc.get("subject"), "body": doc.get("body")}
            return None
        doc = await self._templates.find_one({"id": template_id}, projection)
        if not doc:
            return None
        return {"template_id": template_id, "name": doc.get("name"), "subject": doc.get("subject"), "body": doc.get("body")}

    async def claim_due_channel(self, now: datetime) -> Optional[Dict[str, Any]]:
        query = {"channels": {"$elemMatch": {"status": {"$in": ["QUEUED", "RETRY_DUE"]}, "next_attempt_at": {"$lte": now}}}}

        for pr in ["HIGH", "NORMAL", "LOW"]:
            doc = await self._notifications.find_one_and_update(
                {**query, "priority": pr},
                {"$set": {"channels.$[c].status": "SENDING", "channels.$[c].updated_at": now, "updated_at": now}},
                array_filters=[{"c.status": {"$in": ["QUEUED", "RETRY_DUE"]}, "c.next_attempt_at": {"$lte": now}}],
                return_document=ReturnDocument.AFTER,
            )
            if doc:
                claimed = None
                for ch in doc.get("channels", []):
                    if ch.get("status") == "SENDING" and ch.get("updated_at") == now:
                        claimed = ch
                        break
                if not claimed:
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
        await self._attempts.insert_one(
            {
                "notification_id": notification_id,
                "channel": channel,
                "attempt_no": attempt_no,
                "outcome": outcome,
                "provider_status_code": provider_status_code,
                "provider_response": provider_response,
                "error": error,
                "created_at": now,
            }
        )

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

    async def apply_provider_receipt(self, notification_id: str, channel: str, new_status: str, now: datetime) -> bool:
        if not ObjectId.is_valid(notification_id):
            return False

        doc = await self._notifications.find_one({"_id": ObjectId(notification_id)}, {"channels": 1})
        if not doc:
            return False

        current = None
        for ch in doc.get("channels", []):
            if ch.get("channel") == channel:
                current = ch.get("status")
                break
        if current is None:
            return False

        if current == "FAILED":
            return True

        if new_status == "DELIVERED":
            if current == "READ":
                return True
        elif new_status == "READ":
            pass
        else:
            return False

        res = await self._notifications.update_one(
            {"_id": ObjectId(notification_id), "channels.channel": channel},
            {"$set": {"channels.$.status": new_status, "channels.$.updated_at": now, "updated_at": now}},
        )
        return res.matched_count == 1

    # ----------- Journey tracking (Step 10) -----------

    async def append_event(
        self,
        notification_id: str,
        event: Dict[str, Any],
    ) -> None:
        """
        Appends an event to notifications.events[].
        Event is expected to already contain timestamps/correlation fields.
        No-op if notification_id is invalid.
        """
        if not ObjectId.is_valid(notification_id):
            return

        await self._notifications.update_one(
            {"_id": ObjectId(notification_id)},
            {"$push": {"events": event}},
        )
