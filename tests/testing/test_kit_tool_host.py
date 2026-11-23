"""The ``tool_host`` kit: a butler's tools on a real clone, with no daemon lifecycle.

bu-rvgzmf.2 option B. Tests whose subject is a tool's SQL use this instead of a boot.
"""

from __future__ import annotations

import time

import pytest

from butlers.testing.daemon import write_butler_config

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_registers_core_tools_without_starting_the_daemon(tool_host, tmp_path) -> None:
    host = await tool_host(write_butler_config(tmp_path, "general"))

    names = {tool.name for tool in await host.mcp.list_tools()}
    assert {"remind", "notify", "status"} <= names
    # Nothing in the lifecycle ran: no spawner, no Switchboard, no MCP server.
    assert host.daemon.spawner is None
    assert host.daemon.switchboard_client is None
    assert host.daemon.config.name == "general"


async def test_a_tools_sql_really_hits_the_clone(tool_host, tmp_path) -> None:
    from butlers.testing.db import open_pool

    host = await tool_host(write_butler_config(tmp_path, "general"))

    result = await host.call(
        "remind", message="Water the plants", channel="telegram", delay_minutes=30
    )

    admin = await open_pool(host.admin_url)
    try:
        prompt = await admin.fetchval(
            "SELECT prompt FROM general.scheduled_tasks WHERE id = $1::uuid", result["id"]
        )
    finally:
        await admin.close()
    assert "Water the plants" in prompt
    # The butler's own pool sees the same row, through its production role.
    assert await host.pool.fetchval("SELECT count(*) FROM scheduled_tasks") >= 1


async def test_each_host_gets_its_own_clone(tool_host, tmp_path) -> None:
    first = await tool_host(write_butler_config(tmp_path / "a", "general"))
    second = await tool_host(write_butler_config(tmp_path / "b", "general"))

    await first.call("remind", message="Only in the first", channel="telegram", delay_minutes=5)

    assert first.daemon.db.db_name != second.daemon.db.db_name
    assert await second.pool.fetchval("SELECT count(*) FROM scheduled_tasks") == 0


async def test_host_cost_is_reported(tool_host, tmp_path) -> None:
    began = time.monotonic()
    host = await tool_host(write_butler_config(tmp_path, "general"))
    elapsed = time.monotonic() - began

    assert host.setup_seconds > 0
    print(f"tool_host setup: {host.setup_seconds:.3f}s (wall {elapsed:.3f}s)")


async def test_a_db_free_host_registers_the_same_tools_on_a_given_pool(
    tool_host, tmp_path, monkeypatch
) -> None:
    # Handler tests whose subject is not SQL hand the host a DB-free pool: no clone,
    # no lifecycle, the daemon's real registration. What makes that host cheap is
    # that it never clones the template or connects a pool, so assert exactly that
    # rather than a wall-clock bound, which CI load broke (bu-5aclrp).
    from butlers.testing.fake_pool import fake_pool

    def no_db_work(*_args, **_kwargs):
        raise AssertionError("a DB-free host cloned the template or connected a pool")

    monkeypatch.setattr("butlers.testing.db.migrated_url", no_db_work)
    monkeypatch.setattr("butlers.db.Database.connect", no_db_work)
    pool = fake_pool()
    host = await tool_host(write_butler_config(tmp_path, "general"), pool=pool)

    assert host.pool is pool
    assert host.admin_url == ""
    names = {tool.name for tool in await host.mcp.list_tools()}
    assert {"remind", "notify", "status"} <= names


async def test_db_free_hosts_need_no_fixture_or_container(tmp_path) -> None:
    # Unit-lane handler tests build the host themselves; no Postgres is involved.
    from butlers.testing.fake_pool import fake_pool
    from butlers.testing.tool_host import host_tools

    host = await host_tools(write_butler_config(tmp_path, "general"), pool=fake_pool())

    assert (await host.tool("remind")).__name__ == "remind"
