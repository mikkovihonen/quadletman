from typing import Annotated

from pydantic import BaseModel

from ..constraints import N_, SECRET_NAME_CN, FieldConstraints
from ..sanitized import (
    SafeSecretName,
    SafeSlug,
    SafeTimestamp,
    SafeUUID,
    enforce_model_safety,
)


@enforce_model_safety
class SecretCreate(BaseModel):
    name: Annotated[
        SafeSecretName,
        SECRET_NAME_CN,
        FieldConstraints(
            description=N_("Name of this secret"),
            label_hint=N_("alphanumeric, dots, hyphens"),
        ),
    ]


@enforce_model_safety
class Secret(SecretCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp
