from typing import Annotated

from pydantic import BaseModel, model_validator

from ..constraints import (
    ABS_PATH_CN,
    AUTO_UPDATE_POLICY_CHOICES,
    IDENTIFIER_CN,
    N_,
    PORT_MAPPING_CN,
    RESOURCE_NAME_CN,
    UNIT_NAME_CN,
    FieldConstraints,
)
from ..sanitized import (
    SafeAbsPath,
    SafeAbsPathOrEmpty,
    SafeAutoUpdatePolicy,
    SafeIdentifier,
    SafeMultilineStr,
    SafePortMapping,
    SafeResourceName,
    SafeSlug,
    SafeStr,
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
        "qm_yaml_content": "Kubernetes YAML body — quadletman-managed inline content written to file",
    }
)
@enforce_model_safety
class KubeCreate(BaseModel):
    """Create a .kube Quadlet unit for Kubernetes YAML deployment."""

    qm_name: Annotated[
        SafeResourceName,
        RESOURCE_NAME_CN,
        FieldConstraints(
            description=N_("Name of this Kubernetes unit"),
            label_hint=N_("lowercase, a-z 0-9 and hyphens"),
            placeholder=N_("my-kube"),
        ),
    ]
    qm_yaml_content: Annotated[
        SafeMultilineStr,
        FieldConstraints(
            description=N_("Kubernetes YAML deployment manifest"),
            label_hint=N_("Kubernetes YAML"),
        ),
    ]
    # Podman 4.4.0 (base kube fields — gated by KUBE_UNITS feature flag)
    yaml: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Yaml"),
        ABS_PATH_CN,
        FieldConstraints(
            description=N_("Override the auto-generated YAML file path"),
            label_hint=N_("absolute path"),
            placeholder=N_("/path/to/deployment.yaml"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")
    config_map: Annotated[
        list[SafeAbsPath],
        VersionSpan(introduced=(4, 4, 0), quadlet_key="ConfigMap"),
        ABS_PATH_CN,
        FieldConstraints(
            description=N_("ConfigMap files to include"),
            label_hint=N_("file paths"),
            placeholder=N_("/path/to/configmap.yaml"),
        ),
    ] = []
    network: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Network"),
        FieldConstraints(
            description=N_("Network mode for the pod"),
            label_hint=N_("e.g. host, none, or compartment name"),
            placeholder=N_("host"),
        ),
    ] = SafeStr.trusted("", "default")
    publish_ports: Annotated[
        list[SafePortMapping],
        VersionSpan(introduced=(4, 4, 0), quadlet_key="PublishPort"),
        PORT_MAPPING_CN,
        FieldConstraints(
            description=N_("Ports published from the pod"),
            label_hint=N_("e.g. 8080:80"),
            placeholder=N_("8080:80/tcp"),
        ),
    ] = []
    # Podman 4.5.0
    log_driver: Annotated[
        SafeIdentifier,
        VersionSpan(introduced=(4, 5, 0), quadlet_key="LogDriver"),
        IDENTIFIER_CN,
        FieldConstraints(
            description=N_("Logging driver for containers"),
            label_hint=N_("e.g. journald, k8s-file"),
            placeholder=N_("journald"),
        ),
    ] = SafeIdentifier.trusted("", "default")
    user_ns: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 5, 0), quadlet_key="UserNS"),
        FieldConstraints(
            description=N_("User namespace mode"),
            label_hint=N_("e.g. auto, keep-id, host"),
            placeholder=N_("keep-id"),
        ),
    ] = SafeStr.trusted("", "default")
    # Podman 4.7.0
    auto_update: Annotated[
        SafeAutoUpdatePolicy,
        VersionSpan(introduced=(4, 7, 0), quadlet_key="AutoUpdate"),
        AUTO_UPDATE_POLICY_CHOICES,
        FieldConstraints(
            description=N_("Automatic image update policy"),
            label_hint=N_("checks for newer images"),
        ),
    ] = SafeAutoUpdatePolicy.trusted("", "default")
    # Podman 4.8.0
    exit_code_propagation: Annotated[
        SafeIdentifier,
        VersionSpan(introduced=(4, 8, 0), quadlet_key="ExitCodePropagation"),
        IDENTIFIER_CN,
        FieldConstraints(
            description=N_("How container exit codes propagate to the pod"),
            label_hint=N_("e.g. all, any, none"),
            placeholder=N_("all"),
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
            placeholder=N_("--userns=keep-id"),
        ),
    ] = []
    kube_down_force: Annotated[
        bool,
        VersionSpan(introduced=(5, 0, 0), quadlet_key="KubeDownForce"),
        FieldConstraints(
            description=N_("Force removal of pods on service stop"),
            label_hint=N_("forces removal on stop"),
        ),
    ] = False
    # Podman 5.2.0
    set_working_directory: Annotated[
        SafeAbsPathOrEmpty,
        VersionSpan(introduced=(5, 2, 0), quadlet_key="SetWorkingDirectory"),
        ABS_PATH_CN,
        FieldConstraints(
            description=N_("Set working directory for Kubernetes pods"),
            label_hint=N_("absolute path"),
            placeholder=N_("/app"),
        ),
    ] = SafeAbsPathOrEmpty.trusted("", "default")
    # Podman 5.3.0
    service_name: Annotated[
        SafeUnitName,
        VersionSpan(introduced=(5, 3, 0), quadlet_key="ServiceName"),
        UNIT_NAME_CN,
        FieldConstraints(
            description=N_("Override the systemd service name"),
            label_hint=N_("systemd unit name"),
            placeholder=N_("my-kube.service"),
        ),
    ] = SafeUnitName.trusted("", "default")


@enforce_model_safety
class Kube(KubeCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        _loads(d, "config_map", "publish_ports", "global_args", "podman_args")
        for f in (
            "yaml",
            "network",
            "log_driver",
            "user_ns",
            "auto_update",
            "exit_code_propagation",
            "containers_conf_module",
            "set_working_directory",
            "service_name",
        ):
            d.setdefault(f, "")
        d.setdefault("kube_down_force", 0)
        _sanitize_db_row(d, Kube)
        return d
