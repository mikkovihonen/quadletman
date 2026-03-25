from typing import Annotated

from pydantic import BaseModel, model_validator

from ..constraints import (
    ABS_PATH_CN,
    IDENTIFIER_CN,
    IP_ADDRESS_CN,
    N_,
    RESOURCE_NAME_CN,
    UNIT_NAME_CN,
    FieldConstraints,
)
from ..sanitized import (
    SafeAbsPathOrEmpty,
    SafeIdentifier,
    SafeIpAddress,
    SafeNetDriver,
    SafeResourceName,
    SafeResourceNameOrEmpty,
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
    }
)
@enforce_model_safety
class NetworkCreate(BaseModel):
    """Configures a named Podman network unit for a compartment."""

    qm_name: Annotated[
        SafeResourceName,
        RESOURCE_NAME_CN,
        FieldConstraints(
            description=N_("Network name"),
            label_hint=N_("lowercase slug"),
            placeholder=N_("app-network"),
        ),
    ]
    network_name: Annotated[
        SafeResourceNameOrEmpty,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="NetworkName"),
        FieldConstraints(
            description=N_("Override the auto-generated network name"),
            label_hint=N_("lowercase, a-z 0-9 and hyphens"),
            placeholder=N_("app-network"),
        ),
    ] = SafeResourceNameOrEmpty.trusted("", "default")
    driver: Annotated[
        SafeNetDriver,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Driver"),
        FieldConstraints(
            description=N_("Network driver"),
            label_hint=N_("network type"),
        ),
    ] = SafeNetDriver.trusted("", "default")
    subnet: Annotated[
        SafeIpAddress,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Subnet"),
        IP_ADDRESS_CN,
        FieldConstraints(
            description=N_("Subnet for container IP addresses"),
            label_hint=N_("CIDR notation"),
            placeholder=N_("10.89.0.0/24"),
        ),
    ] = SafeIpAddress.trusted("", "default")
    gateway: Annotated[
        SafeIpAddress,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Gateway"),
        IP_ADDRESS_CN,
        FieldConstraints(
            description=N_("Host-side gateway for the subnet"),
            label_hint=N_("first IP in subnet"),
            placeholder=N_("10.89.0.1"),
        ),
    ] = SafeIpAddress.trusted("", "default")
    ipv6: Annotated[
        bool,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="IPv6"),
        FieldConstraints(
            description=N_("Enable IPv6 networking"),
            label_hint=N_("dual-stack networking"),
        ),
    ] = False
    internal: Annotated[
        bool,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Internal"),
        FieldConstraints(
            description=N_("Isolate network from external routing"),
            label_hint=N_("no external routing"),
        ),
    ] = False
    dns_enabled: Annotated[
        bool,
        VersionSpan(introduced=(4, 7, 0), quadlet_key="DNS"),
        FieldConstraints(
            description=N_("Containers can reach each other by name"),
            label_hint=N_("container name resolution"),
        ),
    ] = False
    disable_dns: Annotated[
        bool,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="DisableDNS"),
        FieldConstraints(
            description=N_("Disable DNS plugin for the network"),
            label_hint=N_("disables DNS plugin"),
        ),
    ] = False
    ip_range: Annotated[
        SafeIpAddress,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="IPRange"),
        IP_ADDRESS_CN,
        FieldConstraints(
            description=N_("Narrower range within the subnet for container IPs"),
            label_hint=N_("CIDR subset of subnet"),
            placeholder=N_("10.89.0.128/25"),
        ),
    ] = SafeIpAddress.trusted("", "default")
    label: Annotated[
        dict[SafeStr, SafeStr],
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Label"),
        FieldConstraints(
            description=N_("Labels attached to the network"),
            label_hint=N_("key=value pairs"),
            placeholder=N_("env=production"),
        ),
    ] = {}
    options: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Options"),
        FieldConstraints(
            description=N_("Driver-specific network options"),
            label_hint=N_("key=value pairs"),
            placeholder=N_("mtu=1500"),
        ),
    ] = SafeStr.trusted("", "default")
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
            placeholder=N_("--opt=mtu=9000"),
        ),
    ] = []
    ipam_driver: Annotated[
        SafeIdentifier,
        VersionSpan(introduced=(5, 0, 0), quadlet_key="IPAMDriver"),
        IDENTIFIER_CN,
        FieldConstraints(
            description=N_("IP address management driver"),
            label_hint=N_("e.g. dhcp, host-local"),
            placeholder=N_("host-local"),
        ),
    ] = SafeIdentifier.trusted("", "default")
    dns: Annotated[
        SafeIpAddress,
        VersionSpan(
            introduced=(5, 0, 0),
            quadlet_key="DNS",
        ),
        IP_ADDRESS_CN,
        FieldConstraints(
            description=N_("DNS server containers use for external lookups"),
            label_hint=N_("e.g. 8.8.8.8 or gateway IP"),
            placeholder=N_("8.8.8.8"),
        ),
    ] = SafeIpAddress.trusted("", "default")
    service_name: Annotated[
        SafeUnitName,
        VersionSpan(introduced=(5, 3, 0), quadlet_key="ServiceName"),
        UNIT_NAME_CN,
        FieldConstraints(
            description=N_("Override the systemd service name"),
            label_hint=N_("systemd unit name"),
            placeholder=N_("app-network.service"),
        ),
    ] = SafeUnitName.trusted("", "default")
    network_delete_on_stop: Annotated[
        bool,
        VersionSpan(introduced=(5, 5, 0), quadlet_key="NetworkDeleteOnStop"),
        FieldConstraints(
            description=N_("Delete the network when the service stops"),
            label_hint=N_("recreated on next start"),
        ),
    ] = False
    interface_name: Annotated[
        SafeIdentifier,
        VersionSpan(introduced=(5, 6, 0), quadlet_key="InterfaceName"),
        IDENTIFIER_CN,
        FieldConstraints(
            description=N_("Custom network interface name"),
            label_hint=N_("e.g. eth0"),
            placeholder=N_("podman1"),
        ),
    ] = SafeIdentifier.trusted("", "default")


@enforce_model_safety
class Network(NetworkCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        d.setdefault("ipv6", 0)
        d.setdefault("internal", 0)
        d.setdefault("dns_enabled", 0)
        d.setdefault("disable_dns", 0)
        d.setdefault("network_delete_on_stop", 0)
        for f in (
            "network_name",
            "driver",
            "subnet",
            "gateway",
            "ip_range",
            "options",
            "containers_conf_module",
            "ipam_driver",
            "dns",
            "service_name",
            "interface_name",
        ):
            d.setdefault(f, "")
        _loads(d, "label", "global_args", "podman_args")
        d.setdefault("label", {})
        d.setdefault("global_args", [])
        d.setdefault("podman_args", [])
        _sanitize_db_row(d, Network)
        return d
