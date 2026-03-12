import json
import re
import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}[a-z0-9]$|^[a-z0-9]$")


def new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Volume models
# ---------------------------------------------------------------------------


class VolumeCreate(BaseModel):
    name: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_-]*$")
    selinux_context: str = "container_file_t"
    owner_uid: int = Field(default=0, ge=0)
    """Container UID that should own this volume directory.

    0 (default) = service user (host UID).  Any other value N causes the directory
    to be owned by the helper user qm-{service_id}-N (host UID = subuid_start + N),
    so that container processes running as UID N have direct ownership access.
    """


class Volume(VolumeCreate):
    id: str
    service_id: str
    host_path: str = ""  # populated by service layer
    created_at: str


class VolumeMount(BaseModel):
    """A managed service volume mounted into a container."""

    volume_id: str  # references volumes.id
    container_path: str
    options: str = "Z"  # SELinux relabeling by default


class BindMount(BaseModel):
    """An arbitrary host path mounted into a container."""

    host_path: str
    container_path: str
    options: str = ""


# ---------------------------------------------------------------------------
# Container models
# ---------------------------------------------------------------------------


class ContainerCreate(BaseModel):
    name: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_-]*$")
    image: str
    environment: dict[str, str] = {}
    ports: list[str] = []
    volumes: list[VolumeMount] = []
    labels: dict[str, str] = {}
    network: str = "host"
    restart_policy: str = "always"
    exec_start_pre: str = ""
    memory_limit: str = ""
    cpu_quota: str = ""
    depends_on: list[str] = []
    sort_order: int = 0
    apparmor_profile: str = ""
    build_context: str = ""
    build_file: str = ""
    containerfile_content: str = ""
    bind_mounts: list[BindMount] = []
    run_user: str = ""
    user_ns: str = ""  # kept for DB compat, superseded by uid_map/gid_map
    uid_map: list[str] = []
    gid_map: list[str] = []

    @field_validator("ports")
    @classmethod
    def validate_ports(cls, v: list[str]) -> list[str]:
        # Accepted forms (all optionally suffixed with /tcp or /udp):
        #   port                      e.g. 80
        #   host_port:container_port  e.g. 8080:80
        #   ip:host_port:container_port  e.g. 127.0.0.1:8080:80
        #   :container_port           e.g. :80  (OS picks host port)
        _PORT = r"\d{1,5}"
        _IP = r"[\d.:]+"  # IPv4 or IPv6
        _PROTO = r"(/tcp|/udp)?"
        pattern = re.compile(
            rf"^({_IP}:)?{_PORT}?:{_PORT}{_PROTO}$"
            rf"|^{_PORT}{_PROTO}$"
        )
        for port in v:
            if not pattern.match(port):
                raise ValueError(f"Invalid port mapping: {port!r}")
        return v


class ContainerUpdate(ContainerCreate):
    pass


class Container(ContainerCreate):
    id: str
    service_id: str
    created_at: str
    updated_at: str

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
        return cls(**d)


# ---------------------------------------------------------------------------
# Service models
# ---------------------------------------------------------------------------


class ServiceCreate(BaseModel):
    id: str = Field(..., description="Slug used as service ID and user suffix")
    display_name: str
    description: str = ""

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(
                "Service ID must be 1-32 lowercase alphanumeric chars and hyphens, "
                "start and end with alphanumeric"
            )
        if v.startswith("qm-"):
            raise ValueError("Service ID must not start with 'qm-'")
        return v


class ServiceUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None


class Service(BaseModel):
    id: str
    display_name: str
    description: str
    linux_user: str
    created_at: str
    updated_at: str
    containers: list[Container] = []
    volumes: list[Volume] = []

    @classmethod
    def from_row(cls, row: Any) -> "Service":
        return cls(**dict(row), containers=[], volumes=[])


class ServiceStatus(BaseModel):
    service_id: str
    containers: list[dict[str, str]] = []


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------


class SystemEvent(BaseModel):
    id: int
    service_id: str | None
    container_id: str | None
    event_type: str
    message: str
    created_at: str
