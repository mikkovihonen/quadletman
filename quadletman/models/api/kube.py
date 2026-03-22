from typing import Annotated

from pydantic import BaseModel, model_validator

from ..sanitized import (
    SafeAutoUpdatePolicy,
    SafeMultilineStr,
    SafePortMapping,
    SafeResourceName,
    SafeSlug,
    SafeStr,
    SafeTimestamp,
    SafeUUID,
    enforce_model_safety,
)
from ..version_span import VersionSpan, enforce_model_version_gating
from .common import _loads


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

    name: SafeResourceName
    yaml_content: SafeMultilineStr
    # Podman 4.4.0 (base kube fields — gated by KUBE_UNITS feature flag)
    config_map: list[SafeStr] = []
    network: Annotated[SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="Network")] = (
        SafeStr.trusted("", "default")
    )
    publish_ports: Annotated[
        list[SafePortMapping], VersionSpan(introduced=(4, 4, 0), quadlet_key="PublishPort")
    ] = []
    # Podman 4.5.0
    log_driver: Annotated[SafeStr, VersionSpan(introduced=(4, 5, 0), quadlet_key="LogDriver")] = (
        SafeStr.trusted("", "default")
    )
    user_ns: Annotated[SafeStr, VersionSpan(introduced=(4, 5, 0), quadlet_key="UserNS")] = (
        SafeStr.trusted("", "default")
    )
    # Podman 4.7.0
    auto_update: Annotated[
        SafeAutoUpdatePolicy, VersionSpan(introduced=(4, 7, 0), quadlet_key="AutoUpdate")
    ] = SafeAutoUpdatePolicy.trusted("", "default")
    # Podman 4.8.0
    exit_code_propagation: Annotated[
        SafeStr, VersionSpan(introduced=(4, 8, 0), quadlet_key="ExitCodePropagation")
    ] = SafeStr.trusted("", "default")
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
    kube_down_force: Annotated[
        bool, VersionSpan(introduced=(5, 0, 0), quadlet_key="KubeDownForce")
    ] = False
    # Podman 5.2.0
    set_working_directory: Annotated[
        SafeStr, VersionSpan(introduced=(5, 2, 0), quadlet_key="SetWorkingDirectory")
    ] = SafeStr.trusted("", "default")
    # Podman 5.3.0
    service_name: Annotated[
        SafeStr, VersionSpan(introduced=(5, 3, 0), quadlet_key="ServiceName")
    ] = SafeStr.trusted("", "default")


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
        return d
