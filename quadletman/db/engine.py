"""SQLAlchemy async engine, session factory, and FastAPI dependency."""

import logging

from sqlalchemy import event
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
    cursor.execute(f"PRAGMA busy_timeout={settings.db_busy_timeout}")
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


# init_db() has been moved to db/migrate.py to keep engine.py free from
# alembic imports (engine.py is imported by nearly every service module).
