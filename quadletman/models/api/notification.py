from pydantic import BaseModel

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
    container_name: SafeStr = SafeStr.trusted("", "default")  # empty = any container in compartment
    event_type: _EventType = "on_failure"
    webhook_url: SafeWebhookUrl
    webhook_secret: SafeStr = SafeStr.trusted("", "default")
    enabled: bool = True


@enforce_model_safety
class NotificationHook(NotificationHookCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp
