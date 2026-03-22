from pydantic import BaseModel

from ..sanitized import (
    SafeSecretName,
    SafeSlug,
    SafeTimestamp,
    SafeUUID,
    enforce_model_safety,
)


@enforce_model_safety
class SecretCreate(BaseModel):
    name: SafeSecretName


@enforce_model_safety
class Secret(SecretCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp
