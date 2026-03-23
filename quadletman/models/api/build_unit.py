from typing import Annotated

from pydantic import BaseModel, field_validator, model_validator

from ..choices import PULL_POLICY_CHOICES
from ..sanitized import (
    SafeImageRef,
    SafeIntOrEmpty,
    SafeMultilineStr,
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
        "image_tag": "build output tag — always required for build units, not version-dependent",
        "containerfile_content": "inline Containerfile text managed by service layer, not a Quadlet key",
        "build_context": "filesystem path set by service layer after writing Containerfile, not user input",
        "build_file": "custom Containerfile filename set by service layer, not user input",
    }
)
@enforce_model_safety
class BuildUnitCreate(BaseModel):
    """Create a .build Quadlet unit that builds a container image from a Containerfile.

    Requires Podman 5.2.0+ (.build unit files).  On older Podman versions the
    "Locally Built Images" section is hidden entirely via feature-level gating
    (``podman.build_units``).
    """

    name: SafeResourceName
    image_tag: SafeImageRef
    containerfile_content: SafeMultilineStr = SafeMultilineStr.trusted("", "default")
    # build_context and build_file are set by the service layer, not user input
    build_context: SafeStr = SafeStr.trusted("", "default")
    build_file: SafeStr = SafeStr.trusted("", "default")
    # Podman 5.2.0 (base .build unit fields)
    annotation: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 2, 0), quadlet_key="Annotation")
    ] = []
    arch: Annotated[SafeStr, VersionSpan(introduced=(5, 2, 0), quadlet_key="Arch")] = (
        SafeStr.trusted("", "default")
    )
    auth_file: Annotated[SafeStr, VersionSpan(introduced=(5, 2, 0), quadlet_key="AuthFile")] = (
        SafeStr.trusted("", "default")
    )
    containers_conf_module: Annotated[
        SafeStr, VersionSpan(introduced=(5, 2, 0), quadlet_key="ContainersConfModule")
    ] = SafeStr.trusted("", "default")
    dns: Annotated[list[SafeStr], VersionSpan(introduced=(5, 2, 0), quadlet_key="DNS")] = []
    dns_option: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 2, 0), quadlet_key="DNSOption")
    ] = []
    dns_search: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 2, 0), quadlet_key="DNSSearch")
    ] = []
    env: Annotated[
        dict[SafeStr, SafeStr], VersionSpan(introduced=(5, 2, 0), quadlet_key="Environment")
    ] = {}
    force_rm: Annotated[bool, VersionSpan(introduced=(5, 2, 0), quadlet_key="ForceRM")] = False
    global_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 2, 0), quadlet_key="GlobalArgs")
    ] = []
    group_add: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 2, 0), quadlet_key="GroupAdd")
    ] = []
    label: Annotated[
        dict[SafeStr, SafeStr], VersionSpan(introduced=(5, 2, 0), quadlet_key="Label")
    ] = {}
    network: Annotated[SafeStr, VersionSpan(introduced=(5, 2, 0), quadlet_key="Network")] = (
        SafeStr.trusted("", "default")
    )
    podman_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 2, 0), quadlet_key="PodmanArgs")
    ] = []
    pull: Annotated[
        SafePullPolicy, VersionSpan(introduced=(5, 2, 0), quadlet_key="Pull"), PULL_POLICY_CHOICES
    ] = SafePullPolicy.trusted("", "default")
    secret: Annotated[list[SafeStr], VersionSpan(introduced=(5, 2, 0), quadlet_key="Secret")] = []
    target: Annotated[SafeStr, VersionSpan(introduced=(5, 2, 0), quadlet_key="Target")] = (
        SafeStr.trusted("", "default")
    )
    tls_verify: Annotated[bool, VersionSpan(introduced=(5, 2, 0), quadlet_key="TLSVerify")] = True
    variant: Annotated[SafeStr, VersionSpan(introduced=(5, 2, 0), quadlet_key="Variant")] = (
        SafeStr.trusted("", "default")
    )
    volume: Annotated[list[SafeStr], VersionSpan(introduced=(5, 2, 0), quadlet_key="Volume")] = []
    # Podman 5.3.0
    service_name: Annotated[
        SafeStr, VersionSpan(introduced=(5, 3, 0), quadlet_key="ServiceName")
    ] = SafeStr.trusted("", "default")
    # Podman 5.5.0
    retry: Annotated[SafeIntOrEmpty, VersionSpan(introduced=(5, 5, 0), quadlet_key="Retry")] = (
        SafeIntOrEmpty.trusted("", "default")
    )
    retry_delay: Annotated[
        SafeTimeDuration, VersionSpan(introduced=(5, 5, 0), quadlet_key="RetryDelay")
    ] = SafeTimeDuration.trusted("", "default")
    # Podman 5.7.0
    build_args: Annotated[
        dict[SafeStr, SafeStr], VersionSpan(introduced=(5, 7, 0), quadlet_key="BuildArg")
    ] = {}
    ignore_file: Annotated[SafeStr, VersionSpan(introduced=(5, 7, 0), quadlet_key="IgnoreFile")] = (
        SafeStr.trusted("", "default")
    )

    @field_validator("image_tag")
    @classmethod
    def validate_image_tag(cls, v: str) -> SafeImageRef:
        return SafeImageRef.of(v, "image_tag")


@enforce_model_safety
class BuildUnit(BuildUnitCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp
    updated_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        _loads(
            d,
            "annotation",
            "dns",
            "dns_option",
            "dns_search",
            "env",
            "global_args",
            "group_add",
            "label",
            "podman_args",
            "secret",
            "volume",
            "build_args",
        )
        for f in (
            "containerfile_content",
            "build_context",
            "build_file",
            "arch",
            "auth_file",
            "containers_conf_module",
            "network",
            "pull",
            "target",
            "variant",
            "service_name",
            "retry",
            "retry_delay",
            "ignore_file",
        ):
            d.setdefault(f, "")
        d.setdefault("force_rm", 0)
        d.setdefault("tls_verify", 1)
        return d
