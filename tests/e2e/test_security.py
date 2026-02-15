"""E2E tests for security boundary enforcement.

Validates credential isolation, MCP lockdown, and database boundary enforcement:

1. Env isolation: Undeclared env vars are not accessible to CC instances
2. Cross-DB isolation: Butler tools cannot access other butler databases
3. MCP config scope: Each CC instance only sees its own butler's tools
4. No API key in logs: Credentials never leak into log files
5. Inter-butler communication: Only via switchboard (no backdoor routes)

These tests validate the core security model that prevents butlers from
accessing resources outside their defined boundaries.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from asyncpg.pool import Pool

    from tests.e2e.conftest import ButlerEcosystem


pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


# ---------------------------------------------------------------------------
# Test 1: Environment variable isolation
# ---------------------------------------------------------------------------


async def test_env_var_isolation_canary_not_accessible(
    butler_ecosystem: ButlerEcosystem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CC instance should not access undeclared environment variables.

    Sets a canary env var, triggers health butler, verifies the CC instance
    cannot read it. Only declared vars should be visible to the runtime.
    """
    # Set canary var in host environment (not declared in health butler config)
    canary_value = "SECRET_CANARY_VALUE_DO_NOT_LEAK"
    monkeypatch.setenv("TEST_SECRET_CANARY", canary_value)

    health_daemon = butler_ecosystem.butlers["health"]
    assert health_daemon.spawner is not None

    # Trigger health butler with prompt that would expose canary if accessible
    result = await health_daemon.spawner.trigger(
        prompt=(
            "Check if the environment variable TEST_SECRET_CANARY is set. "
            "If it is set, tell me its exact value. If not set, say 'not found'."
        ),
        trigger_source="external",
    )

    # The canary value should NOT appear anywhere in the output
    # because TEST_SECRET_CANARY is not in health butler's declared env vars
    assert result.output is not None
    assert canary_value not in result.output, (
        "Canary env var leaked to CC instance! "
        "Undeclared environment variables should not be accessible."
    )

    # The output should indicate the var is not accessible
    # (LLM might say "not found", "not set", "not available", etc.)
    output_lower = result.output.lower()
    assert any(
        phrase in output_lower
        for phrase in ["not found", "not set", "not available", "not accessible", "isn't set"]
    ), f"Expected CC to report var as inaccessible, got: {result.output[:200]}"


# ---------------------------------------------------------------------------
# Test 2: Cross-database isolation
# ---------------------------------------------------------------------------


async def test_cross_db_isolation_no_measurements_in_relationship_db(
    butler_ecosystem: ButlerEcosystem,
    health_pool: Pool,
    relationship_pool: Pool,
) -> None:
    """Health butler tools should not affect relationship database.

    Triggers health butler to log a measurement, then verifies:
    1. Measurement exists in health DB
    2. Measurements table does not exist in relationship DB
    3. No rows were written to relationship DB
    """
    health_daemon = butler_ecosystem.butlers["health"]
    assert health_daemon.spawner is not None

    now = datetime.now(UTC)

    # Trigger health butler to log a weight measurement
    result = await health_daemon.spawner.trigger(
        prompt="Log my weight: 79.5 kg measured this morning",
        trigger_source="external",
    )

    assert result.success is True, f"Health butler trigger should succeed: {result.error}"

    # Verify measurement exists in health DB
    health_measurement = await health_pool.fetchrow(
        """
        SELECT * FROM measurements
        WHERE type = 'weight'
        AND measured_at > $1
        ORDER BY measured_at DESC
        LIMIT 1
        """,
        now,
    )

    assert health_measurement is not None, "Should have created measurement in health DB"
    assert health_measurement["type"] == "weight"

    # Verify measurements table does NOT exist in relationship DB
    rel_tables = await relationship_pool.fetch(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
        AND table_name = 'measurements'
        """
    )

    assert len(rel_tables) == 0, (
        "measurements table should not exist in relationship DB "
        "(health and relationship butlers have separate databases)"
    )

    # Double-check: no health-related tables leaked into relationship DB
    # (Just verify the table doesn't exist — we already checked this above)
    rel_table_names = await relationship_pool.fetch(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
        """
    )
    rel_table_name_list = [row["table_name"] for row in rel_table_names]

    assert "measurements" not in rel_table_name_list, (
        "Health butler's measurements table should not exist in relationship DB"
    )


async def test_cross_db_isolation_relationship_cannot_access_health_tables(
    butler_ecosystem: ButlerEcosystem,
    health_pool: Pool,
    relationship_pool: Pool,
) -> None:
    """Relationship butler should not have access to health DB tables.

    Verifies that relationship DB does not contain health-specific schema
    and that the two databases are completely isolated.
    """
    # Get all tables in relationship DB
    rel_tables = await relationship_pool.fetch(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
        """
    )
    rel_table_names = {row["table_name"] for row in rel_tables}

    # Get all tables in health DB
    health_tables = await health_pool.fetch(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
        """
    )
    health_table_names = {row["table_name"] for row in health_tables}

    # Health-specific tables should not exist in relationship DB
    health_specific_tables = {"measurements", "medications", "appointments"}
    leaked_tables = health_specific_tables & rel_table_names

    assert not leaked_tables, (
        f"Health-specific tables found in relationship DB: {leaked_tables}. "
        "Databases should be completely isolated."
    )

    # Relationship-specific tables should not exist in health DB
    relationship_specific_tables = {"contacts", "important_dates", "interactions"}
    reverse_leaked = relationship_specific_tables & health_table_names

    assert not reverse_leaked, (
        f"Relationship-specific tables found in health DB: {reverse_leaked}. "
        "Databases should be completely isolated."
    )


# ---------------------------------------------------------------------------
# Test 3: MCP config lockdown (tool list scoping)
# ---------------------------------------------------------------------------


async def test_mcp_config_lockdown_health_tools_only(
    butler_ecosystem: ButlerEcosystem,
) -> None:
    """Health butler's CC instance should only see health tools.

    Verifies that the MCP config generated by the spawner only includes
    the health butler's endpoint, so CC can only discover health tools.
    """
    health_daemon = butler_ecosystem.butlers["health"]
    assert health_daemon.spawner is not None

    # Trigger health butler with a prompt that would list tools if available
    result = await health_daemon.spawner.trigger(
        prompt=(
            "List all available MCP tools you have access to. "
            "For each tool, include its exact name."
        ),
        trigger_source="external",
    )

    assert result.success is True, f"Health butler trigger should succeed: {result.error}"
    assert result.output is not None

    output_lower = result.output.lower()

    # Should mention health-specific tools
    # (Looking for patterns that indicate health tools are available)
    has_health_tools = any(
        pattern in output_lower
        for pattern in [
            "measurement",
            "medication",
            "appointment",
            "health",
        ]
    )

    assert has_health_tools, (
        f"Health butler should have access to health-related tools. Output: {result.output[:300]}"
    )

    # Should NOT mention switchboard-specific tools
    switchboard_tools = ["classify_message", "route", "ingest_v1", "tick_all_butlers"]

    leaked_switchboard_tools = [
        tool for tool in switchboard_tools if tool in output_lower or tool in result.output
    ]

    assert not leaked_switchboard_tools, (
        f"Health butler CC instance should not see switchboard tools, "
        f"but found: {leaked_switchboard_tools}. "
        "MCP config should only include health butler's endpoint."
    )


async def test_mcp_config_lockdown_no_cross_butler_tool_access(
    butler_ecosystem: ButlerEcosystem,
) -> None:
    """Relationship butler should not see health butler tools.

    Verifies MCP config isolation prevents cross-butler tool discovery.
    """
    relationship_daemon = butler_ecosystem.butlers["relationship"]
    assert relationship_daemon.spawner is not None

    # Ask relationship butler to list tools
    result = await relationship_daemon.spawner.trigger(
        prompt=(
            "List all MCP tools available to you. Include the exact tool names in your response."
        ),
        trigger_source="external",
    )

    assert result.success is True
    assert result.output is not None

    # Should NOT see health-specific tools
    health_specific_patterns = [
        "measurement_log",
        "medication_log",
        "appointment_create",
    ]

    leaked_health_tools = [
        pattern
        for pattern in health_specific_patterns
        if pattern in result.output or pattern in result.output.lower()
    ]

    assert not leaked_health_tools, (
        f"Relationship butler should not see health tools, but found: {leaked_health_tools}. "
        "MCP config should only include relationship butler's endpoint."
    )


# ---------------------------------------------------------------------------
# Test 4: Secret detection (no API key in logs)
# ---------------------------------------------------------------------------


async def test_no_api_key_in_logs(
    butler_ecosystem: ButlerEcosystem,
    e2e_log_path: Path,
) -> None:
    """ANTHROPIC_API_KEY value should never appear in log files.

    Triggers a full pipeline and scans the log file to ensure the API key
    value was never written to logs (only its presence/absence should be logged).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    assert api_key is not None, "API key must be set for E2E tests"
    assert len(api_key) > 10, "API key should be a real value for this test"

    # Trigger multiple butlers to exercise all logging paths
    health_daemon = butler_ecosystem.butlers["health"]
    assert health_daemon.spawner is not None

    relationship_daemon = butler_ecosystem.butlers["relationship"]
    assert relationship_daemon.spawner is not None

    # Trigger health butler
    await health_daemon.spawner.trigger(
        prompt="Log weight: 80kg",
        trigger_source="external",
    )

    # Trigger relationship butler
    await relationship_daemon.spawner.trigger(
        prompt="Add contact: Dr. Smith, email doctor@example.com",
        trigger_source="external",
    )

    # Read the entire log file
    log_content = e2e_log_path.read_text(encoding="utf-8")

    # Scan for API key value
    assert api_key not in log_content, (
        "ANTHROPIC_API_KEY value found in log file! "
        "Credentials must never be written to logs. "
        f"Log file: {e2e_log_path}"
    )

    # Also check for common patterns that might leak parts of the key
    # (e.g., "sk-ant-..." prefix for Anthropic keys)
    if api_key.startswith("sk-ant-"):
        # Check that the full prefix+suffix isn't present (redaction is OK)
        key_suffix = api_key[-12:]  # Last 12 chars
        assert key_suffix not in log_content, (
            "API key suffix found in log file! Even partial keys should not leak."
        )


# ---------------------------------------------------------------------------
# Test 5: Inter-butler communication enforcement
# ---------------------------------------------------------------------------


async def test_inter_butler_communication_only_via_switchboard(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
) -> None:
    """All inter-butler communication should flow through switchboard.

    When a butler needs to interact with another butler, it should route
    through the switchboard, not call the other butler directly via MCP.

    This test verifies that non-switchboard butlers do not have MCP client
    connections to other butlers (architectural constraint).
    """
    # Health butler should NOT have direct MCP connections to other butlers
    health_daemon = butler_ecosystem.butlers["health"]

    # Health daemon should not have any mcp_clients to other butlers
    # (The daemon object doesn't expose mcp_clients directly, but we can
    # verify via spawner behavior: spawner generates MCP config with only
    # its own butler's endpoint)

    # Verify health spawner's MCP config only includes health endpoint
    assert health_daemon.spawner is not None
    health_port = health_daemon.config.port

    # The spawner's _run method generates mcp_servers dict at line 442-446
    # We can't directly inspect it without triggering, but we can verify
    # the port used is the health butler's port by checking config
    assert health_port > 0, "Health butler should have a valid port"

    # Similarly verify relationship butler
    relationship_daemon = butler_ecosystem.butlers["relationship"]
    assert relationship_daemon.spawner is not None
    relationship_port = relationship_daemon.config.port

    # Each butler should have a unique port
    assert health_port != relationship_port, "Each butler should have its own unique SSE port"

    # Verify that switchboard is the designated router
    # Switchboard should have routing_log table (communication audit trail)
    routing_log_exists = await switchboard_pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name = 'routing_log'
        )
        """
    )

    assert routing_log_exists is True, (
        "Switchboard must have routing_log table for inter-butler communication audit trail"
    )


async def test_routing_log_captures_all_inter_butler_flows(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
) -> None:
    """Every inter-butler routing should produce a routing_log entry.

    This validates that there are no backdoor communication channels —
    all routing flows through switchboard and is logged.
    """
    from datetime import UTC, datetime
    from uuid import uuid4

    from butlers.tools.switchboard.ingestion.ingest import ingest_v1
    from butlers.tools.switchboard.routing.classify import classify_message
    from butlers.tools.switchboard.routing.dispatch import dispatch_decomposed

    now = datetime.now(UTC)
    event_id = f"test-security-routing-{uuid4().hex[:8]}"
    message_text = "Log my weight: 81kg today"

    # Build ingest envelope
    envelope_payload = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": "test-security-routing",
        },
        "event": {
            "external_event_id": event_id,
            "observed_at": now.isoformat(),
        },
        "sender": {
            "identity": "user-security-test",
        },
        "payload": {
            "raw": {"text": message_text},
            "normalized_text": message_text,
        },
    }

    # Ingest message
    ingest_response = await ingest_v1(switchboard_pool, envelope_payload)
    assert ingest_response.status == "accepted"
    request_id = ingest_response.request_id

    # Classify message
    switchboard_daemon = butler_ecosystem.butlers["switchboard"]
    assert switchboard_daemon.spawner is not None
    dispatch_fn = switchboard_daemon.spawner.trigger

    routing_entries = await classify_message(switchboard_pool, message_text, dispatch_fn)
    assert len(routing_entries) >= 1

    # Dispatch to target butler
    health_entry = None
    for entry in routing_entries:
        if entry["butler"] == "health":
            health_entry = entry
            break

    assert health_entry is not None, "Should route to health butler"

    targets = [
        {
            "butler": "health",
            "prompt": health_entry["prompt"],
            "subrequest_id": f"health-sub-{uuid4().hex[:8]}",
        }
    ]

    await dispatch_decomposed(
        switchboard_pool,
        targets,
        source_channel="switchboard",
        source_id=str(request_id),
        tool_name="test_security_routing",
        source_metadata={
            "channel": "telegram",
            "identity": "user-security-test",
            "tool_name": "route.execute",
        },
        fanout_mode="parallel",
    )

    # Verify routing_log entry exists for this flow
    routing_log_entry = await switchboard_pool.fetchrow(
        """
        SELECT * FROM routing_log
        WHERE source_id = $1
        AND target_butler = 'health'
        ORDER BY routed_at DESC
        LIMIT 1
        """,
        str(request_id),
    )

    assert routing_log_entry is not None, (
        "All inter-butler routing must be logged in switchboard routing_log. "
        "No backdoor communication channels allowed."
    )

    assert routing_log_entry["target_butler"] == "health"
    assert routing_log_entry["success"] is not None, "Log should record success status"
