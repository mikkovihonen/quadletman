"""Tests for the database migration runner."""

import aiosqlite
import pytest

from quadletman.database import init_db


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
