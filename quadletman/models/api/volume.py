from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from ..constraints import (
    ABS_PATH_CN,
    IMAGE_REF_CN,
    INT_OR_EMPTY_CN,
    N_,
    RESOURCE_NAME_CN,
    SELINUX_CONTEXT_CHOICES,
    UNIT_NAME_CN,
    FieldChoices,
    FieldConstraints,
)
from ..sanitized import (
    SafeAbsPath,
    SafeAbsPathOrEmpty,
    SafeImageRefOrEmpty,
    SafeIntOrEmpty,
    SafeResourceName,
    SafeSELinuxContext,
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
        "selinux_context": "host-side label applied by quadletman, not a Quadlet volume key",
        "owner_uid": "host-side ownership managed by quadletman, not a Quadlet volume key",
        "use_quadlet": "quadletman toggle between host-dir and Quadlet volume, not a Podman concept",
    }
)
@enforce_model_safety
class VolumeCreate(BaseModel):
    name: Annotated[
        SafeResourceName,
        RESOURCE_NAME_CN,
        FieldConstraints(
            description=N_("Name of this volume"),
            label_hint=N_("lowercase, a-z 0-9 and hyphens"),
            placeholder=N_("my-volume"),
        ),
    ]
    selinux_context: Annotated[
        SafeSELinuxContext,
        SELINUX_CONTEXT_CHOICES,
        FieldConstraints(description=N_("SELinux security context for volume files")),
    ] = SafeSELinuxContext.trusted("container_file_t", "default")
    owner_uid: Annotated[
        int,
        FieldConstraints(
            description=N_("Container UID that owns this volume directory"),
            label_hint=N_("integer"),
        ),
    ] = Field(default=0, ge=0)
    """Container UID that should own this volume directory.

    0 (default) = compartment root (host UID).  Any other value N causes the directory
    to be owned by the helper user qm-{compartment_id}-N (host UID = subuid_start + N),
    so that container processes running as UID N have direct ownership access.
    """
    # Quadlet-managed volume (generates a .volume unit instead of a host directory)
    use_quadlet: Annotated[
        bool,
        FieldConstraints(
            description=N_("Use a Podman-managed named volume instead of a host directory"),
            label_hint=N_("named volume instead of host directory"),
        ),
    ] = False
    vol_driver: Annotated[
        SafeStr,
        VersionSpan(
            introduced=(4, 4, 0),
            quadlet_key="Driver",
            value_constraints={"image": (5, 0, 0)},
        ),
        FieldChoices(dynamic=True, empty_label="local (default)"),
        FieldConstraints(
            description=N_("Volume storage driver"),
            label_hint=N_("storage backend"),
        ),
    ] = SafeStr.trusted("", "default")  # e.g. "local", "overlay"
    vol_device: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Device"),
        FieldConstraints(
            description=N_("Device or remote path for the volume"),
            label_hint=N_("absolute path"),
            placeholder=N_("/dev/sdb1"),
        ),
    ] = SafeStr.trusted("", "default")
    vol_options: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Options"),
        FieldConstraints(
            description=N_("Driver-specific mount options"),
            label_hint=N_("key=value pairs"),
            placeholder=N_("rw,noexec"),
        ),
    ] = SafeStr.trusted("", "default")
    vol_copy: Annotated[
        bool,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Copy"),
        FieldConstraints(
            description=N_("Copy image data into the volume on first use"),
            label_hint=N_("default: on"),
        ),
    ] = True
    vol_group: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Group"),
        FieldConstraints(
            description=N_("Group ownership for the volume"),
            label_hint=N_("GID or group name"),
            placeholder=N_("1000"),
        ),
    ] = SafeStr.trusted("", "default")
    # Podman 4.4.0 (base volume fields — gated by QUADLET feature flag)
    vol_gid: Annotated[
        SafeIntOrEmpty,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="GID"),
        INT_OR_EMPTY_CN,
        FieldConstraints(
            description=N_("GID for volume ownership"),
            label_hint=N_("integer"),
            placeholder="0",
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    vol_uid: Annotated[
        SafeIntOrEmpty,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="UID"),
        INT_OR_EMPTY_CN,
        FieldConstraints(
            description=N_("UID for volume ownership"),
            label_hint=N_("integer"),
            placeholder="0",
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    vol_user: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="User"),
        FieldConstraints(
            description=N_("User name for volume ownership"),
            label_hint=N_("username or UID"),
            placeholder=N_("1000"),
        ),
    ] = SafeStr.trusted("", "default")
    vol_image: Annotated[
        SafeImageRefOrEmpty,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Image"),
        IMAGE_REF_CN,
        FieldConstraints(
            description=N_("Image to use as volume source"),
            label_hint=N_("e.g. docker.io/library/nginx:latest"),
            placeholder=N_("docker.io/library/data:latest"),
        ),
    ] = SafeImageRefOrEmpty.trusted("", "default")
    vol_label: Annotated[
        dict[SafeStr, SafeStr],
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Label"),
        FieldConstraints(
            description=N_("Labels attached to the volume"),
            label_hint=N_("key=value pairs"),
            placeholder=N_("app=my-volume"),
        ),
    ] = {}
    vol_type: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Type"),
        FieldConstraints(
            description=N_("Volume filesystem type"),
            label_hint=N_("e.g. ext4, tmpfs"),
            placeholder=N_("ext4"),
        ),
    ] = SafeStr.trusted("", "default")
    # Podman 5.0.0
    vol_containers_conf_module: Annotated[
        SafeStr,
        VersionSpan(introduced=(5, 0, 0), quadlet_key="ContainersConfModule"),
        FieldConstraints(
            description=N_("containers.conf module to load"),
            label_hint=N_("absolute path"),
            placeholder=N_("/etc/containers/containers.conf.d/custom.conf"),
        ),
    ] = SafeStr.trusted("", "default")
    vol_global_args: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 0, 0), quadlet_key="GlobalArgs"),
        FieldConstraints(
            description=N_("Global Podman CLI arguments"),
            label_hint=N_("one per line"),
            placeholder=N_("--log-level=debug"),
        ),
    ] = []
    vol_podman_args: Annotated[
        list[SafeStr],
        VersionSpan(introduced=(5, 0, 0), quadlet_key="PodmanArgs"),
        FieldConstraints(
            description=N_("Additional Podman arguments"),
            label_hint=N_("one per line"),
            placeholder=N_("--opt=o=size=100m"),
        ),
    ] = []
    # Podman 5.3.0
    service_name: Annotated[
        SafeUnitName,
        VersionSpan(introduced=(5, 3, 0), quadlet_key="ServiceName"),
        UNIT_NAME_CN,
        FieldConstraints(
            description=N_("Override the systemd service name"),
            label_hint=N_("systemd unit name"),
            placeholder=N_("my-volume.service"),
        ),
    ] = SafeUnitName.trusted("", "default")


@enforce_model_safety
class VolumeUpdate(BaseModel):
    owner_uid: int = 0


@enforce_model_safety
class VolumeMount(BaseModel):
    """A managed service volume mounted into a container."""

    volume_id: SafeUUID  # references volumes.id
    container_path: Annotated[
        SafeAbsPath,
        ABS_PATH_CN,
        FieldConstraints(
            description=N_("Mount path inside the container"),
            label_hint=N_("absolute path"),
            placeholder=N_("/data"),
        ),
    ]
    options: Annotated[
        SafeStr,
        FieldConstraints(
            description=N_("Mount options"),
            label_hint=N_("e.g. Z, ro"),
            placeholder=N_("Z"),
        ),
    ] = SafeStr.trusted("Z", "default")  # SELinux relabeling by default


@enforce_model_safety
class Volume(VolumeCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    host_path: SafeAbsPathOrEmpty = SafeAbsPathOrEmpty.trusted("", "default")
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
        _sanitize_db_row(d, Volume)
        return d
