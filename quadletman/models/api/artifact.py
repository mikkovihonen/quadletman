from typing import Annotated

from pydantic import BaseModel, model_validator

from ..constraints import (
    ABS_PATH_CN,
    IMAGE_REF_CN,
    N_,
    RESOURCE_NAME_CN,
    UNIT_NAME_CN,
    FieldConstraints,
)
from ..sanitized import (
    SafeAbsPathOrEmpty,
    SafeImageRef,
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
    }
)
@enforce_model_safety
class ArtifactCreate(BaseModel):
    """Create a .artifact Quadlet unit for OCI artifact management (Podman 5.7.0+)."""

    qm_name: Annotated[
        SafeResourceName,
        RESOURCE_NAME_CN,
        FieldConstraints(
            description=N_("Name of this artifact unit"),
            label_hint=N_("lowercase, a-z 0-9 and hyphens"),
            placeholder=N_("my-artifact"),
        ),
    ]
    artifact: Annotated[
        SafeImageRef,
        VersionSpan(introduced=(5, 7, 0), quadlet_key="Artifact"),
        IMAGE_REF_CN,
        FieldConstraints(
            description=N_("OCI artifact image reference"),
            label_hint=N_("e.g. docker.io/library/nginx:latest"),
            placeholder=N_("docker.io/library/data:latest"),
        ),
    ]
    # Podman 5.7.0 (base artifact fields — gated by ARTIFACT_UNITS feature flag)
    auth_file: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(introduced=(5, 7, 0), quadlet_key="AuthFile"),
        ABS_PATH_CN,
        FieldConstraints(
            description=N_("Registry authentication file path"),
            label_hint=N_("absolute path"),
            placeholder=N_("/run/containers/auth.json"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")
    cert_dir: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(introduced=(5, 7, 0), quadlet_key="CertDir"),
        ABS_PATH_CN,
        FieldConstraints(
            description=N_("Directory with TLS certificates"),
            label_hint=N_("absolute path"),
            placeholder=N_("/etc/containers/certs.d"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")
    containers_conf_module: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(introduced=(5, 7, 0), quadlet_key="ContainersConfModule"),
        ABS_PATH_CN,
        FieldConstraints(
            description=N_("containers.conf module to load"),
            label_hint=N_("absolute path"),
            placeholder=N_("/etc/containers/containers.conf.d/custom.conf"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")
    creds: Annotated[
        SafeStr,
        VersionSpan(introduced=(5, 7, 0), quadlet_key="Creds"),
        FieldConstraints(
            description=N_("Registry credentials"),
            label_hint=N_("user:password"),
            placeholder=N_("user:password"),
        ),
    ] = SafeStr.trusted("", "default")
    decryption_key: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(introduced=(5, 7, 0), quadlet_key="DecryptionKey"),
        ABS_PATH_CN,
        FieldConstraints(
            description=N_("Decryption key for encrypted images"),
            label_hint=N_("absolute path"),
            placeholder=N_("/path/to/key.pem"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")
    global_args: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 7, 0), quadlet_key="GlobalArgs"),
        FieldConstraints(
            description=N_("Global Podman CLI arguments"),
            label_hint=N_("one per line"),
            placeholder=N_("--log-level=debug"),
        ),
    ] = []
    podman_args: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 7, 0), quadlet_key="PodmanArgs"),
        FieldConstraints(
            description=N_("Additional Podman arguments"),
            label_hint=N_("one per line"),
            placeholder=N_("--tls-verify=false"),
        ),
    ] = []
    quiet: Annotated[
        bool,
        VersionSpan(introduced=(5, 7, 0), quadlet_key="Quiet"),
        FieldConstraints(
            description=N_("Suppress output during pull"),
            label_hint=N_("quieter operation"),
        ),
    ] = False
    retry: Annotated[
        SafeIntOrEmpty,
        VersionSpan(introduced=(5, 7, 0), quadlet_key="Retry"),
        FieldConstraints(
            description=N_("Number of pull retries"),
            label_hint=N_("integer"),
            placeholder="3",
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    retry_delay: Annotated[
        SafeTimeDuration,
        VersionSpan(introduced=(5, 7, 0), quadlet_key="RetryDelay"),
        FieldConstraints(
            description=N_("Delay between pull retries"),
            label_hint=N_("e.g. 30s, 5min"),
            placeholder=N_("5s"),
        ),
    ] = SafeTimeDuration.trusted("", "default")
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
    tls_verify: Annotated[
        bool,
        VersionSpan(introduced=(5, 7, 0), quadlet_key="TLSVerify"),
        FieldConstraints(
            description=N_("Verify TLS certificates for registries"),
            label_hint=N_("default: on"),
        ),
    ] = True


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
        _loads(d, "global_args", "podman_args")
        for f in (
            "auth_file",
            "cert_dir",
            "containers_conf_module",
            "creds",
            "decryption_key",
            "retry",
            "retry_delay",
            "service_name",
        ):
            d.setdefault(f, "")
        d.setdefault("quiet", 0)
        d.setdefault("tls_verify", 1)
        _sanitize_db_row(d, Artifact)
        return d
