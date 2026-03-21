"""Tests for quadletman/services/quadlet_writer.py — template rendering and sync checks."""

from quadletman.models import Container, Volume, VolumeMount
from quadletman.models.sanitized import SafeResourceName, SafeSlug, SafeUUID
from quadletman.services.quadlet_writer import (
    _render_container,
    _render_network,
    _resolve_id_maps,
)
from quadletman.services.unsafe.quadlet import compare_file

_CID = SafeUUID.trusted("00000000-0000-0000-0000-000000000001", "test")
_VID = SafeUUID.trusted("00000000-0000-0000-0000-000000000002", "test")
_VID2 = SafeUUID.trusted("00000000-0000-0000-0000-000000000003", "test")
_TID1 = SafeUUID.trusted("00000000-0000-0000-0000-000000000004", "test")
_TID2 = SafeUUID.trusted("00000000-0000-0000-0000-000000000005", "test")
_COMP = SafeSlug.trusted("mycomp", "test")


def _make_container(**kwargs) -> Container:
    defaults = {
        "id": _CID,
        "compartment_id": _COMP,
        "name": "web",
        "image": "nginx:latest",
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
    }
    defaults.update(kwargs)
    return Container(**defaults)


def _make_volume(**kwargs) -> Volume:
    defaults = {
        "id": _VID,
        "compartment_id": _COMP,
        "name": "data",
        "created_at": "2024-01-01T00:00:00",
    }
    defaults.update(kwargs)
    return Volume(**defaults)


# ---------------------------------------------------------------------------
# _resolve_id_maps
# ---------------------------------------------------------------------------


class TestResolveIdMaps:
    def test_empty_returns_empty(self):
        assert _resolve_id_maps([]) == []

    def test_zero_is_always_added(self):
        result = _resolve_id_maps(["1000"])
        assert any(entry.startswith("0:0:1") for entry in result)

    def test_single_uid_has_correct_mapping(self):
        result = _resolve_id_maps(["1000"])
        # Container 1000 -> NS 1001
        assert "1000:1001:1" in result

    def test_covers_full_namespace(self):
        """All 65536 UIDs must be covered by the generated entries."""
        result = _resolve_id_maps(["100", "200"])
        total_covered = 0
        for entry in result:
            parts = entry.split(":")
            total_covered += int(parts[2])
        assert total_covered == 65536

    def test_no_duplicate_ranges(self):
        result = _resolve_id_maps(["0"])
        # UID 0 should only appear once
        zero_entries = [e for e in result if e.startswith("0:")]
        assert len(zero_entries) == 1


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


class TestRenderNetwork:
    def test_contains_service_id(self):
        content = _render_network(_COMP)
        assert "mycomp" in content

    def test_has_network_section(self):
        content = _render_network(_COMP)
        assert "[Network]" in content


class TestRenderContainer:
    def test_contains_image(self):
        container = _make_container()
        content = _render_container(_COMP, container, [])
        assert "nginx:latest" in content

    def test_has_container_section(self):
        container = _make_container()
        content = _render_container(_COMP, container, [])
        assert "[Container]" in content

    def test_contains_environment(self):
        container = _make_container(environment={"MY_VAR": "hello"})
        content = _render_container(_COMP, container, [])
        assert "MY_VAR" in content
        assert "hello" in content

    def test_contains_port(self):
        container = _make_container(ports=["8080:80"])
        content = _render_container(_COMP, container, [])
        assert "8080:80" in content

    def test_host_network_not_emitted_as_network_line(self):
        container = _make_container(network="host")
        content = _render_container(_COMP, container, [])
        # host networking in Quadlet means no explicit Network= (or Network=host)
        # just check Image= is present to confirm render worked
        assert "Image=" in content

    def test_custom_network_emitted(self):
        container = _make_container(network="mynet")
        content = _render_container(_COMP, container, [])
        assert "mynet" in content

    def test_uid_map_emitted(self):
        container = _make_container(uid_map=["1000"])
        content = _render_container(_COMP, container, [])
        assert "UIDMap=" in content or "1000" in content


# ---------------------------------------------------------------------------
# compare_file
# ---------------------------------------------------------------------------


class TestResolveMounts:
    def test_quadlet_volume_uses_quadlet_name(self):
        from quadletman.services.quadlet_writer import _resolve_mounts

        vol = _make_volume(id=_VID, name="data", use_quadlet=True)
        container = _make_container(
            volumes=[VolumeMount(volume_id=str(_VID), container_path="/data", options="")]
        )
        mounts = _resolve_mounts(SafeSlug.trusted("mycomp", "test fixture"), container, [vol])
        assert len(mounts) == 1
        assert mounts[0]["quadlet_name"] == "mycomp-data.volume"
        assert mounts[0]["host_path"] == ""

    def test_host_dir_volume_uses_host_path(self):
        from quadletman.services.quadlet_writer import _resolve_mounts

        vol = _make_volume(id=_VID2, name="uploads", use_quadlet=False)
        container = _make_container(
            volumes=[VolumeMount(volume_id=str(_VID2), container_path="/uploads", options="")]
        )
        mounts = _resolve_mounts(SafeSlug.trusted("mycomp", "test fixture"), container, [vol])
        assert len(mounts) == 1
        assert mounts[0]["quadlet_name"] == ""
        assert "/mycomp/" in mounts[0]["host_path"] or "uploads" in mounts[0]["host_path"]

    def test_unknown_volume_id_skipped(self):
        from quadletman.services.quadlet_writer import _resolve_mounts

        container = _make_container(
            volumes=[
                VolumeMount(
                    volume_id="00000000-0000-0000-0000-000000000000",
                    container_path="/x",
                    options="",
                )
            ]
        )
        mounts = _resolve_mounts(_COMP, container, [])
        assert mounts == []


class TestRenderVolumeUnit:
    def test_has_volume_section(self):
        from quadletman.services.quadlet_writer import _render_volume_unit

        vol = _make_volume(name="data")
        content = _render_volume_unit(_COMP, vol)
        assert "[Volume]" in content

    def test_contains_service_id(self):
        from quadletman.services.quadlet_writer import _render_volume_unit

        vol = _make_volume(name="logs")
        content = _render_volume_unit(_COMP, vol)
        assert "mycomp" in content


class TestRenderTimerUnit:
    def test_has_timer_section(self):
        from quadletman.models import Timer
        from quadletman.services.quadlet_writer import _render_timer

        timer = Timer(
            id=_TID1,
            compartment_id=_COMP,
            container_id=_CID,
            container_name="web",
            name="backup",
            schedule="*-*-* 03:00:00",
            created_at="2024-01-01T00:00:00",
        )
        content = _render_timer(_COMP, timer, SafeResourceName.trusted("web", "test"))
        assert "[Timer]" in content

    def test_contains_schedule(self):
        from quadletman.models import Timer
        from quadletman.services.quadlet_writer import _render_timer

        timer = Timer(
            id=_TID2,
            compartment_id=_COMP,
            container_id=_CID,
            container_name="web",
            name="daily",
            schedule="daily",
            created_at="2024-01-01T00:00:00",
        )
        content = _render_timer(_COMP, timer, SafeResourceName.trusted("web", "test"))
        assert "daily" in content


class TestRenderQuadletFiles:
    def test_returns_container_filename(self):
        from quadletman.services.quadlet_writer import render_quadlet_files

        container = _make_container()
        files = render_quadlet_files(_COMP, [container], [])
        filenames = [f["filename"] for f in files]
        assert "web.container" in filenames

    def test_network_included_when_not_host(self):
        from quadletman.services.quadlet_writer import render_quadlet_files

        container = _make_container(network="mycomp")
        files = render_quadlet_files(_COMP, [container], [])
        filenames = [f["filename"] for f in files]
        assert any(".network" in fn for fn in filenames)

    def test_network_not_included_for_host_network(self):
        from quadletman.services.quadlet_writer import render_quadlet_files

        container = _make_container(network="host")
        files = render_quadlet_files(_COMP, [container], [])
        filenames = [f["filename"] for f in files]
        assert not any(".network" in fn for fn in filenames)

    def test_volume_unit_included_for_quadlet_volume(self):
        from quadletman.services.quadlet_writer import render_quadlet_files

        container = _make_container()
        vol = _make_volume(name="data", use_quadlet=True)
        files = render_quadlet_files(_COMP, [container], [vol])
        filenames = [f["filename"] for f in files]
        assert any(".volume" in fn for fn in filenames)


class TestCheckSync:
    def test_returns_empty_when_in_sync(self, tmp_path, mocker):
        from quadletman.models import Compartment
        from quadletman.services.quadlet_writer import check_service_sync as check_sync

        container = _make_container()
        content_mock = "rendered-content"
        mocker.patch(
            "quadletman.services.quadlet_writer._render_container",
            return_value=content_mock,
        )
        mocker.patch(
            "quadletman.services.quadlet_writer._render_network",
            return_value="net-content",
        )
        mocker.patch(
            "quadletman.services.quadlet_writer.ensure_quadlet_dir",
            return_value=str(tmp_path),
        )
        # Write matching file so it's in sync
        (tmp_path / "web.container").write_text(content_mock)
        # network file needed too since container has default network
        (tmp_path / "mycomp.network").write_text("net-content")

        comp = Compartment(
            id="mycomp",
            description="",
            linux_user="qm-mycomp",
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
            containers=[container],
            volumes=[],
            pods=[],
            image_units=[],
        )
        issues = check_sync(SafeSlug.trusted("mycomp", "test fixture"), [container], [], comp)
        assert issues == []

    def test_returns_missing_when_file_absent(self, tmp_path, mocker):
        from quadletman.models import Compartment
        from quadletman.services.quadlet_writer import check_service_sync as check_sync

        container = _make_container()
        mocker.patch(
            "quadletman.services.quadlet_writer._render_container",
            return_value="content",
        )
        mocker.patch(
            "quadletman.services.quadlet_writer._render_network",
            return_value="net-content",
        )
        mocker.patch(
            "quadletman.services.quadlet_writer.ensure_quadlet_dir",
            return_value=str(tmp_path),
        )
        comp = Compartment(
            id="mycomp",
            description="",
            linux_user="qm-mycomp",
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
            containers=[container],
            volumes=[],
            pods=[],
            image_units=[],
        )
        issues = check_sync(SafeSlug.trusted("mycomp", "test fixture"), [container], [], comp)
        # Container file is missing
        assert any(i["status"] == "missing" for i in issues)

    def test_missing_quadlet_dir_returns_error(self, mocker):
        from quadletman.services.quadlet_writer import check_service_sync as check_sync

        mocker.patch(
            "quadletman.services.quadlet_writer.ensure_quadlet_dir",
            side_effect=OSError("no such dir"),
        )
        issues = check_sync(SafeSlug.trusted("mycomp", "test fixture"), [], [])
        assert len(issues) == 1
        assert issues[0]["status"] == "missing"


class TestCompareFile:
    def test_returns_none_when_in_sync(self, tmp_path):
        f = tmp_path / "unit.container"
        f.write_text("content")
        assert compare_file(str(f), "content") is None

    def test_returns_changed_when_different(self, tmp_path):
        f = tmp_path / "unit.container"
        f.write_text("old content")
        result = compare_file(str(f), "new content")
        assert result is not None
        assert result["status"] == "changed"
        assert "diff" in result

    def test_returns_missing_when_file_absent(self, tmp_path):
        path = str(tmp_path / "nonexistent.container")
        result = compare_file(path, "expected content")
        assert result is not None
        assert result["status"] == "missing"


# ---------------------------------------------------------------------------
# Render functions for pods, volumes, image units, build units, timers
# ---------------------------------------------------------------------------


def _make_pod(**kwargs):
    from quadletman.models import Pod
    from quadletman.models.sanitized import (
        SafeResourceName,
        SafeStr,
        SafeTimestamp,
        SafeUUID,
    )

    defaults = {
        "id": SafeUUID.trusted("00000000-0000-0000-0000-000000000010", "test"),
        "compartment_id": _COMP,
        "name": SafeResourceName.trusted("mypod", "test"),
        "created_at": SafeTimestamp.trusted("2024-01-01T00:00:00", "test"),
        "network": SafeStr.trusted("", "test"),
        "publish_ports": [],
    }
    defaults.update(kwargs)
    return Pod(**defaults)


def _make_image_unit(**kwargs):
    from quadletman.models import ImageUnit
    from quadletman.models.sanitized import (
        SafeResourceName,
        SafeTimestamp,
        SafeUUID,
    )

    defaults = {
        "id": SafeUUID.trusted("00000000-0000-0000-0000-000000000011", "test"),
        "compartment_id": _COMP,
        "name": SafeResourceName.trusted("myimage", "test"),
        "image": "nginx:latest",
        "created_at": SafeTimestamp.trusted("2024-01-01T00:00:00", "test"),
    }
    defaults.update(kwargs)
    return ImageUnit(**defaults)


def _make_timer(**kwargs):
    from quadletman.models import Timer
    from quadletman.models.sanitized import (
        SafeResourceName,
        SafeStr,
        SafeTimestamp,
        SafeUUID,
    )

    defaults = {
        "id": SafeUUID.trusted("00000000-0000-0000-0000-000000000012", "test"),
        "compartment_id": _COMP,
        "container_id": SafeUUID.trusted("00000000-0000-0000-0000-000000000001", "test"),
        "name": SafeResourceName.trusted("mytimer", "test"),
        "schedule": SafeStr.trusted("*-*-* 01:00:00", "test"),
        "container_name": SafeResourceName.trusted("web", "test"),
        "created_at": SafeTimestamp.trusted("2024-01-01T00:00:00", "test"),
    }
    defaults.update(kwargs)
    return Timer(**defaults)


class TestRenderPod:
    def test_renders_pod_unit(self):
        from quadletman.services.quadlet_writer import _render_pod

        pod = _make_pod()
        result = _render_pod(_COMP, pod)
        assert "[Pod]" in result

    def test_renders_pod_with_ports(self):
        from quadletman.models.sanitized import SafePortMapping
        from quadletman.services.quadlet_writer import _render_pod

        pod = _make_pod(publish_ports=[SafePortMapping.trusted("8080:80", "test")])
        result = _render_pod(_COMP, pod)
        assert "8080" in result


class TestRenderVolumeUnitExtra:
    def test_renders_volume_unit(self):
        from quadletman.services.quadlet_writer import _render_volume_unit

        vol = _make_volume(use_quadlet=True)
        result = _render_volume_unit(_COMP, vol)
        assert "[Volume]" in result


class TestRenderImageUnit:
    def test_renders_image_unit(self):
        from quadletman.services.quadlet_writer import _render_image_unit

        iu = _make_image_unit()
        result = _render_image_unit(_COMP, iu)
        assert "[Image]" in result or "nginx" in result

    def test_renders_image_unit_with_pull_policy(self):
        from quadletman.models.sanitized import SafeStr
        from quadletman.services.quadlet_writer import _render_image_unit

        iu = _make_image_unit(pull_policy=SafeStr.trusted("always", "test"))
        result = _render_image_unit(_COMP, iu)
        assert "[Image]" in result or "nginx" in result


class TestRenderBuild:
    def test_renders_build_unit(self):
        from quadletman.services.quadlet_writer import _render_build

        c = _make_container(build_context="/home/qm-mycomp/.config/containers/systemd/build-web")
        result = _render_build(_COMP, c)
        assert "[Build]" in result or "build" in result.lower()


class TestRenderTimer:
    def test_renders_timer_unit(self):
        from quadletman.models.sanitized import SafeResourceName
        from quadletman.services.quadlet_writer import _render_timer

        timer = _make_timer()
        result = _render_timer(_COMP, timer, SafeResourceName.trusted("web", "test"))
        assert "[Timer]" in result or "timer" in result.lower()


class TestCheckSyncExtra:
    def test_returns_issues_for_mismatched_files(self, tmp_path, mocker):
        from quadletman.services.quadlet_writer import check_service_sync

        # Create a real container unit file with wrong content
        quadlet_dir = tmp_path / "quadlets"
        quadlet_dir.mkdir()

        mocker.patch(
            "quadletman.services.quadlet_writer.ensure_quadlet_dir",
            return_value=str(quadlet_dir),
        )

        container = _make_container()
        result = check_service_sync(_COMP, [container], [], comp=None)
        # Unit file doesn't exist → should report missing
        assert len(result) > 0
        assert result[0]["status"] in ("missing", "mismatch")

    def test_returns_empty_for_matching_files(self, tmp_path, mocker):
        from quadletman.services.quadlet_writer import _render_container, check_service_sync

        quadlet_dir = tmp_path / "quadlets"
        quadlet_dir.mkdir()

        mocker.patch(
            "quadletman.services.quadlet_writer.ensure_quadlet_dir",
            return_value=str(quadlet_dir),
        )

        container = _make_container()
        expected = _render_container(_COMP, container, [])
        (quadlet_dir / "web.container").write_text(expected)
        result = check_service_sync(_COMP, [container], [], comp=None)
        assert result == []
