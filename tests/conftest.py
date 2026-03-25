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
from quadletman.services import host, systemd_manager

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
def simulate_root_mode():
    """Force host.py to use the root code path during tests.

    Tests mock os.chown/os.chmod/etc. and assume the root code path.
    Without this, the non-root code path (which calls subprocess via sudo)
    would be taken and tests would fail with AdminSessionRequired.
    """
    old = host._is_root
    host._is_root = True
    yield
    host._is_root = old


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
    from quadletman.auth import require_auth
    from quadletman.db.engine import get_db
    from quadletman.main import app
    from quadletman.routers.api import init_podman_globals

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
