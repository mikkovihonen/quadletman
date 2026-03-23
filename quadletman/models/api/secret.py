from typing import Annotated

from pydantic import BaseModel

from ..constraints import SECRET_NAME_CN
from ..sanitized import (
    SafeSecretName,
    SafeSlug,
    SafeTimestamp,
    SafeUUID,
    enforce_model_safety,
)


@enforce_model_safety
class SecretCreate(BaseModel):
    name: Annotated[SafeSecretName, SECRET_NAME_CN]


@enforce_model_safety
class Secret(SecretCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp
