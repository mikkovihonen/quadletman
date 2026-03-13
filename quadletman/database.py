import logging
import os
import sqlite3
from pathlib import Path

import aiosqlite

from .config import settings

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
    finally:
        await db.close()


async def init_db() -> None:
    os.makedirs(os.path.dirname(settings.db_path), exist_ok=True)
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
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
            try:
                # executescript issues an implicit COMMIT before running
                await db.executescript(migration_file.read_text())
            except sqlite3.OperationalError as exc:
                if "duplicate column name" in str(exc):
                    logger.info(
                        "Migration %s already applied (column exists), skipping",
                        migration_file.name,
                    )
                else:
                    raise
            await db.execute(
                "INSERT INTO schema_migrations (name) VALUES (?)", (migration_file.name,)
            )
            await db.commit()

    logger.info("Database initialized at %s", settings.db_path)
