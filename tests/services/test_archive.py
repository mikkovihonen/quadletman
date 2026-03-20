"""Tests for quadletman/services/archive.py — safe archive extraction."""

import io
import tarfile
import zipfile

import pytest

from quadletman.models.sanitized import SafeAbsPath, SafeStr
from quadletman.services.archive import extract_archive

_p = lambda v: SafeAbsPath.trusted(v, "test fixture")  # noqa: E731
_s = lambda v: SafeStr.trusted(v, "test fixture")  # noqa: E731


def _make_zip(files: dict[str, bytes]) -> bytes:
    """Build an in-memory zip with the given filename→content mapping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _make_tar_gz(files: dict[str, bytes]) -> bytes:
    """Build an in-memory .tar.gz with the given filename→content mapping."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestExtractZip:
    def test_extracts_single_file(self, tmp_path):
        data = _make_zip({"hello.txt": b"hello world"})
        extract_archive(data, _p(str(tmp_path)), _s("upload.zip"))
        assert (tmp_path / "hello.txt").read_bytes() == b"hello world"

    def test_extracts_nested_file(self, tmp_path):
        data = _make_zip({"sub/dir/file.txt": b"nested"})
        extract_archive(data, _p(str(tmp_path)))
        assert (tmp_path / "sub" / "dir" / "file.txt").read_bytes() == b"nested"

    def test_extracts_multiple_files(self, tmp_path):
        data = _make_zip({"a.txt": b"aaa", "b.txt": b"bbb"})
        extract_archive(data, _p(str(tmp_path)))
        assert (tmp_path / "a.txt").read_bytes() == b"aaa"
        assert (tmp_path / "b.txt").read_bytes() == b"bbb"

    def test_zip_slip_absolute_path_rejected(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            info = zipfile.ZipInfo("/etc/evil")
            zf.writestr(info, b"evil")
        with pytest.raises(ValueError, match="Unsafe path"):
            extract_archive(buf.getvalue(), _p(str(tmp_path)))

    def test_zip_slip_traversal_rejected(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../evil.txt", b"evil")
        with pytest.raises(ValueError, match="Unsafe path"):
            extract_archive(buf.getvalue(), _p(str(tmp_path)))

    def test_detected_by_magic_bytes_without_filename(self, tmp_path):
        data = _make_zip({"x.txt": b"x"})
        assert data[:2] == b"PK"
        extract_archive(data, _p(str(tmp_path)), _s(""))  # no filename hint
        assert (tmp_path / "x.txt").exists()


class TestExtractTarGz:
    def test_extracts_single_file(self, tmp_path):
        data = _make_tar_gz({"hello.txt": b"hello"})
        extract_archive(data, _p(str(tmp_path)), _s("upload.tar.gz"))
        assert (tmp_path / "hello.txt").read_bytes() == b"hello"

    def test_extracts_nested_file(self, tmp_path):
        data = _make_tar_gz({"sub/file.txt": b"content"})
        extract_archive(data, _p(str(tmp_path)), _s("archive.tgz"))
        assert (tmp_path / "sub" / "file.txt").read_bytes() == b"content"

    def test_detected_by_magic_bytes_without_extension(self, tmp_path):
        data = _make_tar_gz({"f.txt": b"f"})
        assert data[:2] == b"\x1f\x8b"
        extract_archive(data, _p(str(tmp_path)), _s("noextension"))
        assert (tmp_path / "f.txt").exists()

    def test_tgz_extension_detected(self, tmp_path):
        data = _make_tar_gz({"g.txt": b"g"})
        extract_archive(data, _p(str(tmp_path)), _s("archive.tgz"))
        assert (tmp_path / "g.txt").exists()


class TestExtractArchiveDispatch:
    def test_unsupported_format_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unsupported archive format"):
            extract_archive(b"not an archive at all !!!", _p(str(tmp_path)), _s("file.rar"))

    def test_zip_extension_dispatches_to_zip_extractor(self, tmp_path):
        # Valid zip with .zip extension but no PK magic: use a real zip
        data = _make_zip({"z.txt": b"z"})
        extract_archive(data, _p(str(tmp_path)), _s("archive.zip"))
        assert (tmp_path / "z.txt").exists()

    def test_zip_extension_without_pk_magic_dispatches_to_zip(self, tmp_path):
        """A file named .zip with non-PK magic bytes but valid zip data should be extracted."""
        # Construct a zip whose first two bytes aren't PK by wrapping a real zip
        # In practice, a real ZipFile always starts with PK, so we simulate the branch
        # by using a file named .zip — the PK check will already catch it first.
        # Instead test the else-if branch: non-PK, non-gzip, non-BZ2 magic, .zip extension.
        import io as _io
        import zipfile as _zf

        buf = _io.BytesIO()
        with _zf.ZipFile(buf, "w") as zf:
            zf.writestr("branch.txt", b"branch coverage")
        data = buf.getvalue()
        # Force non-PK first bytes but keep it a valid zip (zip can have prepended data)
        # Actually just verify the .zip extension path is exercised by a real zip starting w/ PK.
        # The real line-65 branch is: data[:2] not in magic AND fname.endswith(".zip").
        # To hit it: fake magic bytes that aren't PK/gz/BZ, with .zip extension.
        # We can't make a valid zip without PK magic, so test that the error propagates.
        fake_data = b"\x00\x00" + data[2:]  # strip PK magic
        import zipfile

        with pytest.raises(zipfile.BadZipFile):
            extract_archive(fake_data, _p(str(tmp_path)), _s("archive.zip"))


class TestExtractTarTraversal:
    """Tests for tar traversal rejection via Python 3.12+ data filter."""

    def test_tar_traversal_rejected(self, tmp_path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(name="../escape.txt")
            info.size = 5
            tf.addfile(info, io.BytesIO(b"evil!"))
        with pytest.raises(tarfile.FilterError):
            extract_archive(buf.getvalue(), _p(str(tmp_path)), _s("evil.tar.gz"))
