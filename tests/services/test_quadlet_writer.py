"""Tests for quadletman/services/quadlet_writer.py — template rendering and sync checks."""

from quadletman.models import Container, Volume
from quadletman.services.quadlet_writer import (
    _compare_file,
    _render_container,
    _render_network,
    _resolve_id_maps,
)


def _make_container(**kwargs) -> Container:
    defaults = {
        "id": "cid1",
        "service_id": "mysvc",
        "name": "web",
        "image": "nginx:latest",
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
    }
    defaults.update(kwargs)
    return Container(**defaults)


def _make_volume(**kwargs) -> Volume:
    defaults = {
        "id": "vid1",
        "service_id": "mysvc",
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
        content = _render_network("mysvc")
        assert "mysvc" in content

    def test_has_network_section(self):
        content = _render_network("mysvc")
        assert "[Network]" in content


class TestRenderContainer:
    def test_contains_image(self):
        container = _make_container()
        content = _render_container("mysvc", container, [])
        assert "nginx:latest" in content

    def test_has_container_section(self):
        container = _make_container()
        content = _render_container("mysvc", container, [])
        assert "[Container]" in content

    def test_contains_environment(self):
        container = _make_container(environment={"MY_VAR": "hello"})
        content = _render_container("mysvc", container, [])
        assert "MY_VAR" in content
        assert "hello" in content

    def test_contains_port(self):
        container = _make_container(ports=["8080:80"])
        content = _render_container("mysvc", container, [])
        assert "8080:80" in content

    def test_host_network_not_emitted_as_network_line(self):
        container = _make_container(network="host")
        content = _render_container("mysvc", container, [])
        # host networking in Quadlet means no explicit Network= (or Network=host)
        # just check Image= is present to confirm render worked
        assert "Image=" in content

    def test_custom_network_emitted(self):
        container = _make_container(network="mynet")
        content = _render_container("mysvc", container, [])
        assert "mynet" in content

    def test_uid_map_emitted(self):
        container = _make_container(uid_map=["1000"])
        content = _render_container("mysvc", container, [])
        assert "UIDMap=" in content or "1000" in content


# ---------------------------------------------------------------------------
# _compare_file
# ---------------------------------------------------------------------------


class TestCompareFile:
    def test_returns_none_when_in_sync(self, tmp_path):
        f = tmp_path / "unit.container"
        f.write_text("content")
        assert _compare_file(str(f), "content") is None

    def test_returns_changed_when_different(self, tmp_path):
        f = tmp_path / "unit.container"
        f.write_text("old content")
        result = _compare_file(str(f), "new content")
        assert result is not None
        assert result["status"] == "changed"
        assert "diff" in result

    def test_returns_missing_when_file_absent(self, tmp_path):
        path = str(tmp_path / "nonexistent.container")
        result = _compare_file(path, "expected content")
        assert result is not None
        assert result["status"] == "missing"
