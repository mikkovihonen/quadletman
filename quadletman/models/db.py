import json
from typing import Any

from pydantic import BaseModel

from .api import (
    ContainerCreate,
    ImageUnitCreate,
    NotificationHookCreate,
    PodCreate,
    SecretCreate,
    TimerCreate,
    VolumeCreate,
    _Direction,
    _EventType,
    _Proto,
)
from .sanitized import (
    SafeIpAddress,
    SafeMultilineStr,
    SafeResourceName,
    SafeSlug,
    SafeStr,
    SafeTimestamp,
    SafeUUID,
    enforce_model,
)


@enforce_model
class Volume(VolumeCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    host_path: SafeStr = SafeStr.trusted(
        "", "default"
    )  # populated by service layer; empty for quadlet-managed volumes
    created_at: SafeTimestamp

    @classmethod
    def from_row(cls, row) -> "Volume":
        d = dict(row)
        d.setdefault("use_quadlet", 0)
        d.setdefault("vol_driver", "")
        d.setdefault("vol_device", "")
        d.setdefault("vol_options", "")
        d.setdefault("vol_copy", 1)
        d.setdefault("vol_group", "")
        d["host_path"] = SafeStr.of(d.get("host_path") or "", "db:host_path")
        d["id"] = SafeUUID.of(d["id"], "db:id")
        d["compartment_id"] = SafeSlug.of(d["compartment_id"], "db:compartment_id")
        d["created_at"] = SafeTimestamp.of(d["created_at"], "db:created_at")
        return cls(**d)


@enforce_model
class Container(ContainerCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp
    updated_at: SafeTimestamp

    @classmethod
    def from_row(cls, row: Any) -> "Container":
        d = dict(row)
        d["environment"] = json.loads(d["environment"])
        d["ports"] = json.loads(d["ports"])
        d["volumes"] = json.loads(d["volumes"])
        d["labels"] = json.loads(d["labels"])
        d["depends_on"] = json.loads(d["depends_on"])
        d["bind_mounts"] = json.loads(d.get("bind_mounts") or "[]")
        d["uid_map"] = json.loads(d.get("uid_map") or "[]")
        d["gid_map"] = json.loads(d.get("gid_map") or "[]")
        # Boolean fields stored as INTEGER in SQLite
        d.setdefault("notify_healthy", 0)
        d.setdefault("no_new_privileges", 0)
        d.setdefault("read_only", 0)
        d.setdefault("privileged", 0)
        # String fields added in migration 010 (absent in old rows)
        for _f in (
            "health_cmd",
            "health_interval",
            "health_timeout",
            "health_retries",
            "health_start_period",
            "health_on_failure",
            "auto_update",
            "environment_file",
            "exec_cmd",
            "entrypoint",
            "seccomp_profile",
            "working_dir",
            "hostname",
        ):
            d.setdefault(_f, "")
        # JSON list/dict fields added in migration 011
        for _f in (
            "drop_caps",
            "add_caps",
            "mask_paths",
            "unmask_paths",
            "dns",
            "dns_search",
            "dns_option",
        ):
            d[_f] = json.loads(d.get(_f) or "[]")
        d["sysctl"] = json.loads(d.get("sysctl") or "{}")
        # String fields added in migration 012
        for _f in ("pod_name", "log_driver", "exec_start_post", "exec_stop"):
            d.setdefault(_f, "")
        d["log_opt"] = json.loads(d.get("log_opt") or "{}")
        # JSON list added in migration 002
        d["secrets"] = json.loads(d.get("secrets") or "[]")
        # Fields added in migration 003
        d["devices"] = json.loads(d.get("devices") or "[]")
        d["network_aliases"] = json.loads(d.get("network_aliases") or "[]")
        for _f in ("runtime", "service_extra", "memory_reservation", "cpu_weight", "io_weight"):
            d.setdefault(_f, "")
        d.setdefault("init", 0)
        d["id"] = SafeUUID.of(d["id"], "db:id")
        d["compartment_id"] = SafeSlug.of(d["compartment_id"], "db:compartment_id")
        d["created_at"] = SafeTimestamp.of(d["created_at"], "db:created_at")
        d["updated_at"] = SafeTimestamp.of(d["updated_at"], "db:updated_at")
        return cls(**d)


@enforce_model
class Pod(PodCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp

    @classmethod
    def from_row(cls, row) -> "Pod":
        d = dict(row)
        d["publish_ports"] = json.loads(d.get("publish_ports") or "[]")
        d["id"] = SafeUUID.of(d["id"], "db:id")
        d["compartment_id"] = SafeSlug.of(d["compartment_id"], "db:compartment_id")
        d["created_at"] = SafeTimestamp.of(d["created_at"], "db:created_at")
        return cls(**d)


@enforce_model
class ImageUnit(ImageUnitCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp

    @classmethod
    def from_row(cls, row) -> "ImageUnit":
        d = dict(row)
        d["id"] = SafeUUID.of(d["id"], "db:id")
        d["compartment_id"] = SafeSlug.of(d["compartment_id"], "db:compartment_id")
        d["created_at"] = SafeTimestamp.of(d["created_at"], "db:created_at")
        return cls(**d)


@enforce_model
class Compartment(BaseModel):
    id: SafeSlug
    description: SafeStr
    linux_user: SafeStr
    created_at: SafeTimestamp
    updated_at: SafeTimestamp
    containers: list[Container] = []
    volumes: list[Volume] = []
    pods: list["Pod"] = []
    image_units: list["ImageUnit"] = []
    # Shared network unit configuration (P2)
    net_driver: SafeStr = SafeStr.trusted("", "default")
    net_subnet: SafeStr = SafeStr.trusted("", "default")
    net_gateway: SafeStr = SafeStr.trusted("", "default")
    net_ipv6: bool = False
    net_internal: bool = False
    net_dns_enabled: bool = False
    connection_monitor_enabled: bool = True
    process_monitor_enabled: bool = True
    connection_history_retention_days: int | None = None

    @classmethod
    def from_row(cls, row: Any) -> "Compartment":
        d = dict(row)
        # Boolean fields stored as INTEGER in SQLite (added in migration 011)
        d.setdefault("net_ipv6", 0)
        d.setdefault("net_internal", 0)
        d.setdefault("net_dns_enabled", 0)
        # String fields added in migration 011
        for _f in ("net_driver", "net_subnet", "net_gateway"):
            d.setdefault(_f, "")
        # Boolean fields added in migrations 006/007
        for _f in ("connection_monitor_enabled", "process_monitor_enabled"):
            d.setdefault(_f, 1)
            d[_f] = bool(d[_f])
        # Nullable integer added in migration 008
        d.setdefault("connection_history_retention_days", None)
        d["id"] = SafeSlug.of(d["id"], "db:id")
        d["description"] = SafeStr.of(d["description"], "db:description")
        d["linux_user"] = SafeStr.of(d["linux_user"], "db:linux_user")
        d["net_driver"] = SafeStr.of(d["net_driver"], "db:net_driver")
        d["net_subnet"] = SafeStr.of(d["net_subnet"], "db:net_subnet")
        d["net_gateway"] = SafeStr.of(d["net_gateway"], "db:net_gateway")
        d["created_at"] = SafeTimestamp.of(d["created_at"], "db:created_at")
        d["updated_at"] = SafeTimestamp.of(d["updated_at"], "db:updated_at")
        return cls(**d, containers=[], volumes=[], pods=[], image_units=[])


@enforce_model
class SystemEvent(BaseModel):
    id: int
    compartment_id: SafeSlug | None
    container_id: SafeStr | None
    event_type: _EventType
    message: SafeMultilineStr
    created_at: SafeTimestamp

    @classmethod
    def from_row(cls, row: Any) -> "SystemEvent":
        d = dict(row)
        if d.get("compartment_id") is not None:
            d["compartment_id"] = SafeSlug.of(d["compartment_id"], "db:compartment_id")
        if d.get("container_id") is not None:
            d["container_id"] = SafeStr.of(d["container_id"], "db:container_id")
        d["message"] = SafeMultilineStr.of(d["message"], "db:message")
        d["created_at"] = SafeTimestamp.of(d["created_at"], "db:created_at")
        return cls(**d)


@enforce_model
class Secret(SecretCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp

    @classmethod
    def from_row(cls, row) -> "Secret":
        d = dict(row)
        d["id"] = SafeUUID.of(d["id"], "db:id")
        d["compartment_id"] = SafeSlug.of(d["compartment_id"], "db:compartment_id")
        d["created_at"] = SafeTimestamp.of(d["created_at"], "db:created_at")
        return cls(**d)


@enforce_model
class Timer(TimerCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    container_name: SafeResourceName = SafeResourceName.trusted(
        "", "default"
    )  # populated by service layer
    created_at: SafeTimestamp

    @classmethod
    def from_row(cls, row) -> "Timer":
        d = dict(row)
        d.setdefault("container_name", "")
        d["id"] = SafeUUID.of(d["id"], "db:id")
        d["compartment_id"] = SafeSlug.of(d["compartment_id"], "db:compartment_id")
        d["container_name"] = SafeResourceName.of(d["container_name"], "db:container_name")
        d["created_at"] = SafeTimestamp.of(d["created_at"], "db:created_at")
        return cls(**d)


@enforce_model
class Template(BaseModel):
    id: SafeUUID
    name: SafeStr
    description: SafeStr
    config_json: SafeMultilineStr
    created_at: SafeTimestamp

    @classmethod
    def from_row(cls, row) -> "Template":
        d = dict(row)
        d["id"] = SafeUUID.of(d["id"], "db:id")
        d["name"] = SafeStr.of(d["name"], "db:name")
        d["description"] = SafeStr.of(d["description"], "db:description")
        d["config_json"] = SafeMultilineStr.of(d["config_json"], "db:config_json")
        d["created_at"] = SafeTimestamp.of(d["created_at"], "db:created_at")
        return cls(**d)


@enforce_model
class NotificationHook(NotificationHookCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    created_at: SafeTimestamp

    @classmethod
    def from_row(cls, row) -> "NotificationHook":
        d = dict(row)
        d["id"] = SafeUUID.of(d["id"], "db:id")
        d["compartment_id"] = SafeSlug.of(d["compartment_id"], "db:compartment_id")
        d["created_at"] = SafeTimestamp.of(d["created_at"], "db:created_at")
        return cls(**d)


@enforce_model
class Process(BaseModel):
    id: SafeUUID
    compartment_id: SafeSlug
    process_name: SafeStr
    cmdline: SafeMultilineStr
    known: bool
    times_seen: int
    first_seen_at: SafeTimestamp
    last_seen_at: SafeTimestamp

    @classmethod
    def from_row(cls, row) -> "Process":
        d = dict(row)
        d["known"] = bool(d["known"])
        d["id"] = SafeUUID.of(d["id"], "db:id")
        d["compartment_id"] = SafeSlug.of(d["compartment_id"], "db:compartment_id")
        d["process_name"] = SafeStr.of(d["process_name"], "db:process_name")
        d["cmdline"] = SafeMultilineStr.of(d["cmdline"], "db:cmdline")
        d["first_seen_at"] = SafeTimestamp.of(d["first_seen_at"], "db:first_seen_at")
        d["last_seen_at"] = SafeTimestamp.of(d["last_seen_at"], "db:last_seen_at")
        return cls(**d)


@enforce_model
class Connection(BaseModel):
    id: SafeUUID
    compartment_id: SafeSlug
    container_name: SafeResourceName
    proto: _Proto
    dst_ip: SafeIpAddress
    dst_port: int
    direction: _Direction
    times_seen: int
    first_seen_at: SafeTimestamp
    last_seen_at: SafeTimestamp
    whitelisted: bool = False  # computed from whitelist rules; not stored in DB

    @classmethod
    def from_row(cls, row) -> "Connection":
        d = dict(row)
        d.pop("known", None)  # column removed in migration 008; ignore if still present
        d.setdefault("direction", "outbound")  # column added in migration 009
        d.setdefault("whitelisted", False)
        d["id"] = SafeUUID.of(d["id"], "db:id")
        d["compartment_id"] = SafeSlug.of(d["compartment_id"], "db:compartment_id")
        d["container_name"] = SafeResourceName.of(d["container_name"], "db:container_name")
        d["dst_ip"] = SafeIpAddress.of(d["dst_ip"], "db:dst_ip")
        d["first_seen_at"] = SafeTimestamp.of(d["first_seen_at"], "db:first_seen_at")
        d["last_seen_at"] = SafeTimestamp.of(d["last_seen_at"], "db:last_seen_at")
        return cls(**d)


@enforce_model
class WhitelistRule(BaseModel):
    id: SafeUUID
    compartment_id: SafeSlug
    description: SafeStr
    container_name: SafeResourceName | None
    proto: _Proto | None
    dst_ip: SafeIpAddress | None
    dst_port: int | None
    direction: _Direction | None
    sort_order: int
    created_at: SafeTimestamp

    @classmethod
    def from_row(cls, row) -> "WhitelistRule":
        d = dict(row)
        d.setdefault("direction", None)  # column added in migration 009
        d["id"] = SafeUUID.of(d["id"], "db:id")
        d["compartment_id"] = SafeSlug.of(d["compartment_id"], "db:compartment_id")
        d["description"] = SafeStr.of(d["description"], "db:description")
        if d.get("container_name") is not None:
            d["container_name"] = SafeResourceName.of(d["container_name"], "db:container_name")
        if d.get("dst_ip") is not None:
            d["dst_ip"] = SafeIpAddress.of(d["dst_ip"], "db:dst_ip")
        d["created_at"] = SafeTimestamp.of(d["created_at"], "db:created_at")
        return cls(**d)
