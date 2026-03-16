"""Tests for quadletman/services/metrics.py — resource metrics helpers."""

from quadletman.routers._helpers import _fmt_bytes
from quadletman.services.metrics import _dir_size, _dir_size_excluding


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
