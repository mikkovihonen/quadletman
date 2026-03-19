"""Safe archive extraction helpers shared by routers that accept uploads."""

import io
import os
import tarfile
import zipfile

from ..models import sanitized
from ..models.sanitized import SafeAbsPath, SafeStr


@sanitized.enforce
def _extract_zip(data: bytes, dest: SafeAbsPath) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.infolist():
            # Zip-slip prevention: check BEFORE extracting this member.
            # Use realpath so that any symlinks already written by prior
            # members are followed — catching symlink-based traversal.
            member_path = os.path.realpath(os.path.join(dest, member.filename))
            if not member_path.startswith(dest + os.sep) and member_path != dest:
                raise ValueError(f"Unsafe path in archive: {member.filename}")
            zf.extract(member, dest)
            # Re-validate after extraction in case the member itself was a
            # symlink that now resolves outside dest.
            member_path = os.path.realpath(os.path.join(dest, member.filename))
            if not member_path.startswith(dest + os.sep) and member_path != dest:
                os.unlink(os.path.join(dest, member.filename))
                raise ValueError(f"Unsafe symlink in archive: {member.filename}")


@sanitized.enforce
def _extract_tar(data: bytes, dest: SafeAbsPath) -> None:
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        # Python 3.12+ provides a safe extraction filter that blocks
        # absolute paths, symlink traversal, and dangerous member types.
        if hasattr(tarfile, "data_filter"):
            tf.extractall(dest, filter="data")
        else:
            # Fallback: extract member-by-member, re-checking realpath
            # AFTER each member so symlinks created by prior members are
            # caught before subsequent members follow them.
            for member in tf.getmembers():
                member_path = os.path.realpath(os.path.join(dest, member.name))
                if not member_path.startswith(dest + os.sep) and member_path != dest:
                    raise ValueError(f"Unsafe path in archive: {member.name}")
                tf.extract(member, dest)
                # Re-check after write — catches symlinks that now point outside.
                member_path = os.path.realpath(os.path.join(dest, member.name))
                if not member_path.startswith(dest + os.sep) and member_path != dest:
                    extracted = os.path.join(dest, member.name)
                    if os.path.lexists(extracted):
                        os.unlink(extracted)
                    raise ValueError(f"Unsafe symlink in archive: {member.name}")


@sanitized.enforce
def extract_archive(
    data: bytes, dest: SafeAbsPath, filename: SafeStr = SafeStr.trusted("", "default")
) -> None:
    """Detect archive format and extract safely into dest.

    Raises ValueError for unsupported formats or unsafe paths (zip-slip /
    symlink traversal).  The caller is responsible for creating dest beforehand.
    """
    fname = filename.lower()
    if data[:2] == b"PK":
        _extract_zip(data, dest)
    elif data[:2] in (b"\x1f\x8b", b"BZ") or fname.endswith(
        (".tar.gz", ".tgz", ".tar.bz2", ".tar")
    ):
        _extract_tar(data, dest)
    elif fname.endswith(".zip"):
        _extract_zip(data, dest)
    else:
        raise ValueError("Unsupported archive format. Upload a .zip or .tar.gz file.")
