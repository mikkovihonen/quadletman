"""Tests for the database migration runner."""

import aiosqlite
import pytest

import quadletman.database as _db_module
from quadletman.database import get_db, init_db


@pytest.mark.anyio
async def test_fresh_db_has_all_migrations_applied():
    async with aiosqlite.connect(":memory:") as db:
        await init_db(db)
        async with db.execute("SELECT name FROM schema_migrations ORDER BY name") as cur:
            names = [row[0] for row in await cur.fetchall()]
    assert names == [
        "001_initial.sql",
        "002_secrets_timers_templates_notifications.sql",
        "003_devices_runtime_init_resources_aliases.sql",
        "004_process_monitor.sql",
        "005_connection_monitor.sql",
        "006_connection_monitor_toggle.sql",
        "007_process_monitor_toggle.sql",
        "008_connection_whitelist.sql",
        "009_connection_direction.sql",
    ]


@pytest.mark.anyio
async def test_init_db_is_idempotent():
    """Running init_db twice on the same connection must not raise."""
    async with aiosqlite.connect(":memory:") as db:
        await init_db(db)
        await init_db(db)


@pytest.mark.anyio
async def test_schema_has_expected_tables():
    async with aiosqlite.connect(":memory:") as db:
        await init_db(db)
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ) as cur:
            tables = {row[0] for row in await cur.fetchall()}
    assert {
        "schema_migrations",
        "compartments",
        "containers",
        "volumes",
        "pods",
        "image_units",
        "system_events",
    } <= tables


@pytest.mark.anyio
async def test_compartments_has_network_columns():
    async with aiosqlite.connect(":memory:") as db:
        await init_db(db)
        async with db.execute("PRAGMA table_info(compartments)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
    assert {
        "net_driver",
        "net_subnet",
        "net_gateway",
        "net_ipv6",
        "net_internal",
        "net_dns_enabled",
    } <= columns


@pytest.mark.anyio
async def test_containers_has_all_columns():
    async with aiosqlite.connect(":memory:") as db:
        await init_db(db)
        async with db.execute("PRAGMA table_info(containers)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
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


@pytest.mark.anyio
async def test_init_db_no_arg_creates_file(tmp_path, monkeypatch):
    """init_db(db=None) opens its own connection to settings.db_path and applies migrations."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(_db_module.settings, "db_path", db_file)
    await init_db()
    # Verify the file was created and contains the migrations table
    async with (
        aiosqlite.connect(db_file) as conn,
        conn.execute("SELECT COUNT(*) FROM schema_migrations") as cur,
    ):
        (count,) = await cur.fetchone()
    assert count > 0


@pytest.mark.anyio
async def test_get_db_yields_connection(tmp_path, monkeypatch):
    """get_db() yields a working aiosqlite connection with WAL mode and FK support."""
    db_file = str(tmp_path / "getdb.db")
    monkeypatch.setattr(_db_module.settings, "db_path", db_file)
    gen = get_db()
    conn = await gen.__anext__()
    try:
        assert conn is not None
        # Verify foreign keys are on
        async with conn.execute("PRAGMA foreign_keys") as cur:
            row = await cur.fetchone()
        assert row[0] == 1
    finally:
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
