# app/scripts/seed_dev_data.py

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from app.core.config import settings
from app.db.mongo import close_mongo_connection, connect_to_mongo, get_db
from app.repositories.notification_repository import NotificationRepository


async def seed() -> None:
    # Only seed in dev
    # if settings.ENV.strip().lower() != "dev":
    #     return

    await connect_to_mongo()
    try:
        db = get_db()
        repo = NotificationRepository(db)
        await repo.create_indexes()

        users = db["users"]
        templates = db["notification_templates"]
        notifications = db["notifications"]
        attempts = db["delivery_attempts"]

        now = datetime.now(timezone.utc)

        # ----- Users (3) -----
        user_docs = [
            {"id": "user_001", "email": "dev.user1@example.com", "phone_number": "+10000000001", "name": "Dev User 1", "created_at": now},
            {"id": "user_002", "email": "dev.user2@example.com", "phone_number": "+10000000002", "name": "Dev User 2", "created_at": now},
            {"id": "user_003", "email": "dev.user3@example.com", "phone_number": "+10000000003", "name": "Dev User 3", "created_at": now},
        ]
        for doc in user_docs:
            await users.update_one({"email": doc["email"]}, {"$setOnInsert": doc}, upsert=True)

        # ----- Templates (3) -----
        template_docs = [
            {"id": "tpl_001", "name": "welcome", "subject": "Welcome", "body": "Hello {{name}}", "created_at": now},
            {"id": "tpl_002", "name": "otp", "subject": "Your OTP", "body": "Your code is {{code}}", "created_at": now},
            {"id": "tpl_003", "name": "receipt", "subject": "Payment Receipt", "body": "Thanks {{name}}. Amount: {{amount}}", "created_at": now},
        ]
        for doc in template_docs:
            await templates.update_one({"name": doc["name"]}, {"$setOnInsert": doc}, upsert=True)

        # ----- Notifications (3) -----
        # Use deterministic idempotency keys to allow repeatable runs without duplicates.
        idempotency_keys = [
            UUID("11111111-1111-4111-8111-111111111111"),
            UUID("22222222-2222-4222-8222-222222222222"),
            UUID("33333333-3333-4333-8333-333333333333"),
        ]

        notif_docs = [
            {
                "idempotency_key": str(idempotency_keys[0]),
                "user_id": "user_001",
                "template_id": "tpl_001",
                "template_params": {"name": "Dev User 1"},
                "channels": [
                    {"channel": "EMAIL", "status": "QUEUED", "attempt_count": 0, "last_error": None, "next_attempt_at": now, "created_at": now, "updated_at": now},
                    {"channel": "SMS", "status": "QUEUED", "attempt_count": 0, "last_error": None, "next_attempt_at": now, "created_at": now, "updated_at": now},
                ],
                "priority": "NORMAL",
                "events": [{"type": "ACCEPTED", "at": now, "channels": ["EMAIL", "SMS"]}],
                "created_at": now,
                "updated_at": now,
            },
            {
                "idempotency_key": str(idempotency_keys[1]),
                "user_id": "user_002",
                "template_id": "tpl_002",
                "template_params": {"code": "123456"},
                "channels": [
                    {"channel": "EMAIL", "status": "SENT", "attempt_count": 1, "last_error": None, "next_attempt_at": now, "created_at": now, "updated_at": now},
                ],
                "priority": "HIGH",
                "events": [{"type": "ACCEPTED", "at": now, "channels": ["EMAIL"]}],
                "created_at": now,
                "updated_at": now,
            },
            {
                "idempotency_key": str(idempotency_keys[2]),
                "user_id": "user_003",
                "template_id": "tpl_003",
                "template_params": {"name": "Dev User 3", "amount": "49.99"},
                "channels": [
                    {"channel": "PUSH", "status": "RETRY_DUE", "attempt_count": 2, "last_error": "timeout", "next_attempt_at": now, "created_at": now, "updated_at": now},
                ],
                "priority": "LOW",
                "events": [{"type": "ACCEPTED", "at": now, "channels": ["PUSH"]}],
                "created_at": now,
                "updated_at": now,
            },
        ]

        for doc in notif_docs:
            # Enforces idempotency uniqueness: (user_id, idempotency_key)
            existing = await notifications.find_one({"user_id": doc["user_id"], "idempotency_key": doc["idempotency_key"]})
            if existing is None:
                await notifications.insert_one(doc)

        # ----- Delivery attempts (3 total, minimal) -----
        # Insert a few attempts tied to seeded notifications if not already present.
        # We lookup notification docs by (user_id, idempotency_key) to get inserted _id.
        for doc in notif_docs:
            n = await notifications.find_one({"user_id": doc["user_id"], "idempotency_key": doc["idempotency_key"]}, {"_id": 1})
            if not n:
                continue
            nid = str(n["_id"])

            # Upsert a single attempt record per notification for dev visibility
            await attempts.update_one(
                {"notification_id": nid, "channel": doc["channels"][0]["channel"], "attempt_no": 1},
                {
                    "$setOnInsert": {
                        "notification_id": nid,
                        "channel": doc["channels"][0]["channel"],
                        "attempt_no": 1,
                        "outcome": "SUCCESS" if doc["channels"][0]["status"] == "SENT" else "FAILURE",
                        "provider_status_code": 200 if doc["channels"][0]["status"] == "SENT" else 504,
                        "provider_response": None,
                        "error": None if doc["channels"][0]["status"] == "SENT" else "timeout",
                        "created_at": now,
                    }
                },
                upsert=True,
            )

    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(seed())
