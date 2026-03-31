"""Shared pytest fixtures for the quadletman test suite.

Safety: tests must never run as root because they mock all system calls;
running as root would allow accidental system modifications if a mock is missed.
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from quadletman.db.orm import Base
from quadletman.services import systemd_manager

# ---------------------------------------------------------------------------
# Root guard — abort early if someone runs pytest as root
# ---------------------------------------------------------------------------
if os.getuid() == 0:
    pytest.exit(
        "Tests must not run as root — they mock all system calls. Run as a normal user.",
        returncode=1,
    )


# ---------------------------------------------------------------------------
# Clear systemd_manager TTL caches between tests so cached responses from one
# test do not bleed into the next.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_systemd_status_cache():
    systemd_manager._unit_status_cache.clear()
    systemd_manager._unit_text_cache.clear()
    yield
    systemd_manager._unit_status_cache.clear()
    systemd_manager._unit_text_cache.clear()


@pytest.fixture(autouse=True)
def mock_host_for_tests(request, mocker):
    """Provide fake admin credentials and direct-I/O read/write helpers for tests.

    With root mode removed, host.py always uses sudo escalation for mutations
    and ``sudo -u <owner>`` for reads.  In tests:
    - Admin credentials are faked so ``_escalate_cmd`` doesn't raise.
    - Read helpers fall back to direct OS calls so tests that create real files
      on disk can read them without a real sudo.
    - Mutating host.* calls use direct OS calls instead of sudo.

    Tests that test host.py internals directly can skip this fixture by
    adding ``@pytest.mark.no_host_mock`` to the test class or function.
    """
    if request.node.get_closest_marker("no_host_mock"):
        # Only provide fake credentials — let the test control host.* behavior
        mocker.patch(
            "quadletman.services.host.get_admin_credentials",
            return_value=("testadmin", "testpass"),
        )
        return
    import os
    import stat as stat_mod

    from quadletman.services import host

    mocker.patch(
        "quadletman.services.host.get_admin_credentials",
        return_value=("testadmin", "testpass"),
    )

    # Read helpers — use direct OS calls instead of sudo in tests
    def _read_text(path, owner="", **_kw):
        try:
            with open(path) as f:
                return f.read()
        except (FileNotFoundError, PermissionError):
            return None

    def _read_bytes(path, owner="", limit=8192, **_kw):
        try:
            with open(path, "rb") as f:
                return f.read(limit)
        except (FileNotFoundError, PermissionError):
            return None

    def _path_exists(path, owner="", **_kw):
        return os.path.exists(path)

    def _path_isdir(path, owner="", **_kw):
        return os.path.isdir(path)

    def _path_isfile(path, owner="", **_kw):
        return os.path.isfile(path)

    def _path_islink(path, owner="", **_kw):
        return os.path.islink(path)

    def _readlink(path, owner="", **_kw):
        try:
            return os.readlink(path)
        except (FileNotFoundError, OSError):
            return None

    def _listdir(path, owner="", **_kw):
        try:
            return os.listdir(path)
        except OSError:
            return []

    def _stat_entry(path, owner="", **_kw):
        try:
            st = os.stat(path)
            return {
                "is_dir": stat_mod.S_ISDIR(st.st_mode),
                "size": st.st_size,
                "mode": st.st_mode,
            }
        except OSError:
            return None

    mocker.patch.object(host, "read_text", side_effect=_read_text)
    mocker.patch.object(host, "read_bytes", side_effect=_read_bytes)
    mocker.patch.object(host, "path_exists", side_effect=_path_exists)
    mocker.patch.object(host, "path_isdir", side_effect=_path_isdir)
    mocker.patch.object(host, "path_isfile", side_effect=_path_isfile)
    mocker.patch.object(host, "path_islink", side_effect=_path_islink)
    mocker.patch.object(host, "readlink", side_effect=_readlink)
    mocker.patch.object(host, "listdir", side_effect=_listdir)
    mocker.patch.object(host, "stat_entry", side_effect=_stat_entry)

    # Mutating wrappers — use direct OS calls in tests instead of sudo
    def _makedirs(path, **kwargs):
        os.makedirs(path, **kwargs)

    def _write_text(path, content, uid, gid, mode=0o600):
        with open(path, "w") as f:
            f.write(content)

    def _write_bytes(path, data, uid, gid, mode=0o600):
        with open(path, "wb") as f:
            f.write(data)

    def _unlink(path):
        os.unlink(path)

    def _chmod(path, mode):
        os.chmod(path, mode)

    def _chown(path, uid, gid):
        pass  # no-op in tests (running as unprivileged user)

    def _rmtree(path, **kwargs):
        import shutil

        shutil.rmtree(path, **kwargs)

    def _rename(src, dst):
        os.rename(src, dst)

    def _symlink(src, dst):
        os.symlink(src, dst)

    def _append_text(path, content):
        with open(path, "a") as f:
            f.write(content)

    def _write_lines(path, lines):
        with open(path, "w") as f:
            f.writelines(lines)

    mocker.patch.object(host, "makedirs", side_effect=_makedirs)
    mocker.patch.object(host, "write_text", side_effect=_write_text)
    mocker.patch.object(host, "write_bytes", side_effect=_write_bytes)
    mocker.patch.object(host, "unlink", side_effect=_unlink)
    mocker.patch.object(host, "chmod", side_effect=_chmod)
    mocker.patch.object(host, "chown", side_effect=_chown)
    mocker.patch.object(host, "rmtree", side_effect=_rmtree)
    mocker.patch.object(host, "rename", side_effect=_rename)
    mocker.patch.object(host, "symlink", side_effect=_symlink)
    mocker.patch.object(host, "append_text", side_effect=_append_text)
    mocker.patch.object(host, "write_lines", side_effect=_write_lines)


# ---------------------------------------------------------------------------
# In-memory database fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def db():
    """Async in-memory SQLite session with all tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_fk(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# HTTP test client fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(db):
    """AsyncClient targeting the FastAPI app with auth bypassed and in-memory DB injected."""
    from quadletman.db.engine import get_db
    from quadletman.main import app
    from quadletman.routers.api import init_podman_globals
    from quadletman.routers.helpers.common import require_auth

    init_podman_globals()

    async def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    from quadletman.models.sanitized import SafeUsername

    app.dependency_overrides[require_auth] = lambda: SafeUsername.trusted(
        "testuser", "test fixture"
    )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
