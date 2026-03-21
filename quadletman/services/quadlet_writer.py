"""Quadlet file generation and management."""

import logging
import os
import pwd
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..models import Compartment, Container, ImageUnit, Pod, Timer, Volume, sanitized
from ..models.sanitized import SafeAbsPath, SafeResourceName, SafeSlug
from . import host
from .unsafe.quadlet import compare_file, render_unit
from .user_manager import ensure_quadlet_dir, get_home
from .volume_manager import volume_path

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "quadlet"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=False,  # templates generate systemd INI files, not HTML; autoescaping would corrupt unit file values
    trim_blocks=True,
    lstrip_blocks=True,
)


_UID_NAMESPACE_SIZE = 65536


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
            if vol.use_quadlet:
                resolved_mounts.append(
                    {
                        "quadlet_name": f"{service_id}-{vol.name}.volume",
                        "host_path": "",
                        "container_path": vm.container_path,
                        "options": vm.options,
                    }
                )
            else:
                resolved_mounts.append(
                    {
                        "quadlet_name": "",
                        "host_path": volume_path(
                            service_id, SafeResourceName.of(vol.name, "volume_name")
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
    )


@sanitized.enforce
def _render_pod(service_id: SafeSlug, pod: Pod) -> str:
    return render_unit(_jinja_env, "pod.ini.j2", service_id=service_id, pod=pod)


@sanitized.enforce
def _render_volume_unit(service_id: SafeSlug, volume: Volume) -> str:
    return render_unit(_jinja_env, "volume.ini.j2", service_id=service_id, volume=volume)


@sanitized.enforce
def _render_image_unit(service_id: SafeSlug, image_unit: ImageUnit) -> str:
    from ..podman_version import get_features

    return render_unit(
        _jinja_env,
        "image.ini.j2",
        service_id=service_id,
        image_unit=image_unit,
        podman=get_features(),
    )


@sanitized.enforce
def _render_build(service_id: SafeSlug, container: Container) -> str:
    return render_unit(_jinja_env, "build.ini.j2", service_id=service_id, container=container)


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
def _render_network(service_id: SafeSlug, comp: "Compartment | None" = None) -> str:
    return render_unit(_jinja_env, "network.ini.j2", service_id=service_id, comp=comp)


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

    needs_network = any(c.network != "host" and not c.pod_name for c in containers)
    if needs_network:
        net_path = os.path.join(quadlet_dir, f"{service_id}.network")
        issue = compare_file(net_path, _render_network(service_id, comp))
        if issue:
            issues.append(issue)

    # Pod units
    for pod in comp.pods if comp else []:
        pod_path = os.path.join(quadlet_dir, f"{pod.name}.pod")
        issue = compare_file(pod_path, _render_pod(service_id, pod))
        if issue:
            issues.append(issue)

    # Quadlet-managed volume units
    for vol in service_volumes:
        if vol.use_quadlet:
            vol_path = os.path.join(quadlet_dir, f"{service_id}-{vol.name}.volume")
            issue = compare_file(vol_path, _render_volume_unit(service_id, vol))
            if issue:
                issues.append(issue)

    # Image units
    for iu in comp.image_units if comp else []:
        img_path = os.path.join(quadlet_dir, f"{iu.name}.image")
        issue = compare_file(img_path, _render_image_unit(service_id, iu))
        if issue:
            issues.append(issue)

    for container in containers:
        if container.build_context:
            build_path = os.path.join(quadlet_dir, f"{container.name}-build.build")
            issue = compare_file(build_path, _render_build(service_id, container))
            if issue:
                issues.append(issue)

        unit_path = os.path.join(quadlet_dir, f"{container.name}.container")
        issue = compare_file(unit_path, _render_container(service_id, container, service_volumes))
        if issue:
            issues.append(issue)

    # Timer units
    container_map = {c.id: c.name for c in containers}
    for timer in timers or []:
        container_name = SafeResourceName.of(
            container_map.get(timer.container_id, timer.container_name), "container_name"
        )
        timer_path = os.path.join(quadlet_dir, f"{timer.name}.timer")
        issue = compare_file(timer_path, _render_timer(service_id, timer, container_name))
        if issue:
            issues.append(issue)

    return issues


@host.audit("WRITE_BUILD_UNIT", lambda sid, c, *_: f"{sid}/{c.name}")
@sanitized.enforce
def write_build_unit(service_id: SafeSlug, container: Container) -> str:
    """Render and write a .build quadlet file. Returns systemd unit name."""
    quadlet_dir = ensure_quadlet_dir(service_id)
    username = f"qm-{service_id}"
    pw = pwd.getpwnam(username)

    content = _render_build(service_id, container)

    build_file = os.path.join(quadlet_dir, f"{container.name}-build.build")
    host.write_text(SafeAbsPath.of(build_file, "build_file"), content, pw.pw_uid, pw.pw_gid)

    unit_name = f"{container.name}-build.service"
    logger.info("Wrote build unit %s for service %s", unit_name, service_id)
    return unit_name


@host.audit("WRITE_CONTAINER_UNIT", lambda sid, c, *_: f"{sid}/{c.name}")
@sanitized.enforce
def write_container_unit(
    service_id: SafeSlug,
    container: Container,
    service_volumes: list[Volume],
) -> str:
    """Render and write a .container quadlet file. Returns systemd unit name."""
    quadlet_dir = ensure_quadlet_dir(service_id)
    username = f"qm-{service_id}"
    pw = pwd.getpwnam(username)

    if container.build_context:
        write_build_unit(service_id, container)

    content = _render_container(service_id, container, service_volumes)

    unit_file = os.path.join(quadlet_dir, f"{container.name}.container")
    host.write_text(SafeAbsPath.of(unit_file, "unit_file"), content, pw.pw_uid, pw.pw_gid)

    unit_name = f"{container.name}.service"
    logger.info("Wrote quadlet unit %s for service %s", unit_name, service_id)
    return unit_name


@host.audit("WRITE_NETWORK_UNIT", lambda sid, *_: sid)
@sanitized.enforce
def write_network_unit(service_id: SafeSlug, comp: "Compartment | None" = None) -> None:
    """Write a shared .network quadlet file for multi-container services."""
    quadlet_dir = ensure_quadlet_dir(service_id)
    username = f"qm-{service_id}"
    pw = pwd.getpwnam(username)

    content = _render_network(service_id, comp)

    net_file = os.path.join(quadlet_dir, f"{service_id}.network")
    host.write_text(SafeAbsPath.of(net_file, "net_file"), content, pw.pw_uid, pw.pw_gid)
    logger.info("Wrote network unit for service %s", service_id)


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
        files.append({"filename": f"{pod.name}.pod", "content": _render_pod(service_id, pod)})

    # Quadlet-managed volume units
    for vol in service_volumes:
        if vol.use_quadlet:
            files.append(
                {
                    "filename": f"{service_id}-{vol.name}.volume",
                    "content": _render_volume_unit(service_id, vol),
                }
            )

    # Image units
    for iu in comp.image_units if comp else []:
        files.append(
            {"filename": f"{iu.name}.image", "content": _render_image_unit(service_id, iu)}
        )

    needs_network = any(c.network != "host" and not c.pod_name for c in containers)
    if needs_network:
        files.append(
            {"filename": f"{service_id}.network", "content": _render_network(service_id, comp)}
        )

    for container in containers:
        if container.build_context:
            files.append(
                {
                    "filename": f"{container.name}-build.build",
                    "content": _render_build(service_id, container),
                }
            )
        files.append(
            {
                "filename": f"{container.name}.container",
                "content": _render_container(service_id, container, service_volumes),
            }
        )

    container_map = {c.id: c.name for c in containers}
    for timer in timers or []:
        container_name = SafeResourceName.of(
            container_map.get(timer.container_id, timer.container_name), "container_name"
        )
        files.append(
            {
                "filename": f"{timer.name}.timer",
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
        sections.append(f"# FileName={pod.name}\n{content.rstrip()}")

    # Quadlet-managed volume units
    for vol in service_volumes:
        if vol.use_quadlet:
            content = _render_volume_unit(service_id, vol)
            sections.append(f"# FileName={service_id}-{vol.name}\n{content.rstrip()}")

    # Image units
    for iu in comp.image_units if comp else []:
        content = _render_image_unit(service_id, iu)
        sections.append(f"# FileName={iu.name}\n{content.rstrip()}")

    for container in containers:
        if container.build_context:
            content = _render_build(service_id, container)
            sections.append(f"# FileName={container.name}-build\n{content.rstrip()}")

        content = _render_container(service_id, container, service_volumes)
        sections.append(f"# FileName={container.name}\n{content.rstrip()}")

    needs_network = any(c.network != "host" and not c.pod_name for c in containers)
    if needs_network:
        content = _render_network(service_id, comp)
        sections.append(f"# FileName={service_id}\n{content.rstrip()}")

    return "\n---\n".join(sections) + "\n"


@host.audit("WRITE_POD_UNIT", lambda sid, pod, *_: f"{sid}/{pod.name}")
@sanitized.enforce
def write_pod_unit(service_id: SafeSlug, pod: Pod) -> str:
    """Render and write a .pod quadlet file. Returns systemd unit name."""
    quadlet_dir = ensure_quadlet_dir(service_id)
    username = f"qm-{service_id}"
    pw = pwd.getpwnam(username)

    content = _render_pod(service_id, pod)
    pod_file = os.path.join(quadlet_dir, f"{pod.name}.pod")
    host.write_text(SafeAbsPath.of(pod_file, "pod_file"), content, pw.pw_uid, pw.pw_gid)
    logger.info("Wrote pod unit %s.pod for service %s", pod.name, service_id)
    return f"{pod.name}-pod.service"


@host.audit("WRITE_VOLUME_UNIT", lambda sid, vol, *_: f"{sid}/{vol.name}")
@sanitized.enforce
def write_volume_unit(service_id: SafeSlug, volume: Volume) -> None:
    """Render and write a .volume quadlet file for a quadlet-managed volume."""
    quadlet_dir = ensure_quadlet_dir(service_id)
    username = f"qm-{service_id}"
    pw = pwd.getpwnam(username)

    content = _render_volume_unit(service_id, volume)
    vol_file = os.path.join(quadlet_dir, f"{service_id}-{volume.name}.volume")
    host.write_text(SafeAbsPath.of(vol_file, "vol_file"), content, pw.pw_uid, pw.pw_gid)
    logger.info(
        "Wrote volume unit %s-%s.volume for service %s", service_id, volume.name, service_id
    )


@host.audit("WRITE_IMAGE_UNIT", lambda sid, iu, *_: f"{sid}/{iu.name}")
@sanitized.enforce
def write_image_unit(service_id: SafeSlug, image_unit: ImageUnit) -> str:
    """Render and write a .image quadlet file. Returns systemd unit name."""
    quadlet_dir = ensure_quadlet_dir(service_id)
    username = f"qm-{service_id}"
    pw = pwd.getpwnam(username)

    content = _render_image_unit(service_id, image_unit)
    img_file = os.path.join(quadlet_dir, f"{image_unit.name}.image")
    host.write_text(SafeAbsPath.of(img_file, "img_file"), content, pw.pw_uid, pw.pw_gid)
    logger.info("Wrote image unit %s.image for service %s", image_unit.name, service_id)
    return f"{image_unit.name}-image.service"


@host.audit("REMOVE_POD_UNIT", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def remove_pod_unit(service_id: SafeSlug, pod_name: SafeResourceName) -> None:
    quadlet_dir = os.path.join(get_home(service_id), ".config", "containers", "systemd")
    pod_file = os.path.join(quadlet_dir, f"{pod_name}.pod")
    if os.path.exists(pod_file):
        host.unlink(SafeAbsPath.of(pod_file, "pod_file"))
        logger.info("Removed pod unit %s.pod for service %s", pod_name, service_id)


@host.audit("REMOVE_VOLUME_UNIT", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def remove_volume_unit(service_id: SafeSlug, volume_name: SafeResourceName) -> None:
    quadlet_dir = os.path.join(get_home(service_id), ".config", "containers", "systemd")
    vol_file = os.path.join(quadlet_dir, f"{service_id}-{volume_name}.volume")
    if os.path.exists(vol_file):
        host.unlink(SafeAbsPath.of(vol_file, "vol_file"))
        logger.info(
            "Removed volume unit %s-%s.volume for service %s", service_id, volume_name, service_id
        )


@host.audit("REMOVE_IMAGE_UNIT", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def remove_image_unit(service_id: SafeSlug, image_name: SafeResourceName) -> None:
    quadlet_dir = os.path.join(get_home(service_id), ".config", "containers", "systemd")
    img_file = os.path.join(quadlet_dir, f"{image_name}.image")
    if os.path.exists(img_file):
        host.unlink(SafeAbsPath.of(img_file, "img_file"))
        logger.info("Removed image unit %s.image for service %s", image_name, service_id)


@host.audit("REMOVE_BUILD_UNIT", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def remove_build_unit(service_id: SafeSlug, container_name: SafeResourceName) -> None:
    quadlet_dir = os.path.join(get_home(service_id), ".config", "containers", "systemd")
    build_file = os.path.join(quadlet_dir, f"{container_name}-build.build")
    if os.path.exists(build_file):
        host.unlink(SafeAbsPath.of(build_file, "build_file"))
        logger.info("Removed build unit %s-build.build for service %s", container_name, service_id)


@host.audit("REMOVE_CONTAINER_UNIT", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def remove_container_unit(service_id: SafeSlug, container_name: SafeResourceName) -> None:
    quadlet_dir = os.path.join(get_home(service_id), ".config", "containers", "systemd")
    unit_file = os.path.join(quadlet_dir, f"{container_name}.container")
    if os.path.exists(unit_file):
        host.unlink(SafeAbsPath.of(unit_file, "unit_file"))
        logger.info("Removed quadlet unit %s.container for service %s", container_name, service_id)
    remove_build_unit(service_id, container_name)


@host.audit("WRITE_TIMER_UNIT", lambda sid, t, *_: f"{sid}/{t.name}")
@sanitized.enforce
def write_timer_unit(service_id: SafeSlug, timer: Timer, container_name: SafeResourceName) -> str:
    """Render and write a .timer systemd unit file. Returns the timer unit name."""
    quadlet_dir = ensure_quadlet_dir(service_id)
    username = f"qm-{service_id}"
    pw = pwd.getpwnam(username)

    content = _render_timer(service_id, timer, container_name)

    timer_file = os.path.join(quadlet_dir, f"{timer.name}.timer")
    host.write_text(SafeAbsPath.of(timer_file, "timer_file"), content, pw.pw_uid, pw.pw_gid)
    logger.info("Wrote timer unit %s.timer for service %s", timer.name, service_id)
    return f"{timer.name}.timer"


@host.audit("REMOVE_TIMER_UNIT", lambda sid, name, *_: f"{sid}/{name}")
@sanitized.enforce
def remove_timer_unit(service_id: SafeSlug, timer_name: SafeResourceName) -> None:
    quadlet_dir = os.path.join(get_home(service_id), ".config", "containers", "systemd")
    timer_file = os.path.join(quadlet_dir, f"{timer_name}.timer")
    if os.path.exists(timer_file):
        host.unlink(SafeAbsPath.of(timer_file, "timer_file"))
        logger.info("Removed timer unit %s.timer for service %s", timer_name, service_id)


@host.audit("REMOVE_NETWORK_UNIT", lambda sid, *_: sid)
@sanitized.enforce
def remove_network_unit(service_id: SafeSlug) -> None:
    quadlet_dir = os.path.join(get_home(service_id), ".config", "containers", "systemd")
    net_file = os.path.join(quadlet_dir, f"{service_id}.network")
    if os.path.exists(net_file):
        host.unlink(SafeAbsPath.of(net_file, "net_file"))
