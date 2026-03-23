from typing import Annotated

from pydantic import BaseModel

from ..choices import EVENT_TYPE_CHOICES, FieldChoices
from ..sanitized import (
    SafeSlug,
    SafeStr,
    SafeTimestamp,
    SafeUUID,
    SafeWebhookUrl,
    enforce_model_safety,
)
from .common import _EventType


@enforce_model_safety
class NotificationHookCreate(BaseModel):
    container_name: Annotated[
        SafeStr, FieldChoices(dynamic=True, empty_label="— any container —")
    ] = SafeStr.trusted("", "default")  # empty = any container in compartment
    event_type: Annotated[_EventType, EVENT_TYPE_CHOICES] = "on_failure"
    webhook_url: SafeWebhookUrl
    webhook_secret: SafeStr = SafeStr.trusted("", "default")
    enabled: bool = True


@enforce_model_safety
class NotificationHook(NotificationHookCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp
