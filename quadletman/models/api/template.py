from pydantic import BaseModel, Field, field_validator

from ..sanitized import (
    SafeMultilineStr,
    SafeSlug,
    SafeStr,
    SafeTimestamp,
    SafeUUID,
    enforce_model_safety,
)


@enforce_model_safety
class TemplateCreate(BaseModel):
    name: SafeStr
    description: SafeStr = SafeStr.trusted("", "default")
    source_compartment_id: SafeSlug


@enforce_model_safety
class TemplateInstantiate(BaseModel):
    """Body for POST /api/compartments/from-template/{template_id}."""

    compartment_id: SafeSlug = Field(..., description="New compartment ID (slug)")
    description: SafeStr = SafeStr.trusted("", "default")

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
    name: SafeStr
    description: SafeStr
    config_json: SafeMultilineStr
    created_at: SafeTimestamp
