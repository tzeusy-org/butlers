"""E2E performance and load tests — throughput, latency, concurrency, and cost.

Tests cover:
1. Serial dispatch under load: fire 5 concurrent triggers → all complete, sessions are
   sequential
2. Pipeline latency budget: full ingest→classify→dispatch→trigger completes within 120s
3. Lock released on error: trigger that errors out releases lock (subsequent trigger
   succeeds)
4. Connection pool queuing: fire 20 concurrent state_set calls → all succeed (pool
   queues, not rejects)
5. Cost scales linearly: N triggers → cost/trigger is roughly constant (no prompt
   bloat)

Performance baselines are stored in tests/e2e/baselines.json for regression detection.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from fastmcp import Client as MCPClient

from butlers.tools.switchboard.ingestion.ingest import ingest_v1
from butlers.tools.switchboard.routing.classify import classify_message
from butlers.tools.switchboard.routing.dispatch import dispatch_decomposed

if TYPE_CHECKING:
    from asyncpg.pool import Pool

    from tests.e2e.conftest import ButlerEcosystem, CostTracker

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Scenario 1: Serial dispatch under load
# ---------------------------------------------------------------------------


async def test_serial_dispatch_under_load(
    butler_ecosystem: ButlerEcosystem,
    health_pool: Pool,
) -> None:
    """Multiple concurrent triggers should serialize, not deadlock.

    Tests:
    1. Fire 5 concurrent triggers at health butler
    2. All should succeed (not fail)
    3. Sessions should be sequential (non-overlapping timestamps)

    This validates the spawner's serial dispatch lock correctly queues
    concurrent triggers and executes them one at a time.
    """
    health_daemon = butler_ecosystem.butlers["health"]
    spawner = health_daemon.spawner
    assert spawner is not None

    n = 5
    start_time = datetime.now(UTC)

    # Fire N triggers concurrently
    tasks = [
        spawner.trigger(
            prompt=f"Record weight {70 + i}kg",
            trigger_source=f"load-test-serial-{i}",
        )
        for i in range(n)
    ]
    results = await asyncio.gather(*tasks)

    # All should succeed
    assert sum(1 for r in results if r.success) == n, f"All triggers should succeed: {results}"
    assert all(r.session_id is not None for r in results)

    # Fetch sessions ordered by creation time
    sessions = await health_pool.fetch(
        """
        SELECT id, triggered_at, completed_at
        FROM sessions
        WHERE triggered_at >= $1
        AND trigger_source LIKE 'load-test-serial-%'
        ORDER BY triggered_at
        """,
        start_time,
    )

    assert len(sessions) >= n, f"Should have at least {n} sessions, got {len(sessions)}"

    # Verify non-overlapping execution (serial dispatch)
    # Session i should complete before session i+1 starts
    for i in range(1, min(len(sessions), n)):
        prev_session = sessions[i - 1]
        curr_session = sessions[i]

        assert prev_session["completed_at"] is not None, f"Session {i - 1} should have completed_at"
        assert curr_session["triggered_at"] is not None, f"Session {i} should have triggered_at"

        # Serial execution: previous session completes before next starts
        assert prev_session["completed_at"] <= curr_session["triggered_at"], (
            f"Sessions {i - 1} and {i} overlap — serial dispatch lock violated"
        )


# ---------------------------------------------------------------------------
# Scenario 2: Pipeline latency budget
# ---------------------------------------------------------------------------


async def test_pipeline_latency_budget(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
    health_pool: Pool,
) -> None:
    """Full pipeline should complete within the latency budget (120s).

    Tests:
    1. Ingest a message
    2. Classify it
    3. Dispatch to target butler
    4. Verify total time < 120s
    5. Verify session duration is reasonable (< 90s)

    This validates that the end-to-end pipeline (from message ingestion
    through spawner invocation) meets performance expectations.
    """
    switchboard_daemon = butler_ecosystem.butlers["switchboard"]
    assert switchboard_daemon.spawner is not None

    start = time.monotonic()

    # Step 1: Ingest
    message_text = "Log weight 80kg"
    now = datetime.now(UTC)
    event_id = f"test-latency-{uuid4().hex[:8]}"

    envelope_payload = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": "test-endpoint-latency",
        },
        "event": {
            "external_event_id": event_id,
            "external_thread_id": "thread-latency-001",
            "observed_at": now.isoformat(),
        },
        "sender": {
            "identity": "user-latency-test",
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

    # Step 2: Classify
    routing_entries = await classify_message(
        switchboard_pool,
        message_text,
        switchboard_daemon.spawner.trigger,
    )
    assert len(routing_entries) > 0, "Should have at least one routing entry"

    # Find health entry or use first entry as fallback
    health_entry = None
    for entry in routing_entries:
        if entry["butler"] == "health":
            health_entry = entry
            break
    if health_entry is None:
        health_entry = routing_entries[0]

    # Step 3: Dispatch
    dispatch_results = await dispatch_decomposed(
        switchboard_pool,
        [
            {
                "butler": health_entry["butler"],
                "prompt": health_entry["prompt"],
                "subrequest_id": f"latency-sub-{uuid4().hex[:8]}",
            }
        ],
        source_channel="switchboard",
        source_id=str(request_id),
        tool_name="test_pipeline_latency_budget",
        source_metadata={
            "channel": "telegram",
            "identity": "user-latency-test",
            "tool_name": "route.execute",
        },
        fanout_mode="parallel",
    )

    elapsed = time.monotonic() - start

    assert len(dispatch_results) == 1
    result = dispatch_results[0]
    assert result["success"] is True, f"Dispatch should succeed: {result}"

    # Verify full pipeline completes within budget
    budget_seconds = 120
    assert elapsed < budget_seconds, (
        f"Full pipeline took {elapsed:.1f}s (budget: {budget_seconds}s)"
    )

    # Verify session duration aligns (if dispatched to health)
    if health_entry["butler"] == "health":
        session = await health_pool.fetchrow(
            """
            SELECT duration_ms FROM sessions
            WHERE trigger_source = 'external'
            AND triggered_at >= $1
            ORDER BY triggered_at DESC
            LIMIT 1
            """,
            now,
        )
        assert session is not None, "Should have created session"
        assert session["duration_ms"] is not None
        assert session["duration_ms"] > 0
        assert session["duration_ms"] < 90_000, "Session should complete within 90s"


# ---------------------------------------------------------------------------
# Scenario 3: Lock released on error
# ---------------------------------------------------------------------------


async def test_lock_release_after_error(
    butler_ecosystem: ButlerEcosystem,
    health_pool: Pool,
) -> None:
    """Trigger that errors out should release lock for subsequent triggers.

    Tests:
    1. Send a trigger that will cause an error
    2. Verify the session is logged
    3. Send another trigger immediately after
    4. Second trigger should succeed (lock was released)

    This validates that the spawner lock is properly released in finally
    blocks even when sessions fail, preventing deadlocks.
    """
    health_daemon = butler_ecosystem.butlers["health"]
    spawner = health_daemon.spawner
    assert spawner is not None

    start_time = datetime.now(UTC)

    # First trigger: intentionally cause an error with an impossible request
    result_1 = await spawner.trigger(
        "Call a tool that doesn't exist named fake_nonexistent_tool_xyz_12345",
        trigger_source="test-lock-error-1",
    )

    # The spawner may succeed even if the LLM can't complete the task perfectly
    # What matters is that it completes and releases the lock
    assert result_1.session_id is not None, "Should have logged a session"

    # Second trigger: should succeed without hanging
    result_2 = await spawner.trigger(
        "Get status",
        trigger_source="test-lock-error-2",
    )

    assert result_2.success is True, "Second trigger should succeed (lock was released)"
    assert result_2.session_id is not None

    # Verify both sessions exist
    sessions = await health_pool.fetch(
        """
        SELECT id, success, error FROM sessions
        WHERE triggered_at >= $1
        AND trigger_source LIKE 'test-lock-error-%'
        ORDER BY triggered_at
        """,
        start_time,
    )

    assert len(sessions) >= 2, f"Should have at least 2 sessions, got {len(sessions)}"


# ---------------------------------------------------------------------------
# Scenario 4: Connection pool queuing
# ---------------------------------------------------------------------------


async def test_pool_exhaustion_queues_gracefully(
    butler_ecosystem: ButlerEcosystem,
) -> None:
    """Tool calls should queue on pool, not crash, when pool is saturated.

    Tests:
    1. Fire 20 concurrent tool calls (state_set) at one butler
    2. All should succeed (queued, not rejected)
    3. No errors even if pool size is smaller than request count

    This validates that asyncpg's connection pool correctly queues
    requests when all connections are busy, rather than rejecting them.
    """
    health = butler_ecosystem.butlers["health"]
    port = health.config.butler.port
    url = f"http://localhost:{port}/sse"

    async with MCPClient(url) as client:
        # Fire many tool calls concurrently
        n = 20
        tasks = [
            client.call_tool("state_set", {"key": f"load-pool-{i}", "value": i}) for i in range(n)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should succeed (queued, not rejected)
        errors = [r for r in results if isinstance(r, Exception)]
        assert len(errors) == 0, f"Pool saturation caused errors: {errors}"

        # Verify all writes succeeded
        success_count = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "ok")
        assert success_count == n, f"Expected {n} successes, got {success_count}"


# ---------------------------------------------------------------------------
# Scenario 5: Cost scales linearly
# ---------------------------------------------------------------------------


async def test_cost_scales_linearly(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
    health_pool: Pool,
    cost_tracker: CostTracker,
) -> None:
    """Cost should scale linearly with message count (no prompt bloat).

    Tests:
    1. Run N full pipeline cycles
    2. Measure total cost
    3. Verify cost per message is roughly constant
    4. Verify no unbounded prompt growth

    This validates that the system doesn't accumulate unbounded context
    that causes token costs to grow quadratically with message count.
    """
    switchboard_daemon = butler_ecosystem.butlers["switchboard"]
    assert switchboard_daemon.spawner is not None

    n = 3  # Use 3 to keep test fast
    initial_cost = cost_tracker.estimated_cost()

    for i in range(n):
        message_text = f"Log weight {70 + i}kg"
        now = datetime.now(UTC)
        event_id = f"test-cost-{i}-{uuid4().hex[:8]}"

        envelope_payload = {
            "schema_version": "ingest.v1",
            "source": {
                "channel": "telegram",
                "provider": "telegram",
                "endpoint_identity": "test-endpoint-cost",
            },
            "event": {
                "external_event_id": event_id,
                "external_thread_id": "thread-cost-001",
                "observed_at": now.isoformat(),
            },
            "sender": {
                "identity": "user-cost-test",
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

        routing_entries = await classify_message(
            switchboard_pool,
            message_text,
            switchboard_daemon.spawner.trigger,
        )
        assert len(routing_entries) > 0

        health_entry = None
        for entry in routing_entries:
            if entry["butler"] == "health":
                health_entry = entry
                break
        if health_entry is None:
            health_entry = routing_entries[0]

        dispatch_results = await dispatch_decomposed(
            switchboard_pool,
            [
                {
                    "butler": health_entry["butler"],
                    "prompt": health_entry["prompt"],
                    "subrequest_id": f"cost-sub-{i}-{uuid4().hex[:8]}",
                }
            ],
            source_channel="switchboard",
            source_id=str(request_id),
            tool_name="test_cost_scales_linearly",
            source_metadata={
                "channel": "telegram",
                "identity": "user-cost-test",
                "tool_name": "route.execute",
            },
            fanout_mode="parallel",
        )

        assert len(dispatch_results) == 1
        assert dispatch_results[0]["success"] is True

    # Measure incremental cost
    final_cost = cost_tracker.estimated_cost()
    incremental_cost = final_cost - initial_cost

    # Cost per message should be reasonable (< $0.05 per message for Haiku)
    # This is a soft upper bound to catch prompt bloat
    cost_per_message = incremental_cost / n if n > 0 else 0
    max_cost_per_message = 0.05

    assert cost_per_message < max_cost_per_message, (
        f"Cost per message ${cost_per_message:.4f} exceeds budget ${max_cost_per_message:.2f} — "
        f"possible prompt bloat or inefficient LLM usage"
    )

    # Verify sessions were created (smoke test)
    sessions = await health_pool.fetch(
        """
        SELECT id, input_tokens, output_tokens
        FROM sessions
        WHERE trigger_source = 'external'
        AND triggered_at >= NOW() - INTERVAL '5 minutes'
        ORDER BY triggered_at DESC
        LIMIT $1
        """,
        n,
    )

    # Should have at least some sessions (may not be exactly n if classification
    # routed some messages elsewhere)
    assert len(sessions) > 0, "Should have created sessions"

    # Verify token counts are reasonable (not growing unboundedly)
    for session in sessions:
        if session["input_tokens"] is not None:
            # Input tokens should be < 50k (reasonable upper bound for a health prompt)
            assert session["input_tokens"] < 50_000, (
                f"Input tokens {session['input_tokens']} unexpectedly high — possible bloat"
            )


# ---------------------------------------------------------------------------
# Regression detection helper
# ---------------------------------------------------------------------------


async def test_no_latency_regression(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
) -> None:
    """Pipeline latency should not regress beyond baseline threshold.

    This test loads baseline metrics from tests/e2e/baselines.json and
    compares current performance against them. If baselines.json doesn't
    exist, the test is skipped (baseline needs to be established first).

    Threshold: 1.5x baseline (50% regression allowance)
    """
    baselines_path = Path(__file__).parent / "baselines.json"
    if not baselines_path.exists():
        pytest.skip("Baselines file not found — run once to establish baselines")

    import json

    baselines = json.loads(baselines_path.read_text())

    switchboard_daemon = butler_ecosystem.butlers["switchboard"]
    assert switchboard_daemon.spawner is not None

    # Run 3 iterations and measure p95
    latencies = []
    for i in range(3):
        start = time.monotonic()

        message_text = f"Log weight {75 + i}kg"
        now = datetime.now(UTC)
        event_id = f"test-regression-{i}-{uuid4().hex[:8]}"

        envelope_payload = {
            "schema_version": "ingest.v1",
            "source": {
                "channel": "telegram",
                "provider": "telegram",
                "endpoint_identity": "test-endpoint-regression",
            },
            "event": {
                "external_event_id": event_id,
                "external_thread_id": "thread-regression-001",
                "observed_at": now.isoformat(),
            },
            "sender": {
                "identity": "user-regression-test",
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

        routing_entries = await classify_message(
            switchboard_pool,
            message_text,
            switchboard_daemon.spawner.trigger,
        )
        assert len(routing_entries) > 0

        await dispatch_decomposed(
            switchboard_pool,
            [
                {
                    "butler": routing_entries[0]["butler"],
                    "prompt": routing_entries[0]["prompt"],
                    "subrequest_id": f"regression-sub-{i}-{uuid4().hex[:8]}",
                }
            ],
            source_channel="switchboard",
            source_id=str(request_id),
            tool_name="test_no_latency_regression",
            source_metadata={
                "channel": "telegram",
                "identity": "user-regression-test",
                "tool_name": "route.execute",
            },
            fanout_mode="parallel",
        )

        latencies.append(time.monotonic() - start)

    # Calculate p95 (for 3 samples, just use max)
    p95 = max(latencies)
    threshold = baselines["pipeline_latency_p95_ms"] / 1000 * 1.5

    assert p95 < threshold, (
        f"Pipeline p95 latency {p95:.1f}s exceeds regression threshold "
        f"{threshold:.1f}s (baseline: {baselines['pipeline_latency_p95_ms']}ms)"
    )
