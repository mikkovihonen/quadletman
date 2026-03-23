from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..constraints import RESOURCE_NAME_CN, SELINUX_CONTEXT_CHOICES, FieldChoices
from ..sanitized import (
    SafeAbsPath,
    SafeImageRef,
    SafeIntOrEmpty,
    SafeResourceName,
    SafeSELinuxContext,
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
        "selinux_context": "host-side label applied by quadletman, not a Quadlet volume key",
        "owner_uid": "host-side ownership managed by quadletman, not a Quadlet volume key",
        "use_quadlet": "quadletman toggle between host-dir and Quadlet volume, not a Podman concept",
    }
)
@enforce_model_safety
class VolumeCreate(BaseModel):
    name: Annotated[SafeResourceName, RESOURCE_NAME_CN]
    selinux_context: Annotated[SafeSELinuxContext, SELINUX_CONTEXT_CHOICES] = (
        SafeSELinuxContext.trusted("container_file_t", "default")
    )
    owner_uid: int = Field(default=0, ge=0)
    """Container UID that should own this volume directory.

    0 (default) = compartment root (host UID).  Any other value N causes the directory
    to be owned by the helper user qm-{compartment_id}-N (host UID = subuid_start + N),
    so that container processes running as UID N have direct ownership access.
    """
    # Quadlet-managed volume (generates a .volume unit instead of a host directory)
    use_quadlet: bool = False
    vol_driver: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 4, 0),
            quadlet_key="Driver",
            value_constraints={"image": (5, 0, 0)},
        ),
        FieldChoices(dynamic=True, empty_label="local (default)"),
    ] = SafeStr.trusted("", "default")  # e.g. "local", "overlay"
    vol_device: Annotated[SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="Device")] = (
        SafeStr.trusted("", "default")
    )
    vol_options: Annotated[SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="Options")] = (
        SafeStr.trusted("", "default")
    )
    vol_copy: Annotated[bool, VersionSpan(introduced=(4, 4, 0), quadlet_key="Copy")] = True
    vol_group: Annotated[SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="Group")] = (
        SafeStr.trusted("", "default")
    )
    # Podman 4.4.0 (base volume fields — gated by QUADLET feature flag)
    vol_gid: Annotated[SafeIntOrEmpty, VersionSpan(introduced=(4, 4, 0), quadlet_key="GID")] = (
        SafeIntOrEmpty.trusted("", "default")
    )
    vol_uid: Annotated[SafeIntOrEmpty, VersionSpan(introduced=(4, 4, 0), quadlet_key="UID")] = (
        SafeIntOrEmpty.trusted("", "default")
    )
    vol_user: Annotated[SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="User")] = (
        SafeStr.trusted("", "default")
    )
    vol_image: Annotated[
        SafeImageRef | Literal[""], VersionSpan(introduced=(4, 4, 0), quadlet_key="Image")
    ] = SafeStr.trusted("", "default")
    vol_label: Annotated[
        dict[SafeStr, SafeStr], VersionSpan(introduced=(4, 4, 0), quadlet_key="Label")
    ] = {}
    vol_type: Annotated[SafeStr, VersionSpan(introduced=(4, 4, 0), quadlet_key="Type")] = (
        SafeStr.trusted("", "default")
    )
    # Podman 5.0.0
    vol_containers_conf_module: Annotated[
        SafeStr, VersionSpan(introduced=(5, 0, 0), quadlet_key="ContainersConfModule")
    ] = SafeStr.trusted("", "default")
    vol_global_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 0, 0), quadlet_key="GlobalArgs")
    ] = []
    vol_podman_args: Annotated[
        list[SafeStr], VersionSpan(introduced=(5, 0, 0), quadlet_key="PodmanArgs")
    ] = []
    # Podman 5.3.0
    service_name: Annotated[
        SafeStr, VersionSpan(introduced=(5, 3, 0), quadlet_key="ServiceName")
    ] = SafeStr.trusted("", "default")

    @field_validator("vol_image")
    @classmethod
    def validate_vol_image(cls, v: str) -> SafeImageRef | Literal[""]:
        if not v:
            return v
        return SafeImageRef.of(v, "vol_image")


@enforce_model_safety
class VolumeUpdate(BaseModel):
    owner_uid: int = 0


@enforce_model_safety
class VolumeMount(BaseModel):
    """A managed service volume mounted into a container."""

    volume_id: SafeUUID  # references volumes.id
    container_path: SafeAbsPath
    options: SafeStr = SafeStr.trusted("Z", "default")  # SELinux relabeling by default


@enforce_model_safety
class Volume(VolumeCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    host_path: SafeStr = SafeStr.trusted("", "default")
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        d.setdefault("use_quadlet", 0)
        d.setdefault("vol_driver", "")
        d.setdefault("vol_device", "")
        d.setdefault("vol_options", "")
        d.setdefault("vol_copy", 1)
        d.setdefault("vol_group", "")
        d.setdefault("host_path", "")
        d.setdefault("vol_gid", "")
        d.setdefault("vol_uid", "")
        d.setdefault("vol_user", "")
        d.setdefault("vol_image", "")
        d.setdefault("vol_type", "")
        d.setdefault("vol_containers_conf_module", "")
        d.setdefault("service_name", "")
        _loads(d, "vol_label", "vol_global_args", "vol_podman_args")
        return d
