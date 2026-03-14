import logging
import os
import re
from pathlib import Path

import aiosqlite

from .config import settings

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Matches a simple ALTER TABLE ... ADD COLUMN statement (no triggers, no BEGIN/END).
_ADD_COLUMN_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)\b",
    re.IGNORECASE,
)


async def _column_exists(db: aiosqlite.Connection, table: str, column: str) -> bool:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        rows = await cur.fetchall()
    # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
    return any(row[1] == column for row in rows)


def _sql_body(segment: str) -> str:
    """Return the SQL body of a semicolon-delimited segment, stripping leading comment lines.

    A segment like '-- comment\\nALTER TABLE ...' returns 'ALTER TABLE ...'.
    A segment that is only comments or whitespace returns ''.
    """
    for line in segment.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            # First non-empty, non-comment line found — return from here onward.
            idx = segment.index(line)
            return segment[idx:].strip()
    return ""


async def _run_migration(db: aiosqlite.Connection, sql: str, name: str) -> None:
    """Execute a migration file.

    Migrations that consist entirely of ADD COLUMN statements are executed
    one-by-one with a pre-flight column-existence check so that re-running
    them on a DB that already has the columns is safe.

    All other migrations (which may contain triggers with BEGIN…END blocks
    and embedded semicolons) are executed via executescript, which handles
    compound statements correctly.
    """
    # Build a list of (sql_body, full_segment) pairs, skipping blank/comment-only segments.
    segments = [(_sql_body(s), s) for s in sql.split(";")]
    segments = [(body, raw) for body, raw in segments if body]

    # Only use per-statement path when EVERY statement is a simple ADD COLUMN.
    # This avoids breaking multi-statement migrations that contain triggers or
    # CREATE TABLE blocks that happen to be preceded by a comment.
    all_add_column = bool(segments) and all(_ADD_COLUMN_RE.match(body) for body, _ in segments)

    if all_add_column:
        for body, _ in segments:
            m = _ADD_COLUMN_RE.match(body)
            table, column = m.group(1), m.group(2)
            if await _column_exists(db, table, column):
                logger.info(
                    "Migration %s: column %s.%s already exists, skipping", name, table, column
                )
                continue
            await db.execute(body)
    else:
        # executescript issues an implicit COMMIT — safe for DDL-only migrations.
        await db.executescript(sql)


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
            await _run_migration(db, migration_file.read_text(), migration_file.name)
            await db.execute(
                "INSERT INTO schema_migrations (name) VALUES (?)", (migration_file.name,)
            )
            await db.commit()

    logger.info("Database initialized at %s", settings.db_path)
