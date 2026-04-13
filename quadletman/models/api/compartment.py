from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator

from ..constraints import (
    N_,
    SLUG_CN,
    FieldConstraints,
)
from ..sanitized import (
    SafeSlug,
    SafeStr,
    SafeTimestamp,
    SafeUsername,
    enforce_model_safety,
)
from .artifact import Artifact
from .build import Build
from .common import _sanitize_db_row
from .container import Container
from .image import Image
from .network import Network
from .pod import Pod
from .volume import Volume


@enforce_model_safety
class CompartmentCreate(BaseModel):
    id: Annotated[
        SafeSlug,
        SLUG_CN,
        FieldConstraints(
            description=N_("Unique identifier for this compartment"),
            label_hint=N_("lowercase slug"),
            placeholder=N_("my-compartment"),
        ),
    ] = Field(..., description="Slug used as compartment ID and user suffix")
    description: Annotated[
        SafeStr,
        FieldConstraints(
            description=N_("Description of this compartment"),
            label_hint=N_("free text"),
            placeholder=N_("My compartment description"),
        ),
    ] = SafeStr.trusted("", "default")

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> SafeSlug:
        slug = SafeSlug.of(v, "id")
        if slug.startswith("qm-"):
            raise ValueError("Compartment ID must not start with 'qm-'")
        return slug


@enforce_model_safety
class CompartmentUpdate(BaseModel):
    description: SafeStr | None = None

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str | None) -> SafeStr | None:
        if v is None:
            return v
        return SafeStr.of(v, "description")


@enforce_model_safety
class CompartmentStatus(BaseModel):
    compartment_id: SafeSlug
    containers: list[dict[SafeStr, SafeStr]] = []


@enforce_model_safety
class Compartment(BaseModel):
    id: SafeSlug
    description: SafeStr
    linux_user: SafeUsername
    created_at: SafeTimestamp
    updated_at: SafeTimestamp
    containers: list[Container] = []
    volumes: list[Volume] = []
    pods: list[Pod] = []
    images: list[Image] = []
    builds: list[Build] = []
    networks: list[Network] = []
    artifacts: list[Artifact] = []
    connection_monitor_enabled: bool = False
    process_monitor_enabled: bool = False
    connection_history_retention_days: int | None = None
    agent_last_seen: SafeTimestamp | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        d.setdefault("connection_monitor_enabled", 0)
        d.setdefault("process_monitor_enabled", 0)
        d.setdefault("connection_history_retention_days", None)
        d.setdefault("agent_last_seen", None)
        d.setdefault("containers", [])
        d.setdefault("volumes", [])
        d.setdefault("pods", [])
        d.setdefault("images", [])
        d.setdefault("networks", [])
        _sanitize_db_row(d, Compartment)
        return d
