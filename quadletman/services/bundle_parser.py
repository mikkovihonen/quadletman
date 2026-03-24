"""Parser for .quadlets multi-unit bundle files (Podman 5.8.0+).

The .quadlets format separates unit definitions with '---' delimiter lines.
Each section begins with a '# FileName=<name>' comment that names the output
unit file. The unit type is detected from the INI section header ([Container],
[Network], [Volume], etc.).
"""

import re

from quadletman.models import sanitized
from quadletman.models.sanitized import SafeMultilineStr, SafeStr
from quadletman.models.service import (
    BundleParseResult,
    ParsedContainer,
    ParsedImageUnit,
    ParsedPod,
    ParsedVolumeUnit,
)


@sanitized.enforce
def parse_quadlets_bundle(content: SafeMultilineStr) -> BundleParseResult:
    """Parse a .quadlets multi-unit bundle file into structured objects."""
    result = BundleParseResult()

    for section in _split_sections(content):
        if not section.strip():
            continue

        filename = _extract_filename(section)
        section_type = _detect_type(section)
        fields = _parse_ini_multi(section)

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
            result.skipped_section_types.append(SafeStr.of(section_type, "parse_quadlets_bundle"))

    return result


@sanitized.enforce
def _split_sections(content: SafeMultilineStr) -> list[SafeMultilineStr]:
    sections: list[SafeMultilineStr] = []
    current: list[str] = []
    for line in content.splitlines():
        if line.strip() == "---":
            if current:
                sections.append(SafeMultilineStr.trusted("\n".join(current), "_split_sections"))
                current = []
        else:
            current.append(line)
    if current:
        sections.append(SafeMultilineStr.trusted("\n".join(current), "_split_sections"))
    return sections


@sanitized.enforce
def _extract_filename(section_text: SafeMultilineStr) -> SafeStr:
    for line in section_text.splitlines():
        m = re.match(r"#\s*FileName\s*=\s*(.+)", line.strip())
        if m:
            return SafeStr.of(m.group(1).strip(), "_extract_filename")
    return SafeStr.trusted("", "_extract_filename")


@sanitized.enforce
def _detect_type(section_text: SafeMultilineStr) -> str:
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


@sanitized.enforce
def _parse_ini_multi(content: SafeMultilineStr) -> dict[str, list[str]]:
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


@sanitized.enforce
def _build_container(
    filename: SafeStr,
    fields: dict[str, list[str]],
    warnings: list[SafeStr],
) -> ParsedContainer | None:
    image_vals = fields.get("Container.Image", [])
    if not image_vals:
        warnings.append(
            SafeStr.of(f"Section '{filename}': no Image= found, skipping", "_build_container")
        )
        return None

    # Prefer FileName as the container name; fall back to ContainerName
    name = str(filename)
    if not name:
        container_name_vals = fields.get("Container.ContainerName", [])
        raw = container_name_vals[0] if container_name_vals else ""
        # Strip a "serviceprefix-" prefix (e.g. "myapp-web" -> "web")
        name = raw.split("-", 1)[1] if "-" in raw else raw
    if not name:
        warnings.append(
            SafeStr.trusted("Section with no FileName and no ContainerName, skipping", "hardcoded")
        )
        return None

    # Environment: KEY=value lines
    environment: dict[SafeStr, SafeStr] = {}
    for val in fields.get("Container.Environment", []):
        k, _, v = val.partition("=")
        if k.strip():
            environment[SafeStr.of(k.strip(), "_build_container")] = SafeStr.of(
                v.strip(), "_build_container"
            )

    # Published ports
    ports = [SafeStr.of(p, "_build_container") for p in fields.get("Container.PublishPort", [])]

    # Labels: KEY=value lines
    labels: dict[SafeStr, SafeStr] = {}
    for val in fields.get("Container.Label", []):
        k, _, v = val.partition("=")
        if k.strip():
            labels[SafeStr.of(k.strip(), "_build_container")] = SafeStr.of(
                v.strip(), "_build_container"
            )

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
    skipped_volumes: list[SafeStr] = []
    for vol in fields.get("Container.Volume", []):
        skipped_volumes.append(SafeStr.of(vol, "_build_container"))
        warnings.append(
            SafeStr.of(
                f"Container '{name}': volume mount '{vol}' skipped — "
                "add managed volumes via the UI after import",
                "_build_container",
            )
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
    depends_on: list[SafeStr] = []
    for after_val in fields.get("Unit.After", []):
        for part in after_val.split():
            if part.endswith(".service") and not part.endswith("-pod.service"):
                depends_on.append(SafeStr.of(part[: -len(".service")], "_build_container"))

    return ParsedContainer(
        qm_name=SafeStr.of(name, "_build_container"),
        image=SafeStr.of(image_vals[0], "_build_container"),
        environment=environment,
        ports=ports,
        labels=labels,
        network=SafeStr.of(network, "_build_container"),
        restart_policy=SafeStr.of(restart_policy, "_build_container"),
        exec_start_pre=SafeStr.of(exec_start_pre, "_build_container"),
        exec_start_post=SafeStr.of(exec_start_post, "_build_container"),
        exec_stop=SafeStr.of(exec_stop, "_build_container"),
        memory_limit=SafeStr.of(memory_limit, "_build_container"),
        cpu_quota=SafeStr.of(cpu_quota, "_build_container"),
        depends_on=depends_on,
        apparmor_profile=SafeStr.of(apparmor_profile, "_build_container"),
        pod=SafeStr.of(pod_name, "_build_container"),
        log_driver=SafeStr.of(log_driver, "_build_container"),
        working_dir=SafeStr.of(working_dir, "_build_container"),
        hostname=SafeStr.of(hostname, "_build_container"),
        no_new_privileges=no_new_privileges,
        read_only=read_only,
        skipped_volumes=skipped_volumes,
    )


@sanitized.enforce
def _build_pod(filename: SafeStr, fields: dict[str, list[str]]) -> ParsedPod | None:
    name = str(filename)
    if not name:
        pod_name_vals = fields.get("Pod.PodName", [])
        if pod_name_vals:
            raw = pod_name_vals[0]
            # Strip service prefix if present (e.g. "myapp-mypod" -> "mypod")
            name = raw.split("-", 1)[1] if "-" in raw else raw
    if not name:
        return None

    publish_ports = [SafeStr.of(p, "_build_pod") for p in fields.get("Pod.PublishPort", [])]

    network = ""
    net_vals = fields.get("Pod.Network", [])
    if net_vals:
        net = net_vals[0].split(":")[0]
        network = net[: -len(".network")] if net.endswith(".network") else net

    return ParsedPod(
        qm_name=SafeStr.of(name, "_build_pod"),
        network=SafeStr.of(network, "_build_pod"),
        publish_ports=publish_ports,
    )


@sanitized.enforce
def _build_volume_unit(filename: SafeStr, fields: dict[str, list[str]]) -> ParsedVolumeUnit | None:
    name = str(filename)
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
        qm_name=SafeStr.of(name, "_build_volume_unit"),
        driver=SafeStr.of(vol_driver, "_build_volume_unit"),
        device=SafeStr.of(vol_device, "_build_volume_unit"),
        options=SafeStr.of(vol_options, "_build_volume_unit"),
        copy=vol_copy,
    )


@sanitized.enforce
def _build_image_unit(
    filename: SafeStr,
    fields: dict[str, list[str]],
    warnings: list[SafeStr],
) -> ParsedImageUnit | None:
    name = str(filename)
    if not name:
        warnings.append(SafeStr.trusted("Image section with no FileName, skipping", "hardcoded"))
        return None

    image_vals = fields.get("Image.Image", [])
    if not image_vals:
        warnings.append(
            SafeStr.of(f"Image section '{name}': no Image= found, skipping", "_build_image_unit")
        )
        return None

    auth_file = (fields.get("Image.AuthFile") or [""])[0]

    return ParsedImageUnit(
        qm_name=SafeStr.of(name, "_build_image_unit"),
        image=SafeStr.of(image_vals[0], "_build_image_unit"),
        auth_file=SafeStr.of(auth_file, "_build_image_unit"),
    )
