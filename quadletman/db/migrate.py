"""Alembic migration runner — separated from engine.py to keep the engine
module free from alembic imports (engine.py is imported by nearly every service)."""

import logging
import os
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from ..config.settings import settings
from .engine import engine

logger = logging.getLogger(__name__)

_DB_URL = f"sqlite+aiosqlite:///{settings.db_path}"


def _alembic_dir() -> str:
    return str(Path(__file__).parent.parent / "alembic")


def _alembic_upgrade(sync_conn, alembic_cfg):
    alembic_cfg.attributes["connection"] = sync_conn

    # If the DB already has application tables but no alembic_version row (i.e. it was
    # created by the old numbered-SQL migration runner), stamp it as the baseline revision
    # so Alembic skips the CREATE TABLE statements it would otherwise re-run.
    mc = MigrationContext.configure(sync_conn)
    current_rev = mc.get_current_revision()
    if current_rev is None:
        existing_tables = sa_inspect(sync_conn).get_table_names()
        if "compartments" in existing_tables:
            logger.info("Existing pre-Alembic database detected — stamping baseline revision.")
            command.stamp(alembic_cfg, "0001")

    command.upgrade(alembic_cfg, "head")


async def init_db() -> None:
    """Create database directory and run Alembic migrations."""
    db_dir = os.path.dirname(str(settings.db_path))
    os.makedirs(db_dir, mode=0o700, exist_ok=True)

    # Verify connectivity
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))

    # Run pending Alembic migrations (synchronously via run_sync)
    alembic_cfg = AlembicConfig()
    alembic_cfg.set_main_option("script_location", _alembic_dir())
    alembic_cfg.set_main_option("sqlalchemy.url", _DB_URL)

    async with engine.begin() as conn:
        await conn.run_sync(_alembic_upgrade, alembic_cfg)

    logger.info("Database initialised at %s", settings.db_path)
