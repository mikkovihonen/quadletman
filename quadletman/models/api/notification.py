from typing import Annotated

from pydantic import BaseModel

from ..constraints import EVENT_TYPE_CHOICES, N_, WEBHOOK_URL_CN, FieldChoices, FieldConstraints
from ..sanitized import (
    SafeResourceNameOrEmpty,
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
        SafeResourceNameOrEmpty,
        FieldChoices(dynamic=True, empty_label="— any container —"),
        FieldConstraints(
            description=N_("Container to watch, or any"),
            label_hint=N_("leave empty for any container"),
        ),
    ] = SafeResourceNameOrEmpty.trusted("", "default")  # empty = any container in compartment
    event_type: Annotated[
        _EventType,
        EVENT_TYPE_CHOICES,
        FieldConstraints(
            description=N_("Event that triggers the webhook"),
            label_hint=N_("what triggers the webhook"),
        ),
    ] = "on_failure"
    webhook_url: Annotated[
        SafeWebhookUrl,
        WEBHOOK_URL_CN,
        FieldConstraints(
            description=N_("URL to receive the webhook notification"),
            label_hint=N_("https://..."),
            placeholder=N_("https://hooks.example.com/notify"),
        ),
    ]
    webhook_secret: Annotated[
        SafeStr,
        FieldConstraints(
            description=N_("Shared secret for webhook authentication"),
            label_hint=N_("shared secret"),
            placeholder=N_("my-secret-key"),
        ),
    ] = SafeStr.trusted("", "default")
    enabled: Annotated[
        bool,
        FieldConstraints(
            description=N_("Whether this hook is active"),
            label_hint=N_("default: on"),
        ),
    ] = True


@enforce_model_safety
class NotificationHook(NotificationHookCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp
