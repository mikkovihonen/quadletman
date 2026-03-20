"""Tests for the database layer (SQLAlchemy + Alembic)."""

import contextlib

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from quadletman.db.engine import get_db
from quadletman.db.orm import Base


def _make_engine():
    return create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)


@pytest.mark.anyio
async def test_fresh_db_has_all_expected_tables():
    """Creating the schema from ORM metadata produces all expected tables."""
    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.connect() as conn:
        result = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    assert {
        "compartments",
        "containers",
        "volumes",
        "pods",
        "image_units",
        "system_events",
    } <= set(result)
    await engine.dispose()


@pytest.mark.anyio
async def test_schema_is_idempotent():
    """create_all called twice on the same engine must not raise."""
    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest.mark.anyio
async def test_compartments_has_network_columns():
    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.connect() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("compartments")}
        )
    assert {
        "net_driver",
        "net_subnet",
        "net_gateway",
        "net_ipv6",
        "net_internal",
        "net_dns_enabled",
    } <= columns
    await engine.dispose()


@pytest.mark.anyio
async def test_containers_has_all_columns():
    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.connect() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("containers")}
        )
    assert {
        "apparmor_profile",
        "bind_mounts",
        "health_cmd",
        "auto_update",
        "exec_stop",
        "pod_name",
        "log_driver",
        "working_dir",
        "privileged",
        "uid_map",
        "gid_map",
    } <= columns
    await engine.dispose()


@pytest.mark.anyio
async def test_get_db_yields_async_session(tmp_path, monkeypatch):
    """get_db() yields a working AsyncSession that can execute queries."""
    import quadletman.db.engine as engine_module

    db_file = str(tmp_path / "getdb.db")
    new_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", echo=False)
    async with new_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(engine_module, "engine", new_engine)
    monkeypatch.setattr(
        engine_module,
        "AsyncSessionLocal",
        async_sessionmaker(new_engine, expire_on_commit=False, class_=AsyncSession),
    )

    gen = get_db()
    session = await gen.__anext__()
    try:
        assert isinstance(session, AsyncSession)
        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1
    finally:
        with contextlib.suppress(Exception):
            await gen.aclose()
    await new_engine.dispose()
