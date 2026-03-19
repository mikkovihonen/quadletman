"""SQLAlchemy async engine, session factory, and FastAPI dependency."""

import logging
import os

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config.settings import settings

# Tell @sanitized.enforce that AsyncSession has been reviewed — it is not a
# Pydantic/data model that needs string-field validation; it is a SQLAlchemy
# session object and contains no user-supplied str data.
AsyncSession._sanitized_enforce_model = True  # type: ignore[attr-defined]

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
    os.makedirs(db_dir, exist_ok=True)

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

    alembic_cfg.attributes["connection"] = sync_conn
    command.upgrade(alembic_cfg, "head")
