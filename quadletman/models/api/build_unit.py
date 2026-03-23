from typing import Annotated

from pydantic import BaseModel, field_validator, model_validator

from ..constraints import (
    ABS_PATH_CN,
    ANNOTATION_CN,
    ENV_VAR_NAME_CN,
    HOSTNAME_CN,
    IDENTIFIER_CN,
    IMAGE_REF_CN,
    INT_OR_EMPTY_CN,
    IP_ADDRESS_CN,
    N_,
    PULL_POLICY_CHOICES,
    RESOURCE_NAME_CN,
    TIME_DURATION_CN,
    UNIT_NAME_CN,
    USER_GROUP_REF_CN,
    FieldConstraints,
)
from ..sanitized import (
    SafeAbsPathOrEmpty,
    SafeEnvVarName,
    SafeHostname,
    SafeIdentifier,
    SafeImageRef,
    SafeIntOrEmpty,
    SafeIpAddress,
    SafeMultilineStr,
    SafePullPolicy,
    SafeResourceName,
    SafeSlug,
    SafeStr,
    SafeTimeDuration,
    SafeTimestamp,
    SafeUnitName,
    SafeUserGroupRef,
    SafeUUID,
    enforce_model_safety,
)
from ..version_span import VersionSpan, enforce_model_version_gating
from .common import _loads, _sanitize_db_row


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

    name: Annotated[
        SafeResourceName,
        RESOURCE_NAME_CN,
        FieldConstraints(
            description=N_("Name of this build unit"),
            label_hint=N_("lowercase, a-z 0-9 and hyphens"),
            placeholder=N_("my-build"),
        ),
    ]
    image_tag: Annotated[
        SafeImageRef,
        IMAGE_REF_CN,
        FieldConstraints(
            description=N_("Tag assigned to the built image"),
            label_hint=N_("e.g. docker.io/library/nginx:latest"),
            placeholder=N_("localhost/my-app:latest"),
        ),
    ]
    containerfile_content: Annotated[
        SafeMultilineStr,
        FieldConstraints(description=N_("Inline Containerfile content")),
    ] = SafeMultilineStr.trusted("", "default")
    # build_context and build_file are set by the service layer, not user input
    build_context: Annotated[
        SafeAbsPathOrEmpty,
        ABS_PATH_CN,
        FieldConstraints(
            description=N_("Build context directory path"),
            label_hint=N_("absolute path"),
            placeholder=N_("/home/user/build"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")
    build_file: Annotated[
        SafeIdentifier,
        IDENTIFIER_CN,
        FieldConstraints(
            description=N_("Custom Containerfile filename"),
            label_hint=N_("e.g. Containerfile"),
            placeholder=N_("Containerfile"),
        ),
    ] = SafeIdentifier.trusted("", "default")
    # Podman 5.2.0 (base .build unit fields)
    annotation: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 2, 0), quadlet_key="Annotation"),
        ANNOTATION_CN,
        FieldConstraints(
            description=N_("Annotations attached to the built image"),
            label_hint=N_("key=value pairs"),
            placeholder=N_("key=value"),
        ),
    ] = []
    arch: Annotated[
        SafeIdentifier,
        VersionSpan(introduced=(5, 2, 0), quadlet_key="Arch"),
        IDENTIFIER_CN,
        FieldConstraints(
            description=N_("Target CPU architecture"),
            label_hint=N_("e.g. amd64, arm64"),
            placeholder=N_("amd64"),
        ),
    ] = SafeIdentifier.trusted("", "default")
    auth_file: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(introduced=(5, 2, 0), quadlet_key="AuthFile"),
        ABS_PATH_CN,
        FieldConstraints(
            description=N_("Registry authentication file path"),
            label_hint=N_("absolute path"),
            placeholder=N_("/run/containers/auth.json"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")
    containers_conf_module: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(introduced=(5, 2, 0), quadlet_key="ContainersConfModule"),
        ABS_PATH_CN,
        FieldConstraints(
            description=N_("containers.conf module to load"),
            label_hint=N_("absolute path"),
            placeholder=N_("/etc/containers/containers.conf.d/custom.conf"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")
    dns: Annotated[
        list[SafeIpAddress],
        VersionSpan(introduced=(5, 2, 0), quadlet_key="DNS"),
        IP_ADDRESS_CN,
        FieldConstraints(
            description=N_("Custom DNS servers"),
            label_hint=N_("e.g. 10.88.0.5"),
            placeholder=N_("10.88.0.1"),
        ),
    ] = []
    dns_option: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 2, 0), quadlet_key="DNSOption"),
        FieldConstraints(
            description=N_("DNS resolver options"),
            label_hint=N_("one per line"),
            placeholder=N_("ndots:5"),
        ),
    ] = []
    dns_search: Annotated[
        list[SafeHostname],
        VersionSpan(introduced=(5, 2, 0), quadlet_key="DNSSearch"),
        HOSTNAME_CN,
        FieldConstraints(
            description=N_("DNS search domains"),
            label_hint=N_("domain names"),
            placeholder=N_("example.com"),
        ),
    ] = []
    env: Annotated[
        dict[SafeEnvVarName, SafeStr],
        VersionSpan(introduced=(5, 2, 0), quadlet_key="Environment"),
        ENV_VAR_NAME_CN,
        FieldConstraints(
            description=N_("Build-time environment variables"),
            label_hint=N_("key=value pairs"),
            placeholder=N_("MY_VAR=my-value"),
        ),
    ] = {}
    force_rm: Annotated[
        bool,
        VersionSpan(introduced=(5, 2, 0), quadlet_key="ForceRM"),
        FieldConstraints(
            description=N_("Remove intermediate build containers"),
            label_hint=N_("cleans up after build"),
        ),
    ] = False
    global_args: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 2, 0), quadlet_key="GlobalArgs"),
        FieldConstraints(
            description=N_("Global Podman CLI arguments"),
            label_hint=N_("one per line"),
            placeholder=N_("--log-level=debug"),
        ),
    ] = []
    group_add: Annotated[
        list[SafeUserGroupRef],
        VersionSpan(introduced=(5, 2, 0), quadlet_key="GroupAdd"),
        USER_GROUP_REF_CN,
        FieldConstraints(
            description=N_("Additional groups for the build process"),
            label_hint=N_("GID or group name"),
            placeholder=N_("video"),
        ),
    ] = []
    label: Annotated[
        dict[SafeStr, SafeStr],
        VersionSpan(introduced=(5, 2, 0), quadlet_key="Label"),
        FieldConstraints(
            description=N_("Labels applied to the built image"),
            label_hint=N_("key=value pairs"),
            placeholder=N_("version=1.0"),
        ),
    ] = {}
    network: Annotated[
        SafeIdentifier,
        VersionSpan(introduced=(5, 2, 0), quadlet_key="Network"),
        IDENTIFIER_CN,
        FieldConstraints(
            description=N_("Network mode for the build"),
            label_hint=N_("e.g. host, none, or compartment name"),
            placeholder=N_("host"),
        ),
    ] = SafeIdentifier.trusted("", "default")
    podman_args: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 2, 0), quadlet_key="PodmanArgs"),
        FieldConstraints(
            description=N_("Additional Podman arguments"),
            label_hint=N_("one per line"),
            placeholder=N_("--squash"),
        ),
    ] = []
    pull: Annotated[
        SafePullPolicy,
        VersionSpan(introduced=(5, 2, 0), quadlet_key="Pull"),
        PULL_POLICY_CHOICES,
        FieldConstraints(
            description=N_("Image pull policy for the base image"),
            label_hint=N_("when to pull the base image"),
        ),
    ] = SafePullPolicy.trusted("", "default")
    secret: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 2, 0), quadlet_key="Secret"),
        FieldConstraints(
            description=N_("Secrets available during build"),
            label_hint=N_("alphanumeric, dots, hyphens"),
            placeholder=N_("my-secret"),
        ),
    ] = []
    target: Annotated[
        SafeIdentifier,
        VersionSpan(introduced=(5, 2, 0), quadlet_key="Target"),
        IDENTIFIER_CN,
        FieldConstraints(
            description=N_("Multi-stage build target"),
            label_hint=N_("stage name"),
            placeholder=N_("production"),
        ),
    ] = SafeIdentifier.trusted("", "default")
    tls_verify: Annotated[
        bool,
        VersionSpan(introduced=(5, 2, 0), quadlet_key="TLSVerify"),
        FieldConstraints(
            description=N_("Verify TLS certificates for registries"),
            label_hint=N_("default: on"),
        ),
    ] = True
    variant: Annotated[
        SafeIdentifier,
        VersionSpan(introduced=(5, 2, 0), quadlet_key="Variant"),
        IDENTIFIER_CN,
        FieldConstraints(
            description=N_("Target image variant"),
            label_hint=N_("e.g. v8"),
            placeholder=N_("v8"),
        ),
    ] = SafeIdentifier.trusted("", "default")
    volume: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 2, 0), quadlet_key="Volume"),
        FieldConstraints(
            description=N_("Volumes mounted during build"),
            label_hint=N_("one per line"),
            placeholder=N_("/data:/data:Z"),
        ),
    ] = []
    # Podman 5.3.0
    service_name: Annotated[
        SafeUnitName,
        VersionSpan(introduced=(5, 3, 0), quadlet_key="ServiceName"),
        UNIT_NAME_CN,
        FieldConstraints(
            description=N_("Override the systemd service name"),
            label_hint=N_("systemd unit name"),
            placeholder=N_("my-build.service"),
        ),
    ] = SafeUnitName.trusted("", "default")
    # Podman 5.5.0
    retry: Annotated[
        SafeIntOrEmpty,
        VersionSpan(introduced=(5, 5, 0), quadlet_key="Retry"),
        INT_OR_EMPTY_CN,
        FieldConstraints(
            description=N_("Number of pull retries"),
            label_hint=N_("integer"),
            placeholder="3",
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    retry_delay: Annotated[
        SafeTimeDuration,
        VersionSpan(introduced=(5, 5, 0), quadlet_key="RetryDelay"),
        TIME_DURATION_CN,
        FieldConstraints(
            description=N_("Delay between pull retries"),
            label_hint=N_("e.g. 30s, 5min"),
            placeholder=N_("5s"),
        ),
    ] = SafeTimeDuration.trusted("", "default")
    # Podman 5.7.0
    build_args: Annotated[
        dict[SafeStr, SafeStr],
        VersionSpan(introduced=(5, 7, 0), quadlet_key="BuildArg"),
        FieldConstraints(
            description=N_("Build-time arguments passed to the Containerfile"),
            label_hint=N_("key=value pairs"),
            placeholder=N_("VERSION=1.0"),
        ),
    ] = {}
    ignore_file: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(introduced=(5, 7, 0), quadlet_key="IgnoreFile"),
        ABS_PATH_CN,
        FieldConstraints(
            description=N_("Path to container ignore file"),
            label_hint=N_("absolute path"),
            placeholder=N_("/path/to/.containerignore"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")

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
        _sanitize_db_row(d, BuildUnit)
        return d
