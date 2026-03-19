"""Tests for quadletman/services/metrics.py — resource metrics helpers."""

import json
from unittest.mock import MagicMock

import psutil

from quadletman.models.sanitized import SafeSlug
from quadletman.routers._helpers import _fmt_bytes
from quadletman.services.metrics import (
    _dir_size,
    _dir_size_excluding,
    get_connections,
    get_container_ips,
    get_disk_breakdown,
    get_metrics,
    get_processes,
)


def _sid(s: str) -> SafeSlug:
    return SafeSlug.trusted(s, "test")


class TestDirSize:
    def test_empty_dir_is_zero(self, tmp_path):
        assert _dir_size(str(tmp_path)) == 0

    def test_single_file(self, tmp_path):
        (tmp_path / "a.txt").write_bytes(b"hello")
        assert _dir_size(str(tmp_path)) == 5

    def test_multiple_files(self, tmp_path):
        (tmp_path / "a.txt").write_bytes(b"aa")
        (tmp_path / "b.txt").write_bytes(b"bbb")
        assert _dir_size(str(tmp_path)) == 5

    def test_nested_directories(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "x.txt").write_bytes(b"xxxxx")
        assert _dir_size(str(tmp_path)) == 5

    def test_nonexistent_path_returns_zero(self, tmp_path):
        assert _dir_size(str(tmp_path / "ghost")) == 0

    def test_symlinks_not_followed(self, tmp_path):
        real = tmp_path / "real.txt"
        real.write_bytes(b"data")
        link = tmp_path / "link.txt"
        link.symlink_to(real)
        # symlinked file should not be double-counted
        assert _dir_size(str(tmp_path)) == 4


class TestDirSizeExcluding:
    def test_excludes_subtree(self, tmp_path):
        keep = tmp_path / "keep.txt"
        keep.write_bytes(b"keepme")
        sub = tmp_path / "exclude_dir"
        sub.mkdir()
        (sub / "big.txt").write_bytes(b"x" * 1000)
        result = _dir_size_excluding(str(tmp_path), str(sub))
        assert result == 6

    def test_empty_exclusion_counts_all(self, tmp_path):
        (tmp_path / "f.txt").write_bytes(b"abc")
        # exclude a path that doesn't exist → counts everything
        result = _dir_size_excluding(str(tmp_path), str(tmp_path / "nonexistent"))
        assert result == 3

    def test_nonexistent_base_returns_zero(self, tmp_path):
        assert _dir_size_excluding(str(tmp_path / "ghost"), "/some/path") == 0


class TestFmtBytes:
    def test_bytes_under_1024(self):
        assert _fmt_bytes(512) == "512 B"

    def test_kilobytes(self):
        assert _fmt_bytes(2048) == "2.0 KB"

    def test_megabytes(self):
        assert _fmt_bytes(3 * 1024 * 1024) == "3.0 MB"

    def test_gigabytes(self):
        assert _fmt_bytes(2 * 1024**3) == "2.0 GB"

    def test_zero_bytes(self):
        assert _fmt_bytes(0) == "0 B"


class TestGetProcesses:
    def test_returns_list(self, mocker):
        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 123,
            "uids": MagicMock(real=9999),
            "name": "testproc",
            "cmdline": ["testproc", "--flag"],
            "cpu_percent": 1.5,
            "memory_info": MagicMock(rss=1024),
            "status": "running",
        }
        mocker.patch(
            "quadletman.services.metrics.psutil.process_iter",
            return_value=[mock_proc],
        )
        result = get_processes(9999)
        assert len(result) == 1
        assert result[0]["pid"] == 123

    def test_excludes_other_uid(self, mocker):
        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 456,
            "uids": MagicMock(real=1234),
            "name": "other",
            "cmdline": [],
            "cpu_percent": 0.0,
            "memory_info": None,
            "status": "sleeping",
        }
        mocker.patch(
            "quadletman.services.metrics.psutil.process_iter",
            return_value=[mock_proc],
        )
        result = get_processes(9999)
        assert result == []

    def test_handles_no_such_process(self, mocker):
        mock_proc = MagicMock()
        mock_proc.info = MagicMock(side_effect=psutil.NoSuchProcess(pid=999))

        class _RaisingProc:
            @property
            def info(self):
                raise psutil.NoSuchProcess(999)

        mocker.patch(
            "quadletman.services.metrics.psutil.process_iter",
            return_value=[_RaisingProc()],
        )
        # Should not raise
        result = get_processes(9999)
        assert result == []


class TestGetMetrics:
    def test_returns_dict_with_zeros_for_nonexistent_uid(self, mocker, tmp_path):
        mocker.patch(
            "quadletman.services.metrics.psutil.process_iter",
            return_value=[],
        )
        mocker.patch(
            "quadletman.services.metrics._VOLUMES_BASE",
            str(tmp_path),
        )
        result = get_metrics(_sid("mycomp"), 99999)
        assert result["cpu_percent"] == 0.0
        assert result["mem_bytes"] == 0
        assert result["proc_count"] == 0

    def test_aggregates_uid_processes(self, mocker, tmp_path):
        mock_proc = MagicMock()
        mock_proc.info = {
            "uids": MagicMock(real=8888),
            "cpu_percent": 5.0,
            "memory_info": MagicMock(rss=2048),
        }
        mocker.patch(
            "quadletman.services.metrics.psutil.process_iter",
            return_value=[mock_proc],
        )
        mocker.patch(
            "quadletman.services.metrics._VOLUMES_BASE",
            str(tmp_path),
        )
        result = get_metrics(_sid("mycomp"), 8888)
        assert result["cpu_percent"] >= 5.0
        assert result["mem_bytes"] == 2048
        assert result["proc_count"] == 1


class TestGetDiskBreakdown:
    def test_returns_empty_when_podman_fails(self, mocker, tmp_path):
        mocker.patch(
            "quadletman.services.metrics._podman_cmd",
            return_value=["echo"],
        )
        mocker.patch(
            "quadletman.services.metrics._VOLUMES_BASE",
            str(tmp_path),
        )
        mocker.patch(
            "quadletman.services.user_manager.get_home",
            return_value=str(tmp_path),
        )
        failed = MagicMock(returncode=1, stdout="")
        mocker.patch(
            "quadletman.services.metrics.subprocess.run",
            return_value=failed,
        )
        result = get_disk_breakdown(_sid("mycomp"))
        assert "images" in result
        assert isinstance(result["images"], list)

    def test_parses_image_json(self, mocker, tmp_path):
        images_json = json.dumps([{"Names": ["nginx:latest"], "Size": 50000}])
        mocker.patch(
            "quadletman.services.metrics._podman_cmd",
            return_value=["echo"],
        )
        mocker.patch(
            "quadletman.services.metrics._VOLUMES_BASE",
            str(tmp_path),
        )
        mocker.patch(
            "quadletman.services.user_manager.get_home",
            return_value=str(tmp_path),
        )
        ok_images = MagicMock(returncode=0, stdout=images_json)
        ok_ps = MagicMock(returncode=0, stdout="[]")
        mocker.patch(
            "quadletman.services.metrics.subprocess.run",
            side_effect=[ok_images, ok_ps],
        )
        result = get_disk_breakdown(_sid("mycomp"))
        assert len(result["images"]) == 1
        assert result["images"][0]["name"] == "nginx:latest"


class TestGetContainerIps:
    def test_returns_empty_when_podman_fails(self, mocker):
        mocker.patch(
            "quadletman.services.metrics._podman_cmd",
            return_value=["echo"],
        )
        failed = MagicMock(returncode=1, stdout="")
        mocker.patch("quadletman.services.metrics.subprocess.run", return_value=failed)
        result = get_container_ips(_sid("mycomp"))
        assert result == {}

    def test_returns_empty_when_no_containers(self, mocker):
        mocker.patch(
            "quadletman.services.metrics._podman_cmd",
            return_value=["echo"],
        )
        ok = MagicMock(returncode=0, stdout="[]")
        mocker.patch("quadletman.services.metrics.subprocess.run", return_value=ok)
        result = get_container_ips(_sid("mycomp"))
        assert result == {}

    def test_extracts_ip_from_inspect(self, mocker):
        ps_json = json.dumps([{"Names": ["web"], "Id": "abc"}])
        inspect_json = json.dumps(
            [
                {
                    "Name": "/web",
                    "NetworkSettings": {"Networks": {"bridge": {"IPAddress": "10.88.0.5"}}},
                }
            ]
        )
        mocker.patch(
            "quadletman.services.metrics._podman_cmd",
            return_value=["echo"],
        )
        ok_ps = MagicMock(returncode=0, stdout=ps_json)
        ok_inspect = MagicMock(returncode=0, stdout=inspect_json)
        mocker.patch(
            "quadletman.services.metrics.subprocess.run",
            side_effect=[ok_ps, ok_inspect],
        )
        result = get_container_ips(_sid("mycomp"))
        assert "10.88.0.5" in result
        assert result["10.88.0.5"] == "web"


class TestGetConnections:
    def test_returns_empty_when_no_container_ips(self, mocker):
        mocker.patch(
            "quadletman.services.metrics.get_container_ips",
            return_value={},
        )
        result = get_connections(_sid("mycomp"))
        assert result == []

    def test_parses_outbound_connection(self, mocker):
        mocker.patch(
            "quadletman.services.metrics.get_container_ips",
            return_value={"10.88.0.5": "web"},
        )
        conntrack_out = (
            "tcp  6 431999 ESTABLISHED src=10.88.0.5 dst=1.2.3.4 sport=54321 dport=443 ...\n"
        )
        ok = MagicMock(returncode=0, stdout=conntrack_out)
        mocker.patch("quadletman.services.metrics.subprocess.run", return_value=ok)
        result = get_connections(_sid("mycomp"))
        assert len(result) == 1
        assert result[0]["container_name"] == "web"
        assert result[0]["direction"] == "outbound"
        assert result[0]["dst_port"] == 443

    def test_handles_conntrack_not_found(self, mocker):
        mocker.patch(
            "quadletman.services.metrics.get_container_ips",
            return_value={"10.88.0.5": "web"},
        )
        mocker.patch(
            "quadletman.services.metrics.subprocess.run",
            side_effect=FileNotFoundError("conntrack not found"),
        )
        result = get_connections(_sid("mycomp"))
        assert result == []

    def test_parses_inbound_connection(self, mocker):
        mocker.patch(
            "quadletman.services.metrics.get_container_ips",
            return_value={"10.88.0.5": "web"},
        )
        conntrack_out = (
            "tcp  6 431999 ESTABLISHED src=8.8.8.8 dst=10.88.0.5 sport=12345 dport=80 ...\n"
        )
        ok = MagicMock(returncode=0, stdout=conntrack_out)
        mocker.patch("quadletman.services.metrics.subprocess.run", return_value=ok)
        result = get_connections(_sid("mycomp"))
        assert len(result) == 1
        assert result[0]["direction"] == "inbound"
