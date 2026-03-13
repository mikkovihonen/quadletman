"""Quadlet file generation and management."""

import difflib
import logging
import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..models import Container, ImageUnit, Pod, Service, Volume
from .user_manager import ensure_quadlet_dir, get_home
from .volume_manager import volume_path

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "quadlet"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)


_UID_NAMESPACE_SIZE = 65536


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


def _resolve_mounts(
    service_id: str, container: Container, service_volumes: list[Volume]
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
                        "host_path": volume_path(service_id, vol.name),
                        "container_path": vm.container_path,
                        "options": vm.options,
                    }
                )
    return resolved_mounts


def _render_container(service_id: str, container: Container, service_volumes: list[Volume]) -> str:
    resolved_mounts = _resolve_mounts(service_id, container, service_volumes)
    resolved_uid_map = _resolve_id_maps(container.uid_map)
    effective_gid_ids = container.gid_map if container.gid_map else container.uid_map
    resolved_gid_map = _resolve_id_maps(effective_gid_ids)
    return _jinja_env.get_template("container.ini.j2").render(
        service_id=service_id,
        container=container,
        resolved_mounts=resolved_mounts,
        resolved_uid_map=resolved_uid_map,
        resolved_gid_map=resolved_gid_map,
    )


def _render_pod(service_id: str, pod: Pod) -> str:
    return _jinja_env.get_template("pod.ini.j2").render(service_id=service_id, pod=pod)


def _render_volume_unit(service_id: str, volume: Volume) -> str:
    return _jinja_env.get_template("volume.ini.j2").render(service_id=service_id, volume=volume)


def _render_image_unit(service_id: str, image_unit: ImageUnit) -> str:
    return _jinja_env.get_template("image.ini.j2").render(
        service_id=service_id, image_unit=image_unit
    )


def _render_build(service_id: str, container: Container) -> str:
    return _jinja_env.get_template("build.ini.j2").render(
        service_id=service_id, container=container
    )


def _render_network(service_id: str, svc: "Service | None" = None) -> str:
    return _jinja_env.get_template("network.ini.j2").render(service_id=service_id, svc=svc)


def _compare_file(path: str, expected: str) -> dict | None:
    """Return a sync issue dict if the file is missing or differs, else None."""
    filename = os.path.basename(path)
    try:
        with open(path) as _f:
            actual = _f.read()
    except FileNotFoundError:
        diff = "".join(
            difflib.unified_diff(
                [],
                expected.splitlines(keepends=True),
                fromfile=f"{filename} (on disk)",
                tofile=f"{filename} (expected)",
            )
        )
        return {"file": filename, "status": "missing", "diff": diff or "(file missing)"}
    if actual != expected:
        diff = "".join(
            difflib.unified_diff(
                actual.splitlines(keepends=True),
                expected.splitlines(keepends=True),
                fromfile=f"{filename} (on disk)",
                tofile=f"{filename} (expected)",
            )
        )
        return {"file": filename, "status": "changed", "diff": diff}
    return None


def check_service_sync(
    service_id: str,
    containers: list[Container],
    service_volumes: list[Volume],
    svc: "Service | None" = None,
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
        issue = _compare_file(net_path, _render_network(service_id, svc))
        if issue:
            issues.append(issue)

    # Pod units
    for pod in svc.pods if svc else []:
        pod_path = os.path.join(quadlet_dir, f"{pod.name}.pod")
        issue = _compare_file(pod_path, _render_pod(service_id, pod))
        if issue:
            issues.append(issue)

    # Quadlet-managed volume units
    for vol in service_volumes:
        if vol.use_quadlet:
            vol_path = os.path.join(quadlet_dir, f"{service_id}-{vol.name}.volume")
            issue = _compare_file(vol_path, _render_volume_unit(service_id, vol))
            if issue:
                issues.append(issue)

    # Image units
    for iu in svc.image_units if svc else []:
        img_path = os.path.join(quadlet_dir, f"{iu.name}.image")
        issue = _compare_file(img_path, _render_image_unit(service_id, iu))
        if issue:
            issues.append(issue)

    for container in containers:
        if container.build_context:
            build_path = os.path.join(quadlet_dir, f"{container.name}-build.build")
            issue = _compare_file(build_path, _render_build(service_id, container))
            if issue:
                issues.append(issue)

        unit_path = os.path.join(quadlet_dir, f"{container.name}.container")
        issue = _compare_file(unit_path, _render_container(service_id, container, service_volumes))
        if issue:
            issues.append(issue)

    return issues


def write_build_unit(service_id: str, container: Container) -> str:
    """Render and write a .build quadlet file. Returns systemd unit name."""
    import pwd

    quadlet_dir = ensure_quadlet_dir(service_id)
    username = f"qm-{service_id}"
    pw = pwd.getpwnam(username)

    tmpl = _jinja_env.get_template("build.ini.j2")
    content = tmpl.render(service_id=service_id, container=container)

    build_file = os.path.join(quadlet_dir, f"{container.name}-build.build")
    with open(build_file, "w") as f:
        f.write(content)
    os.chown(build_file, pw.pw_uid, pw.pw_gid)
    os.chmod(build_file, 0o600)

    unit_name = f"{container.name}-build.service"
    logger.info("Wrote build unit %s for service %s", unit_name, service_id)
    return unit_name


def write_container_unit(
    service_id: str,
    container: Container,
    service_volumes: list[Volume],
) -> str:
    """Render and write a .container quadlet file. Returns systemd unit name."""
    import pwd

    quadlet_dir = ensure_quadlet_dir(service_id)
    username = f"qm-{service_id}"
    pw = pwd.getpwnam(username)

    if container.build_context:
        write_build_unit(service_id, container)

    resolved_mounts = _resolve_mounts(service_id, container, service_volumes)
    resolved_uid_map = _resolve_id_maps(container.uid_map)
    effective_gid_ids = container.gid_map if container.gid_map else container.uid_map
    resolved_gid_map = _resolve_id_maps(effective_gid_ids)

    tmpl = _jinja_env.get_template("container.ini.j2")
    content = tmpl.render(
        service_id=service_id,
        container=container,
        resolved_mounts=resolved_mounts,
        resolved_uid_map=resolved_uid_map,
        resolved_gid_map=resolved_gid_map,
    )

    unit_file = os.path.join(quadlet_dir, f"{container.name}.container")
    with open(unit_file, "w") as f:
        f.write(content)
    os.chown(unit_file, pw.pw_uid, pw.pw_gid)
    os.chmod(unit_file, 0o600)

    unit_name = f"{container.name}.service"
    logger.info("Wrote quadlet unit %s for service %s", unit_name, service_id)
    return unit_name


def write_network_unit(service_id: str, svc: "Service | None" = None) -> None:
    """Write a shared .network quadlet file for multi-container services."""
    import pwd

    quadlet_dir = ensure_quadlet_dir(service_id)
    username = f"qm-{service_id}"
    pw = pwd.getpwnam(username)

    tmpl = _jinja_env.get_template("network.ini.j2")
    content = tmpl.render(service_id=service_id, svc=svc)

    net_file = os.path.join(quadlet_dir, f"{service_id}.network")
    with open(net_file, "w") as f:
        f.write(content)
    os.chown(net_file, pw.pw_uid, pw.pw_gid)
    os.chmod(net_file, 0o600)
    logger.info("Wrote network unit for service %s", service_id)


def render_quadlet_files(
    service_id: str,
    containers: list[Container],
    service_volumes: list[Volume],
    svc: "Service | None" = None,
) -> list[dict]:
    """Render all quadlet files for a service as a list of {filename, content} dicts."""
    files: list[dict] = []

    # Pod units
    for pod in svc.pods if svc else []:
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
    for iu in svc.image_units if svc else []:
        files.append(
            {"filename": f"{iu.name}.image", "content": _render_image_unit(service_id, iu)}
        )

    needs_network = any(c.network != "host" and not c.pod_name for c in containers)
    if needs_network:
        files.append(
            {"filename": f"{service_id}.network", "content": _render_network(service_id, svc)}
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

    return files


def export_service_bundle(
    service_id: str,
    containers: list[Container],
    service_volumes: list[Volume],
    svc: "Service | None" = None,
) -> str:
    """Render all quadlet units for a service as a .quadlets bundle string."""
    sections: list[str] = []

    # Pod units
    for pod in svc.pods if svc else []:
        content = _render_pod(service_id, pod)
        sections.append(f"# FileName={pod.name}\n{content.rstrip()}")

    # Quadlet-managed volume units
    for vol in service_volumes:
        if vol.use_quadlet:
            content = _render_volume_unit(service_id, vol)
            sections.append(f"# FileName={service_id}-{vol.name}\n{content.rstrip()}")

    # Image units
    for iu in svc.image_units if svc else []:
        content = _render_image_unit(service_id, iu)
        sections.append(f"# FileName={iu.name}\n{content.rstrip()}")

    for container in containers:
        if container.build_context:
            tmpl = _jinja_env.get_template("build.ini.j2")
            content = tmpl.render(service_id=service_id, container=container)
            sections.append(f"# FileName={container.name}-build\n{content.rstrip()}")

        resolved_mounts = _resolve_mounts(service_id, container, service_volumes)
        tmpl = _jinja_env.get_template("container.ini.j2")
        content = tmpl.render(
            service_id=service_id,
            container=container,
            resolved_mounts=resolved_mounts,
            resolved_uid_map=_resolve_id_maps(container.uid_map),
            resolved_gid_map=_resolve_id_maps(
                container.gid_map if container.gid_map else container.uid_map
            ),
        )
        sections.append(f"# FileName={container.name}\n{content.rstrip()}")

    needs_network = any(c.network != "host" and not c.pod_name for c in containers)
    if needs_network:
        tmpl = _jinja_env.get_template("network.ini.j2")
        content = tmpl.render(service_id=service_id, svc=svc)
        sections.append(f"# FileName={service_id}\n{content.rstrip()}")

    return "\n---\n".join(sections) + "\n"


def write_pod_unit(service_id: str, pod: Pod) -> str:
    """Render and write a .pod quadlet file. Returns systemd unit name."""
    import pwd

    quadlet_dir = ensure_quadlet_dir(service_id)
    username = f"qm-{service_id}"
    pw = pwd.getpwnam(username)

    content = _render_pod(service_id, pod)
    pod_file = os.path.join(quadlet_dir, f"{pod.name}.pod")
    with open(pod_file, "w") as f:
        f.write(content)
    os.chown(pod_file, pw.pw_uid, pw.pw_gid)
    os.chmod(pod_file, 0o600)
    logger.info("Wrote pod unit %s.pod for service %s", pod.name, service_id)
    return f"{pod.name}-pod.service"


def write_volume_unit(service_id: str, volume: Volume) -> None:
    """Render and write a .volume quadlet file for a quadlet-managed volume."""
    import pwd

    quadlet_dir = ensure_quadlet_dir(service_id)
    username = f"qm-{service_id}"
    pw = pwd.getpwnam(username)

    content = _render_volume_unit(service_id, volume)
    vol_file = os.path.join(quadlet_dir, f"{service_id}-{volume.name}.volume")
    with open(vol_file, "w") as f:
        f.write(content)
    os.chown(vol_file, pw.pw_uid, pw.pw_gid)
    os.chmod(vol_file, 0o600)
    logger.info(
        "Wrote volume unit %s-%s.volume for service %s", service_id, volume.name, service_id
    )


def write_image_unit(service_id: str, image_unit: ImageUnit) -> str:
    """Render and write a .image quadlet file. Returns systemd unit name."""
    import pwd

    quadlet_dir = ensure_quadlet_dir(service_id)
    username = f"qm-{service_id}"
    pw = pwd.getpwnam(username)

    content = _render_image_unit(service_id, image_unit)
    img_file = os.path.join(quadlet_dir, f"{image_unit.name}.image")
    with open(img_file, "w") as f:
        f.write(content)
    os.chown(img_file, pw.pw_uid, pw.pw_gid)
    os.chmod(img_file, 0o600)
    logger.info("Wrote image unit %s.image for service %s", image_unit.name, service_id)
    return f"{image_unit.name}-image.service"


def remove_pod_unit(service_id: str, pod_name: str) -> None:
    quadlet_dir = os.path.join(get_home(service_id), ".config", "containers", "systemd")
    pod_file = os.path.join(quadlet_dir, f"{pod_name}.pod")
    if os.path.exists(pod_file):
        os.unlink(pod_file)
        logger.info("Removed pod unit %s.pod for service %s", pod_name, service_id)


def remove_volume_unit(service_id: str, volume_name: str) -> None:
    quadlet_dir = os.path.join(get_home(service_id), ".config", "containers", "systemd")
    vol_file = os.path.join(quadlet_dir, f"{service_id}-{volume_name}.volume")
    if os.path.exists(vol_file):
        os.unlink(vol_file)
        logger.info(
            "Removed volume unit %s-%s.volume for service %s", service_id, volume_name, service_id
        )


def remove_image_unit(service_id: str, image_name: str) -> None:
    quadlet_dir = os.path.join(get_home(service_id), ".config", "containers", "systemd")
    img_file = os.path.join(quadlet_dir, f"{image_name}.image")
    if os.path.exists(img_file):
        os.unlink(img_file)
        logger.info("Removed image unit %s.image for service %s", image_name, service_id)


def remove_build_unit(service_id: str, container_name: str) -> None:
    quadlet_dir = os.path.join(get_home(service_id), ".config", "containers", "systemd")
    build_file = os.path.join(quadlet_dir, f"{container_name}-build.build")
    if os.path.exists(build_file):
        os.unlink(build_file)
        logger.info("Removed build unit %s-build.build for service %s", container_name, service_id)


def remove_container_unit(service_id: str, container_name: str) -> None:
    quadlet_dir = os.path.join(get_home(service_id), ".config", "containers", "systemd")
    unit_file = os.path.join(quadlet_dir, f"{container_name}.container")
    if os.path.exists(unit_file):
        os.unlink(unit_file)
        logger.info("Removed quadlet unit %s.container for service %s", container_name, service_id)
    remove_build_unit(service_id, container_name)


def remove_network_unit(service_id: str) -> None:
    quadlet_dir = os.path.join(get_home(service_id), ".config", "containers", "systemd")
    net_file = os.path.join(quadlet_dir, f"{service_id}.network")
    if os.path.exists(net_file):
        os.unlink(net_file)
