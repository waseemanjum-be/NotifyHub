# app/api/v1/routes.py

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.db.mongo import get_db
from app.schemas.notifications import (
    NotificationCreateRequest,
    NotificationCreateResponse,
    NotificationReadRequest,
    NotificationStatusResponse,
    ProviderReceiptRequest,
)
from app.services.notification_service import NotificationService

router = APIRouter()


def get_notification_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> NotificationService:
    return NotificationService(db=db)


@router.post(
    "/notifications",
    response_model=NotificationCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_notification(
    payload: NotificationCreateRequest,
    svc: NotificationService = Depends(get_notification_service),
) -> NotificationCreateResponse:
    return await svc.create_notification(payload)


@router.get(
    "/notifications/{notification_id}",
    response_model=NotificationStatusResponse,
)
async def get_notification(
    notification_id: str,
    svc: NotificationService = Depends(get_notification_service),
) -> NotificationStatusResponse:
    return await svc.get_notification_status(notification_id)


@router.post(
    "/notifications/{notification_id}/read",
    response_model=NotificationStatusResponse,
)
async def mark_notification_read(
    notification_id: str,
    payload: NotificationReadRequest,
    svc: NotificationService = Depends(get_notification_service),
) -> NotificationStatusResponse:
    return await svc.mark_read(notification_id, payload)


@router.post(
    "/notifications/{notification_id}/receipt",
    response_model=NotificationStatusResponse,
)
async def provider_receipt(
    notification_id: str,
    payload: ProviderReceiptRequest,
    x_provider_token: str | None = Header(default=None, alias="X-Provider-Token"),
    svc: NotificationService = Depends(get_notification_service),
) -> NotificationStatusResponse:
    """
    Optional provider callback endpoint:
    - Enabled purely by configuration.
    - If PROVIDER_CALLBACK_TOKEN is set, require matching X-Provider-Token header.
    """
    if settings.PROVIDER_CALLBACK_TOKEN:
        if not x_provider_token or x_provider_token != settings.PROVIDER_CALLBACK_TOKEN:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized provider callback")

    return await svc.apply_receipt(notification_id, payload)
