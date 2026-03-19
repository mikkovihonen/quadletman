"""Alembic environment — async SQLite via aiosqlite."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# ---------------------------------------------------------------------------
# Alembic config object — gives access to .ini values
# ---------------------------------------------------------------------------

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Import ORM metadata so autogenerate can diff against it
# ---------------------------------------------------------------------------

from quadletman.db.orm import Base  # noqa: E402

target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_url() -> str:
    """Return the DB URL: prefer the ini option when it looks like a real URL,
    then fall back to application settings."""
    url = config.get_main_option("sqlalchemy.url") or ""
    # Ignore the alembic.ini placeholder value
    if url and not url.startswith("driver://"):
        return url
    from quadletman.config.settings import settings

    return f"sqlite+aiosqlite:///{settings.db_path}"


# ---------------------------------------------------------------------------
# Offline mode (generate SQL without connecting)
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # required for SQLite ALTER TABLE emulation
        compare_type=False,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode (connect to the database)
# ---------------------------------------------------------------------------


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # required for SQLite ALTER TABLE emulation
        compare_type=False,  # SQLite stores Boolean as INTEGER, JSON as TEXT — ignore type noise
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    # If a sync connection was injected (e.g. from engine.init_db()), use it directly.
    connectable = config.attributes.get("connection", None)

    if connectable is not None:
        # Called via run_sync from an async context — connectable is a sync Connection.
        do_run_migrations(connectable)
        return

    url = _get_url()
    async_engine = create_async_engine(url)
    async with async_engine.connect() as conn:
        await conn.run_sync(do_run_migrations)
    await async_engine.dispose()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if context.is_offline_mode():
    run_migrations_offline()
elif config.attributes.get("connection") is not None:
    # Called programmatically via run_sync — connection already injected; no event loop needed.
    do_run_migrations(config.attributes["connection"])
else:
    asyncio.run(run_migrations_online())
