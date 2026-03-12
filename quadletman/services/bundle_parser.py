"""Parser for .quadlets multi-unit bundle files (Podman 5.8.0+).

The .quadlets format separates unit definitions with '---' delimiter lines.
Each section begins with a '# FileName=<name>' comment that names the output
unit file. The unit type is detected from the INI section header ([Container],
[Network], [Volume], etc.).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ParsedContainer:
    name: str
    image: str
    environment: dict[str, str] = field(default_factory=dict)
    ports: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    network: str = "host"
    restart_policy: str = "always"
    exec_start_pre: str = ""
    memory_limit: str = ""
    cpu_quota: str = ""
    depends_on: list[str] = field(default_factory=list)
    apparmor_profile: str = ""
    skipped_volumes: list[str] = field(default_factory=list)


@dataclass
class BundleParseResult:
    containers: list[ParsedContainer] = field(default_factory=list)
    skipped_section_types: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_quadlets_bundle(content: str) -> BundleParseResult:
    """Parse a .quadlets multi-unit bundle file into structured objects."""
    result = BundleParseResult()

    for section_text in _split_sections(content):
        section_text = section_text.strip()
        if not section_text:
            continue

        filename = _extract_filename(section_text)
        section_type = _detect_type(section_text)
        fields = _parse_ini_multi(section_text)

        if section_type == "container":
            parsed = _build_container(filename, fields, result.warnings)
            if parsed:
                result.containers.append(parsed)
        elif section_type in ("network", "volume", "kube", "image", "build", "pod") or section_type:
            result.skipped_section_types.append(section_type)

    return result


def _split_sections(content: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    for line in content.splitlines():
        if line.strip() == "---":
            if current:
                sections.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current))
    return sections


def _extract_filename(section_text: str) -> str:
    for line in section_text.splitlines():
        m = re.match(r"#\s*FileName\s*=\s*(.+)", line.strip())
        if m:
            return m.group(1).strip()
    return ""


def _detect_type(section_text: str) -> str:
    _TYPE_MAP = {
        "[Container]": "container",
        "[Network]": "network",
        "[Volume]": "volume",
        "[Kube]": "kube",
        "[Image]": "image",
        "[Build]": "build",
        "[Pod]": "pod",
    }
    for line in section_text.splitlines():
        t = _TYPE_MAP.get(line.strip())
        if t:
            return t
    return ""


def _parse_ini_multi(content: str) -> dict[str, list[str]]:
    """Parse INI content into Section.Key -> [values], supporting duplicate keys."""
    result: dict[str, list[str]] = {}
    current_section = ""
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1]
            continue
        if "=" in stripped:
            key, _, val = stripped.partition("=")
            full_key = f"{current_section}.{key.strip()}"
            result.setdefault(full_key, []).append(val.strip())
    return result


def _build_container(
    filename: str,
    fields: dict[str, list[str]],
    warnings: list[str],
) -> ParsedContainer | None:
    image_vals = fields.get("Container.Image", [])
    if not image_vals:
        warnings.append(f"Section '{filename}': no Image= found, skipping")
        return None

    # Prefer FileName as the container name; fall back to ContainerName
    name = filename
    if not name:
        container_name_vals = fields.get("Container.ContainerName", [])
        raw = container_name_vals[0] if container_name_vals else ""
        # Strip a "serviceprefix-" prefix (e.g. "myapp-web" -> "web")
        name = raw.split("-", 1)[1] if "-" in raw else raw
    if not name:
        warnings.append("Section with no FileName and no ContainerName, skipping")
        return None

    # Environment: KEY=value lines
    environment: dict[str, str] = {}
    for val in fields.get("Container.Environment", []):
        k, _, v = val.partition("=")
        if k.strip():
            environment[k.strip()] = v.strip()

    # Published ports
    ports = fields.get("Container.PublishPort", [])

    # Labels: KEY=value lines
    labels: dict[str, str] = {}
    for val in fields.get("Container.Label", []):
        k, _, v = val.partition("=")
        if k.strip():
            labels[k.strip()] = v.strip()

    # Network: "host" or "<name>.network" -> strip suffix
    network = "host"
    net_vals = fields.get("Container.Network", [])
    if net_vals:
        net = net_vals[0]
        if net == "host":
            network = "host"
        elif net.endswith(".network"):
            network = net[: -len(".network")]
        else:
            network = net

    # Volumes: can't auto-map host paths, record as warnings
    skipped_volumes: list[str] = []
    for vol in fields.get("Container.Volume", []):
        skipped_volumes.append(vol)
        warnings.append(
            f"Container '{name}': volume mount '{vol}' skipped — "
            "add managed volumes via the UI after import"
        )

    apparmor_profile = (fields.get("Container.AppArmor") or [""])[0]

    # [Service] fields
    restart_policy = (fields.get("Service.Restart") or ["always"])[0]
    memory_limit = (fields.get("Service.MemoryLimit") or [""])[0]
    cpu_quota = (fields.get("Service.CPUQuota") or [""])[0]
    exec_start_pre = (fields.get("Service.ExecStartPre") or [""])[0]

    # depends_on from After= (space-separated "<name>.service" tokens)
    depends_on: list[str] = []
    for after_val in fields.get("Unit.After", []):
        for part in after_val.split():
            if part.endswith(".service"):
                depends_on.append(part[: -len(".service")])

    return ParsedContainer(
        name=name,
        image=image_vals[0],
        environment=environment,
        ports=ports,
        labels=labels,
        network=network,
        restart_policy=restart_policy,
        exec_start_pre=exec_start_pre,
        memory_limit=memory_limit,
        cpu_quota=cpu_quota,
        depends_on=depends_on,
        apparmor_profile=apparmor_profile,
        skipped_volumes=skipped_volumes,
    )
