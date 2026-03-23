from typing import Annotated

from pydantic import BaseModel, model_validator

from ..constraints import IMAGE_REF_CN, N_, RESOURCE_NAME_CN, UNIT_NAME_CN, FieldConstraints
from ..sanitized import (
    SafeImageRef,
    SafeResourceName,
    SafeSlug,
    SafeStr,
    SafeTimestamp,
    SafeUnitName,
    SafeUUID,
    enforce_model_safety,
)
from ..version_span import VersionSpan, enforce_model_version_gating
from .common import _sanitize_db_row


@enforce_model_version_gating(
    exempt={
        "name": "identity field — quadletman resource name, not a Quadlet key",
        "image": "artifact image reference — always required, not version-dependent",
    }
)
@enforce_model_safety
class ArtifactCreate(BaseModel):
    """Create a .artifact Quadlet unit for OCI artifact management (Podman 5.7.0+)."""

    name: Annotated[
        SafeResourceName,
        RESOURCE_NAME_CN,
        FieldConstraints(
            description=N_("Name of this artifact unit"),
            label_hint=N_("lowercase, a-z 0-9 and hyphens"),
            placeholder=N_("my-artifact"),
        ),
    ]
    image: Annotated[
        SafeImageRef,
        IMAGE_REF_CN,
        FieldConstraints(
            description=N_("OCI artifact image reference"),
            label_hint=N_("e.g. docker.io/library/nginx:latest"),
            placeholder=N_("docker.io/library/data:latest"),
        ),
    ]
    # Podman 5.7.0 (base artifact fields — gated by ARTIFACT_UNITS feature flag)
    digest: Annotated[
        SafeStr,
        VersionSpan(introduced=(5, 7, 0), quadlet_key="Digest"),
        FieldConstraints(
            description=N_("Content digest for the artifact"),
            label_hint=N_("OCI content digest"),
            placeholder=N_("sha256:abc123..."),
        ),
    ] = SafeStr.trusted("", "default")
    service_name: Annotated[
        SafeUnitName,
        VersionSpan(introduced=(5, 7, 0), quadlet_key="ServiceName"),
        UNIT_NAME_CN,
        FieldConstraints(
            description=N_("Override the systemd service name"),
            label_hint=N_("systemd unit name"),
            placeholder=N_("my-artifact.service"),
        ),
    ] = SafeUnitName.trusted("", "default")


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
        _sanitize_db_row(d, Artifact)
        return d
