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

from quadletman.auth import get_admin_credentials
from quadletman.models import sanitized
from quadletman.models.sanitized import SafeAbsPath, log_safe

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
def append_text(path: SafeAbsPath, content) -> None:
    """Append text to a file."""
    _log.info("APPEND %s", log_safe(path))
    if is_root():
        with open(path, "a") as f:
            f.write(content)
    else:
        # Use tee -a via sudo to append to a file we don't own
        run(
            ["tee", "-a", str(path)],
            admin=True,
            input=content,
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
        )


@sanitized.enforce
def write_lines(path: SafeAbsPath, lines) -> None:
    """Overwrite a file with the given lines (no ownership change)."""
    _log.info("WRITE %s", log_safe(path))
    if is_root():
        with open(path, "w") as f:
            f.writelines(lines)
    else:
        content = "".join(lines)
        run(
            ["tee", str(path)],
            admin=True,
            input=content,
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
        )


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
