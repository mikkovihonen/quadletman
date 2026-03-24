import warnings
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from ..constraints import (
    ABS_PATH_CN,
    IDENTIFIER_CN,
    IMAGE_REF_CN,
    INT_OR_EMPTY_CN,
    N_,
    RESOURCE_NAME_CN,
    SELINUX_CONTEXT_CHOICES,
    UNIT_NAME_CN,
    USER_GROUP_REF_CN,
    FieldChoices,
    FieldConstraints,
)
from ..sanitized import (
    SafeAbsPath,
    SafeAbsPathOrEmpty,
    SafeIdentifier,
    SafeImageRefOrEmpty,
    SafeIntOrEmpty,
    SafeResourceName,
    SafeResourceNameOrEmpty,
    SafeSELinuxContext,
    SafeSlug,
    SafeStr,
    SafeTimestamp,
    SafeUnitName,
    SafeUserGroupRef,
    SafeUUID,
    enforce_model_safety,
)
from ..version_span import VersionSpan, enforce_model_version_gating
from .common import _loads, _sanitize_db_row

# "copy" is the Podman Quadlet key Copy= — shadowing BaseModel.copy() (deprecated
# in Pydantic v2, replaced by model_copy()) is intentional and harmless.
warnings.filterwarnings("ignore", message='Field name "copy".*shadows an attribute')


@enforce_model_version_gating(
    exempt={
        "qm_name": "identity field — quadletman resource name, not a Quadlet key",
        "qm_selinux_context": "host-side label applied by quadletman, not a Quadlet volume key",
        "qm_owner_uid": "host-side ownership managed by quadletman, not a Quadlet volume key",
        "qm_use_quadlet": "quadletman toggle between host-dir and Quadlet volume, not a Podman concept",
    }
)
@enforce_model_safety
class VolumeCreate(BaseModel):
    qm_name: Annotated[
        SafeResourceName,
        RESOURCE_NAME_CN,
        FieldConstraints(
            description=N_("Name of this volume"),
            label_hint=N_("lowercase, a-z 0-9 and hyphens"),
            placeholder=N_("my-volume"),
        ),
    ]
    qm_selinux_context: Annotated[
        SafeSELinuxContext,
        SELINUX_CONTEXT_CHOICES,
        FieldConstraints(description=N_("SELinux security context for volume files")),
    ] = SafeSELinuxContext.trusted("container_file_t", "default")
    qm_owner_uid: Annotated[
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
    qm_use_quadlet: Annotated[
        bool,
        FieldConstraints(
            description=N_("Use a Podman-managed named volume instead of a host directory"),
            label_hint=N_("named volume instead of host directory"),
        ),
    ] = False
    volume_name: Annotated[
        SafeResourceNameOrEmpty,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="VolumeName"),
        FieldConstraints(
            description=N_("Override the auto-generated volume name"),
            label_hint=N_("lowercase, a-z 0-9 and hyphens"),
            placeholder=N_("my-volume"),
        ),
    ] = SafeResourceNameOrEmpty.trusted("", "default")
    driver: Annotated[
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
    device: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Device"),
        FieldConstraints(
            description=N_("Device or remote path for the volume"),
            label_hint=N_("absolute path"),
            placeholder=N_("/dev/sdb1"),
        ),
    ] = SafeStr.trusted("", "default")
    options: Annotated[
        SafeStr,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Options"),
        FieldConstraints(
            description=N_("Driver-specific mount options"),
            label_hint=N_("key=value pairs"),
            placeholder=N_("rw,noexec"),
        ),
    ] = SafeStr.trusted("", "default")
    copy: Annotated[
        bool,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Copy"),
        FieldConstraints(
            description=N_("Copy image data into the volume on first use"),
            label_hint=N_("default: on"),
        ),
    ] = True
    group: Annotated[
        SafeUserGroupRef,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Group"),
        USER_GROUP_REF_CN,
        FieldConstraints(
            description=N_("Group ownership for the volume"),
            label_hint=N_("GID or group name"),
            placeholder=N_("1000"),
        ),
    ] = SafeUserGroupRef.trusted("", "default")
    # Podman 4.4.0 (base volume fields — gated by QUADLET feature flag)
    gid: Annotated[
        SafeIntOrEmpty,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="GID"),
        INT_OR_EMPTY_CN,
        FieldConstraints(
            description=N_("GID for volume ownership"),
            label_hint=N_("integer"),
            placeholder="0",
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    uid: Annotated[
        SafeIntOrEmpty,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="UID"),
        INT_OR_EMPTY_CN,
        FieldConstraints(
            description=N_("UID for volume ownership"),
            label_hint=N_("integer"),
            placeholder="0",
        ),
    ] = SafeIntOrEmpty.trusted("", "default")
    user: Annotated[
        SafeUserGroupRef,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="User"),
        USER_GROUP_REF_CN,
        FieldConstraints(
            description=N_("User name for volume ownership"),
            label_hint=N_("username or UID"),
            placeholder=N_("1000"),
        ),
    ] = SafeUserGroupRef.trusted("", "default")
    image: Annotated[
        SafeImageRefOrEmpty,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Image"),
        IMAGE_REF_CN,
        FieldConstraints(
            description=N_("Image to use as volume source"),
            label_hint=N_("e.g. docker.io/library/nginx:latest"),
            placeholder=N_("docker.io/library/data:latest"),
        ),
    ] = SafeImageRefOrEmpty.trusted("", "default")
    label: Annotated[
        dict[SafeStr, SafeStr],
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Label"),
        FieldConstraints(
            description=N_("Labels attached to the volume"),
            label_hint=N_("key=value pairs"),
            placeholder=N_("app=my-volume"),
        ),
    ] = {}
    type: Annotated[
        SafeIdentifier,
        VersionSpan(introduced=(4, 4, 0), quadlet_key="Type"),
        IDENTIFIER_CN,
        FieldConstraints(
            description=N_("Volume filesystem type"),
            label_hint=N_("e.g. ext4, tmpfs"),
            placeholder=N_("ext4"),
        ),
    ] = SafeIdentifier.trusted("", "default")
    # Podman 5.0.0
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
    qm_owner_uid: int = 0


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
    qm_host_path: SafeAbsPathOrEmpty = SafeAbsPathOrEmpty.trusted("", "default")
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        d.setdefault("qm_use_quadlet", 0)
        d.setdefault("volume_name", "")
        d.setdefault("driver", "")
        d.setdefault("device", "")
        d.setdefault("options", "")
        d.setdefault("copy", 1)
        d.setdefault("group", "")
        d.setdefault("qm_host_path", "")
        d.setdefault("gid", "")
        d.setdefault("uid", "")
        d.setdefault("user", "")
        d.setdefault("image", "")
        d.setdefault("type", "")
        d.setdefault("containers_conf_module", "")
        d.setdefault("service_name", "")
        _loads(d, "label", "global_args", "podman_args")
        _sanitize_db_row(d, Volume)
        return d
