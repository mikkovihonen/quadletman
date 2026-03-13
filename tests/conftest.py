"""Shared pytest fixtures for the quadletman test suite.

Safety: tests must never run as root because they mock all system calls;
running as root would allow accidental system modifications if a mock is missed.
"""

import os

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

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


async def _apply_migrations(db: aiosqlite.Connection) -> None:
    """Apply all SQL migrations to an in-memory database."""
    from pathlib import Path

    migrations_dir = Path(__file__).parent.parent / "quadletman" / "migrations"

    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )
    await db.commit()

    for migration_file in sorted(migrations_dir.glob("*.sql")):
        import sqlite3

        try:
            await db.executescript(migration_file.read_text())
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc):
                raise
        await db.execute(
            "INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)",
            (migration_file.name,),
        )
        await db.commit()


@pytest.fixture
async def db():
    """Async in-memory SQLite connection with all migrations applied."""
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await _apply_migrations(conn)
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
