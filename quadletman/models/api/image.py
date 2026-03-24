from typing import Annotated

from pydantic import BaseModel, model_validator

from ..constraints import (
    ABS_PATH_CN,
    IDENTIFIER_CN,
    IMAGE_REF_CN,
    INT_OR_EMPTY_CN,
    N_,
    RESOURCE_NAME_CN,
    TIME_DURATION_CN,
    UNIT_NAME_CN,
    FieldConstraints,
)
from ..sanitized import (
    SafeAbsPathOrEmpty,
    SafeIdentifier,
    SafeImageRef,
    SafeImageRefOrEmpty,
    SafeIntOrEmpty,
    SafeResourceName,
    SafeSlug,
    SafeStr,
    SafeTimeDuration,
    SafeTimestamp,
    SafeUnitName,
    SafeUUID,
    enforce_model_safety,
)
from ..version_span import VersionSpan, enforce_model_version_gating
from .common import _loads, _sanitize_db_row


@enforce_model_version_gating(
    exempt={
        "qm_name": "identity field — quadletman resource name, not a Quadlet key",
        "image": "image reference — always required for image units, not version-dependent",
    }
)
@enforce_model_safety
class ImageCreate(BaseModel):
    qm_name: Annotated[
        SafeResourceName,
        RESOURCE_NAME_CN,
        FieldConstraints(
            description=N_("Name of this image unit"),
            label_hint=N_("lowercase, a-z 0-9 and hyphens"),
            placeholder=N_("my-image"),
        ),
    ]
    image: Annotated[
        SafeImageRefOrEmpty,
        IMAGE_REF_CN,
        FieldConstraints(
            description=N_("Image reference to pull"),
            label_hint=N_("e.g. docker.io/library/nginx:latest"),
            placeholder=N_("docker.io/library/nginx:latest"),
        ),
    ] = SafeImageRefOrEmpty.trusted("", "default")
    auth_file: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(introduced=(4, 8, 0), quadlet_key="AuthFile"),
        ABS_PATH_CN,
        FieldConstraints(
            description=N_("Registry authentication file path"),
            label_hint=N_("absolute path"),
            placeholder=N_("/run/containers/auth.json"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")
    # Podman 4.8.0 (base image unit fields — gated by IMAGE_UNITS feature flag)
    all_tags: Annotated[
        bool,
        VersionSpan(introduced=(4, 8, 0), quadlet_key="AllTags"),
        FieldConstraints(
            description=N_("Pull all tags for the image"),
            label_hint=N_("downloads every tag for the image"),
        ),
    ] = False
    arch: Annotated[
        SafeIdentifier,
        VersionSpan(introduced=(4, 8, 0), quadlet_key="Arch"),
        IDENTIFIER_CN,
        FieldConstraints(
            description=N_("Target CPU architecture"),
            label_hint=N_("e.g. amd64, arm64"),
            placeholder=N_("amd64"),
        ),
    ] = SafeIdentifier.trusted("", "default")
    cert_dir: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(introduced=(4, 8, 0), quadlet_key="CertDir"),
        ABS_PATH_CN,
        FieldConstraints(
            description=N_("Directory with TLS certificates for the registry"),
            label_hint=N_("absolute path"),
            placeholder=N_("/etc/containers/certs.d"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")
    creds: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 8, 0), quadlet_key="Creds"),
        FieldConstraints(
            description=N_("Registry credentials"),
            label_hint=N_("user:password"),
            placeholder=N_("user:password"),
        ),
    ] = SafeStr.trusted("", "default")
    decryption_key: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 8, 0), quadlet_key="DecryptionKey"),
        FieldConstraints(
            description=N_("Key for decrypting the image"),
            label_hint=N_("key or passphrase"),
            placeholder=N_("/path/to/key.pem"),
        ),
    ] = SafeStr.trusted("", "default")
    os: Annotated[
        SafeIdentifier,
        VersionSpan(introduced=(4, 8, 0), quadlet_key="OS"),
        IDENTIFIER_CN,
        FieldConstraints(
            description=N_("Target operating system"),
            label_hint=N_("e.g. linux"),
            placeholder=N_("linux"),
        ),
    ] = SafeIdentifier.trusted("", "default")
    tls_verify: Annotated[
        bool,
        VersionSpan(introduced=(4, 8, 0), quadlet_key="TLSVerify"),
        FieldConstraints(
            description=N_("Verify TLS certificates for registries"),
            label_hint=N_("default: on"),
        ),
    ] = True
    variant: Annotated[
        SafeIdentifier,
        VersionSpan(introduced=(4, 8, 0), quadlet_key="Variant"),
        IDENTIFIER_CN,
        FieldConstraints(
            description=N_("Target image variant"),
            label_hint=N_("e.g. v8"),
            placeholder=N_("v8"),
        ),
    ] = SafeIdentifier.trusted("", "default")
    # Podman 5.0.0
    containers_conf_module: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(introduced=(5, 0, 0), quadlet_key="ContainersConfModule"),
        ABS_PATH_CN,
        FieldConstraints(
            description=N_("containers.conf module to load"),
            label_hint=N_("absolute path"),
            placeholder=N_("/etc/containers/containers.conf.d/custom.conf"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")
    global_args: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 0, 0), quadlet_key="GlobalArgs"),
        FieldConstraints(
            description=N_("Global Podman CLI arguments"),
            label_hint=N_("one per line"),
            placeholder=N_("--log-level=debug"),
        ),
    ] = []
    podman_args: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 0, 0), quadlet_key="PodmanArgs"),
        FieldConstraints(
            description=N_("Additional Podman arguments"),
            label_hint=N_("one per line"),
            placeholder=N_("--tls-verify=false"),
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
            placeholder=N_("my-image.service"),
        ),
    ] = SafeUnitName.trusted("", "default")
    image_tags: Annotated[
        list[SafeImageRef],
        VersionSpan(introduced=(5, 3, 0), quadlet_key="ImageTag"),
        IMAGE_REF_CN,
        FieldConstraints(
            description=N_("Additional tags to apply to the pulled image"),
            label_hint=N_("e.g. docker.io/library/nginx:latest"),
            placeholder=N_("docker.io/library/nginx:stable"),
        ),
    ] = []
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
    # Podman 5.6.0
    policy: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(introduced=(5, 6, 0), quadlet_key="Policy"),
        ABS_PATH_CN,
        FieldConstraints(
            description=N_("Image signature verification policy"),
            label_hint=N_("absolute path to JSON"),
            placeholder=N_("/etc/containers/policy.json"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")


@enforce_model_safety
class Image(ImageCreate):
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
        _sanitize_db_row(d, Image)
        return d
