from pydantic import BaseModel

from ..sanitized import (
    SafeMultilineStr,
    SafeSlug,
    SafeTimestamp,
    SafeUUID,
    enforce_model_safety,
)
from .common import _EventType


@enforce_model_safety
class SystemEvent(BaseModel):
    id: int
    compartment_id: SafeSlug | None
    container_id: SafeUUID | None
    event_type: _EventType
    message: SafeMultilineStr
    created_at: SafeTimestamp
