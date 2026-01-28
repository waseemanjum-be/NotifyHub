# app/workers/delivery_worker.py

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.core.config import settings
from app.db.mongo import connect_to_mongo, close_mongo_connection, get_db
from app.repositories.notification_repository import NotificationRepository
from app.services.provider_client import ProviderClient, ProviderResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay_seconds: int = 2
    max_delay_seconds: int = 300
    jitter_ratio: float = 0.2


def compute_next_attempt_at(
    now: datetime,
    attempt_no: int,
    policy: RetryPolicy,
) -> datetime:
    # Exponential backoff: base * 2^(attempt_no-1), capped + jitter
    delay = policy.base_delay_seconds * (2 ** max(0, attempt_no - 1))
    delay = min(delay, policy.max_delay_seconds)
    jitter = delay * policy.jitter_ratio
    delay = delay + random.uniform(-jitter, jitter)
    delay = max(0.0, delay)
    return now + timedelta(seconds=delay)


class DeliveryWorker:
    def __init__(self) -> None:
        self._db = get_db()
        self._repo = NotificationRepository(self._db)
        self._provider = ProviderClient()
        self._policy = RetryPolicy()

    async def start(self) -> None:
        await self._repo.create_indexes()

        logger.info("Delivery worker started")
        while True:
            now = datetime.now(timezone.utc)
            job = await self._repo.claim_due_channel(now=now)

            if not job:
                await asyncio.sleep(0.5)
                continue

            await self._process_job(job, now)

    async def _process_job(self, job: Dict[str, Any], now: datetime) -> None:
        notification_id = job["notification_id"]
        channel = job["channel"]
        attempt_count = int(job.get("attempt_count", 0))
        attempt_no = attempt_count + 1

        # Build provider payload (generic; provider decides how to handle it)
        provider_payload: Dict[str, Any] = {
            "notification_id": notification_id,
            "user_id": job["user_id"],
            "template_id": job["template_id"],
            "template_params": job.get("template_params", {}),
            "channel": channel,
            "priority": job.get("priority", "NORMAL"),
        }

        result = await self._provider.send(channel=channel, payload=provider_payload)

        if result.ok:
            # QUEUED/RETRY_DUE -> SENDING -> SENT
            await self._repo.record_delivery_attempt(
                notification_id=notification_id,
                channel=channel,
                attempt_no=attempt_no,
                outcome="SUCCESS",
                provider_status_code=result.status_code,
                provider_response=result.response_json,
                error=None,
                now=now,
            )
            await self._repo.update_channel_after_attempt(
                notification_id=notification_id,
                channel=channel,
                new_status="SENT",
                attempt_count=attempt_no,
                next_attempt_at=None,
                last_error=None,
                now=now,
            )
            logger.info(
                "Delivery success",
                extra={
                    "notification_id": notification_id,
                    "channel": channel,
                    "attempt_no": attempt_no,
                    "provider_status": result.status_code,
                },
            )
            return

        # Failure path: retry or fail permanently
        retryable = self._is_retryable(result)
        if retryable and attempt_no < self._policy.max_attempts:
            next_attempt_at = compute_next_attempt_at(now=now, attempt_no=attempt_no, policy=self._policy)
            await self._repo.record_delivery_attempt(
                notification_id=notification_id,
                channel=channel,
                attempt_no=attempt_no,
                outcome="FAILURE",
                provider_status_code=result.status_code,
                provider_response=result.response_json,
                error=result.error,
                now=now,
            )
            await self._repo.update_channel_after_attempt(
                notification_id=notification_id,
                channel=channel,
                new_status="RETRY_DUE",
                attempt_count=attempt_no,
                next_attempt_at=next_attempt_at,
                last_error=result.error,
                now=now,
            )
            logger.warning(
                "Delivery failed; scheduled retry",
                extra={
                    "notification_id": notification_id,
                    "channel": channel,
                    "attempt_no": attempt_no,
                    "next_attempt_at": next_attempt_at.isoformat(),
                    "provider_status": result.status_code,
                    "error": result.error,
                },
            )
            return

        # Permanent failure
        await self._repo.record_delivery_attempt(
            notification_id=notification_id,
            channel=channel,
            attempt_no=attempt_no,
            outcome="FAILURE",
            provider_status_code=result.status_code,
            provider_response=result.response_json,
            error=result.error,
            now=now,
        )
        await self._repo.update_channel_after_attempt(
            notification_id=notification_id,
            channel=channel,
            new_status="FAILED",
            attempt_count=attempt_no,
            next_attempt_at=None,
            last_error=result.error,
            now=now,
        )
        logger.error(
            "Delivery failed; marked FAILED",
            extra={
                "notification_id": notification_id,
                "channel": channel,
                "attempt_no": attempt_no,
                "provider_status": result.status_code,
                "error": result.error,
            },
        )

    def _is_retryable(self, result: ProviderResult) -> bool:
        if result.status_code is None:
            # Network/timeout errors
            return True
        return int(result.status_code) in set(settings.PROVIDER_RETRYABLE_STATUS_CODES)


async def run_worker() -> None:
    await connect_to_mongo()
    try:
        worker = DeliveryWorker()
        await worker.start()
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(run_worker())
