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
    exec_start_post: str = ""
    exec_stop: str = ""
    memory_limit: str = ""
    cpu_quota: str = ""
    depends_on: list[str] = field(default_factory=list)
    apparmor_profile: str = ""
    pod_name: str = ""
    log_driver: str = ""
    working_dir: str = ""
    hostname: str = ""
    no_new_privileges: bool = False
    read_only: bool = False
    skipped_volumes: list[str] = field(default_factory=list)


@dataclass
class ParsedPod:
    name: str
    network: str = ""
    publish_ports: list[str] = field(default_factory=list)


@dataclass
class ParsedVolumeUnit:
    name: str
    vol_driver: str = ""
    vol_device: str = ""
    vol_options: str = ""
    vol_copy: bool = True


@dataclass
class ParsedImageUnit:
    name: str
    image: str
    pull_policy: str = ""
    auth_file: str = ""


@dataclass
class BundleParseResult:
    containers: list[ParsedContainer] = field(default_factory=list)
    pods: list[ParsedPod] = field(default_factory=list)
    volume_units: list[ParsedVolumeUnit] = field(default_factory=list)
    image_units: list[ParsedImageUnit] = field(default_factory=list)
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
        elif section_type == "pod":
            parsed_pod = _build_pod(filename, fields)
            if parsed_pod:
                result.pods.append(parsed_pod)
        elif section_type == "volume":
            parsed_vol = _build_volume_unit(filename, fields)
            if parsed_vol:
                result.volume_units.append(parsed_vol)
        elif section_type == "image":
            parsed_img = _build_image_unit(filename, fields, result.warnings)
            if parsed_img:
                result.image_units.append(parsed_img)
        elif section_type in ("network", "kube", "build") or section_type:
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
        net = net_vals[0].split(":")[0]  # strip :alias=... suffix
        if net == "host":
            network = "host"
        elif net.endswith(".network"):
            network = net[: -len(".network")]
        else:
            network = net

    # Pod assignment
    pod_name = ""
    pod_vals = fields.get("Container.Pod", [])
    if pod_vals:
        raw_pod = pod_vals[0]
        pod_name = raw_pod[: -len(".pod")] if raw_pod.endswith(".pod") else raw_pod

    # Volumes: can't auto-map, record as warnings
    skipped_volumes: list[str] = []
    for vol in fields.get("Container.Volume", []):
        skipped_volumes.append(vol)
        warnings.append(
            f"Container '{name}': volume mount '{vol}' skipped — "
            "add managed volumes via the UI after import"
        )

    apparmor_profile = (fields.get("Container.AppArmor") or [""])[0]
    log_driver = (fields.get("Container.LogDriver") or [""])[0]
    working_dir = (fields.get("Container.WorkingDir") or [""])[0]
    hostname = (fields.get("Container.HostName") or [""])[0]
    no_new_privileges = bool(fields.get("Container.NoNewPrivileges"))
    read_only = bool(fields.get("Container.ReadOnly"))

    # [Service] fields
    restart_policy = (fields.get("Service.Restart") or ["always"])[0]
    memory_limit = (fields.get("Service.MemoryLimit") or [""])[0]
    cpu_quota = (fields.get("Service.CPUQuota") or [""])[0]
    exec_start_pre = (fields.get("Service.ExecStartPre") or [""])[0]
    exec_start_post = (fields.get("Service.ExecStartPost") or [""])[0]
    exec_stop = (fields.get("Service.ExecStop") or [""])[0]

    # depends_on from After= (space-separated "<name>.service" tokens)
    depends_on: list[str] = []
    for after_val in fields.get("Unit.After", []):
        for part in after_val.split():
            if part.endswith(".service") and not part.endswith("-pod.service"):
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
        exec_start_post=exec_start_post,
        exec_stop=exec_stop,
        memory_limit=memory_limit,
        cpu_quota=cpu_quota,
        depends_on=depends_on,
        apparmor_profile=apparmor_profile,
        pod_name=pod_name,
        log_driver=log_driver,
        working_dir=working_dir,
        hostname=hostname,
        no_new_privileges=no_new_privileges,
        read_only=read_only,
        skipped_volumes=skipped_volumes,
    )


def _build_pod(filename: str, fields: dict[str, list[str]]) -> ParsedPod | None:
    name = filename
    if not name:
        pod_name_vals = fields.get("Pod.PodName", [])
        if pod_name_vals:
            raw = pod_name_vals[0]
            # Strip service prefix if present (e.g. "myapp-mypod" -> "mypod")
            name = raw.split("-", 1)[1] if "-" in raw else raw
    if not name:
        return None

    publish_ports = fields.get("Pod.PublishPort", [])

    network = ""
    net_vals = fields.get("Pod.Network", [])
    if net_vals:
        net = net_vals[0].split(":")[0]
        network = net[: -len(".network")] if net.endswith(".network") else net

    return ParsedPod(name=name, network=network, publish_ports=publish_ports)


def _build_volume_unit(filename: str, fields: dict[str, list[str]]) -> ParsedVolumeUnit | None:
    name = filename
    if not name:
        vol_name_vals = fields.get("Volume.VolumeName", [])
        if vol_name_vals:
            raw = vol_name_vals[0]
            name = raw.split("-", 1)[1] if "-" in raw else raw
    if not name:
        return None

    vol_driver = (fields.get("Volume.Driver") or [""])[0]
    vol_device = (fields.get("Volume.Device") or [""])[0]
    vol_options = (fields.get("Volume.Options") or [""])[0]
    copy_vals = fields.get("Volume.Copy", [])
    vol_copy = (copy_vals[0].lower() != "false") if copy_vals else True

    return ParsedVolumeUnit(
        name=name,
        vol_driver=vol_driver,
        vol_device=vol_device,
        vol_options=vol_options,
        vol_copy=vol_copy,
    )


def _build_image_unit(
    filename: str,
    fields: dict[str, list[str]],
    warnings: list[str],
) -> ParsedImageUnit | None:
    name = filename
    if not name:
        warnings.append("Image section with no FileName, skipping")
        return None

    image_vals = fields.get("Image.Image", [])
    if not image_vals:
        warnings.append(f"Image section '{name}': no Image= found, skipping")
        return None

    pull_policy = (fields.get("Image.PullPolicy") or [""])[0]
    auth_file = (fields.get("Image.AuthFile") or [""])[0]

    return ParsedImageUnit(
        name=name,
        image=image_vals[0],
        pull_policy=pull_policy,
        auth_file=auth_file,
    )
