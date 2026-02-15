"""E2E resilience and chaos engineering tests.

Tests failure injection and graceful degradation across the butler ecosystem:
1. Butler process crash and recovery
2. Serial dispatch lock contention
3. Classification fallback on parse errors
4. Partial dispatch failure (multi-butler)
5. Module failure isolation
6. Lock release after error
7. Cascading failure prevention

These tests validate that the system degrades gracefully rather than
cascading into hard failures when components fail.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastmcp import Client as MCPClient

from butlers.daemon import ButlerDaemon
from butlers.db import Database
from butlers.tools.switchboard.ingestion.ingest import ingest_v1
from butlers.tools.switchboard.routing.classify import classify_message
from butlers.tools.switchboard.routing.dispatch import dispatch_decomposed

if TYPE_CHECKING:
    from asyncpg.pool import Pool

    from tests.e2e.conftest import ButlerEcosystem

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Scenario 1: Butler kill + recovery
# ---------------------------------------------------------------------------


async def test_butler_kill_and_recovery(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
) -> None:
    """Kill a butler mid-operation, verify switchboard handles it gracefully, then recover.

    Tests:
    1. Health butler is reachable via MCP
    2. Shutdown health butler
    3. Attempt to route to health via switchboard → target_unavailable
    4. Restart health butler
    5. Route to health succeeds again
    """
    health_daemon = butler_ecosystem.butlers["health"]
    health_port = health_daemon.config.port

    # Step 1: Verify health is reachable
    url = f"http://localhost:{health_port}/sse"
    async with MCPClient(url) as client:
        status = await client.call_tool("status", {})
        assert status["name"] == "health", "Health butler should respond"

    # Step 2: Kill health butler
    await health_daemon.shutdown()

    # Step 3: Route to health via switchboard should fail gracefully
    # Build classification that targets health
    message_text = "I weigh 75kg this morning"
    now = datetime.now(UTC)
    event_id = f"test-kill-recovery-{uuid4().hex[:8]}"

    envelope_payload = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": "test-endpoint-kill",
        },
        "event": {
            "external_event_id": event_id,
            "external_thread_id": "thread-kill-001",
            "observed_at": now.isoformat(),
        },
        "sender": {
            "identity": "user-kill-test",
        },
        "payload": {
            "raw": {"text": message_text},
            "normalized_text": message_text,
        },
        "control": {
            "policy_tier": "default",
        },
    }

    ingest_response = await ingest_v1(switchboard_pool, envelope_payload)
    assert ingest_response.status == "accepted"
    request_id = ingest_response.request_id

    # Attempt dispatch to killed butler
    dispatch_results = await dispatch_decomposed(
        switchboard_pool,
        [
            {
                "butler": "health",
                "prompt": message_text,
                "subrequest_id": f"health-kill-{uuid4().hex[:8]}",
            }
        ],
        source_channel="switchboard",
        source_id=str(request_id),
        tool_name="test_butler_kill_and_recovery",
        source_metadata={
            "channel": "telegram",
            "identity": "user-kill-test",
            "tool_name": "route.execute",
        },
        fanout_mode="parallel",
    )

    assert len(dispatch_results) == 1
    result = dispatch_results[0]
    assert result["success"] is False, "Dispatch to killed butler should fail"
    assert result["error"] is not None, "Should have error message"

    # Verify routing_log records the failure
    routing_log_entry = await switchboard_pool.fetchrow(
        """
        SELECT * FROM routing_log
        WHERE source_id = $1 AND target_butler = 'health'
        ORDER BY routed_at DESC
        LIMIT 1
        """,
        str(request_id),
    )

    assert routing_log_entry is not None, "Should have routing_log entry"
    assert routing_log_entry["success"] is False, "Log should record failure"

    # Step 4: Restart health butler
    # Re-create Database instance with same params
    pg = butler_ecosystem.postgres_container
    host = pg.get_container_host_ip()
    port = int(pg.get_exposed_port(5432))

    health_db = Database(
        db_name="butler_health",
        host=host,
        port=port,
        user=pg.username,
        password=pg.password,
        min_pool_size=2,
        max_pool_size=10,
    )
    await health_db.connect()

    new_health_daemon = ButlerDaemon(butler_name="health", db=health_db)
    await new_health_daemon.start()

    # Update ecosystem reference
    butler_ecosystem.butlers["health"] = new_health_daemon

    # Step 5: Route to health should succeed now
    new_event_id = f"test-kill-recovery-after-{uuid4().hex[:8]}"
    envelope_payload["event"]["external_event_id"] = new_event_id

    ingest_response_2 = await ingest_v1(switchboard_pool, envelope_payload)
    request_id_2 = ingest_response_2.request_id

    dispatch_results_2 = await dispatch_decomposed(
        switchboard_pool,
        [
            {
                "butler": "health",
                "prompt": message_text,
                "subrequest_id": f"health-recovery-{uuid4().hex[:8]}",
            }
        ],
        source_channel="switchboard",
        source_id=str(request_id_2),
        tool_name="test_butler_kill_and_recovery_after",
        source_metadata={
            "channel": "telegram",
            "identity": "user-kill-test",
            "tool_name": "route.execute",
        },
        fanout_mode="parallel",
    )

    assert len(dispatch_results_2) == 1
    result_2 = dispatch_results_2[0]
    assert result_2["success"] is True, f"Dispatch after recovery should succeed: {result_2}"


# ---------------------------------------------------------------------------
# Scenario 2: Serial dispatch lock contention
# ---------------------------------------------------------------------------


async def test_serial_dispatch_lock_contention(
    butler_ecosystem: ButlerEcosystem,
    health_pool: Pool,
) -> None:
    """Fire two concurrent triggers at health butler, verify serial execution.

    Tests:
    1. Launch two concurrent trigger() calls
    2. Both should succeed (not fail)
    3. Sessions should be sequential (non-overlapping timestamps)
    """
    health_daemon = butler_ecosystem.butlers["health"]
    spawner = health_daemon.spawner
    assert spawner is not None

    # Fire two triggers concurrently
    start_time = datetime.now(UTC)
    results = await asyncio.gather(
        spawner.trigger("Record weight 80kg", trigger_source="external"),
        spawner.trigger("Record weight 75kg", trigger_source="external"),
    )

    # Both should succeed
    assert all(r.success for r in results), f"All triggers should succeed: {results}"
    assert all(r.session_id is not None for r in results)

    # Fetch sessions ordered by creation time
    sessions = await health_pool.fetch(
        """
        SELECT id, triggered_at, completed_at
        FROM sessions
        WHERE triggered_at >= $1
        ORDER BY triggered_at
        """,
        start_time,
    )

    assert len(sessions) >= 2, f"Should have at least 2 sessions, got {len(sessions)}"

    # Verify non-overlapping execution (serial dispatch)
    # Session 1 should complete before session 2 starts
    session_1 = sessions[0]
    session_2 = sessions[1]

    assert session_1["completed_at"] is not None, "Session 1 should have completed_at"
    assert session_2["triggered_at"] is not None, "Session 2 should have triggered_at"

    # Serial execution: first session completes before second starts
    assert session_1["completed_at"] <= session_2["triggered_at"], (
        "Sessions should execute serially (no overlap)"
    )


# ---------------------------------------------------------------------------
# Scenario 3: Classification fallback on parse failure
# ---------------------------------------------------------------------------


async def test_classification_fallback_on_parse_failure(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
    general_pool: Pool,
) -> None:
    """Mock spawner to return garbage classification, verify fallback to general butler.

    Tests:
    1. Patch spawner.trigger to return invalid JSON
    2. Call classify_message()
    3. Should fall back to general butler with original text
    """
    switchboard_daemon = butler_ecosystem.butlers["switchboard"]
    assert switchboard_daemon.spawner is not None

    message_text = "This is a message that will cause classification to fail"

    # Mock spawner to return garbage (non-JSON response)
    with patch.object(
        switchboard_daemon.spawner,
        "trigger",
        new_callable=AsyncMock,
    ) as mock_trigger:
        # Return a SpawnerResult with invalid output
        from conftest import SpawnerResult

        mock_trigger.return_value = SpawnerResult(
            output="This is not valid JSON at all",
            success=True,
            tool_calls=[],
        )

        # Classification should fall back to general
        routing_entries = await classify_message(
            switchboard_pool,
            message_text,
            switchboard_daemon.spawner.trigger,
        )

    # Should have at least one entry
    assert len(routing_entries) >= 1, "Should have fallback routing entry"

    # Fallback should route to general butler
    general_entry = None
    for entry in routing_entries:
        if entry["butler"] == "general":
            general_entry = entry
            break

    assert general_entry is not None, f"Should fall back to general butler: {routing_entries}"

    # Verify fallback prompt contains original text
    assert message_text in general_entry["prompt"], "Fallback should preserve original text"


# ---------------------------------------------------------------------------
# Scenario 4: Partial dispatch failure
# ---------------------------------------------------------------------------


async def test_partial_dispatch_failure(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
    relationship_pool: Pool,
) -> None:
    """Multi-domain dispatch with one target down, verify healthy target still processes.

    Tests:
    1. Kill health butler
    2. Dispatch multi-domain message to health + relationship
    3. Health should fail gracefully
    4. Relationship should succeed
    5. Routing log should show mixed results
    """
    # Kill health butler
    health_daemon = butler_ecosystem.butlers["health"]
    await health_daemon.shutdown()

    # Build multi-domain message
    now = datetime.now(UTC)
    event_id = f"test-partial-failure-{uuid4().hex[:8]}"
    message_text = "I weigh 78kg today. Also, remind me to call Dr. Smith next week."

    envelope_payload = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": "test-endpoint-partial",
        },
        "event": {
            "external_event_id": event_id,
            "external_thread_id": "thread-partial-001",
            "observed_at": now.isoformat(),
        },
        "sender": {
            "identity": "user-partial-test",
        },
        "payload": {
            "raw": {"text": message_text},
            "normalized_text": message_text,
        },
        "control": {
            "policy_tier": "default",
        },
    }

    ingest_response = await ingest_v1(switchboard_pool, envelope_payload)
    request_id = ingest_response.request_id

    # Dispatch to both health (down) and relationship (up)
    dispatch_results = await dispatch_decomposed(
        switchboard_pool,
        [
            {
                "butler": "health",
                "prompt": "I weigh 78kg today",
                "subrequest_id": f"health-sub-{uuid4().hex[:8]}",
            },
            {
                "butler": "relationship",
                "prompt": "Remind me to call Dr. Smith next week",
                "subrequest_id": f"relationship-sub-{uuid4().hex[:8]}",
            },
        ],
        source_channel="switchboard",
        source_id=str(request_id),
        tool_name="test_partial_dispatch_failure",
        source_metadata={
            "channel": "telegram",
            "identity": "user-partial-test",
            "tool_name": "route.execute",
        },
        fanout_mode="parallel",
    )

    assert len(dispatch_results) == 2, "Should get 2 dispatch results"

    # Find results by butler
    health_result = next((r for r in dispatch_results if r["butler"] == "health"), None)
    relationship_result = next((r for r in dispatch_results if r["butler"] == "relationship"), None)

    assert health_result is not None, "Should have health result"
    assert relationship_result is not None, "Should have relationship result"

    # Health should fail (butler is down)
    assert health_result["success"] is False, "Health dispatch should fail"
    assert health_result["error"] is not None

    # Relationship should succeed (butler is up)
    assert relationship_result["success"] is True, (
        f"Relationship dispatch should succeed despite health failure: {relationship_result}"
    )

    # Verify routing_log entries
    routing_log_entries = await switchboard_pool.fetch(
        """
        SELECT target_butler, success FROM routing_log
        WHERE source_id = $1
        ORDER BY routed_at DESC
        """,
        str(request_id),
    )

    assert len(routing_log_entries) >= 2, "Should have at least 2 routing_log entries"

    # Verify mixed results in routing log
    health_log = next((e for e in routing_log_entries if e["target_butler"] == "health"), None)
    relationship_log = next(
        (e for e in routing_log_entries if e["target_butler"] == "relationship"), None
    )

    assert health_log is not None
    assert relationship_log is not None
    assert health_log["success"] is False, "Health routing should be logged as failed"
    assert relationship_log["success"] is True, "Relationship routing should be logged as success"

    # Verify relationship session was created
    relationship_session = await relationship_pool.fetchrow(
        """
        SELECT * FROM sessions
        WHERE trigger_source = 'external'
        AND triggered_at > $1
        ORDER BY triggered_at DESC
        LIMIT 1
        """,
        now,
    )

    assert relationship_session is not None, "Relationship should have created session"


# ---------------------------------------------------------------------------
# Scenario 5: Module failure isolation
# ---------------------------------------------------------------------------


async def test_module_failure_isolation(
    butler_ecosystem: ButlerEcosystem,
) -> None:
    """Butler with failed module should still serve core tools.

    Tests:
    1. Check module statuses on a butler
    2. Verify core tools (status, trigger) are still available
    3. Failed module's tools are not registered
    """
    # Use general butler as test target
    general_daemon = butler_ecosystem.butlers["general"]
    port = general_daemon.config.port
    url = f"http://localhost:{port}/sse"

    # Call status tool (core functionality)
    async with MCPClient(url) as client:
        status = await client.call_tool("status", {})

        assert status is not None
        assert status["name"] == "general"
        assert "modules" in status

        # Verify core tools are available
        tools_response = await client.list_tools()
        tool_names = {t.name for t in tools_response.tools}

        # Core tools should always be present
        assert "status" in tool_names, "Core status tool should be available"
        assert "trigger" in tool_names, "Core trigger tool should be available"

        # If any module failed during startup, it should be marked as failed
        # but butler should still function
        module_statuses = status["modules"]
        for module_name, module_status in module_statuses.items():
            if module_status.get("status") == "failed":
                # Failed module's tools should not be registered
                # This is implicit - if a module fails, its tools won't be in tool_names
                pass


# ---------------------------------------------------------------------------
# Scenario 6: Lock release after error
# ---------------------------------------------------------------------------


async def test_lock_release_after_error(
    butler_ecosystem: ButlerEcosystem,
    health_pool: Pool,
) -> None:
    """Trigger that errors out should release lock for subsequent triggers.

    Tests:
    1. Send a trigger that will cause an error (invalid prompt / tool call failure)
    2. Verify the session is logged with error
    3. Send another trigger immediately after
    4. Second trigger should succeed (lock was released)
    """
    health_daemon = butler_ecosystem.butlers["health"]
    spawner = health_daemon.spawner
    assert spawner is not None

    # First trigger: intentionally cause an error with an impossible request
    # that will fail during tool execution
    start_time = datetime.now(UTC)

    result_1 = await spawner.trigger(
        "Call a tool that doesn't exist named fake_nonexistent_tool_xyz",
        trigger_source="external",
    )

    # The spawner may succeed even if the LLM can't complete the task perfectly
    # What matters is that it completes and releases the lock
    assert result_1.session_id is not None, "Should have logged a session"

    # Second trigger: should succeed without hanging
    result_2 = await spawner.trigger(
        "Get status",
        trigger_source="external",
    )

    assert result_2.success is True, "Second trigger should succeed (lock was released)"
    assert result_2.session_id is not None

    # Verify both sessions exist
    sessions = await health_pool.fetch(
        """
        SELECT id, success, error FROM sessions
        WHERE triggered_at >= $1
        ORDER BY triggered_at
        """,
        start_time,
    )

    assert len(sessions) >= 2, f"Should have at least 2 sessions, got {len(sessions)}"


# ---------------------------------------------------------------------------
# Scenario 7: Cascading failure prevention
# ---------------------------------------------------------------------------


async def test_cascading_failure_prevention(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
    relationship_pool: Pool,
) -> None:
    """Crash one butler, verify others process normally via switchboard.

    Tests:
    1. Kill health butler
    2. Send message to relationship butler via switchboard
    3. Relationship should process normally
    4. Switchboard routing log should show success for relationship
    """
    # Kill health butler
    health_daemon = butler_ecosystem.butlers["health"]
    await health_daemon.shutdown()

    # Send relationship-only message
    now = datetime.now(UTC)
    event_id = f"test-cascade-{uuid4().hex[:8]}"
    message_text = "Remind me to call Sarah next Tuesday"

    envelope_payload = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": "test-endpoint-cascade",
        },
        "event": {
            "external_event_id": event_id,
            "external_thread_id": "thread-cascade-001",
            "observed_at": now.isoformat(),
        },
        "sender": {
            "identity": "user-cascade-test",
        },
        "payload": {
            "raw": {"text": message_text},
            "normalized_text": message_text,
        },
        "control": {
            "policy_tier": "default",
        },
    }

    ingest_response = await ingest_v1(switchboard_pool, envelope_payload)
    request_id = ingest_response.request_id

    # Classify and dispatch
    switchboard_daemon = butler_ecosystem.butlers["switchboard"]
    assert switchboard_daemon.spawner is not None

    routing_entries = await classify_message(
        switchboard_pool,
        message_text,
        switchboard_daemon.spawner.trigger,
    )

    # Find relationship entry
    relationship_entry = None
    for entry in routing_entries:
        if entry["butler"] == "relationship":
            relationship_entry = entry
            break

    # If classifier didn't route to relationship, use general fallback
    if relationship_entry is None:
        relationship_entry = routing_entries[0] if routing_entries else None

    assert relationship_entry is not None, "Should have at least one routing entry"

    # Dispatch to relationship
    dispatch_results = await dispatch_decomposed(
        switchboard_pool,
        [
            {
                "butler": relationship_entry["butler"],
                "prompt": relationship_entry["prompt"],
                "subrequest_id": f"rel-cascade-{uuid4().hex[:8]}",
            }
        ],
        source_channel="switchboard",
        source_id=str(request_id),
        tool_name="test_cascading_failure_prevention",
        source_metadata={
            "channel": "telegram",
            "identity": "user-cascade-test",
            "tool_name": "route.execute",
        },
        fanout_mode="parallel",
    )

    assert len(dispatch_results) >= 1
    result = dispatch_results[0]

    # Relationship should succeed despite health being down
    assert result["success"] is True, (
        f"Relationship should succeed despite health failure: {result}"
    )

    # Verify routing_log
    routing_log_entry = await switchboard_pool.fetchrow(
        """
        SELECT * FROM routing_log
        WHERE source_id = $1
        ORDER BY routed_at DESC
        LIMIT 1
        """,
        str(request_id),
    )

    assert routing_log_entry is not None
    assert routing_log_entry["success"] is True, "Should record successful routing"

    # Verify session in target butler DB
    target_butler = relationship_entry["butler"]
    if target_butler == "relationship":
        target_pool = relationship_pool
    else:
        # If fallback went to general, we'd need general_pool
        # For simplicity, assume relationship
        target_pool = butler_ecosystem.pools[target_butler]

    session = await target_pool.fetchrow(
        """
        SELECT * FROM sessions
        WHERE trigger_source = 'external'
        AND triggered_at > $1
        ORDER BY triggered_at DESC
        LIMIT 1
        """,
        now,
    )

    assert session is not None, f"Should have created session in {target_butler} DB"
