"""Wrappers for all host-mutating operations with structured audit logging.

Every host mutation goes through one of the wrappers in this module so that all
changes to the underlying Linux system appear in a single, filterable log stream
under the ``quadletman.host`` logger name.

Usage
-----
Subprocess calls that modify the host::

    from . import host
    host.run(["useradd", "--system", username], check=True, capture_output=True, text=True)

Filesystem mutations::

    host.makedirs(path, mode=0o700, exist_ok=True)
    host.unlink(path)
    host.symlink("/dev/null", mask_path)
    host.chmod(path, 0o600)
    host.chown(path, uid, gid)
    host.rename(src, dst)
    host.rmtree(path, ignore_errors=True)
    host.write_text(path, content, uid, gid, mode=0o600)
    host.append_text(path, content)
    host.write_lines(path, lines)

Function-level annotation::

    @host.audit("USER_CREATE", lambda service_id, *a, **kw: service_id)
    def create_service_user(service_id: str) -> int:
        ...

    @host.audit("UNIT_STOP", lambda sid, unit, *a, **kw: f"{sid}/{unit}")
    def stop_unit(service_id: str, unit: str) -> None:
        ...

Read-only subprocess calls (``journalctl``, ``systemctl show``, ``podman info``,
``getsebool``, ``getenforce``, ``stat``) should continue to use
``subprocess.run()`` directly — they do not modify the host and should not
appear in the host audit log.

Privilege escalation
--------------------
When the process is running as root (``os.getuid() == 0``), all operations use
direct system calls (backward compatible).

When running as the ``quadletman`` user (non-root), mutating operations escalate
via the authenticated user's ``sudo`` credentials stored in the request-scoped
ContextVar (see ``auth.py``).  Filesystem wrappers fall back to subprocess
equivalents (``sudo chown``, ``sudo tee``, etc.).
"""

import asyncio
import contextlib
import functools
import inspect
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable

from quadletman.config.settings import settings
from quadletman.models import sanitized
from quadletman.models.sanitized import SafeAbsPath, SafeStr, log_safe
from quadletman.security.auth import get_admin_credentials

_log = logging.getLogger("quadletman.host")

_is_root: bool | None = None  # lazily computed; patchable in tests


def is_root() -> bool:
    """Return True when running as root.  Patchable in tests via ``host._is_root``."""
    global _is_root  # noqa: PLW0603
    if _is_root is None:
        _is_root = os.getuid() == 0
    return _is_root


class AdminSessionRequired(Exception):
    """Raised when a non-root operation requires admin credentials but none are available."""


# ---------------------------------------------------------------------------
# Internal: privilege escalation helpers
# ---------------------------------------------------------------------------


def _escalate_cmd(cmd: list[str]) -> tuple[list[str], dict]:
    """Wrap *cmd* with the authenticated user's sudo if not running as root.

    Returns (cmd, extra_kwargs) where extra_kwargs may contain ``input`` for
    piping the password to ``sudo -S``.
    """
    if is_root():
        return cmd, {}

    creds = get_admin_credentials()
    if not creds:
        raise AdminSessionRequired(
            "Admin operation requires an authenticated web session (not running as root)"
        )
    username, password = creds
    # sudo -u <user> sudo -S <original_cmd>
    # Outer: quadletman → authenticated user (NOPASSWD in sudoers)
    # Inner: authenticated user → root (password from stdin)
    escalated = ["sudo", "-u", username, "sudo", "-S", "--"] + list(cmd)
    return escalated, {"input": password + "\n", "text": True}


# ---------------------------------------------------------------------------
# subprocess wrapper — mutating commands only
# ---------------------------------------------------------------------------


@sanitized.enforce
def run_as_user(owner: SafeStr, cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command as a specific Linux user via sudo.

    In root mode, uses ``sudo -u <owner>`` directly.  In non-root mode,
    the quadletman process user's sudoers grants NOPASSWD access to run
    commands as qm-* users.

    This is for general-purpose commands (mkdir, ln, rm, cat, etc.) that
    need to run as the compartment user.  For systemd/podman commands that
    need ``XDG_RUNTIME_DIR`` and ``DBUS_SESSION_BUS_ADDRESS``, use
    ``systemd_manager._run()`` instead.
    """
    full_cmd = ["sudo", "-u", str(owner)] + cmd
    _log.info("CMD  %s", log_safe(" ".join(full_cmd)))
    kwargs.setdefault("timeout", settings.subprocess_timeout)
    return subprocess.run(full_cmd, cwd="/", capture_output=True, text=True, **kwargs)


def run(cmd: list[str], *, admin: bool = False, **kwargs) -> subprocess.CompletedProcess:
    """Run a mutating subprocess command and emit an audit log entry.

    Parameters
    ----------
    admin:
        If True and the process is not root, the command is escalated via the
        authenticated user's sudo.  Commands that already include their own
        ``sudo -u qm-*`` prefix (e.g. from ``systemd_manager._base_cmd()``)
        should set ``admin=False`` — the sudoers file grants the quadletman
        user NOPASSWD access to those.
    """
    if admin and not is_root():
        cmd, extra = _escalate_cmd(cmd)
        # Merge extra kwargs (input, text) without overwriting explicit caller kwargs
        for k, v in extra.items():
            kwargs.setdefault(k, v)
    _log.info("CMD  %s", log_safe(" ".join(str(a) for a in cmd)))
    kwargs.setdefault("timeout", settings.subprocess_timeout)
    return subprocess.run(cmd, **kwargs)


# ---------------------------------------------------------------------------
# os / shutil wrappers — mutating filesystem operations
# ---------------------------------------------------------------------------


@sanitized.enforce
def makedirs(path: SafeAbsPath, **kwargs) -> None:
    _log.info("MKDIR %s", log_safe(path))
    if is_root():
        os.makedirs(path, **kwargs)
    else:
        mode = kwargs.get("mode", 0o755)
        cmd = ["mkdir", "-p"]
        if mode:
            cmd += ["-m", f"{mode:04o}"]
        cmd.append(str(path))
        run(cmd, admin=True, check=True, capture_output=True)


@sanitized.enforce
def unlink(path: SafeAbsPath) -> None:
    _log.info("UNLINK %s", log_safe(path))
    if is_root():
        os.unlink(path)
    else:
        run(["rm", "-f", str(path)], admin=True, check=True, capture_output=True)


@sanitized.enforce
def symlink(src: SafeAbsPath, dst: SafeAbsPath) -> None:
    _log.info("SYMLINK %s -> %s", log_safe(dst), log_safe(src))
    if is_root():
        os.symlink(src, dst)
    else:
        run(["ln", "-sf", str(src), str(dst)], admin=True, check=True, capture_output=True)


@sanitized.enforce
def chmod(path: SafeAbsPath, mode: int) -> None:
    _log.info("CHMOD %04o %s", mode, log_safe(path))
    if is_root():
        os.chmod(path, mode)
    else:
        run(
            ["chmod", f"{mode:04o}", str(path)],
            admin=True,
            check=True,
            capture_output=True,
        )


@sanitized.enforce
def chown(path: SafeAbsPath, uid: int, gid: int) -> None:
    _log.info("CHOWN %d:%d %s", uid, gid, log_safe(path))
    if is_root():
        os.chown(path, uid, gid)
    else:
        run(
            ["chown", f"{uid}:{gid}", str(path)],
            admin=True,
            check=True,
            capture_output=True,
        )


@sanitized.enforce
def rename(src: SafeAbsPath, dst: SafeAbsPath) -> None:
    _log.info("RENAME %s -> %s", log_safe(src), log_safe(dst))
    if is_root():
        os.rename(src, dst)
    else:
        run(["mv", str(src), str(dst)], admin=True, check=True, capture_output=True)


@sanitized.enforce
def rmtree(path: SafeAbsPath, **kwargs) -> None:
    _log.info("RMTREE %s", log_safe(path))
    if is_root():
        shutil.rmtree(path, **kwargs)
    else:
        run(["rm", "-rf", str(path)], admin=True, check=True, capture_output=True)


@sanitized.enforce
def write_text(path: SafeAbsPath, content, uid: int, gid: int, mode: int = 0o600) -> None:
    """Write a text file then set ownership and permissions.

    Replaces the repeated ``open(..., "w") / os.chown / os.chmod`` triple found
    throughout the service layer.
    """
    _log.info("WRITE %s (uid=%d gid=%d mode=%04o)", log_safe(path), uid, gid, mode)
    if is_root():
        with open(path, "w") as f:
            f.write(content)
        os.chown(path, uid, gid)
        os.chmod(path, mode)
    else:
        # Write to a temp file (owned by quadletman), then use sudo install to
        # atomically move it to the target with correct ownership and permissions.
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".qm") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            run(
                [
                    "install",
                    "-o",
                    str(uid),
                    "-g",
                    str(gid),
                    "-m",
                    f"{mode:04o}",
                    tmp_path,
                    str(path),
                ],
                admin=True,
                check=True,
                capture_output=True,
            )
        finally:
            # Clean up temp file (may already be moved by install)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_path)


@sanitized.enforce
def write_bytes(path: SafeAbsPath, data: bytes, uid: int, gid: int, mode: int = 0o600) -> None:
    """Write binary data to a file then set ownership and permissions.

    Identical to :func:`write_text` but for binary content (file uploads).
    """
    _log.info("WRITE_BYTES %s (uid=%d gid=%d mode=%04o)", log_safe(path), uid, gid, mode)
    if is_root():
        with open(path, "wb") as f:
            f.write(data)
        os.chown(path, uid, gid)
        os.chmod(path, mode)
    else:
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".qm") as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            run(
                [
                    "install",
                    "-o",
                    str(uid),
                    "-g",
                    str(gid),
                    "-m",
                    f"{mode:04o}",
                    tmp_path,
                    str(path),
                ],
                admin=True,
                check=True,
                capture_output=True,
            )
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_path)


@sanitized.enforce
def append_text(path: SafeAbsPath, content) -> None:
    """Append text to a file."""
    _log.info("APPEND %s", log_safe(path))
    if is_root():
        with open(path, "a") as f:
            f.write(content)
    else:
        # Read current content, append, and write via the temp+cp pattern.
        # Cannot use tee with admin=True because sudo -S reads the password
        # from the same stdin that tee reads content from.
        try:
            with open(path) as f:
                existing = f.read()
        except FileNotFoundError:
            existing = ""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".qm") as tmp:
            tmp.write(existing + content)
            tmp_path = tmp.name
        try:
            run(["cp", tmp_path, str(path)], admin=True, check=True, capture_output=True)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_path)


@sanitized.enforce
def write_lines(path: SafeAbsPath, lines) -> None:
    """Overwrite a file with the given lines (no ownership change)."""
    _log.info("WRITE %s", log_safe(path))
    if is_root():
        with open(path, "w") as f:
            f.writelines(lines)
    else:
        # Write to a temp file, then copy over the target via sudo.
        # Cannot use tee with admin=True because sudo -S reads the password
        # from the same stdin that tee reads content from.
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".qm") as tmp:
            tmp.writelines(lines)
            tmp_path = tmp.name
        try:
            run(["cp", tmp_path, str(path)], admin=True, check=True, capture_output=True)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Read helpers — read files owned by qm-* users (non-mutating)
# ---------------------------------------------------------------------------
# In root mode these use direct file I/O.  In non-root mode the quadletman
# process user (qm-dev) cannot read qm-* home directories directly, so
# these helpers fall through to ``sudo -u <owner> cat`` via the sudoers rule.


@sanitized.enforce
def read_text(path: SafeAbsPath, owner: SafeStr = SafeStr.trusted("", "default")) -> str | None:
    """Read a text file, returning its contents or None if it doesn't exist.

    *owner* is the Linux username that owns the file (e.g. ``qm-test``).
    When running as root, *owner* is ignored and the file is read directly.
    When running as non-root, ``sudo -u <owner> cat`` is used.
    """
    if is_root():
        try:
            with open(path) as f:
                return f.read()
        except (FileNotFoundError, PermissionError):
            return None
    result = subprocess.run(
        ["sudo", "-u", owner, "cat", str(path)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        return None
    return result.stdout


@sanitized.enforce
def path_exists(path: SafeAbsPath, owner: SafeStr = SafeStr.trusted("", "default")) -> bool:
    """Check whether a file or directory exists.

    In non-root mode, uses ``sudo -u <owner> test -e`` since the quadletman
    process user may not have traverse permission on parent directories.
    """
    if is_root():
        return os.path.exists(path)
    result = subprocess.run(
        ["sudo", "-u", owner, "test", "-e", str(path)],
        capture_output=True,
        timeout=5,
    )
    return result.returncode == 0


@sanitized.enforce
def path_islink(path: SafeAbsPath, owner: SafeStr = SafeStr.trusted("", "default")) -> bool:
    """Check whether a path is a symbolic link."""
    if is_root():
        return os.path.islink(path)
    result = subprocess.run(
        ["sudo", "-u", owner, "test", "-L", str(path)],
        capture_output=True,
        timeout=5,
    )
    return result.returncode == 0


@sanitized.enforce
def readlink(path: SafeAbsPath, owner: SafeStr = SafeStr.trusted("", "default")) -> str | None:
    """Read the target of a symbolic link, or None if not a link."""
    if is_root():
        try:
            return os.readlink(path)
        except (FileNotFoundError, OSError):
            return None
    result = subprocess.run(
        ["sudo", "-u", owner, "readlink", str(path)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


@sanitized.enforce
def path_isdir(path: SafeAbsPath, owner: SafeStr = SafeStr.trusted("", "default")) -> bool:
    """Check whether a path is a directory.

    In non-root mode, uses ``sudo -u <owner> test -d``.
    """
    if is_root():
        return os.path.isdir(path)
    result = subprocess.run(
        ["sudo", "-u", owner, "test", "-d", str(path)],
        capture_output=True,
        timeout=5,
    )
    return result.returncode == 0


@sanitized.enforce
def path_isfile(path: SafeAbsPath, owner: SafeStr = SafeStr.trusted("", "default")) -> bool:
    """Check whether a path is a regular file.

    In non-root mode, uses ``sudo -u <owner> test -f``.
    """
    if is_root():
        return os.path.isfile(path)
    result = subprocess.run(
        ["sudo", "-u", owner, "test", "-f", str(path)],
        capture_output=True,
        timeout=5,
    )
    return result.returncode == 0


@sanitized.enforce
def listdir(path: SafeAbsPath, owner: SafeStr = SafeStr.trusted("", "default")) -> list[str]:
    """List directory contents.

    In non-root mode, uses ``sudo -u <owner> ls -1a`` (excluding ``.`` and ``..``).
    Returns an empty list if the directory does not exist or is unreadable.
    """
    if is_root():
        try:
            return os.listdir(path)
        except OSError:
            return []
    result = subprocess.run(
        ["sudo", "-u", owner, "ls", "-1a", str(path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return []
    return [n for n in result.stdout.splitlines() if n not in (".", "..")]


@sanitized.enforce
def stat_entry(path: SafeAbsPath, owner: SafeStr = SafeStr.trusted("", "default")) -> dict | None:
    """Return file type, size, and permission mode for a path.

    Returns ``{"is_dir": bool, "size": int, "mode": int}`` or ``None``
    if the path does not exist or cannot be stat'd.

    In non-root mode, uses ``sudo -u <owner> stat -c '%F %s %a'``.
    """
    if is_root():
        try:
            st = os.stat(path)
            import stat as stat_mod

            return {
                "is_dir": stat_mod.S_ISDIR(st.st_mode),
                "size": st.st_size,
                "mode": st.st_mode,
            }
        except OSError:
            return None
    result = subprocess.run(
        ["sudo", "-u", owner, "stat", "-c", "%F %s %a", str(path)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        return None
    parts = result.stdout.strip().rsplit(" ", 2)
    if len(parts) != 3:
        return None
    ftype, size_str, mode_str = parts
    try:
        return {
            "is_dir": ftype.startswith("directory"),
            "size": int(size_str),
            "mode": int(mode_str, 8),
        }
    except ValueError:
        return None


@sanitized.enforce
def read_bytes(
    path: SafeAbsPath, owner: SafeStr = SafeStr.trusted("", "default"), limit: int = 8192
) -> bytes | None:
    """Read up to *limit* bytes from a file.

    In non-root mode, uses ``sudo -u <owner> head -c <limit> <path>``.
    Returns ``None`` if the file does not exist or is unreadable.
    """
    if is_root():
        try:
            with open(path, "rb") as f:
                return f.read(limit)
        except (FileNotFoundError, PermissionError):
            return None
    result = subprocess.run(
        ["sudo", "-u", owner, "head", "-c", str(limit), str(path)],
        capture_output=True,
        timeout=5,
    )
    if result.returncode != 0:
        return None
    return result.stdout


# ---------------------------------------------------------------------------
# @audit decorator — function-level annotation
# ---------------------------------------------------------------------------


def audit(
    action: str,
    target: "str | Callable[..., str] | None" = None,
) -> Callable:
    """Decorator that emits a structured audit log entry when the function is called.

    Parameters
    ----------
    action:
        Short operation label (e.g. ``"USER_CREATE"``, ``"UNIT_STOP"``).
    target:
        * ``None`` — only *action* is logged.
        * A string — logged as-is for every call.
        * A callable(*args, **kwargs) → str — called with the decorated
          function's arguments to produce a per-call target string.

    Works transparently on both sync and async functions.
    """

    def decorator(fn: Callable) -> Callable:
        if not getattr(fn, "_sanitized_enforced", False):
            raise TypeError(
                f"@host.audit: {fn.__qualname__} must also be decorated with "
                f"@sanitized.enforce — add it as the innermost decorator (directly above 'def')"
            )
        _param_names = list(inspect.signature(fn).parameters.keys())

        def _log_provenance(args: tuple) -> None:
            if not _log.isEnabledFor(logging.DEBUG):
                return
            parts = []
            for i, arg in enumerate(args):
                prov = sanitized.provenance(arg)
                if prov is not None:
                    pname = _param_names[i] if i < len(_param_names) else f"arg{i}"
                    type_name, label = prov
                    parts.append(f"{pname}={type_name}({label})")
            if parts:
                _log.debug("PARAMS %-32s %s", action, " ".join(parts))

        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                t = target(*args, **kwargs) if callable(target) else (target or "")
                _log.info("CALL %-32s %s", action, log_safe(t))
                _log_provenance(args)
                return await fn(*args, **kwargs)

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            t = target(*args, **kwargs) if callable(target) else (target or "")
            _log.info("CALL %-32s %s", action, t)
            _log_provenance(args)
            return fn(*args, **kwargs)

        return sync_wrapper

    return decorator
