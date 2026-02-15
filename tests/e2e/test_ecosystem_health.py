"""E2E ecosystem health smoke tests.

Validates that the butler ecosystem boots correctly and all SSE ports
are responding. These tests run before any LLM-dependent tests to fail
fast if infrastructure is broken.

No LLM calls are made in these tests.
"""

from __future__ import annotations

import pytest
from fastmcp import Client as MCPClient

from tests.e2e.conftest import ButlerEcosystem

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_all_butlers_responding(butler_ecosystem: ButlerEcosystem) -> None:
    """Verify all butler SSE endpoints respond to status tool calls."""
    for butler_name, daemon in butler_ecosystem.butlers.items():
        port = daemon.config.butler.port
        url = f"http://localhost:{port}/sse"

        async with MCPClient(url) as client:
            result = await client.call_tool("status", {})
            assert result is not None, f"Butler {butler_name} status returned None"
            assert "butler" in result, f"Butler {butler_name} status missing 'butler' key"
            assert result["butler"] == butler_name


@pytest.mark.asyncio
async def test_databases_provisioned(butler_ecosystem: ButlerEcosystem) -> None:
    """Verify all butler databases were provisioned with core tables."""
    for butler_name, pool in butler_ecosystem.pools.items():
        # Check that core tables exist
        async with pool.acquire() as conn:
            # Query pg_tables to verify state table exists
            result = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT FROM pg_tables
                    WHERE schemaname = 'public'
                    AND tablename = 'state'
                )
                """
            )
            assert result is True, f"Butler {butler_name} missing 'state' table"

            # Check scheduled_tasks table
            result = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT FROM pg_tables
                    WHERE schemaname = 'public'
                    AND tablename = 'scheduled_tasks'
                )
                """
            )
            assert result is True, f"Butler {butler_name} missing 'scheduled_tasks' table"


@pytest.mark.asyncio
async def test_ecosystem_butler_count(butler_ecosystem: ButlerEcosystem) -> None:
    """Verify expected number of butlers are running."""
    # Expected roster butlers based on infrastructure.md
    expected_butlers = {
        "switchboard",
        "general",
        "relationship",
        "health",
        "messenger",
        "heartbeat",
    }
    actual_butlers = set(butler_ecosystem.butlers.keys())
    assert actual_butlers == expected_butlers


@pytest.mark.asyncio
async def test_cost_tracker_initialized(cost_tracker) -> None:
    """Verify cost tracker fixture is available and initialized."""
    assert cost_tracker.llm_calls == 0
    assert cost_tracker.input_tokens == 0
    assert cost_tracker.output_tokens == 0
    assert cost_tracker.estimated_cost() == 0.0
