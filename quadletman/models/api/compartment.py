from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator

from ..sanitized import (
    SafeIpAddress,
    SafeNetDriver,
    SafeSlug,
    SafeStr,
    SafeTimestamp,
    enforce_model_safety,
)
from ..version_span import VersionSpan, enforce_model_version_gating
from .build_unit import BuildUnit
from .common import _loads
from .container import Container
from .image_unit import ImageUnit
from .pod import Pod
from .volume import Volume


@enforce_model_safety
class CompartmentCreate(BaseModel):
    id: SafeSlug = Field(..., description="Slug used as compartment ID and user suffix")
    description: SafeStr = SafeStr.trusted("", "default")

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> SafeSlug:
        slug = SafeSlug.of(v, "id")
        if slug.startswith("qm-"):
            raise ValueError("Compartment ID must not start with 'qm-'")
        return slug


@enforce_model_safety
class CompartmentUpdate(BaseModel):
    description: SafeStr | None = None

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str | None) -> SafeStr | None:
        if v is None:
            return v
        return SafeStr.of(v, "description")


@enforce_model_version_gating
@enforce_model_safety
class CompartmentNetworkUpdate(BaseModel):
    """Configures the optional shared Podman network unit for a compartment."""

    net_driver: Annotated[
        SafeNetDriver, VersionSpan(introduced=(4, 4, 0), quadlet_key="Driver")
    ] = SafeNetDriver.trusted("", "default")
    net_subnet: Annotated[
        SafeIpAddress, VersionSpan(introduced=(4, 4, 0), quadlet_key="Subnet")
    ] = SafeIpAddress.trusted("", "default")  # CIDR, e.g. 10.89.1.0/24
    net_gateway: Annotated[
        SafeIpAddress, VersionSpan(introduced=(4, 4, 0), quadlet_key="Gateway")
    ] = SafeIpAddress.trusted("", "default")  # gateway IP within subnet
    net_ipv6: Annotated[bool, VersionSpan(introduced=(4, 4, 0), quadlet_key="IPv6")] = False
    net_internal: Annotated[bool, VersionSpan(introduced=(4, 4, 0), quadlet_key="Internal")] = (
        False  # isolate from external routing
    )
    net_dns_enabled: Annotated[bool, VersionSpan(introduced=(4, 7, 0), quadlet_key="DNS")] = False
    # Podman 4.4.0 (base network fields — gated by QUADLET feature flag)
    net_disable_dns: Annotated[
        bool, VersionSpan(introduced=(4, 4, 0), quadlet_key="DisableDNS")
    ] = False
    net_ip_range: Annotated[
        SafeIpAddress, VersionSpan(introduced=(4, 4, 0), quadlet_key="IPRange")
    ] = SafeIpAddress.trusted("", "default")
    net_label: Annotated[
        dict[SafeStr, SafeStr], VersionSpan(introduced=(4, 4, 0), quadlet_key="Label")
    ] = {}
    net_options: Annotated[SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="Options")] = (
        SafeStr.trusted("", "default")
    )
    # Podman 5.0.0
    net_containers_conf_module: Annotated[
        SafeStr, VersionSpan(introduced=(5, 0, 0), quadlet_key="ContainersConfModule")
    ] = SafeStr.trusted("", "default")
    net_global_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 0, 0), quadlet_key="GlobalArgs")
    ] = []
    net_podman_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 0, 0), quadlet_key="PodmanArgs")
    ] = []
    net_ipam_driver: Annotated[
        SafeStr, VersionSpan(introduced=(5, 0, 0), quadlet_key="IPAMDriver")
    ] = SafeStr.trusted("", "default")
    net_dns: Annotated[SafeStr, VersionSpan(introduced=(5, 0, 0), quadlet_key="DNS")] = (
        SafeStr.trusted("", "default")
    )
    # Podman 5.3.0
    net_service_name: Annotated[
        SafeStr, VersionSpan(introduced=(5, 3, 0), quadlet_key="ServiceName")
    ] = SafeStr.trusted("", "default")
    # Podman 5.5.0
    net_delete_on_stop: Annotated[
        bool, VersionSpan(introduced=(5, 5, 0), quadlet_key="NetworkDeleteOnStop")
    ] = False
    # Podman 5.6.0
    net_interface_name: Annotated[
        SafeStr, VersionSpan(introduced=(5, 6, 0), quadlet_key="InterfaceName")
    ] = SafeStr.trusted("", "default")


@enforce_model_safety
class CompartmentStatus(BaseModel):
    compartment_id: SafeSlug
    containers: list[dict[SafeStr, SafeStr]] = []


@enforce_model_safety
class Compartment(BaseModel):
    id: SafeSlug
    description: SafeStr
    linux_user: SafeStr
    created_at: SafeTimestamp
    updated_at: SafeTimestamp
    containers: list[Container] = []
    volumes: list[Volume] = []
    pods: list[Pod] = []
    image_units: list[ImageUnit] = []
    build_units: list[BuildUnit] = []
    net_driver: SafeNetDriver = SafeNetDriver.trusted("", "default")
    net_subnet: SafeIpAddress = SafeIpAddress.trusted("", "default")
    net_gateway: SafeIpAddress = SafeIpAddress.trusted("", "default")
    net_ipv6: bool = False
    net_internal: bool = False
    net_dns_enabled: bool = False
    net_disable_dns: bool = False
    net_ip_range: SafeIpAddress = SafeIpAddress.trusted("", "default")
    net_label: dict[SafeStr, SafeStr] = {}
    net_options: SafeStr = SafeStr.trusted("", "default")
    net_containers_conf_module: SafeStr = SafeStr.trusted("", "default")
    net_global_args: list[SafeStr] = []
    net_podman_args: list[SafeStr] = []
    net_ipam_driver: SafeStr = SafeStr.trusted("", "default")
    net_dns: SafeStr = SafeStr.trusted("", "default")
    net_service_name: SafeStr = SafeStr.trusted("", "default")
    net_delete_on_stop: bool = False
    net_interface_name: SafeStr = SafeStr.trusted("", "default")
    connection_monitor_enabled: bool = True
    process_monitor_enabled: bool = True
    connection_history_retention_days: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        d.setdefault("net_ipv6", 0)
        d.setdefault("net_internal", 0)
        d.setdefault("net_dns_enabled", 0)
        d.setdefault("net_disable_dns", 0)
        d.setdefault("net_delete_on_stop", 0)
        for f in (
            "net_driver",
            "net_subnet",
            "net_gateway",
            "net_ip_range",
            "net_options",
            "net_containers_conf_module",
            "net_ipam_driver",
            "net_dns",
            "net_service_name",
            "net_interface_name",
        ):
            d.setdefault(f, "")
        _loads(d, "net_label", "net_global_args", "net_podman_args")
        d.setdefault("net_label", {})
        d.setdefault("net_global_args", [])
        d.setdefault("net_podman_args", [])
        d.setdefault("connection_monitor_enabled", 1)
        d.setdefault("process_monitor_enabled", 1)
        d.setdefault("connection_history_retention_days", None)
        d.setdefault("containers", [])
        d.setdefault("volumes", [])
        d.setdefault("pods", [])
        d.setdefault("image_units", [])
        return d
