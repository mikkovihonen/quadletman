"""Quadlet file generation and management."""

import logging
import os
import pwd
import shutil
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..config import settings
from ..models import (
    Artifact,
    Build,
    Compartment,
    Container,
    Image,
    Kube,
    Network,
    Pod,
    Timer,
    Volume,
    sanitized,
)
from ..models.api import (
    ArtifactCreate,
    BuildCreate,
    ContainerCreate,
    ImageCreate,
    KubeCreate,
    NetworkCreate,
    PodCreate,
    VolumeCreate,
)
from ..models.sanitized import (
    SafeAbsPath,
    SafeResourceName,
    SafeSlug,
    SafeUnitName,
    SafeUsername,
    resolve_safe_path,
)
from ..models.version_span import field_availability
from ..podman import get_features
from . import host, user_manager
from .unsafe.quadlet import compare_file, render_unit
from .user_manager import _username, ensure_quadlet_dir

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "quadlet"
# codeql[py/jinja2/autoescape-false] generates systemd INI files, not HTML — autoescaping would corrupt values
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=False,  # templates generate systemd INI files, not HTML; autoescaping would corrupt unit file values
    trim_blocks=True,
    lstrip_blocks=True,
)

# ---------------------------------------------------------------------------
# Monitoring agent service management
# ---------------------------------------------------------------------------

_AGENT_UNIT_NAME = SafeUnitName.trusted("quadletman-agent.service", "agent_unit")

_AGENT_UNIT_TEMPLATE = """\
[Unit]
Description=quadletman monitoring agent for {compartment_id}
After=network.target

[Service]
Type=simple
ExecStart={agent_bin} --api-socket {agent_socket}
Restart=always
RestartSec=10
Environment=QUADLETMAN_COMPARTMENT_ID={compartment_id}
{extra_env}
[Install]
WantedBy=default.target
"""


_UID_NAMESPACE_SIZE = 65536


def _persist_unit(service_id: SafeSlug, filename: SafeUnitName, content: str) -> None:
    """Write a unit file via the best available method."""
    if get_features().quadlet_cli:
        _install_via_cli(service_id, filename, content)
    else:
        _write_to_disk(service_id, filename, content)


@sanitized.enforce
def _remove_unit(service_id: SafeSlug, filename: SafeUnitName) -> None:
    """Remove a unit file via the best available method."""
    if get_features().quadlet_cli:
        _remove_via_cli(service_id, filename)
    else:
        _unlink_from_disk(service_id, filename)


def _write_to_disk(service_id: SafeSlug, filename: SafeUnitName, content: str) -> None:
    """Write a unit file directly to the compartment's Quadlet directory."""
    quadlet_dir = ensure_quadlet_dir(service_id)
    username = SafeUsername.of(f"qm-{service_id}", "username")
    pw = pwd.getpwnam(username)
    file_path = SafeAbsPath.of(f"{quadlet_dir}/{filename}", "unit_path")
    host.write_text(file_path, content, pw.pw_uid, pw.pw_gid)


@sanitized.enforce
def _unlink_from_disk(service_id: SafeSlug, filename: SafeUnitName) -> None:
    """Remove a unit file directly from the compartment's Quadlet directory."""
    quadlet_dir = ensure_quadlet_dir(service_id)
    file_path = SafeAbsPath.of(f"{quadlet_dir}/{filename}", "unit_path")
    if host.path_exists(file_path, owner=_username(service_id)):
        host.unlink(file_path)


def _install_via_cli(service_id: SafeSlug, filename: SafeUnitName, content: str) -> None:
    """Install a unit file using ``podman quadlet install``."""
    uid = user_manager.get_uid(service_id)
    gid = user_manager.get_service_gid(service_id)
    with tempfile.NamedTemporaryFile(mode="w", suffix=f"-{filename}", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        safe_tmp = SafeAbsPath.of(tmp_path, "quadlet_tmp")
        host.chown(safe_tmp, -1, gid)
        host.chmod(safe_tmp, 0o640)
        host.run(
            [
                "sudo",
                "-u",
                f"qm-{service_id}",
                "/usr/bin/env",
                f"XDG_RUNTIME_DIR=/run/user/{uid}",
                f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
                "/usr/bin/podman",
                "quadlet",
                "install",
                "--no-reload-systemd",
                tmp_path,
            ],
            check=True,
        )
    finally:
        with suppress(OSError):
            os.unlink(tmp_path)


@sanitized.enforce
def _remove_via_cli(service_id: SafeSlug, filename: SafeUnitName) -> None:
    """Remove a unit file using ``podman quadlet rm``."""
    uid = user_manager.get_uid(service_id)
    quadlet_name = filename.rsplit(".", 1)[0] if "." in filename else filename
    host.run(
        [
            "sudo",
            "-u",
            f"qm-{service_id}",
            "/usr/bin/env",
            f"XDG_RUNTIME_DIR=/run/user/{uid}",
            f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
            "/usr/bin/podman",
            "quadlet",
            "rm",
            quadlet_name,
        ],
        check=True,
    )


@sanitized.enforce
def _resolve_id_maps(container_ids: list[str]) -> list[str]:
    """Build UIDMap/GIDMap entries in rootless-user-namespace coordinates.

    In rootless Podman the map values are relative to the rootless user namespace,
    not the real host.  The rootless namespace is:
      - NS UID/GID 0   = service user/group on the host
      - NS UID/GID 1   = subuid/subgid_start + 0
      - NS UID/GID N   = subuid/subgid_start + (N-1)

    Therefore the mapping formula is:
      - Container 0   → NS 0                (→ host service user/group)
      - Container N>0 → NS N+1              (→ host subid_start + N = helper user UID)

    Gap-fill and tail entries follow the same +1 shift so every container
    UID/GID in 0..65535 has a valid mapping.

    If no explicit entries are configured, no lines are emitted and Podman
    maps the full namespace automatically via the subUID/subGID ranges.
    """
    if not container_ids:
        return []

    explicit: set[int] = {int(cid) for cid in container_ids}
    explicit.add(0)  # UID/GID 0 always needs an explicit entry

    entries = []
    prev_end = 0
    for cuid in sorted(explicit):
        if cuid > prev_end:
            # Gap: container prev_end..(cuid-1) → NS (prev_end+1)..cuid
            entries.append(f"{prev_end}:{prev_end + 1}:{cuid - prev_end}")
        if cuid == 0:
            entries.append("0:0:1")
        else:
            entries.append(f"{cuid}:{cuid + 1}:1")
        prev_end = cuid + 1
    if prev_end < _UID_NAMESPACE_SIZE:
        entries.append(f"{prev_end}:{prev_end + 1}:{_UID_NAMESPACE_SIZE - prev_end}")

    return entries


@sanitized.enforce
def _resolve_mounts(
    service_id: SafeSlug, container: Container, service_volumes: list[Volume]
) -> list[dict]:
    """Build the resolved_mounts list for template rendering.

    For quadlet-managed volumes, sets quadlet_name to the volume reference
    (e.g. 'myapp-data.volume') instead of a host path.
    For host-directory volumes, sets host_path as before.
    """
    vol_by_id = {v.id: v for v in service_volumes}
    resolved_mounts = []
    for vm in container.volumes:
        vol = vol_by_id.get(vm.volume_id)
        if vol:
            if vol.qm_use_quadlet:
                resolved_mounts.append(
                    {
                        "quadlet_name": f"{service_id}-{vol.qm_name}.volume",
                        "host_path": "",
                        "container_path": vm.container_path,
                        "options": vm.options,
                    }
                )
            else:
                resolved_mounts.append(
                    {
                        "quadlet_name": "",
                        "host_path": resolve_safe_path(
                            settings.volumes_base,
                            f"{service_id}/{SafeResourceName.of(vol.qm_name, 'volume_name')}",
                        ),
                        "container_path": vm.container_path,
                        "options": vm.options,
                    }
                )
    return resolved_mounts


@sanitized.enforce
def _render_container(
    service_id: SafeSlug, container: Container, service_volumes: list[Volume]
) -> str:
    resolved_mounts = _resolve_mounts(service_id, container, service_volumes)
    resolved_uid_map = _resolve_id_maps(container.uid_map)
    effective_gid_ids = container.gid_map if container.gid_map else container.uid_map
    return render_unit(
        _jinja_env,
        "container.ini.j2",
        service_id=service_id,
        container=container,
        resolved_mounts=resolved_mounts,
        resolved_uid_map=resolved_uid_map,
        resolved_gid_map=_resolve_id_maps(effective_gid_ids),
        v=field_availability(ContainerCreate, get_features().version),
    )


@sanitized.enforce
def _render_pod(service_id: SafeSlug, pod: Pod) -> str:
    return render_unit(
        _jinja_env,
        "pod.ini.j2",
        service_id=service_id,
        pod=pod,
        v=field_availability(PodCreate, get_features().version),
    )


@sanitized.enforce
def _render_volume_unit(service_id: SafeSlug, volume: Volume) -> str:
    return render_unit(
        _jinja_env,
        "volume.ini.j2",
        service_id=service_id,
        volume=volume,
        v=field_availability(VolumeCreate, get_features().version),
    )


@sanitized.enforce
def _render_image_unit(service_id: SafeSlug, image_unit: Image) -> str:
    return render_unit(
        _jinja_env,
        "image.ini.j2",
        service_id=service_id,
        image_unit=image_unit,
        v=field_availability(ImageCreate, get_features().version),
    )


@sanitized.enforce
def _render_build(service_id: SafeSlug, build_unit: Build) -> str:
    return render_unit(
        _jinja_env,
        "build.ini.j2",
        service_id=service_id,
        build_unit=build_unit,
        v=field_availability(BuildCreate, get_features().version),
    )


@sanitized.enforce
def _render_timer(service_id: SafeSlug, timer: Timer, container_name: SafeResourceName) -> str:
    return render_unit(
        _jinja_env,
        "timer.timer.j2",
        service_id=service_id,
        timer=timer,
        container_name=container_name,
    )


@sanitized.enforce
def _render_network(service_id: SafeSlug, network: Network) -> str:
    return render_unit(
        _jinja_env,
        "network.ini.j2",
        service_id=service_id,
        network=network,
        v=field_availability(NetworkCreate, get_features().version),
    )


@sanitized.enforce
def _render_kube(service_id: SafeSlug, kube: Kube) -> str:
    return render_unit(
        _jinja_env,
        "kube.ini.j2",
        service_id=service_id,
        kube=kube,
        v=field_availability(KubeCreate, get_features().version),
    )


@sanitized.enforce
def _render_artifact(service_id: SafeSlug, artifact: Artifact) -> str:
    return render_unit(
        _jinja_env,
        "artifact.ini.j2",
        service_id=service_id,
        artifact=artifact,
        v=field_availability(ArtifactCreate, get_features().version),
    )


@sanitized.enforce
def check_service_sync(
    service_id: SafeSlug,
    containers: list[Container],
    service_volumes: list[Volume],
    comp: "Compartment | None" = None,
    timers: "list[Timer] | None" = None,
) -> list[dict]:
    """Compare on-disk quadlet files against what the DB would generate.

    Returns a list of out-of-sync entries: {file, status: 'missing'|'changed'}.
    An empty list means fully in sync.
    """
    try:
        quadlet_dir = ensure_quadlet_dir(service_id)
    except Exception as exc:
        return [{"file": "(quadlet dir)", "status": "missing", "detail": str(exc)}]

    issues = []
    owner = _username(service_id)

    def _read(path: str) -> str | None:
        return host.read_text(SafeAbsPath.of(path, "unit_path"), owner=owner)

    # Check network units referenced by containers
    network_names_used = {
        c.network
        for c in containers
        if c.network not in ("host", "none", "slirp4netns", "pasta") and not c.pod
    }
    for net in comp.networks if comp else []:
        if net.qm_name in network_names_used:
            net_path = os.path.join(quadlet_dir, f"{net.qm_name}.network")
            issue = compare_file(net_path, _render_network(service_id, net), _read(net_path))
            if issue:
                issues.append(issue)

    # Pod units
    for pod in comp.pods if comp else []:
        pod_path = os.path.join(quadlet_dir, f"{pod.qm_name}.pod")
        issue = compare_file(pod_path, _render_pod(service_id, pod), _read(pod_path))
        if issue:
            issues.append(issue)

    # Quadlet-managed volume units
    for vol in service_volumes:
        if vol.qm_use_quadlet:
            vol_path = os.path.join(quadlet_dir, f"{service_id}-{vol.qm_name}.volume")
            issue = compare_file(vol_path, _render_volume_unit(service_id, vol), _read(vol_path))
            if issue:
                issues.append(issue)

    # Image units
    for iu in comp.images if comp else []:
        img_path = os.path.join(quadlet_dir, f"{iu.qm_name}.image")
        issue = compare_file(img_path, _render_image_unit(service_id, iu), _read(img_path))
        if issue:
            issues.append(issue)

    # Build units
    for bu in comp.builds if comp else []:
        build_path = os.path.join(quadlet_dir, f"{bu.qm_name}.build")
        issue = compare_file(build_path, _render_build(service_id, bu), _read(build_path))
        if issue:
            issues.append(issue)

    for container in containers:
        unit_path = os.path.join(quadlet_dir, f"{container.qm_name}.container")
        issue = compare_file(
            unit_path,
            _render_container(service_id, container, service_volumes),
            _read(unit_path),
        )
        if issue:
            issues.append(issue)

    # Timer units
    container_map = {c.id: c.qm_name for c in containers}
    for timer in timers or []:
        container_name = SafeResourceName.of(
            container_map.get(timer.qm_container_id, timer.qm_container_name), "container_name"
        )
        timer_path = os.path.join(quadlet_dir, f"{timer.qm_name}.timer")
        issue = compare_file(
            timer_path, _render_timer(service_id, timer, container_name), _read(timer_path)
        )
        if issue:
            issues.append(issue)

    return issues


@host.audit("WRITE_BUILD", lambda sid, bu, *_: f"{sid}/{bu.qm_name}")
@sanitized.enforce
def write_build(service_id: SafeSlug, build_unit: Build) -> str:
    """Render and write a .build quadlet file. Returns systemd unit name."""
    content = _render_build(service_id, build_unit)
    _persist_unit(service_id, SafeUnitName.of(f"{build_unit.qm_name}.build", "filename"), content)

    unit_name = f"{build_unit.qm_name}.service"
    logger.info("Wrote build unit %s for service %s", unit_name, service_id)
    return unit_name


@host.audit("WRITE_CONTAINER_UNIT", lambda sid, c, *_: f"{sid}/{c.qm_name}")
@sanitized.enforce
def write_container_unit(
    service_id: SafeSlug,
    container: Container,
    service_volumes: list[Volume],
) -> str:
    """Render and write a .container quadlet file. Returns systemd unit name."""
    content = _render_container(service_id, container, service_volumes)
    _persist_unit(
        service_id, SafeUnitName.of(f"{container.qm_name}.container", "filename"), content
    )

    unit_name = f"{container.qm_name}.service"
    logger.info("Wrote quadlet unit %s for service %s", unit_name, service_id)
    return unit_name


@host.audit("WRITE_NETWORK_UNIT", lambda sid, net, *_: f"{sid}/{net.qm_name}")
@sanitized.enforce
def write_network_unit(service_id: SafeSlug, network: Network) -> None:
    """Write a .network quadlet file for a named network."""
    content = _render_network(service_id, network)
    _persist_unit(service_id, SafeUnitName.of(f"{network.qm_name}.network", "filename"), content)
    logger.info("Wrote network unit %s.network for service %s", network.qm_name, service_id)


@sanitized.enforce
def render_quadlet_files(
    service_id: SafeSlug,
    containers: list[Container],
    service_volumes: list[Volume],
    comp: "Compartment | None" = None,
    timers: "list[Timer] | None" = None,
) -> list[dict]:
    """Render all quadlet files for a service as a list of {filename, content} dicts."""
    files: list[dict] = []

    # Pod units
    for pod in comp.pods if comp else []:
        files.append({"filename": f"{pod.qm_name}.pod", "content": _render_pod(service_id, pod)})

    # Quadlet-managed volume units
    for vol in service_volumes:
        if vol.qm_use_quadlet:
            files.append(
                {
                    "filename": f"{service_id}-{vol.qm_name}.volume",
                    "content": _render_volume_unit(service_id, vol),
                }
            )

    # Image units
    for iu in comp.images if comp else []:
        files.append(
            {"filename": f"{iu.qm_name}.image", "content": _render_image_unit(service_id, iu)}
        )

    # Network units referenced by containers
    network_names_used = {
        c.network
        for c in containers
        if c.network not in ("host", "none", "slirp4netns", "pasta") and not c.pod
    }
    for net in comp.networks if comp else []:
        if net.qm_name in network_names_used:
            files.append(
                {"filename": f"{net.qm_name}.network", "content": _render_network(service_id, net)}
            )

    # Build units
    for bu in comp.builds if comp else []:
        files.append({"filename": f"{bu.qm_name}.build", "content": _render_build(service_id, bu)})

    for container in containers:
        files.append(
            {
                "filename": f"{container.qm_name}.container",
                "content": _render_container(service_id, container, service_volumes),
            }
        )

    container_map = {c.id: c.qm_name for c in containers}
    for timer in timers or []:
        container_name = SafeResourceName.of(
            container_map.get(timer.qm_container_id, timer.qm_container_name), "container_name"
        )
        files.append(
            {
                "filename": f"{timer.qm_name}.timer",
                "content": _render_timer(service_id, timer, container_name),
            }
        )

    return files


@sanitized.enforce
def export_service_bundle(
    service_id: SafeSlug,
    containers: list[Container],
    service_volumes: list[Volume],
    comp: "Compartment | None" = None,
) -> str:
    """Render all quadlet units for a service as a .quadlets bundle string."""
    sections: list[str] = []

    # Pod units
    for pod in comp.pods if comp else []:
        content = _render_pod(service_id, pod)
        sections.append(f"# FileName={pod.qm_name}\n{content.rstrip()}")

    # Quadlet-managed volume units
    for vol in service_volumes:
        if vol.qm_use_quadlet:
            content = _render_volume_unit(service_id, vol)
            sections.append(f"# FileName={service_id}-{vol.qm_name}\n{content.rstrip()}")

    # Image units
    for iu in comp.images if comp else []:
        content = _render_image_unit(service_id, iu)
        sections.append(f"# FileName={iu.qm_name}\n{content.rstrip()}")

    # Build units
    for bu in comp.builds if comp else []:
        content = _render_build(service_id, bu)
        sections.append(f"# FileName={bu.qm_name}\n{content.rstrip()}")

    for container in containers:
        content = _render_container(service_id, container, service_volumes)
        sections.append(f"# FileName={container.qm_name}\n{content.rstrip()}")

    network_names_used = {
        c.network
        for c in containers
        if c.network not in ("host", "none", "slirp4netns", "pasta") and not c.pod
    }
    for net in comp.networks if comp else []:
        if net.qm_name in network_names_used:
            content = _render_network(service_id, net)
            sections.append(f"# FileName={net.qm_name}\n{content.rstrip()}")

    return "\n---\n".join(sections) + "\n"


@host.audit("WRITE_POD_UNIT", lambda sid, pod, *_: f"{sid}/{pod.qm_name}")
@sanitized.enforce
def write_pod_unit(service_id: SafeSlug, pod: Pod) -> str:
    """Render and write a .pod quadlet file. Returns systemd unit name."""
    content = _render_pod(service_id, pod)
    _persist_unit(service_id, SafeUnitName.of(f"{pod.qm_name}.pod", "filename"), content)
    logger.info("Wrote pod unit %s.pod for service %s", pod.qm_name, service_id)
    return f"{pod.qm_name}-pod.service"


@host.audit("WRITE_VOLUME_UNIT", lambda sid, vol, *_: f"{sid}/{vol.qm_name}")
@sanitized.enforce
def write_volume_unit(service_id: SafeSlug, volume: Volume) -> None:
    """Render and write a .volume quadlet file for a quadlet-managed volume."""
    content = _render_volume_unit(service_id, volume)
    _persist_unit(
        service_id, SafeUnitName.of(f"{service_id}-{volume.qm_name}.volume", "filename"), content
    )
    logger.info(
        "Wrote volume unit %s-%s.volume for service %s", service_id, volume.qm_name, service_id
    )


@host.audit("WRITE_IMAGE", lambda sid, iu, *_: f"{sid}/{iu.qm_name}")
@sanitized.enforce
def write_image_unit(service_id: SafeSlug, image_unit: Image) -> str:
    """Render and write a .image quadlet file. Returns systemd unit name."""
    content = _render_image_unit(service_id, image_unit)
    _persist_unit(service_id, SafeUnitName.of(f"{image_unit.qm_name}.image", "filename"), content)
    logger.info("Wrote image unit %s.image for service %s", image_unit.qm_name, service_id)
    return f"{image_unit.qm_name}-image.service"


@host.audit("REMOVE_POD_UNIT", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def remove_pod_unit(service_id: SafeSlug, pod_name: SafeResourceName) -> None:
    _remove_unit(service_id, SafeUnitName.of(f"{pod_name}.pod", "filename"))
    logger.info("Removed pod unit %s.pod for service %s", pod_name, service_id)


@host.audit("REMOVE_VOLUME_UNIT", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def remove_volume_unit(service_id: SafeSlug, volume_name: SafeResourceName) -> None:
    _remove_unit(service_id, SafeUnitName.of(f"{service_id}-{volume_name}.volume", "filename"))
    logger.info(
        "Removed volume unit %s-%s.volume for service %s", service_id, volume_name, service_id
    )


@host.audit("REMOVE_IMAGE_UNIT", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def remove_image_unit(service_id: SafeSlug, image_name: SafeResourceName) -> None:
    _remove_unit(service_id, SafeUnitName.of(f"{image_name}.image", "filename"))
    logger.info("Removed image unit %s.image for service %s", image_name, service_id)


@host.audit("WRITE_ARTIFACT_UNIT", lambda sid, a, *_: f"{sid}/{a.qm_name}")
@sanitized.enforce
def write_artifact_unit(service_id: SafeSlug, artifact: Artifact) -> str:
    """Render and write a .artifact quadlet file. Returns systemd unit name."""
    content = _render_artifact(service_id, artifact)
    _persist_unit(service_id, SafeUnitName.of(f"{artifact.qm_name}.artifact", "filename"), content)
    logger.info("Wrote artifact unit %s.artifact for service %s", artifact.qm_name, service_id)
    return f"{artifact.qm_name}-artifact.service"


@host.audit("REMOVE_ARTIFACT_UNIT", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def remove_artifact_unit(service_id: SafeSlug, artifact_name: SafeResourceName) -> None:
    _remove_unit(service_id, SafeUnitName.of(f"{artifact_name}.artifact", "filename"))
    logger.info("Removed artifact unit %s.artifact for service %s", artifact_name, service_id)


@host.audit("REMOVE_BUILD_UNIT", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def remove_build_unit(service_id: SafeSlug, build_unit_name: SafeResourceName) -> None:
    _remove_unit(service_id, SafeUnitName.of(f"{build_unit_name}.build", "filename"))
    logger.info("Removed build unit %s.build for service %s", build_unit_name, service_id)


@host.audit("REMOVE_CONTAINER_UNIT", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def remove_container_unit(service_id: SafeSlug, container_name: SafeResourceName) -> None:
    _remove_unit(service_id, SafeUnitName.of(f"{container_name}.container", "filename"))
    logger.info("Removed quadlet unit %s.container for service %s", container_name, service_id)


@host.audit("WRITE_TIMER_UNIT", lambda sid, t, *_: f"{sid}/{t.qm_name}")
@sanitized.enforce
def write_timer_unit(service_id: SafeSlug, timer: Timer, container_name: SafeResourceName) -> str:
    """Render and write a .timer systemd unit file. Returns the timer unit name."""
    content = _render_timer(service_id, timer, container_name)
    _persist_unit(service_id, SafeUnitName.of(f"{timer.qm_name}.timer", "filename"), content)
    logger.info("Wrote timer unit %s.timer for service %s", timer.qm_name, service_id)
    return f"{timer.qm_name}.timer"


@host.audit("REMOVE_TIMER_UNIT", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def remove_timer_unit(service_id: SafeSlug, timer_name: SafeResourceName) -> None:
    _remove_unit(service_id, SafeUnitName.of(f"{timer_name}.timer", "filename"))
    logger.info("Removed timer unit %s.timer for service %s", timer_name, service_id)


@host.audit("REMOVE_NETWORK_UNIT", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def remove_network_unit(service_id: SafeSlug, network_name: SafeResourceName) -> None:
    _remove_unit(service_id, SafeUnitName.of(f"{network_name}.network", "filename"))


@host.audit("DEPLOY_AGENT", lambda sid, *_: sid)
@sanitized.enforce
def deploy_agent_service(service_id: SafeSlug) -> None:
    """Deploy the monitoring agent as a systemd --user service for a compartment.

    Only deploys when the main app is running as non-root.  In root mode,
    centralized monitoring loops are used instead.
    """
    if os.getuid() == 0:
        return  # Root mode — no agents

    agent_bin = shutil.which("quadletman-agent") or os.path.join(
        os.path.dirname(sys.executable), "quadletman-agent"
    )
    if not os.path.isfile(agent_bin):
        logger.warning("quadletman-agent not found — skipping agent deployment for %s", service_id)
        return

    # Propagate PYTHONPATH to the agent unit so it can import the project
    # in dev mode where the source tree is not installed system-wide.
    extra_env = ""
    pythonpath = os.environ.get("PYTHONPATH", "")
    if pythonpath:
        extra_env = f"Environment=PYTHONPATH={pythonpath}\n"

    content = _AGENT_UNIT_TEMPLATE.format(
        compartment_id=service_id,
        agent_bin=agent_bin,
        agent_socket=settings.agent_socket,
        extra_env=extra_env,
    )
    # The agent is a plain systemd unit (not a Quadlet source file), so it
    # goes in ~/.config/systemd/user/ rather than the Quadlet directory.
    home = user_manager.get_home(service_id)
    username = SafeUsername.of(f"{settings.service_user_prefix}{service_id}", "username")
    pw = pwd.getpwnam(username)
    unit_dir = SafeAbsPath.of(f"{home}/.config/systemd/user", "systemd_user_dir")
    # Create each directory level with correct ownership via admin sudo.
    for subpath in [".config", ".config/systemd", ".config/systemd/user"]:
        d = SafeAbsPath.of(os.path.join(home, subpath), "systemd_dir_part")
        host.run(
            ["install", "-d", "-o", username, "-g", username, "-m", "0700", str(d)],
            admin=True,
            check=True,
            capture_output=True,
        )
    unit_path = SafeAbsPath.of(f"{unit_dir}/{_AGENT_UNIT_NAME}", "agent_unit_path")
    host.write_text(unit_path, content, pw.pw_uid, pw.pw_gid)
    logger.info("Deployed monitoring agent for compartment %s", service_id)


@host.audit("REMOVE_AGENT", lambda sid, *_: sid)
@sanitized.enforce
def remove_agent_service(service_id: SafeSlug) -> None:
    """Remove the monitoring agent service for a compartment."""
    if os.getuid() == 0:
        return  # Root mode — no agents
    home = user_manager.get_home(service_id)
    owner = _username(service_id)
    unit_path = SafeAbsPath.of(f"{home}/.config/systemd/user/{_AGENT_UNIT_NAME}", "agent_unit_path")
    if host.path_exists(unit_path, owner=owner):
        host.unlink(unit_path)
