"""Shared pytest fixtures for the quadletman test suite.

Safety: tests must never run as root because they mock all system calls;
running as root would allow accidental system modifications if a mock is missed.
"""

import os

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from quadletman.database import init_db

# ---------------------------------------------------------------------------
# Root guard — abort early if someone runs pytest as root
# ---------------------------------------------------------------------------
if os.getuid() == 0:
    pytest.exit(
        "Tests must not run as root — they mock all system calls. Run as a normal user.",
        returncode=1,
    )


# ---------------------------------------------------------------------------
# In-memory database fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def db():
    """Async in-memory SQLite connection with all migrations applied."""
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        await init_db(conn)
        yield conn


# ---------------------------------------------------------------------------
# HTTP test client fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(db):
    """AsyncClient targeting the FastAPI app with auth bypassed and in-memory DB injected."""
    from quadletman.auth import require_auth
    from quadletman.database import get_db
    from quadletman.main import app

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: "testuser"

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
