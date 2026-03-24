"""SQLAlchemy async engine, session factory, and FastAPI dependency."""

import logging
import os

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config.settings import settings

# Tell @sanitized.enforce that AsyncSession has been reviewed — it is not a
# Pydantic/data model that needs string-field validation; it is a SQLAlchemy
# session object and contains no user-supplied str data.
AsyncSession._sanitized_enforce_model_safety = True  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

_DB_URL = f"sqlite+aiosqlite:///{settings.db_path}"

engine = create_async_engine(
    _DB_URL,
    echo=False,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _connection_record):
    """Apply WAL mode and foreign-key enforcement on every new connection."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def get_db() -> AsyncSession:
    """Yield an AsyncSession; commit on success, rollback on exception."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Initialisation helper (called at app startup)
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """Create database directory and run Alembic migrations."""
    from alembic.config import Config as AlembicConfig

    db_dir = os.path.dirname(str(settings.db_path))
    os.makedirs(db_dir, mode=0o700, exist_ok=True)

    # Verify connectivity
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))

    # Run pending Alembic migrations (synchronously via run_sync)
    alembic_cfg = AlembicConfig()
    alembic_cfg.set_main_option("script_location", _alembic_dir())
    alembic_cfg.set_main_option("sqlalchemy.url", _DB_URL)

    # alembic command.upgrade is synchronous and uses a sync connection internally
    async with engine.begin() as conn:
        await conn.run_sync(_alembic_upgrade, alembic_cfg)

    logger.info("Database initialised at %s", settings.db_path)


def _alembic_dir() -> str:
    from pathlib import Path

    return str(Path(__file__).parent.parent / "alembic")


def _alembic_upgrade(sync_conn, alembic_cfg):
    from alembic import command
    from alembic.runtime.migration import MigrationContext

    alembic_cfg.attributes["connection"] = sync_conn

    # If the DB already has application tables but no alembic_version row (i.e. it was
    # created by the old numbered-SQL migration runner), stamp it as the baseline revision
    # so Alembic skips the CREATE TABLE statements it would otherwise re-run.
    mc = MigrationContext.configure(sync_conn)
    current_rev = mc.get_current_revision()
    if current_rev is None:
        from sqlalchemy import inspect as sa_inspect

        existing_tables = sa_inspect(sync_conn).get_table_names()
        if "compartments" in existing_tables:
            logger.info("Existing pre-Alembic database detected — stamping baseline revision.")
            command.stamp(alembic_cfg, "0001")

    command.upgrade(alembic_cfg, "head")
