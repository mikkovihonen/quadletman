from typing import Annotated, Literal

from pydantic import BaseModel, field_validator, model_validator

from ..sanitized import (
    SafeImageRef,
    SafeIntOrEmpty,
    SafePullPolicy,
    SafeResourceName,
    SafeSlug,
    SafeStr,
    SafeTimeDuration,
    SafeTimestamp,
    SafeUUID,
    enforce_model_safety,
)
from ..version_span import VersionSpan, enforce_model_version_gating
from .common import _loads


@enforce_model_version_gating(
    exempt={
        "name": "identity field — quadletman resource name, not a Quadlet key",
        "image": "image reference — always required for image units, not version-dependent",
    }
)
@enforce_model_safety
class ImageUnitCreate(BaseModel):
    name: SafeResourceName
    image: SafeImageRef | Literal[""] = SafeStr.trusted("", "default")
    auth_file: Annotated[SafeStr, VersionSpan(introduced=(4, 8, 0), quadlet_key="AuthFile")] = (
        SafeStr.trusted("", "default")
    )
    pull_policy: Annotated[
        SafePullPolicy,
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="PullPolicy",
        ),
    ] = SafePullPolicy.trusted("", "default")
    # Podman 4.8.0 (base image unit fields — gated by IMAGE_UNITS feature flag)
    all_tags: Annotated[bool, VersionSpan(introduced=(4, 8, 0), quadlet_key="AllTags")] = False
    arch: Annotated[SafeStr, VersionSpan(introduced=(4, 8, 0), quadlet_key="Arch")] = (
        SafeStr.trusted("", "default")
    )
    cert_dir: Annotated[SafeStr, VersionSpan(introduced=(4, 8, 0), quadlet_key="CertDir")] = (
        SafeStr.trusted("", "default")
    )
    creds: Annotated[SafeStr, VersionSpan(introduced=(4, 8, 0), quadlet_key="Creds")] = (
        SafeStr.trusted("", "default")
    )
    decryption_key: Annotated[
        SafeStr, VersionSpan(introduced=(4, 8, 0), quadlet_key="DecryptionKey")
    ] = SafeStr.trusted("", "default")
    os: Annotated[SafeStr, VersionSpan(introduced=(4, 8, 0), quadlet_key="OS")] = SafeStr.trusted(
        "", "default"
    )
    tls_verify: Annotated[bool, VersionSpan(introduced=(4, 8, 0), quadlet_key="TLSVerify")] = True
    variant: Annotated[SafeStr, VersionSpan(introduced=(4, 8, 0), quadlet_key="Variant")] = (
        SafeStr.trusted("", "default")
    )
    # Podman 5.0.0
    containers_conf_module: Annotated[
        SafeStr, VersionSpan(introduced=(5, 0, 0), quadlet_key="ContainersConfModule")
    ] = SafeStr.trusted("", "default")
    global_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 0, 0), quadlet_key="GlobalArgs")
    ] = []
    podman_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 0, 0), quadlet_key="PodmanArgs")
    ] = []
    # Podman 5.3.0
    service_name: Annotated[
        SafeStr, VersionSpan(introduced=(5, 3, 0), quadlet_key="ServiceName")
    ] = SafeStr.trusted("", "default")
    image_tags: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 3, 0), quadlet_key="ImageTag")
    ] = []
    # Podman 5.5.0
    retry: Annotated[SafeIntOrEmpty, VersionSpan(introduced=(5, 5, 0), quadlet_key="Retry")] = (
        SafeIntOrEmpty.trusted("", "default")
    )
    retry_delay: Annotated[
        SafeTimeDuration, VersionSpan(introduced=(5, 5, 0), quadlet_key="RetryDelay")
    ] = SafeTimeDuration.trusted("", "default")
    # Podman 5.6.0
    policy: Annotated[SafeStr, VersionSpan(introduced=(5, 6, 0), quadlet_key="Policy")] = (
        SafeStr.trusted("", "default")
    )

    @field_validator("image")
    @classmethod
    def validate_image(cls, v: str) -> SafeImageRef | Literal[""]:
        if not v:
            return v
        return SafeImageRef.of(v, "image")


@enforce_model_safety
class ImageUnit(ImageUnitCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        _loads(d, "global_args", "podman_args", "image_tags")
        for f in (
            "auth_file",
            "pull_policy",
            "arch",
            "cert_dir",
            "creds",
            "decryption_key",
            "os",
            "variant",
            "containers_conf_module",
            "service_name",
            "retry",
            "retry_delay",
            "policy",
        ):
            d.setdefault(f, "")
        d.setdefault("all_tags", 0)
        d.setdefault("tls_verify", 1)
        return d
