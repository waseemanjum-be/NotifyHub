from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class Channel(str, Enum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    PUSH = "PUSH"


class Priority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


class DeliveryStatus(str, Enum):
    QUEUED = "QUEUED"
    SENDING = "SENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    READ = "READ"
    RETRY_DUE = "RETRY_DUE"
    FAILED = "FAILED"


class NotificationCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    idempotency_key: UUID
    user_id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    template_params: Dict[str, Any] = Field(default_factory=dict)
    channels: List[Channel] = Field(min_length=1)
    priority: Priority = Priority.NORMAL

    @field_validator("channels")
    @classmethod
    def validate_channels(cls, v: List[Channel]) -> List[Channel]:
        if not v:
            raise ValueError("channels must contain at least one channel")
        if len(set(v)) != len(v):
            raise ValueError("channels must not contain duplicates")
        return v


class NotificationCreateResponse(BaseModel):
    model_config = {"extra": "forbid"}

    notification_id: str


class ChannelStatus(BaseModel):
    model_config = {"extra": "forbid"}

    channel: Channel
    status: DeliveryStatus
    attempt_count: int = 0
    last_error: Optional[str] = None


class NotificationStatusResponse(BaseModel):
    model_config = {"extra": "forbid"}

    notification_id: str
    user_id: str
    template_id: str
    priority: Priority
    overall_status: DeliveryStatus
    channels: List[ChannelStatus]


class NotificationReadRequest(BaseModel):
    model_config = {"extra": "forbid"}

    # If None, service may mark all channels as READ (handled in service layer).
    channel: Optional[Channel] = None
