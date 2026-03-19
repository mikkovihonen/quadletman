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
"""

import asyncio
import functools
import inspect
import logging
import os
import shutil
import subprocess
from collections.abc import Callable

from quadletman.models import sanitized
from quadletman.models.sanitized import SafeAbsPath

_log = logging.getLogger("quadletman.host")


# ---------------------------------------------------------------------------
# subprocess wrapper — mutating commands only
# ---------------------------------------------------------------------------


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a mutating subprocess command and emit an audit log entry."""
    _log.info("CMD  %s", " ".join(str(a) for a in cmd))
    return subprocess.run(cmd, **kwargs)


# ---------------------------------------------------------------------------
# os / shutil wrappers — mutating filesystem operations
# ---------------------------------------------------------------------------


@sanitized.enforce
def makedirs(path: SafeAbsPath, **kwargs) -> None:
    _log.info("MKDIR %s", path)
    os.makedirs(path, **kwargs)


@sanitized.enforce
def unlink(path: SafeAbsPath) -> None:
    _log.info("UNLINK %s", path)
    os.unlink(path)


@sanitized.enforce
def symlink(src: SafeAbsPath, dst: SafeAbsPath) -> None:
    _log.info("SYMLINK %s -> %s", dst, src)
    os.symlink(src, dst)


@sanitized.enforce
def chmod(path: SafeAbsPath, mode: int) -> None:
    _log.info("CHMOD %04o %s", mode, path)
    os.chmod(path, mode)


@sanitized.enforce
def chown(path: SafeAbsPath, uid: int, gid: int) -> None:
    _log.info("CHOWN %d:%d %s", uid, gid, path)
    os.chown(path, uid, gid)


@sanitized.enforce
def rename(src: SafeAbsPath, dst: SafeAbsPath) -> None:
    _log.info("RENAME %s -> %s", src, dst)
    os.rename(src, dst)


@sanitized.enforce
def rmtree(path: SafeAbsPath, **kwargs) -> None:
    _log.info("RMTREE %s", path)
    shutil.rmtree(path, **kwargs)


@sanitized.enforce
def write_text(path: SafeAbsPath, content, uid: int, gid: int, mode: int = 0o600) -> None:
    """Write a text file then set ownership and permissions.

    Replaces the repeated ``open(..., "w") / os.chown / os.chmod`` triple found
    throughout the service layer.
    """
    _log.info("WRITE %s (uid=%d gid=%d mode=%04o)", path, uid, gid, mode)
    with open(path, "w") as f:
        f.write(content)
    os.chown(path, uid, gid)
    os.chmod(path, mode)


@sanitized.enforce
def append_text(path: SafeAbsPath, content) -> None:
    """Append text to a file."""
    _log.info("APPEND %s", path)
    with open(path, "a") as f:
        f.write(content)


@sanitized.enforce
def write_lines(path: SafeAbsPath, lines) -> None:
    """Overwrite a file with the given lines (no ownership change)."""
    _log.info("WRITE %s", path)
    with open(path, "w") as f:
        f.writelines(lines)


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
                _log.info("CALL %-32s %s", action, t)
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
