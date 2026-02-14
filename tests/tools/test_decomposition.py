"""Tests for multi-butler message decomposition pipeline.

Covers the full decomposition flow: classify_message_multi returning multiple
targets, dispatch_to_targets routing to each, aggregate_responses combining
results, and edge cases (fallback, partial failure, routing log entries).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with switchboard tables and return a pool."""
    async with provisioned_postgres_pool() as p:
        # Create switchboard tables (mirrors Alembic switchboard migration)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS butler_registry (
                name TEXT PRIMARY KEY,
                endpoint_url TEXT NOT NULL,
                description TEXT,
                modules JSONB NOT NULL DEFAULT '[]',
                last_seen_at TIMESTAMPTZ,
                eligibility_state TEXT NOT NULL DEFAULT 'active',
                liveness_ttl_seconds INTEGER NOT NULL DEFAULT 300,
                quarantined_at TIMESTAMPTZ,
                quarantine_reason TEXT,
                route_contract_min INTEGER NOT NULL DEFAULT 1,
                route_contract_max INTEGER NOT NULL DEFAULT 1,
                capabilities JSONB NOT NULL DEFAULT '[]',
                eligibility_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS routing_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_butler TEXT NOT NULL,
                target_butler TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                success BOOLEAN NOT NULL,
                duration_ms INTEGER,
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

        yield p


@pytest.fixture
async def seeded_pool(pool):
    """Pool with a standard set of butlers for decomposition tests."""
    from butlers.tools.switchboard import register_butler

    await pool.execute("DELETE FROM butler_registry")
    await pool.execute("DELETE FROM routing_log")

    await register_butler(pool, "health", "http://localhost:8101/sse", "Health tracking butler")
    await register_butler(pool, "email", "http://localhost:8102/sse", "Email butler")
    await register_butler(pool, "calendar", "http://localhost:8103/sse", "Calendar butler")
    await register_butler(
        pool, "general", "http://localhost:8100/sse", "General-purpose fallback butler"
    )

    return pool


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


@dataclass
class FakeClassifyResult:
    """Fake CC spawner result for classification."""

    result: str = ""


def _make_dispatch_fn(response_text: str):
    """Create a fake dispatch_fn that returns a FakeClassifyResult."""

    async def dispatch_fn(**kwargs):
        return FakeClassifyResult(result=response_text)

    return dispatch_fn


def _make_failing_dispatch_fn():
    """Create a dispatch_fn that always raises."""

    async def dispatch_fn(**kwargs):
        raise RuntimeError("CC spawner is down")

    return dispatch_fn


def _make_call_fn(results: dict[str, str] | None = None, failures: set[str] | None = None):
    """Create a mock call_fn for route().

    Parameters
    ----------
    results:
        Map of butler name -> result string. Looked up by endpoint_url port.
    failures:
        Set of butler names that should raise an error.
    """
    results = results or {}
    failures = failures or set()

    # Map port numbers to butler names (matches seeded_pool fixture)
    port_to_name = {
        "8101": "health",
        "8102": "email",
        "8103": "calendar",
        "8100": "general",
    }

    async def call_fn(endpoint_url: str, tool_name: str, args: dict):
        # Resolve butler name from endpoint URL port
        butler_name = None
        for port, name in port_to_name.items():
            if f":{port}/" in endpoint_url:
                butler_name = name
                break

        if butler_name and butler_name in failures:
            raise ConnectionError(f"Failed to reach {butler_name}")

        if butler_name and butler_name in results:
            return results[butler_name]

        return f"ack from {butler_name or 'unknown'}"

    return call_fn


# ------------------------------------------------------------------
# Test 1: Multi-domain message → multiple route() calls
# ------------------------------------------------------------------


async def test_multi_domain_message_produces_multiple_routes(seeded_pool):
    """A message spanning multiple domains should classify to multiple targets
    and dispatch_to_targets should call route() once per target."""
    from butlers.tools.switchboard import (
        classify_message_multi,
        dispatch_to_targets,
    )

    pool = seeded_pool

    # Simulate CC returning two butler names (comma-separated or newline-separated)
    dispatch_fn = _make_dispatch_fn("health, email")

    targets = await classify_message_multi(pool, "Log my weight and send the report", dispatch_fn)
    assert isinstance(targets, list)
    assert len(targets) == 2
    assert "health" in targets
    assert "email" in targets

    # Now dispatch to those targets
    call_fn = _make_call_fn(
        results={"health": "weight logged", "email": "report sent"},
    )
    responses = await dispatch_to_targets(
        pool,
        targets=targets,
        message="Log my weight and send the report",
        call_fn=call_fn,
    )

    assert len(responses) == 2
    # Each target should have a response entry
    target_names = [r["target"] for r in responses]
    assert "health" in target_names
    assert "email" in target_names


# ------------------------------------------------------------------
# Test 2: Single-domain message → exactly one route() call
# ------------------------------------------------------------------


async def test_single_domain_message_produces_one_route(seeded_pool):
    """A single-domain message should classify to exactly one target."""
    from butlers.tools.switchboard import (
        classify_message_multi,
        dispatch_to_targets,
    )

    pool = seeded_pool

    dispatch_fn = _make_dispatch_fn("calendar")

    targets = await classify_message_multi(pool, "What meetings do I have tomorrow?", dispatch_fn)
    assert isinstance(targets, list)
    assert len(targets) == 1
    assert targets[0] == "calendar"

    call_fn = _make_call_fn(results={"calendar": "3 meetings tomorrow"})
    responses = await dispatch_to_targets(
        pool,
        targets=targets,
        message="What meetings do I have tomorrow?",
        call_fn=call_fn,
    )

    assert len(responses) == 1
    assert responses[0]["target"] == "calendar"


# ------------------------------------------------------------------
# Test 3: Classification failure → fallback to general
# ------------------------------------------------------------------


async def test_classification_failure_falls_back_to_general(seeded_pool):
    """When the CC spawner fails during classification, the pipeline should
    fall back to routing the full original message to 'general'."""
    from butlers.tools.switchboard import (
        classify_message_multi,
        dispatch_to_targets,
    )

    pool = seeded_pool

    dispatch_fn = _make_failing_dispatch_fn()

    targets = await classify_message_multi(pool, "Something random", dispatch_fn)
    assert targets == ["general"]

    call_fn = _make_call_fn(results={"general": "handled by general"})
    responses = await dispatch_to_targets(
        pool,
        targets=targets,
        message="Something random",
        call_fn=call_fn,
    )

    assert len(responses) == 1
    assert responses[0]["target"] == "general"
    assert responses[0]["result"] is not None


# ------------------------------------------------------------------
# Test 4: Partial sub-route failure → remaining sub-routes still processed
# ------------------------------------------------------------------


async def test_partial_subroute_failure_processes_remaining(seeded_pool):
    """If one sub-route fails, the remaining sub-routes should still be
    dispatched and their results returned."""
    from butlers.tools.switchboard import dispatch_to_targets

    pool = seeded_pool

    # email will fail, health will succeed
    call_fn = _make_call_fn(
        results={"health": "weight logged"},
        failures={"email"},
    )

    responses = await dispatch_to_targets(
        pool,
        targets=["health", "email"],
        message="Log my weight and send the report",
        call_fn=call_fn,
    )

    assert len(responses) == 2

    health_resp = next(r for r in responses if r["target"] == "health")
    email_resp = next(r for r in responses if r["target"] == "email")

    # Health should have succeeded
    assert health_resp.get("error") is None
    assert health_resp["result"] is not None

    # Email should have an error but not prevent health from completing
    assert email_resp.get("error") is not None


# ------------------------------------------------------------------
# Test 5: Response aggregation combines multiple results
# ------------------------------------------------------------------


async def test_aggregate_responses_combines_results(seeded_pool):
    """aggregate_responses should combine multiple sub-route responses
    into a single coherent reply string."""
    from butlers.tools.switchboard import aggregate_responses

    responses = [
        {"target": "health", "result": "Weight logged: 75kg", "error": None},
        {"target": "email", "result": "Report sent to user@example.com", "error": None},
    ]

    aggregated = aggregate_responses(responses)

    assert isinstance(aggregated, str)
    assert "75kg" in aggregated
    assert "user@example.com" in aggregated


async def test_aggregate_responses_includes_errors(seeded_pool):
    """aggregate_responses should note partial failures in the combined reply."""
    from butlers.tools.switchboard import aggregate_responses

    responses = [
        {"target": "health", "result": "Weight logged: 75kg", "error": None},
        {"target": "email", "result": None, "error": "ConnectionError: Failed to reach email"},
    ]

    aggregated = aggregate_responses(responses)

    assert isinstance(aggregated, str)
    # Should include the successful result
    assert "75kg" in aggregated
    # Should mention the failure
    assert "email" in aggregated.lower()
    assert "error" in aggregated.lower() or "fail" in aggregated.lower()


# ------------------------------------------------------------------
# Test 6: routing_log contains one entry per sub-route
# ------------------------------------------------------------------


async def test_routing_log_has_one_entry_per_subroute(seeded_pool):
    """After dispatch_to_targets, the routing_log should contain exactly
    one entry per sub-route that was dispatched."""
    from butlers.tools.switchboard import dispatch_to_targets

    pool = seeded_pool
    await pool.execute("DELETE FROM routing_log")

    call_fn = _make_call_fn(
        results={"health": "ok", "calendar": "ok"},
    )

    await dispatch_to_targets(
        pool,
        targets=["health", "calendar"],
        message="Log my weight and check my schedule",
        call_fn=call_fn,
    )

    rows = await pool.fetch("SELECT * FROM routing_log ORDER BY created_at")
    assert len(rows) == 2

    logged_targets = {row["target_butler"] for row in rows}
    assert logged_targets == {"health", "calendar"}

    # Both should be successful
    for row in rows:
        assert row["success"] is True


async def test_routing_log_records_failures_per_subroute(seeded_pool):
    """routing_log should record both successes and failures for each sub-route."""
    from butlers.tools.switchboard import dispatch_to_targets

    pool = seeded_pool
    await pool.execute("DELETE FROM routing_log")

    call_fn = _make_call_fn(
        results={"health": "ok"},
        failures={"email"},
    )

    await dispatch_to_targets(
        pool,
        targets=["health", "email"],
        message="Log weight and send report",
        call_fn=call_fn,
    )

    rows = await pool.fetch("SELECT * FROM routing_log ORDER BY created_at")
    assert len(rows) == 2

    health_log = next(r for r in rows if r["target_butler"] == "health")
    email_log = next(r for r in rows if r["target_butler"] == "email")

    assert health_log["success"] is True
    assert email_log["success"] is False
    assert email_log["error"] is not None
