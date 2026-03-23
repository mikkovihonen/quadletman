from typing import Annotated

from pydantic import BaseModel, model_validator

from ..constraints import RESOURCE_NAME_CN
from ..sanitized import (
    SafeImageRef,
    SafeResourceName,
    SafeSlug,
    SafeStr,
    SafeTimestamp,
    SafeUUID,
    enforce_model_safety,
)
from ..version_span import VersionSpan, enforce_model_version_gating


@enforce_model_version_gating(
    exempt={
        "name": "identity field — quadletman resource name, not a Quadlet key",
        "image": "artifact image reference — always required, not version-dependent",
    }
)
@enforce_model_safety
class ArtifactCreate(BaseModel):
    """Create a .artifact Quadlet unit for OCI artifact management (Podman 5.7.0+)."""

    name: Annotated[SafeResourceName, RESOURCE_NAME_CN]
    image: SafeImageRef
    # Podman 5.7.0 (base artifact fields — gated by ARTIFACT_UNITS feature flag)
    digest: Annotated[SafeStr, VersionSpan(introduced=(5, 7, 0), quadlet_key="Digest")] = (
        SafeStr.trusted("", "default")
    )
    service_name: Annotated[
        SafeStr, VersionSpan(introduced=(5, 7, 0), quadlet_key="ServiceName")
    ] = SafeStr.trusted("", "default")


@enforce_model_safety
class Artifact(ArtifactCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        for f in ("digest", "service_name"):
            d.setdefault(f, "")
        return d
