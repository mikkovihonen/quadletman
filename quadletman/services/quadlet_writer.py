"""Quadlet file generation and management."""

import difflib
import logging
import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..models import Container, Volume
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


def _render_container(
    service_id: str, container: Container, service_volumes: list[Volume]
) -> str:
    vol_by_id = {v.id: v for v in service_volumes}
    resolved_mounts = []
    for vm in container.volumes:
        vol = vol_by_id.get(vm.volume_id)
        if vol:
            resolved_mounts.append(
                {
                    "host_path": volume_path(service_id, vol.name),
                    "container_path": vm.container_path,
                    "options": vm.options,
                }
            )
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


def _render_build(service_id: str, container: Container) -> str:
    return _jinja_env.get_template("build.ini.j2").render(
        service_id=service_id, container=container
    )


def _render_network(service_id: str) -> str:
    return _jinja_env.get_template("network.ini.j2").render(service_id=service_id)


def _compare_file(path: str, expected: str) -> dict | None:
    """Return a sync issue dict if the file is missing or differs, else None."""
    filename = os.path.basename(path)
    try:
        actual = open(path).read()
    except FileNotFoundError:
        diff = "".join(
            difflib.unified_diff(
                [], expected.splitlines(keepends=True),
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
    service_id: str, containers: list[Container], service_volumes: list[Volume]
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

    if any(c.network != "host" for c in containers):
        net_path = os.path.join(quadlet_dir, f"{service_id}.network")
        issue = _compare_file(net_path, _render_network(service_id))
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

    # Build resolved volume mounts for template
    vol_by_id = {v.id: v for v in service_volumes}
    resolved_mounts = []
    for vm in container.volumes:
        vol = vol_by_id.get(vm.volume_id)
        if vol:
            resolved_mounts.append(
                {
                    "host_path": volume_path(service_id, vol.name),
                    "container_path": vm.container_path,
                    "options": vm.options,
                }
            )
        else:
            logger.warning("Volume %s not found for container %s", vm.volume_id, container.name)

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


def write_network_unit(service_id: str) -> None:
    """Write a shared .network quadlet file for multi-container services."""
    import pwd

    quadlet_dir = ensure_quadlet_dir(service_id)
    username = f"qm-{service_id}"
    pw = pwd.getpwnam(username)

    tmpl = _jinja_env.get_template("network.ini.j2")
    content = tmpl.render(service_id=service_id)

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
) -> list[dict]:
    """Render all quadlet files for a service as a list of {filename, content} dicts."""
    files: list[dict] = []

    if any(c.network != "host" for c in containers):
        files.append(
            {"filename": f"{service_id}.network", "content": _render_network(service_id)}
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
) -> str:
    """Render all quadlet units for a service as a .quadlets bundle string."""
    sections: list[str] = []
    vol_by_id = {v.id: v for v in service_volumes}

    for container in containers:
        if container.build_context:
            tmpl = _jinja_env.get_template("build.ini.j2")
            content = tmpl.render(service_id=service_id, container=container)
            sections.append(f"# FileName={container.name}-build\n{content.rstrip()}")

        resolved_mounts = []
        for vm in container.volumes:
            vol = vol_by_id.get(vm.volume_id)
            if vol:
                resolved_mounts.append(
                    {
                        "host_path": volume_path(service_id, vol.name),
                        "container_path": vm.container_path,
                        "options": vm.options,
                    }
                )

        tmpl = _jinja_env.get_template("container.ini.j2")
        content = tmpl.render(
            service_id=service_id,
            container=container,
            resolved_mounts=resolved_mounts,
        )
        sections.append(f"# FileName={container.name}\n{content.rstrip()}")

    if any(c.network != "host" for c in containers):
        tmpl = _jinja_env.get_template("network.ini.j2")
        content = tmpl.render(service_id=service_id)
        sections.append(f"# FileName={service_id}\n{content.rstrip()}")

    return "\n---\n".join(sections) + "\n"


def remove_build_unit(service_id: str, container_name: str) -> None:
    quadlet_dir = os.path.join(get_home(service_id), ".config", "containers", "systemd")
    build_file = os.path.join(quadlet_dir, f"{container_name}-build.build")
    if os.path.exists(build_file):
        os.unlink(build_file)
        logger.info(
            "Removed build unit %s-build.build for service %s", container_name, service_id
        )


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
