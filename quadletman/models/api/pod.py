from typing import Annotated

from pydantic import BaseModel, model_validator

from ..constraints import N_, RESOURCE_NAME_CN, FieldConstraints
from ..sanitized import (
    SafeByteSize,
    SafeIpAddress,
    SafePortMapping,
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
        "name": "identity field — quadletman resource name, not a Quadlet key",
    }
)
@enforce_model_safety
class PodCreate(BaseModel):
    name: Annotated[
        SafeResourceName,
        RESOURCE_NAME_CN,
        FieldConstraints(
            description=N_("Name of this pod"),
            label_hint=N_("lowercase, a-z 0-9 and hyphens"),
            placeholder=N_("my-pod"),
        ),
    ]
    network: Annotated[
        SafeStr,
        VersionSpan(introduced=(5, 0, 0), quadlet_key="Network"),
        FieldConstraints(
            description=N_("Network for all containers in the pod"),
            label_hint=N_("e.g. host, none, or compartment name"),
            placeholder=N_("host"),
        ),
    ] = SafeStr.trusted("", "default")  # empty = use service default network
    publish_ports: Annotated[
        list[SafePortMapping],
        VersionSpan(introduced=(5, 0, 0), quadlet_key="PublishPort"),
        FieldConstraints(
            description=N_("Ports published from the pod"),
            label_hint=N_("e.g. 8080:80"),
            placeholder=N_("8080:80/tcp"),
        ),
    ] = []
    # Podman 5.0.0 (base pod fields — gated by POD_UNITS feature flag)
    containers_conf_module: Annotated[
        SafeStr,
        VersionSpan(introduced=(5, 0, 0), quadlet_key="ContainersConfModule"),
        FieldConstraints(
            description=N_("containers.conf module to load"),
            label_hint=N_("absolute path"),
            placeholder=N_("/etc/containers/containers.conf.d/custom.conf"),
        ),
    ] = SafeStr.trusted("", "default")
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
    volumes: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 0, 0), quadlet_key="Volume"),
        FieldConstraints(
            description=N_("Volume mounts shared by pod containers"),
            label_hint=N_("one per line"),
            placeholder=N_("/data:/data:Z"),
        ),
    ] = []
    # Podman 5.3.0
    service_name: Annotated[
        SafeUnitName,
        VersionSpan(introduced=(5, 3, 0), quadlet_key="ServiceName"),
        FieldConstraints(
            description=N_("Override the systemd service name"),
            label_hint=N_("systemd unit name"),
            placeholder=N_("my-pod.service"),
        ),
    ] = SafeUnitName.trusted("", "default")
    dns: Annotated[
        list[SafeIpAddress],
        VersionSpan(introduced=(5, 3, 0), quadlet_key="DNS"),
        FieldConstraints(
            description=N_("Custom DNS servers"),
            label_hint=N_("e.g. 10.88.0.5"),
            placeholder=N_("10.88.0.1"),
        ),
    ] = []
    dns_search: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 3, 0), quadlet_key="DNSSearch"),
        FieldConstraints(
            description=N_("DNS search domains"),
            label_hint=N_("domain names"),
            placeholder=N_("example.com"),
        ),
    ] = []
    dns_option: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 3, 0), quadlet_key="DNSOption"),
        FieldConstraints(
            description=N_("DNS resolver options"),
            label_hint=N_("one per line"),
            placeholder=N_("ndots:5"),
        ),
    ] = []
    ip: Annotated[
        SafeIpAddress,
        VersionSpan(introduced=(5, 3, 0), quadlet_key="IP"),
        FieldConstraints(
            description=N_("Static IPv4 address for the pod"),
            label_hint=N_("e.g. 10.88.0.5"),
            placeholder=N_("10.88.0.5"),
        ),
    ] = SafeIpAddress.trusted("", "default")
    ip6: Annotated[
        SafeIpAddress,
        VersionSpan(introduced=(5, 3, 0), quadlet_key="IP6"),
        FieldConstraints(
            description=N_("Static IPv6 address for the pod"),
            label_hint=N_("e.g. 10.88.0.5"),
            placeholder=N_("fd00::2"),
        ),
    ] = SafeIpAddress.trusted("", "default")
    user_ns: Annotated[
        SafeStr,
        VersionSpan(introduced=(5, 3, 0), quadlet_key="UserNS"),
        FieldConstraints(
            description=N_("User namespace mode"),
            label_hint=N_("e.g. auto, keep-id, host"),
            placeholder=N_("keep-id"),
        ),
    ] = SafeStr.trusted("", "default")
    add_host: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 3, 0), quadlet_key="AddHost"),
        FieldConstraints(
            description=N_("Custom host-to-IP mappings"),
            label_hint=N_("key=value pairs"),
            placeholder=N_("myhost:10.0.0.1"),
        ),
    ] = []
    uid_map: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 3, 0), quadlet_key="UIDMap"),
        FieldConstraints(
            description=N_("UID mappings for the user namespace"),
            label_hint=N_("e.g. 0:100000:65536"),
            placeholder=N_("0:100000:65536"),
        ),
    ] = []
    gid_map: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 3, 0), quadlet_key="GIDMap"),
        FieldConstraints(
            description=N_("GID mappings for the user namespace"),
            label_hint=N_("e.g. 0:100000:65536"),
            placeholder=N_("0:100000:65536"),
        ),
    ] = []
    sub_uid_map: Annotated[
        SafeStr,
        VersionSpan(introduced=(5, 3, 0), quadlet_key="SubUIDMap"),
        FieldConstraints(
            description=N_("Subordinate UID mapping"),
            label_hint=N_("e.g. 0:100000:65536"),
            placeholder=N_("containers"),
        ),
    ] = SafeStr.trusted("", "default")
    sub_gid_map: Annotated[
        SafeStr,
        VersionSpan(introduced=(5, 3, 0), quadlet_key="SubGIDMap"),
        FieldConstraints(
            description=N_("Subordinate GID mapping"),
            label_hint=N_("e.g. 0:100000:65536"),
            placeholder=N_("containers"),
        ),
    ] = SafeStr.trusted("", "default")
    network_aliases: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 3, 0), quadlet_key="NetworkAlias"),
        FieldConstraints(
            description=N_("Network aliases for the pod"),
            label_hint=N_("one per line"),
            placeholder=N_("my-pod"),
        ),
    ] = []
    # Podman 5.4.0
    shm_size: Annotated[
        SafeByteSize,
        VersionSpan(introduced=(5, 4, 0), quadlet_key="ShmSize"),
        FieldConstraints(
            description=N_("Size of /dev/shm"),
            label_hint=N_("e.g. 512m, 1G"),
            placeholder=N_("512m"),
        ),
    ] = SafeByteSize.trusted("", "default")
    # Podman 5.5.0
    hostname: Annotated[
        SafeStr,
        VersionSpan(introduced=(5, 5, 0), quadlet_key="HostName"),
        FieldConstraints(
            description=N_("Hostname for the pod"),
            label_hint=N_("e.g. myhost"),
            placeholder=N_("my-pod-host"),
        ),
    ] = SafeStr.trusted("", "default")
    # Podman 5.6.0
    labels: Annotated[
        dict[SafeStr, SafeStr],
        VersionSpan(introduced=(5, 6, 0), quadlet_key="Label"),
        FieldConstraints(
            description=N_("Labels attached to the pod"),
            label_hint=N_("key=value pairs"),
            placeholder=N_("app=my-pod"),
        ),
    ] = {}
    exit_policy: Annotated[
        SafeStr,
        VersionSpan(introduced=(5, 6, 0), quadlet_key="ExitPolicy"),
        FieldConstraints(
            description=N_("Policy for pod exit when containers stop"),
            label_hint=N_("e.g. continue, stop"),
            placeholder=N_("continue"),
        ),
    ] = SafeStr.trusted("", "default")
    # Podman 5.7.0
    stop_timeout: Annotated[
        SafeTimeDuration,
        VersionSpan(introduced=(5, 7, 0), quadlet_key="StopTimeout"),
        FieldConstraints(
            description=N_("Timeout before forcefully stopping the pod"),
            label_hint=N_("e.g. 30s, 5min"),
            placeholder=N_("30s"),
        ),
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
        _sanitize_db_row(d, Pod)
        return d
