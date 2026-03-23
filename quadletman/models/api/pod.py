from typing import Annotated

from pydantic import BaseModel, model_validator

from ..constraints import RESOURCE_NAME_CN
from ..sanitized import (
    SafeByteSize,
    SafeIpAddress,
    SafePortMapping,
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
    }
)
@enforce_model_safety
class PodCreate(BaseModel):
    name: Annotated[SafeResourceName, RESOURCE_NAME_CN]
    network: Annotated[SafeStr, VersionSpan(introduced=(5, 0, 0), quadlet_key="Network")] = (
        SafeStr.trusted("", "default")
    )  # empty = use service default network
    publish_ports: Annotated[
        list[SafePortMapping], VersionSpan(introduced=(5, 0, 0), quadlet_key="PublishPort")
    ] = []
    # Podman 5.0.0 (base pod fields — gated by POD_UNITS feature flag)
    containers_conf_module: Annotated[
        SafeStr, VersionSpan(introduced=(5, 0, 0), quadlet_key="ContainersConfModule")
    ] = SafeStr.trusted("", "default")
    global_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 0, 0), quadlet_key="GlobalArgs")
    ] = []
    podman_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 0, 0), quadlet_key="PodmanArgs")
    ] = []
    volumes: Annotated[list[SafeStr], VersionSpan(introduced=(5, 0, 0), quadlet_key="Volume")] = []
    # Podman 5.3.0
    service_name: Annotated[
        SafeStr, VersionSpan(introduced=(5, 3, 0), quadlet_key="ServiceName")
    ] = SafeStr.trusted("", "default")
    dns: Annotated[list[SafeIpAddress], VersionSpan(introduced=(5, 3, 0), quadlet_key="DNS")] = []
    dns_search: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 3, 0), quadlet_key="DNSSearch")
    ] = []
    dns_option: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 3, 0), quadlet_key="DNSOption")
    ] = []
    ip: Annotated[SafeIpAddress, VersionSpan(introduced=(5, 3, 0), quadlet_key="IP")] = (
        SafeIpAddress.trusted("", "default")
    )
    ip6: Annotated[SafeIpAddress, VersionSpan(introduced=(5, 3, 0), quadlet_key="IP6")] = (
        SafeIpAddress.trusted("", "default")
    )
    user_ns: Annotated[SafeStr, VersionSpan(introduced=(5, 3, 0), quadlet_key="UserNS")] = (
        SafeStr.trusted("", "default")
    )
    add_host: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 3, 0), quadlet_key="AddHost")
    ] = []
    uid_map: Annotated[list[SafeStr], VersionSpan(introduced=(5, 3, 0), quadlet_key="UIDMap")] = []
    gid_map: Annotated[list[SafeStr], VersionSpan(introduced=(5, 3, 0), quadlet_key="GIDMap")] = []
    sub_uid_map: Annotated[SafeStr, VersionSpan(introduced=(5, 3, 0), quadlet_key="SubUIDMap")] = (
        SafeStr.trusted("", "default")
    )
    sub_gid_map: Annotated[SafeStr, VersionSpan(introduced=(5, 3, 0), quadlet_key="SubGIDMap")] = (
        SafeStr.trusted("", "default")
    )
    network_aliases: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 3, 0), quadlet_key="NetworkAlias")
    ] = []
    # Podman 5.4.0
    shm_size: Annotated[SafeByteSize, VersionSpan(introduced=(5, 4, 0), quadlet_key="ShmSize")] = (
        SafeByteSize.trusted("", "default")
    )
    # Podman 5.5.0
    hostname: Annotated[SafeStr, VersionSpan(introduced=(5, 5, 0), quadlet_key="HostName")] = (
        SafeStr.trusted("", "default")
    )
    # Podman 5.6.0
    labels: Annotated[
        dict[SafeStr, SafeStr], VersionSpan(introduced=(5, 6, 0), quadlet_key="Label")
    ] = {}
    exit_policy: Annotated[SafeStr, VersionSpan(introduced=(5, 6, 0), quadlet_key="ExitPolicy")] = (
        SafeStr.trusted("", "default")
    )
    # Podman 5.7.0
    stop_timeout: Annotated[
        SafeTimeDuration, VersionSpan(introduced=(5, 7, 0), quadlet_key="StopTimeout")
    ] = SafeTimeDuration.trusted("", "default")


@enforce_model_safety
class Pod(PodCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        _loads(
            d,
            "publish_ports",
            "global_args",
            "podman_args",
            "volumes",
            "dns",
            "dns_search",
            "dns_option",
            "add_host",
            "uid_map",
            "gid_map",
            "network_aliases",
            "labels",
        )
        for f in (
            "containers_conf_module",
            "service_name",
            "ip",
            "ip6",
            "user_ns",
            "sub_uid_map",
            "sub_gid_map",
            "shm_size",
            "hostname",
            "exit_policy",
            "stop_timeout",
            "network",
        ):
            d.setdefault(f, "")
        return d
