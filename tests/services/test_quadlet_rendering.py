"""Tests for Quadlet template rendering — verify every template produces valid INI output.

Each test class exercises one template with realistic data, checking:
- Correct INI section headers
- Every populated field appears on its own line
- No merged/concatenated lines (the trim_blocks newline-eating bug)
- Optional fields omitted when empty
"""

from quadletman.models import (
    Artifact,
    Build,
    Container,
    Image,
    Kube,
    Network,
    Pod,
    Timer,
    Volume,
    VolumeMount,
)
from quadletman.models.api import (
    ArtifactCreate,
    BuildCreate,
    PodCreate,
)
from quadletman.models.api.container import BindMount
from quadletman.models.sanitized import (
    SafePortMapping,
    SafeResourceName,
    SafeSlug,
    SafeStr,
    SafeTimestamp,
    SafeUUID,
)
from quadletman.models.version_span import field_availability
from quadletman.podman_version import get_features
from quadletman.services.quadlet_writer import (
    _render_artifact,
    _render_build,
    _render_container,
    _render_image_unit,
    _render_kube,
    _render_network,
    _render_pod,
    _render_timer,
    _render_volume_unit,
)

_COMP = SafeSlug.trusted("mycomp", "test")
_NOW = SafeTimestamp.trusted("2024-01-01T00:00:00Z", "test")
_V = get_features().version


def _v(model_cls: type) -> dict[str, bool]:
    """Return the field availability dict for the current Podman version."""
    return field_availability(model_cls, _V)


def _uuid(n: int) -> SafeUUID:
    return SafeUUID.trusted(f"00000000-0000-0000-0000-{n:012d}", "test")


def _assert_no_merged_lines(content: str) -> None:
    """Assert no line contains two INI keys merged together."""
    for line in content.splitlines():
        if "=" not in line or line.startswith("[") or line.startswith("#"):
            continue
        # A merged line would have a second Key= inside the value portion,
        # e.g. "Network=x.networkPublishPort=8080:80"
        value_part = line.split("=", 1)[1] if "=" in line else ""
        # Check for common Quadlet keys appearing inside the value
        for suspect in (
            "PublishPort=",
            "Volume=",
            "Network=",
            "Environment=",
            "Label=",
            "Tmpfs=",
            "Mount=",
            "UIDMap=",
            "GIDMap=",
            "Secret=",
            "DNS=",
            "AddHost=",
            "Annotation=",
            "GlobalArgs=",
            "PodmanArgs=",
            "Image=",
            "Exec=",
        ):
            assert suspect not in value_part, (
                f"Merged line detected: {line!r} contains {suspect!r} inside value"
            )


def _assert_each_on_own_line(content: str, *fragments: str) -> None:
    """Assert each fragment appears on a line by itself (not merged with another key)."""
    lines = content.splitlines()
    for frag in fragments:
        matching = [ln for ln in lines if frag in ln]
        assert matching, f"{frag!r} not found in output"
        for ln in matching:
            _assert_no_merged_lines(ln + "\n")


def _assert_if_present(content: str, *fragments: str) -> None:
    """For version-gated fields: if the fragment appears, it must be on its own line."""
    lines = content.splitlines()
    for frag in fragments:
        matching = [ln for ln in lines if frag in ln]
        for ln in matching:
            _assert_no_merged_lines(ln + "\n")


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------


class TestContainerRendering:
    def _make(self, **kwargs) -> Container:
        defaults = {
            "id": _uuid(1),
            "compartment_id": _COMP,
            "qm_name": "web",
            "image": "nginx:latest",
            "created_at": _NOW,
            "updated_at": _NOW,
        }
        defaults.update(kwargs)
        return Container(**defaults)

    def test_minimal(self):
        c = self._make()
        result = _render_container(_COMP, c, [])
        assert "[Unit]" in result
        assert "[Container]" in result
        assert "[Service]" in result
        assert "[Install]" in result
        assert "Image=nginx:latest" in result
        _assert_no_merged_lines(result)

    def test_network_with_aliases(self):
        """Network line with aliases must not merge with the next key."""
        c = self._make(
            network="appnet",
            network_aliases=["api", "backend"],
            ports=["8080:80/tcp"],
        )
        result = _render_container(_COMP, c, [])
        _assert_each_on_own_line(
            result,
            "Network=appnet.network",
            "PublishPort=8080:80/tcp",
        )
        # Verify all aliases are on the Network line
        net_line = [ln for ln in result.splitlines() if ln.startswith("Network=")][0]
        assert ":alias=web" in net_line
        assert ":alias=api" in net_line
        assert ":alias=backend" in net_line

    def test_network_single_alias(self):
        """Even with only the implicit alias (qm_name), newline must be preserved."""
        c = self._make(network="mynet", ports=["9090:90"])
        result = _render_container(_COMP, c, [])
        _assert_each_on_own_line(result, "Network=mynet.network", "PublishPort=9090:90")

    def test_host_network(self):
        c = self._make(network="host")
        result = _render_container(_COMP, c, [])
        assert "Network=host" in result

    def test_no_network_defaults_to_host(self):
        c = self._make(network="")
        result = _render_container(_COMP, c, [])
        assert "Network=host" in result

    def test_special_networks(self):
        for net in ("none", "slirp4netns", "pasta"):
            c = self._make(network=net)
            result = _render_container(_COMP, c, [])
            assert f"Network={net}" in result

    def test_environment_variables(self):
        c = self._make(environment={"DB_HOST": "localhost", "DB_PORT": "5432"})
        result = _render_container(_COMP, c, [])
        _assert_each_on_own_line(
            result,
            "Environment=DB_HOST=localhost",
            "Environment=DB_PORT=5432",
        )

    def test_multiple_ports(self):
        c = self._make(ports=["8080:80/tcp", "8443:443/tcp"])
        result = _render_container(_COMP, c, [])
        _assert_each_on_own_line(
            result,
            "PublishPort=8080:80/tcp",
            "PublishPort=8443:443/tcp",
        )

    def test_volumes_with_options_no_merge(self):
        """Multiple volumes with options must each get their own line."""
        vol1 = Volume(
            id=_uuid(10),
            compartment_id=_COMP,
            qm_name="data",
            qm_use_quadlet=True,
            created_at=_NOW,
        )
        vol2 = Volume(
            id=_uuid(11),
            compartment_id=_COMP,
            qm_name="logs",
            qm_use_quadlet=True,
            created_at=_NOW,
        )
        c = self._make(
            volumes=[
                VolumeMount(volume_id=str(_uuid(10)), container_path="/data", options="Z"),
                VolumeMount(volume_id=str(_uuid(11)), container_path="/logs", options="ro"),
            ]
        )
        result = _render_container(_COMP, c, [vol1, vol2])
        _assert_each_on_own_line(result, "Volume=mycomp-data.volume", "Volume=mycomp-logs.volume")
        # Each Volume line must be separate
        vol_lines = [ln for ln in result.splitlines() if ln.startswith("Volume=")]
        assert len(vol_lines) == 2
        assert ":Z" in vol_lines[0] or ":Z" in vol_lines[1]
        assert ":ro" in vol_lines[0] or ":ro" in vol_lines[1]

    def test_volumes_without_options(self):
        vol = Volume(
            id=_uuid(10),
            compartment_id=_COMP,
            qm_name="data",
            qm_use_quadlet=True,
            created_at=_NOW,
        )
        c = self._make(
            volumes=[
                VolumeMount(volume_id=str(_uuid(10)), container_path="/data", options=""),
            ]
        )
        result = _render_container(_COMP, c, [vol])
        vol_lines = [ln for ln in result.splitlines() if ln.startswith("Volume=")]
        assert len(vol_lines) == 1
        assert vol_lines[0] == "Volume=mycomp-data.volume:/data"

    def test_host_dir_volumes(self):
        vol = Volume(
            id=_uuid(10),
            compartment_id=_COMP,
            qm_name="uploads",
            qm_use_quadlet=False,
            created_at=_NOW,
        )
        c = self._make(
            volumes=[
                VolumeMount(volume_id=str(_uuid(10)), container_path="/uploads", options="Z"),
            ]
        )
        result = _render_container(_COMP, c, [vol])
        vol_lines = [ln for ln in result.splitlines() if ln.startswith("Volume=")]
        assert len(vol_lines) == 1
        assert "/uploads" in vol_lines[0]
        assert vol_lines[0].endswith(":Z")

    def test_bind_mounts_with_options(self):
        c = self._make(
            bind_mounts=[
                BindMount(host_path="/srv/www", container_path="/var/www", options="ro,Z"),
                BindMount(host_path="/srv/logs", container_path="/var/log/app", options="rw"),
            ]
        )
        result = _render_container(_COMP, c, [])
        vol_lines = [ln for ln in result.splitlines() if ln.startswith("Volume=")]
        assert len(vol_lines) == 2
        _assert_no_merged_lines(result)

    def test_bind_mounts_without_options(self):
        c = self._make(
            bind_mounts=[
                BindMount(host_path="/srv/data", container_path="/data", options=""),
            ]
        )
        result = _render_container(_COMP, c, [])
        vol_lines = [ln for ln in result.splitlines() if ln.startswith("Volume=")]
        assert len(vol_lines) == 1
        assert vol_lines[0] == "Volume=/srv/data:/data"

    def test_uid_gid_maps(self):
        c = self._make(uid_map=["1000"], gid_map=["1000"])
        result = _render_container(_COMP, c, [])
        uid_lines = [ln for ln in result.splitlines() if ln.startswith("UIDMap=")]
        gid_lines = [ln for ln in result.splitlines() if ln.startswith("GIDMap=")]
        assert len(uid_lines) >= 1
        assert len(gid_lines) >= 1
        _assert_no_merged_lines(result)

    def test_labels(self):
        c = self._make(labels={"app": "web", "env": "prod"})
        result = _render_container(_COMP, c, [])
        _assert_each_on_own_line(result, "Label=app=web", "Label=env=prod")

    def test_depends_on_generates_after_requires(self):
        c = self._make(depends_on=["db", "redis"])
        result = _render_container(_COMP, c, [])
        assert "After=" in result
        assert "Requires=" in result

    def test_pod_assignment(self):
        c = self._make(pod="mypod")
        result = _render_container(_COMP, c, [])
        # Pod dependency is always added to [Unit]
        assert "After=mypod-pod.service" in result
        assert "Requires=mypod-pod.service" in result
        # Pod= key is version-gated; if present, Network= should be absent
        if "Pod=mypod.pod" in result:
            net_lines = [ln for ln in result.splitlines() if ln.startswith("Network=")]
            assert len(net_lines) == 0

    def test_boolean_flags(self):
        c = self._make(
            no_new_privileges=True,
            read_only=True,
            init=True,
        )
        result = _render_container(_COMP, c, [])
        assert "NoNewPrivileges=true" in result
        assert "ReadOnly=true" in result
        assert "PodmanArgs=--init" in result

    def test_security_options(self):
        c = self._make(
            drop_caps=["ALL"],
            add_caps=["CAP_NET_BIND_SERVICE"],
        )
        result = _render_container(_COMP, c, [])
        assert "DropCapability=ALL" in result
        assert "AddCapability=CAP_NET_BIND_SERVICE" in result

    def test_health_check(self):
        c = self._make(
            health_cmd="curl -f http://localhost/ || exit 1",
            health_interval="30s",
            health_timeout="10s",
            health_retries="3",
        )
        result = _render_container(_COMP, c, [])
        assert "HealthCmd=" in result
        assert "HealthInterval=30s" in result
        assert "HealthTimeout=10s" in result
        assert "HealthRetries=3" in result

    def test_restart_policy(self):
        c = self._make(restart_policy="on-failure")
        result = _render_container(_COMP, c, [])
        assert "Restart=on-failure" in result

    def test_resource_limits(self):
        c = self._make(memory_limit="512M", cpu_quota="50")
        result = _render_container(_COMP, c, [])
        assert "MemoryLimit=512M" in result
        assert "CPUQuota=50" in result

    def test_all_fields_populated(self):
        """Render a container with many fields to verify no merging occurs."""
        vol = Volume(
            id=_uuid(20),
            compartment_id=_COMP,
            qm_name="data",
            qm_use_quadlet=True,
            created_at=_NOW,
        )
        c = self._make(
            network="appnet",
            network_aliases=["api"],
            ports=["8080:80/tcp", "8443:443/tcp"],
            environment={"APP_ENV": "production"},
            labels={"app": "myapp"},
            volumes=[
                VolumeMount(volume_id=str(_uuid(20)), container_path="/data", options="Z"),
            ],
            bind_mounts=[
                BindMount(
                    host_path="/srv/certs",
                    container_path="/certs",
                    options="ro",
                ),
            ],
            uid_map=["1000"],
            drop_caps=["ALL"],
            add_caps=["CAP_NET_BIND_SERVICE"],
            no_new_privileges=True,
            read_only=True,
            health_cmd="curl -f http://localhost/",
            health_interval="30s",
            restart_policy="always",
        )
        result = _render_container(_COMP, c, [vol])
        _assert_no_merged_lines(result)
        # Verify key lines are all separate
        _assert_each_on_own_line(
            result,
            "Network=appnet.network",
            "PublishPort=8080:80/tcp",
            "PublishPort=8443:443/tcp",
            "Environment=APP_ENV=production",
            "Label=app=myapp",
            "DropCapability=ALL",
            "AddCapability=CAP_NET_BIND_SERVICE",
            "NoNewPrivileges=true",
            "ReadOnly=true",
            "HealthCmd=",
            "HealthInterval=30s",
            "Restart=always",
        )
        vol_lines = [ln for ln in result.splitlines() if ln.startswith("Volume=")]
        assert len(vol_lines) == 2  # one quadlet volume + one bind mount


# ---------------------------------------------------------------------------
# Pod
# ---------------------------------------------------------------------------


class TestPodRendering:
    def _make(self, **kwargs) -> Pod:
        defaults = {
            "id": _uuid(100),
            "compartment_id": _COMP,
            "qm_name": SafeResourceName.trusted("mypod", "test"),
            "created_at": _NOW,
            "network": SafeStr.trusted("", "test"),
            "publish_ports": [],
        }
        defaults.update(kwargs)
        return Pod(**defaults)

    def test_minimal(self):
        pod = self._make()
        result = _render_pod(_COMP, pod)
        assert "[Unit]" in result
        assert "[Pod]" in result
        assert "[Service]" in result
        assert "[Install]" in result
        assert "PodName=mycomp-mypod" in result
        _assert_no_merged_lines(result)

    def test_with_ports(self):
        pod = self._make(
            publish_ports=[
                SafePortMapping.trusted("8080:80", "test"),
                SafePortMapping.trusted("8443:443", "test"),
            ]
        )
        result = _render_pod(_COMP, pod)
        _assert_each_on_own_line(
            result,
            "PublishPort=8080:80",
            "PublishPort=8443:443",
        )

    def test_network_defaults_to_service_id(self):
        pod = self._make()
        result = _render_pod(_COMP, pod)
        assert "Network=mycomp.network" in result

    def test_custom_network(self):
        pod = self._make(network=SafeStr.trusted("custom-net", "test"))
        result = _render_pod(_COMP, pod)
        assert "Network=custom-net.network" in result

    def test_pod_name_override(self):
        pod = self._make(pod_name_override="my-custom-pod")
        result = _render_pod(_COMP, pod)
        # PodName override is version-gated; verify it uses one or the other
        assert "PodName=my-custom-pod" in result or "PodName=mycomp-mypod" in result

    def test_with_dns_and_hosts(self):
        v = _v(PodCreate)
        pod = self._make(
            dns=["8.8.8.8", "1.1.1.1"],
            add_host=["myhost:10.0.0.1"],
        )
        result = _render_pod(_COMP, pod)
        if v.get("dns", True):
            _assert_each_on_own_line(result, "DNS=8.8.8.8", "DNS=1.1.1.1")
        if v.get("add_host", True):
            assert "AddHost=myhost:10.0.0.1" in result
        _assert_no_merged_lines(result)

    def test_with_labels(self):
        v = _v(PodCreate)
        pod = self._make(labels={"app": "myapp", "tier": "frontend"})
        result = _render_pod(_COMP, pod)
        if v.get("labels", True):
            _assert_each_on_own_line(result, "Label=app=myapp", "Label=tier=frontend")
        _assert_no_merged_lines(result)


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------


class TestNetworkRendering:
    def _make(self, **kwargs) -> Network:
        defaults = {
            "id": _uuid(200),
            "compartment_id": _COMP,
            "qm_name": "appnet",
            "created_at": _NOW,
        }
        defaults.update(kwargs)
        return Network(**defaults)

    def test_minimal(self):
        net = self._make()
        result = _render_network(_COMP, net)
        assert "[Network]" in result
        assert "NetworkName=appnet" in result
        _assert_no_merged_lines(result)

    def test_custom_network_name(self):
        net = self._make(network_name="custom-name")
        result = _render_network(_COMP, net)
        assert "NetworkName=custom-name" in result

    def test_full_config(self):
        net = self._make(
            driver="bridge",
            subnet="10.89.0.0/24",
            gateway="10.89.0.1",
            ipv6=True,
            internal=False,
            dns_enabled=True,
            ip_range="10.89.0.128/25",
            label={"env": "prod", "team": "infra"},
            options="mtu=1500",
        )
        result = _render_network(_COMP, net)
        _assert_each_on_own_line(
            result,
            "Driver=bridge",
            "Subnet=10.89.0.0/24",
            "Gateway=10.89.0.1",
            "IPv6=true",
            "IPRange=10.89.0.128/25",
            "Options=mtu=1500",
        )
        assert "Label=env=prod" in result
        assert "Label=team=infra" in result
        _assert_no_merged_lines(result)

    def test_dns_enabled_without_custom_server(self):
        net = self._make(dns_enabled=True)
        result = _render_network(_COMP, net)
        assert "DNS=true" in result

    def test_internal_network(self):
        net = self._make(internal=True)
        result = _render_network(_COMP, net)
        assert "Internal=true" in result

    def test_empty_optionals_omitted(self):
        net = self._make()
        result = _render_network(_COMP, net)
        assert "Driver=" not in result
        assert "Subnet=" not in result
        assert "Gateway=" not in result
        assert "IPRange=" not in result
        assert "Options=" not in result


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------


class TestVolumeRendering:
    def _make(self, **kwargs) -> Volume:
        defaults = {
            "id": _uuid(300),
            "compartment_id": _COMP,
            "qm_name": "data",
            "created_at": _NOW,
        }
        defaults.update(kwargs)
        return Volume(**defaults)

    def test_minimal(self):
        vol = self._make()
        result = _render_volume_unit(_COMP, vol)
        assert "[Volume]" in result
        assert "VolumeName=mycomp-data" in result
        _assert_no_merged_lines(result)

    def test_custom_volume_name(self):
        vol = self._make(volume_name="custom-vol")
        result = _render_volume_unit(_COMP, vol)
        assert "VolumeName=custom-vol" in result

    def test_with_driver_and_options(self):
        vol = self._make(driver="local", options="type=tmpfs,o=size=100m")
        result = _render_volume_unit(_COMP, vol)
        assert "Driver=local" in result
        assert "Options=type=tmpfs,o=size=100m" in result

    def test_with_labels(self):
        vol = self._make(label={"backup": "daily"})
        result = _render_volume_unit(_COMP, vol)
        assert "Label=backup=daily" in result

    def test_copy_false(self):
        vol = self._make(copy=False)
        result = _render_volume_unit(_COMP, vol)
        assert "Copy=false" in result

    def test_copy_true_omitted(self):
        vol = self._make(copy=True)
        result = _render_volume_unit(_COMP, vol)
        assert "Copy=" not in result

    def test_uid_gid(self):
        vol = self._make(uid="1000", gid="1000")
        result = _render_volume_unit(_COMP, vol)
        assert "UID=1000" in result
        assert "GID=1000" in result


# ---------------------------------------------------------------------------
# Image unit
# ---------------------------------------------------------------------------


class TestImageUnitRendering:
    def _make(self, **kwargs) -> Image:
        defaults = {
            "id": _uuid(400),
            "compartment_id": _COMP,
            "qm_name": "web",
            "image": "docker.io/library/nginx:latest",
            "created_at": _NOW,
        }
        defaults.update(kwargs)
        return Image(**defaults)

    def test_minimal(self):
        img = self._make()
        result = _render_image_unit(_COMP, img)
        assert "[Image]" in result
        assert "Image=docker.io/library/nginx:latest" in result
        _assert_no_merged_lines(result)

    def test_with_auth_and_creds(self):
        img = self._make(
            auth_file="/run/containers/auth.json",
            creds="user:pass",
        )
        result = _render_image_unit(_COMP, img)
        assert "AuthFile=/run/containers/auth.json" in result
        assert "Creds=user:pass" in result

    def test_tls_verify_false(self):
        img = self._make(tls_verify=False)
        result = _render_image_unit(_COMP, img)
        assert "TLSVerify=false" in result

    def test_tls_verify_true_omitted(self):
        img = self._make(tls_verify=True)
        result = _render_image_unit(_COMP, img)
        assert "TLSVerify=" not in result

    def test_all_tags(self):
        img = self._make(all_tags=True)
        result = _render_image_unit(_COMP, img)
        assert "AllTags=true" in result

    def test_arch_and_os(self):
        img = self._make(arch="amd64", os="linux")
        result = _render_image_unit(_COMP, img)
        assert "Arch=amd64" in result
        assert "OS=linux" in result


# ---------------------------------------------------------------------------
# Build unit
# ---------------------------------------------------------------------------


class TestBuildRendering:
    def _make(self, **kwargs) -> Build:
        defaults = {
            "id": _uuid(500),
            "compartment_id": _COMP,
            "qm_name": "web-build",
            "image_tag": "localhost/myapp:latest",
            "build_context": "/home/qm-mycomp/.config/containers/systemd/build-web",
            "created_at": _NOW,
            "updated_at": _NOW,
        }
        defaults.update(kwargs)
        return Build(**defaults)

    def test_minimal(self):
        bu = self._make()
        result = _render_build(_COMP, bu)
        assert "[Build]" in result
        assert "ImageTag=localhost/myapp:latest" in result
        assert "SetWorkingDirectory=" in result
        _assert_no_merged_lines(result)

    def test_with_build_args(self):
        v = _v(BuildCreate)
        bu = self._make(build_args={"NODE_ENV": "production", "VERSION": "1.0"})
        result = _render_build(_COMP, bu)
        if v.get("build_args", True):
            _assert_each_on_own_line(
                result,
                "BuildArg=NODE_ENV=production",
                "BuildArg=VERSION=1.0",
            )
        _assert_no_merged_lines(result)

    def test_with_labels(self):
        v = _v(BuildCreate)
        bu = self._make(label={"maintainer": "team"})
        result = _render_build(_COMP, bu)
        if v.get("label", True):
            assert "Label=maintainer=team" in result
        _assert_no_merged_lines(result)

    def test_with_build_file(self):
        bu = self._make(build_file="Dockerfile.prod")
        result = _render_build(_COMP, bu)
        # build_file is always available (base field)
        assert "File=Dockerfile.prod" in result

    def test_with_network(self):
        v = _v(BuildCreate)
        bu = self._make(network="host")
        result = _render_build(_COMP, bu)
        if v.get("network", True):
            assert "Network=host" in result
        _assert_no_merged_lines(result)

    def test_tls_verify_false(self):
        v = _v(BuildCreate)
        bu = self._make(tls_verify=False)
        result = _render_build(_COMP, bu)
        if v.get("tls_verify", True):
            assert "TLSVerify=false" in result
        _assert_no_merged_lines(result)

    def test_with_secrets_and_volumes(self):
        v = _v(BuildCreate)
        bu = self._make(
            secret=["mysecret"],
            volume=["/data:/data:ro"],
        )
        result = _render_build(_COMP, bu)
        if v.get("secret", True):
            assert "Secret=mysecret" in result
        if v.get("volume", True):
            assert "Volume=/data:/data:ro" in result
        _assert_no_merged_lines(result)


# ---------------------------------------------------------------------------
# Timer
# ---------------------------------------------------------------------------


class TestTimerRendering:
    def _make(self, **kwargs) -> Timer:
        defaults = {
            "id": _uuid(600),
            "compartment_id": _COMP,
            "qm_container_id": _uuid(1),
            "qm_container_name": SafeResourceName.trusted("web", "test"),
            "qm_name": "backup",
            "on_calendar": "*-*-* 03:00:00",
            "created_at": _NOW,
        }
        defaults.update(kwargs)
        return Timer(**defaults)

    def test_minimal(self):
        timer = self._make()
        result = _render_timer(_COMP, timer, SafeResourceName.trusted("web", "test"))
        assert "[Unit]" in result
        assert "[Timer]" in result
        assert "[Install]" in result
        assert "OnCalendar=*-*-* 03:00:00" in result
        assert "Unit=web.service" in result
        _assert_no_merged_lines(result)

    def test_on_boot_sec(self):
        timer = self._make(on_calendar="", on_boot_sec="5min")
        result = _render_timer(_COMP, timer, SafeResourceName.trusted("web", "test"))
        assert "OnBootSec=5min" in result

    def test_persistent(self):
        timer = self._make(persistent=True)
        result = _render_timer(_COMP, timer, SafeResourceName.trusted("web", "test"))
        assert "Persistent=true" in result

    def test_random_delay(self):
        timer = self._make(random_delay_sec="1h")
        result = _render_timer(_COMP, timer, SafeResourceName.trusted("web", "test"))
        assert "RandomizedDelaySec=1h" in result

    def test_daily_schedule(self):
        timer = self._make(on_calendar="daily")
        result = _render_timer(_COMP, timer, SafeResourceName.trusted("web", "test"))
        assert "OnCalendar=daily" in result


# ---------------------------------------------------------------------------
# Kube
# ---------------------------------------------------------------------------


class TestKubeRendering:
    def _make(self, **kwargs) -> Kube:
        defaults = {
            "id": _uuid(700),
            "compartment_id": _COMP,
            "qm_name": "myapp",
            "qm_yaml_content": "apiVersion: v1\nkind: Pod\n",
            "created_at": _NOW,
        }
        defaults.update(kwargs)
        return Kube(**defaults)

    def test_minimal(self):
        kube = self._make()
        result = _render_kube(_COMP, kube)
        assert "[Unit]" in result
        assert "[Kube]" in result
        assert "[Service]" in result
        assert "[Install]" in result
        assert "Yaml=" in result
        _assert_no_merged_lines(result)

    def test_with_network_and_ports(self):
        kube = self._make(
            network="mynet",
            publish_ports=[
                SafePortMapping.trusted("8080:80", "test"),
            ],
        )
        result = _render_kube(_COMP, kube)
        assert "Network=mynet.network" in result
        assert "PublishPort=8080:80" in result
        _assert_no_merged_lines(result)

    def test_with_config_maps(self):
        kube = self._make(config_map=["/path/to/configmap.yaml"])
        result = _render_kube(_COMP, kube)
        assert "ConfigMap=/path/to/configmap.yaml" in result


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------


class TestArtifactRendering:
    def _make(self, **kwargs) -> Artifact:
        defaults = {
            "id": _uuid(800),
            "compartment_id": _COMP,
            "qm_name": "mydata",
            "artifact": "docker.io/library/data:latest",
            "created_at": _NOW,
        }
        defaults.update(kwargs)
        return Artifact(**defaults)

    def test_minimal(self):
        art = self._make()
        result = _render_artifact(_COMP, art)
        assert "[Unit]" in result
        assert "[Artifact]" in result
        assert "[Install]" in result
        assert "Artifact=docker.io/library/data:latest" in result
        _assert_no_merged_lines(result)

    def test_with_auth(self):
        v = _v(ArtifactCreate)
        art = self._make(
            auth_file="/run/containers/auth.json",
            creds="user:token",
        )
        result = _render_artifact(_COMP, art)
        if v.get("auth_file", True):
            assert "AuthFile=/run/containers/auth.json" in result
        if v.get("creds", True):
            assert "Creds=user:token" in result
        _assert_no_merged_lines(result)

    def test_tls_verify_false(self):
        v = _v(ArtifactCreate)
        art = self._make(tls_verify=False)
        result = _render_artifact(_COMP, art)
        if v.get("tls_verify", True):
            assert "TLSVerify=false" in result
        _assert_no_merged_lines(result)

    def test_tls_verify_true_omitted(self):
        v = _v(ArtifactCreate)
        art = self._make(tls_verify=True)
        result = _render_artifact(_COMP, art)
        if v.get("tls_verify", True):
            assert "TLSVerify=" not in result

    def test_quiet_mode(self):
        v = _v(ArtifactCreate)
        art = self._make(quiet=True)
        result = _render_artifact(_COMP, art)
        if v.get("quiet", True):
            assert "Quiet=true" in result
        _assert_no_merged_lines(result)

    def test_with_retry(self):
        v = _v(ArtifactCreate)
        art = self._make(retry="3", retry_delay="5s")
        result = _render_artifact(_COMP, art)
        if v.get("retry", True):
            assert "Retry=3" in result
        if v.get("retry_delay", True):
            assert "RetryDelay=5s" in result
        _assert_no_merged_lines(result)


# ---------------------------------------------------------------------------
# Cross-template: every template produces valid INI with no merged lines
# ---------------------------------------------------------------------------


class TestAllTemplatesNoMergedLines:
    """Render every template with populated optional fields and verify no lines merge."""

    def test_container_dense(self):
        vol = Volume(
            id=_uuid(90),
            compartment_id=_COMP,
            qm_name="vol1",
            qm_use_quadlet=True,
            created_at=_NOW,
        )
        c = Container(
            id=_uuid(91),
            compartment_id=_COMP,
            qm_name="app",
            image="myapp:1.0",
            network="net1",
            network_aliases=["svc", "app"],
            ports=["80:80/tcp", "443:443/tcp"],
            environment={"A": "1", "B": "2"},
            labels={"x": "y"},
            volumes=[
                VolumeMount(volume_id=str(_uuid(90)), container_path="/data", options="Z"),
            ],
            bind_mounts=[
                BindMount(host_path="/tmp/test", container_path="/tmp", options="rw"),
            ],
            uid_map=["1000", "2000"],
            gid_map=["1000"],
            drop_caps=["ALL"],
            add_caps=["CAP_NET_BIND_SERVICE", "CAP_SYS_PTRACE"],
            no_new_privileges=True,
            read_only=True,
            health_cmd="true",
            health_interval="10s",
            health_timeout="5s",
            health_retries="3",
            created_at=_NOW,
            updated_at=_NOW,
        )
        result = _render_container(_COMP, c, [vol])
        _assert_no_merged_lines(result)
        # Verify all keys are on separate lines
        lines = result.splitlines()
        for i, line in enumerate(lines):
            if "=" in line and not line.startswith("["):
                key = line.split("=", 1)[0]
                assert key.isalpha() or key.replace("-", "").isalpha() or key == "", (
                    f"Line {i + 1} has unexpected key format: {line!r}"
                )

    def test_pod_dense(self):
        pod = Pod(
            id=_uuid(92),
            compartment_id=_COMP,
            qm_name="fullpod",
            network="podnet",
            publish_ports=[
                SafePortMapping.trusted("80:80", "test"),
                SafePortMapping.trusted("443:443", "test"),
            ],
            dns=["8.8.8.8"],
            add_host=["db:10.0.0.5"],
            labels={"tier": "web"},
            hostname="mypod",
            created_at=_NOW,
        )
        result = _render_pod(_COMP, pod)
        _assert_no_merged_lines(result)

    def test_network_dense(self):
        net = Network(
            id=_uuid(93),
            compartment_id=_COMP,
            qm_name="fullnet",
            driver="bridge",
            subnet="10.89.0.0/24",
            gateway="10.89.0.1",
            ipv6=True,
            internal=True,
            dns_enabled=True,
            ip_range="10.89.0.128/25",
            label={"env": "test"},
            options="mtu=9000",
            created_at=_NOW,
        )
        result = _render_network(_COMP, net)
        _assert_no_merged_lines(result)

    def test_volume_dense(self):
        vol = Volume(
            id=_uuid(94),
            compartment_id=_COMP,
            qm_name="fullvol",
            driver="local",
            device="/dev/sdb1",
            options="type=ext4",
            copy=False,
            uid="1000",
            gid="1000",
            label={"backup": "yes"},
            created_at=_NOW,
        )
        result = _render_volume_unit(_COMP, vol)
        _assert_no_merged_lines(result)

    def test_image_dense(self):
        img = Image(
            id=_uuid(95),
            compartment_id=_COMP,
            qm_name="fullimg",
            image="docker.io/library/nginx:1.25",
            auth_file="/run/auth.json",
            all_tags=True,
            arch="amd64",
            os="linux",
            tls_verify=False,
            creds="user:pass",
            created_at=_NOW,
        )
        result = _render_image_unit(_COMP, img)
        _assert_no_merged_lines(result)

    def test_build_dense(self):
        bu = Build(
            id=_uuid(96),
            compartment_id=_COMP,
            qm_name="fullbuild",
            image_tag="localhost/app:dev",
            build_context="/home/qm-mycomp/.config/containers/systemd/build-web",
            build_file="Dockerfile.dev",
            build_args={"ENV": "dev", "VER": "2"},
            label={"ci": "true"},
            network="host",
            tls_verify=False,
            created_at=_NOW,
            updated_at=_NOW,
        )
        result = _render_build(_COMP, bu)
        _assert_no_merged_lines(result)

    def test_kube_dense(self):
        kube = Kube(
            id=_uuid(97),
            compartment_id=_COMP,
            qm_name="fullkube",
            qm_yaml_content="apiVersion: v1\nkind: Pod\n",
            network="kubenet",
            publish_ports=[SafePortMapping.trusted("8080:80", "test")],
            config_map=["/path/cm.yaml"],
            created_at=_NOW,
        )
        result = _render_kube(_COMP, kube)
        _assert_no_merged_lines(result)

    def test_artifact_dense(self):
        art = Artifact(
            id=_uuid(98),
            compartment_id=_COMP,
            qm_name="fullart",
            artifact="registry.example.com/data:v1",
            auth_file="/run/auth.json",
            creds="user:token",
            tls_verify=False,
            quiet=True,
            retry="5",
            retry_delay="10s",
            created_at=_NOW,
        )
        result = _render_artifact(_COMP, art)
        _assert_no_merged_lines(result)

    def test_timer_dense(self):
        timer = Timer(
            id=_uuid(99),
            compartment_id=_COMP,
            qm_container_id=_uuid(1),
            qm_container_name="web",
            qm_name="fulltimer",
            on_calendar="*-*-* 02:00:00",
            on_boot_sec="5min",
            random_delay_sec="30s",
            persistent=True,
            created_at=_NOW,
        )
        result = _render_timer(_COMP, timer, SafeResourceName.trusted("web", "test"))
        _assert_no_merged_lines(result)
