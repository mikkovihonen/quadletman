from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from ..constraints import N_, FieldConstraints
from ..sanitized import (
    SafeMultilineStr,
    SafeResourceName,
    SafeSlug,
    SafeStr,
    SafeTimestamp,
    SafeUUID,
    enforce_model_safety,
)


@enforce_model_safety
class TemplateCreate(BaseModel):
    name: Annotated[
        SafeResourceName,
        FieldConstraints(
            description=N_("Template name"),
            label_hint=N_("lowercase, a-z 0-9 and hyphens"),
            placeholder=N_("my-template"),
        ),
    ]
    description: Annotated[
        SafeStr,
        FieldConstraints(
            description=N_("Description of this template"),
            label_hint=N_("free text"),
            placeholder=N_("Template description"),
        ),
    ] = SafeStr.trusted("", "default")
    source_compartment_id: SafeSlug


@enforce_model_safety
class TemplateInstantiate(BaseModel):
    """Body for POST /api/compartments/from-template/{template_id}."""

    compartment_id: Annotated[
        SafeSlug,
        FieldConstraints(
            description=N_("ID for the new compartment"),
            label_hint=N_("lowercase slug"),
            placeholder=N_("my-compartment"),
        ),
    ] = Field(..., description="New compartment ID (slug)")
    description: Annotated[
        SafeStr,
        FieldConstraints(
            description=N_("Description of this compartment"),
            label_hint=N_("free text"),
            placeholder=N_("Compartment description"),
        ),
    ] = SafeStr.trusted("", "default")

    @field_validator("compartment_id")
    @classmethod
    def validate_id(cls, v: str) -> SafeSlug:
        slug = SafeSlug.of(v, "compartment_id")
        if slug.startswith("qm-"):
            raise ValueError("Compartment ID must not start with 'qm-'")
        return slug


@enforce_model_safety
class Template(BaseModel):
    id: SafeUUID
    name: SafeResourceName
    description: SafeStr
    config_json: SafeMultilineStr
    created_at: SafeTimestamp
