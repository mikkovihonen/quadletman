from typing import Annotated

from pydantic import BaseModel, model_validator

from ..constraints import AUTO_UPDATE_POLICY_CHOICES, N_, RESOURCE_NAME_CN, FieldConstraints
from ..sanitized import (
    SafeAutoUpdatePolicy,
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
        "name": "identity field — quadletman resource name, not a Quadlet key",
        "yaml_content": "Kubernetes YAML body — always required for kube units, not version-dependent",
        "config_map": "Kubernetes ConfigMap paths — available since kube units were introduced (4.4)",
    }
)
@enforce_model_safety
class KubeCreate(BaseModel):
    """Create a .kube Quadlet unit for Kubernetes YAML deployment."""

    name: Annotated[
        SafeResourceName,
        RESOURCE_NAME_CN,
        FieldConstraints(
            description=N_("Name of this Kubernetes unit"),
            label_hint=N_("lowercase, a-z 0-9 and hyphens"),
        ),
    ]
    yaml_content: Annotated[
        SafeMultilineStr,
        FieldConstraints(
            description=N_("Kubernetes YAML deployment manifest"),
            label_hint=N_("Kubernetes YAML"),
        ),
    ]
    # Podman 4.4.0 (base kube fields — gated by KUBE_UNITS feature flag)
    config_map: Annotated[
        list[SafeStr],
        FieldConstraints(
            description=N_("ConfigMap files to include"),
            label_hint=N_("file paths"),
        ),
    ] = []
    network: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Network"),
        FieldConstraints(
            description=N_("Network mode for the pod"),
            label_hint=N_("e.g. host, none, or compartment name"),
        ),
    ] = SafeStr.trusted("", "default")
    publish_ports: Annotated[
        list[SafePortMapping],
        VersionSpan(introduced=(4, 4, 0), quadlet_key="PublishPort"),
        FieldConstraints(
            description=N_("Ports published from the pod"),
            label_hint=N_("e.g. 8080:80"),
        ),
    ] = []
    # Podman 4.5.0
    log_driver: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 5, 0), quadlet_key="LogDriver"),
        FieldConstraints(
            description=N_("Logging driver for containers"),
            label_hint=N_("e.g. journald, k8s-file"),
        ),
    ] = SafeStr.trusted("", "default")
    user_ns: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 5, 0), quadlet_key="UserNS"),
        FieldConstraints(
            description=N_("User namespace mode"),
            label_hint=N_("e.g. auto, keep-id, host"),
        ),
    ] = SafeStr.trusted("", "default")
    # Podman 4.7.0
    auto_update: Annotated[
        SafeAutoUpdatePolicy,
        VersionSpan(introduced=(4, 7, 0), quadlet_key="AutoUpdate"),
        AUTO_UPDATE_POLICY_CHOICES,
        FieldConstraints(description=N_("Automatic image update policy")),
    ] = SafeAutoUpdatePolicy.trusted("", "default")
    # Podman 4.8.0
    exit_code_propagation: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 8, 0), quadlet_key="ExitCodePropagation"),
        FieldConstraints(
            description=N_("How container exit codes propagate to the pod"),
            label_hint=N_("e.g. all, any, none"),
        ),
    ] = SafeStr.trusted("", "default")
    # Podman 5.0.0
    containers_conf_module: Annotated[
        SafeStr,
        VersionSpan(introduced=(5, 0, 0), quadlet_key="ContainersConfModule"),
        FieldConstraints(
            description=N_("containers.conf module to load"),
            label_hint=N_("absolute path"),
        ),
    ] = SafeStr.trusted("", "default")
    global_args: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 0, 0), quadlet_key="GlobalArgs"),
        FieldConstraints(
            description=N_("Global Podman CLI arguments"),
            label_hint=N_("one per line"),
        ),
    ] = []
    podman_args: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 0, 0), quadlet_key="PodmanArgs"),
        FieldConstraints(
            description=N_("Additional Podman arguments"),
            label_hint=N_("one per line"),
        ),
    ] = []
    kube_down_force: Annotated[
        bool,
        VersionSpan(introduced=(5, 0, 0), quadlet_key="KubeDownForce"),
        FieldConstraints(description=N_("Force removal of pods on service stop")),
    ] = False
    # Podman 5.2.0
    set_working_directory: Annotated[
        SafeStr,
        VersionSpan(introduced=(5, 2, 0), quadlet_key="SetWorkingDirectory"),
        FieldConstraints(
            description=N_("Set working directory for Kubernetes pods"),
            label_hint=N_("absolute path"),
        ),
    ] = SafeStr.trusted("", "default")
    # Podman 5.3.0
    service_name: Annotated[
        SafeUnitName,
        VersionSpan(introduced=(5, 3, 0), quadlet_key="ServiceName"),
        FieldConstraints(
            description=N_("Override the systemd service name"),
            label_hint=N_("systemd unit name"),
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
