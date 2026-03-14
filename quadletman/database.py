import logging
import os
from pathlib import Path

import aiosqlite

from .config import settings

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def _run_migrations(db: aiosqlite.Connection) -> None:
    """Apply any pending migrations to *db*. Caller owns the connection and PRAGMAs."""
    await db.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )
    await db.commit()

    for migration_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        async with db.execute(
            "SELECT 1 FROM schema_migrations WHERE name = ?", (migration_file.name,)
        ) as cur:
            if await cur.fetchone() is not None:
                continue
        logger.info("Applying migration: %s", migration_file.name)
        await db.executescript(migration_file.read_text())
        await db.execute("INSERT INTO schema_migrations (name) VALUES (?)", (migration_file.name,))
        await db.commit()


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
    finally:
        await db.close()


async def init_db(db: aiosqlite.Connection | None = None) -> None:
    """Initialise the database, applying any pending migrations.

    If *db* is provided (e.g. an in-memory connection in tests) that connection is used
    directly and the caller is responsible for its lifecycle and PRAGMA settings.
    If *db* is None the function opens its own connection to settings.db_path.
    """
    if db is not None:
        await _run_migrations(db)
        return

    os.makedirs(os.path.dirname(settings.db_path), exist_ok=True)
    async with aiosqlite.connect(settings.db_path) as conn:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await _run_migrations(conn)

    logger.info("Database initialized at %s", settings.db_path)
