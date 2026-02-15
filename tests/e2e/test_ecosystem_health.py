"""E2E ecosystem health smoke tests.

Validates that the butler ecosystem boots correctly and all SSE ports
are responding. These tests run before any LLM-dependent tests to fail
fast if infrastructure is broken.

No LLM calls are made in these tests.
"""

from __future__ import annotations

import asyncio
import socket

import pytest
from asyncpg import Pool
from fastmcp import Client as MCPClient

from tests.e2e.conftest import ButlerEcosystem

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# SSE Port Reachability Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_butlers_running(butler_ecosystem: ButlerEcosystem) -> None:
    """For each butler, assert SSE port is reachable (socket connect)."""
    for butler_name, daemon in butler_ecosystem.butlers.items():
        port = daemon.config.butler.port

        # Try to establish a TCP connection to the SSE port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        try:
            result = sock.connect_ex(("localhost", port))
            assert result == 0, f"Butler {butler_name} port {port} not reachable"
        finally:
            sock.close()


# ---------------------------------------------------------------------------
# MCP Status Tool Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_butler_status_tools(butler_ecosystem: ButlerEcosystem) -> None:
    """Call status() via MCP client on each butler, assert name/health/modules in response."""
    for butler_name, daemon in butler_ecosystem.butlers.items():
        port = daemon.config.butler.port
        url = f"http://localhost:{port}/sse"

        async with MCPClient(url) as client:
            result = await client.call_tool("status", {})
            assert result is not None, f"Butler {butler_name} status returned None"
            assert "name" in result, f"Butler {butler_name} status missing 'name' key"
            assert result["name"] == butler_name, (
                f"Butler {butler_name} status returned wrong name: {result['name']}"
            )
            assert "modules" in result, f"Butler {butler_name} status missing 'modules' key"
            assert isinstance(result["modules"], dict), (
                f"Butler {butler_name} modules is not a dict"
            )
            assert "health" in result, f"Butler {butler_name} status missing 'health' key"


# ---------------------------------------------------------------------------
# Switchboard Registry Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switchboard_registry(
    butler_ecosystem: ButlerEcosystem, switchboard_pool: Pool
) -> None:
    """Query butler_registry table in switchboard DB, assert all butlers registered."""
    expected_butlers = set(butler_ecosystem.butlers.keys())

    async with switchboard_pool.acquire() as conn:
        rows = await conn.fetch("SELECT name FROM butler_registry")
        registered_butlers = {row["name"] for row in rows}

        missing = expected_butlers - registered_butlers
        extra = registered_butlers - expected_butlers

        assert not missing, f"Butlers not registered in switchboard: {sorted(missing)}"
        assert not extra, f"Extra butlers in registry: {sorted(extra)}"


# ---------------------------------------------------------------------------
# Core Table Existence Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_core_tables_exist(butler_ecosystem: ButlerEcosystem) -> None:
    """For each butler DB, query pg_tables for state, scheduled_tasks, sessions."""
    core_tables = {"state", "scheduled_tasks", "sessions"}

    for butler_name, pool in butler_ecosystem.pools.items():
        async with pool.acquire() as conn:
            for table_name in core_tables:
                exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT FROM pg_tables
                        WHERE schemaname = 'public'
                        AND tablename = $1
                    )
                    """,
                    table_name,
                )
                assert exists, f"Butler {butler_name} missing core table: {table_name}"


# ---------------------------------------------------------------------------
# Health Butler-Specific Table Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_tables_exist(health_pool: Pool) -> None:
    """Query health DB for measurements, medications, conditions, symptoms, meals, research."""
    expected_tables = {
        "measurements",
        "medications",
        "medication_doses",
        "conditions",
        "symptoms",
        "meals",
        "research",
    }

    async with health_pool.acquire() as conn:
        for table_name in expected_tables:
            exists = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT FROM pg_tables
                    WHERE schemaname = 'public'
                    AND tablename = $1
                )
                """,
                table_name,
            )
            assert exists, f"Health butler missing expected table: {table_name}"


# ---------------------------------------------------------------------------
# Relationship Butler-Specific Table Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_relationship_tables_exist(relationship_pool: Pool) -> None:
    """Query relationship DB for contacts, important_dates, notes."""
    expected_tables = {
        "contacts",
        "important_dates",
        "notes",
        "interactions",
        "reminders",
        "relationships",
    }

    async with relationship_pool.acquire() as conn:
        for table_name in expected_tables:
            exists = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT FROM pg_tables
                    WHERE schemaname = 'public'
                    AND tablename = $1
                )
                """,
                table_name,
            )
            assert exists, f"Relationship butler missing expected table: {table_name}"


# ---------------------------------------------------------------------------
# Butler Count and Fixture Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ecosystem_butler_count(butler_ecosystem: ButlerEcosystem) -> None:
    """Verify expected number of butlers are running."""
    # Expected roster butlers based on roster/ directory
    expected_butlers = {
        "switchboard",
        "general",
        "relationship",
        "health",
        "messenger",
        "heartbeat",
    }
    actual_butlers = set(butler_ecosystem.butlers.keys())
    assert actual_butlers == expected_butlers, (
        f"Butler count mismatch. Expected: {sorted(expected_butlers)}, "
        f"Got: {sorted(actual_butlers)}"
    )


@pytest.mark.asyncio
async def test_cost_tracker_initialized(cost_tracker) -> None:
    """Verify cost tracker fixture is available and initialized."""
    assert cost_tracker.llm_calls == 0
    assert cost_tracker.input_tokens == 0
    assert cost_tracker.output_tokens == 0
    assert cost_tracker.estimated_cost() == 0.0


# ---------------------------------------------------------------------------
# Concurrent Health Check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_status_calls(butler_ecosystem: ButlerEcosystem) -> None:
    """Call status() on all butlers concurrently to test parallel SSE handling."""

    async def check_status(butler_name: str, port: int) -> dict:
        url = f"http://localhost:{port}/sse"
        async with MCPClient(url) as client:
            return await client.call_tool("status", {})

    tasks = [
        check_status(name, daemon.config.butler.port)
        for name, daemon in butler_ecosystem.butlers.items()
    ]

    results = await asyncio.gather(*tasks)

    # Verify all returned successfully
    assert len(results) == len(butler_ecosystem.butlers)
    for result in results:
        assert result is not None
        assert "name" in result
        assert "modules" in result
        assert "health" in result
