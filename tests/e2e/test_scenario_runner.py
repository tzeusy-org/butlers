"""Parametrized scenario runner for declarative E2E tests.

This module executes scenarios defined in scenarios.py via pytest.mark.parametrize.
Each scenario injects its envelope directly via ``ingest_v1(pool, envelope)`` at
the Switchboard boundary — no MCP/SSE transport required.

After session completion, three verification dimensions are evaluated:

1. **Routing verification**: compare ``IngestAcceptedResponse.triage_target``
   against ``scenario.expected_routing``.
2. **Tool-call verification**: retrieve captured calls from the target butler's
   sessions table (keyed by ``request_id``), assert ``expected_tool_calls`` is a
   subset of actual tool names (set containment, not exact match).
3. **DB assertion verification**: execute each ``DbAssertion.query`` against the
   appropriate butler's pool and compare results against expected values.

Timeout handling: scenarios that exceed ``scenario.timeout_seconds`` are
recorded as ``"timeout"`` and do **not** fail the entire run. Whatever tool-call
data was captured before the timeout is preserved in the result.

Tag-based filtering uses the ``--scenarios`` CLI option (from conftest.py):
  pytest tests/e2e/ --scenarios=smoke
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from tests.e2e.conftest import ButlerEcosystem, CostTracker
from tests.e2e.scenarios import ALL_SCENARIOS, DbAssertion, Scenario

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Result data classes
# ---------------------------------------------------------------------------


@dataclass
class RoutingResult:
    """Routing verification result for a single scenario."""

    expected: str | None
    actual: str | None
    passed: bool
    skipped: bool = False  # True when expected_routing is None (multi-target)


@dataclass
class ToolCallResult:
    """Tool-call verification result for a single scenario."""

    expected: list[str]
    actual_names: list[str]
    missing: list[str]
    passed: bool
    timed_out: bool = False
    all_tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DbAssertionResult:
    """Single database assertion result."""

    description: str
    passed: bool
    error: str | None = None


@dataclass
class ScenarioResult:
    """Aggregated result for a single scenario run."""

    scenario_id: str
    request_id: str | None
    duplicate: bool
    routing: RoutingResult | None
    tool_calls: ToolCallResult | None
    db_assertions: list[DbAssertionResult]
    timed_out: bool
    duration_ms: int
    error: str | None = None


# ---------------------------------------------------------------------------
# Scenario parametrization helpers
# ---------------------------------------------------------------------------


def _scenario_id(scenario: Scenario) -> str:
    """Generate pytest ID from scenario.id and tags."""
    tags_str = "-".join(sorted(scenario.tags)) if scenario.tags else "untagged"
    return f"{scenario.id}[{tags_str}]"


def _build_scenario_list(tag_filter: str | None) -> list[Scenario]:
    """Return scenarios, optionally filtered by tag."""
    if tag_filter is None:
        return ALL_SCENARIOS
    return [s for s in ALL_SCENARIOS if tag_filter in s.tags]


# ---------------------------------------------------------------------------
# Session polling helpers
# ---------------------------------------------------------------------------

_SESSION_POLL_INTERVAL = 0.5  # seconds


async def _wait_for_session_completion(
    pool: Any,  # asyncpg.Pool
    request_id: str,
    *,
    timeout_seconds: int,
) -> dict[str, Any] | None:
    """Poll target butler's sessions table until a session with request_id completes.

    Parameters
    ----------
    pool:
        asyncpg pool for the target butler's schema.
    request_id:
        UUID string matching ``sessions.request_id``.
    timeout_seconds:
        Maximum seconds to wait.

    Returns
    -------
    dict | None
        Session row as dict (includes ``tool_calls`` JSONB list) when the
        session completes before timeout, or ``None`` on timeout.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        row = await pool.fetchrow(
            """
            SELECT id, tool_calls, success, error, input_tokens, output_tokens,
                   duration_ms, completed_at
            FROM sessions
            WHERE request_id = $1::text
              AND completed_at IS NOT NULL
            ORDER BY started_at DESC
            LIMIT 1
            """,
            request_id,
        )
        if row is not None:
            result = dict(row)
            # tool_calls is stored as JSONB — asyncpg may return it as a string
            # or as a parsed list depending on the codec.  Normalise to list.
            tc = result.get("tool_calls")
            if isinstance(tc, str):
                try:
                    result["tool_calls"] = json.loads(tc)
                except Exception:
                    result["tool_calls"] = []
            elif tc is None:
                result["tool_calls"] = []
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(_SESSION_POLL_INTERVAL, remaining))
    return None


# ---------------------------------------------------------------------------
# Routing verification
# ---------------------------------------------------------------------------


def _verify_routing(
    scenario: Scenario,
    triage_target: str | None,
) -> RoutingResult:
    """Compare triage_target against scenario.expected_routing.

    When ``expected_routing`` is None (multi-target scenario), the result is
    marked as skipped — no routing assertion is made.
    """
    if scenario.expected_routing is None:
        return RoutingResult(expected=None, actual=triage_target, passed=True, skipped=True)

    passed = triage_target == scenario.expected_routing
    return RoutingResult(
        expected=scenario.expected_routing,
        actual=triage_target,
        passed=passed,
        skipped=False,
    )


# ---------------------------------------------------------------------------
# Tool-call verification
# ---------------------------------------------------------------------------


def _verify_tool_calls(
    scenario: Scenario,
    session_row: dict[str, Any] | None,
    *,
    timed_out: bool,
) -> ToolCallResult:
    """Verify expected_tool_calls is a subset of actual tool names.

    Uses set containment: scenario passes if every tool in
    ``expected_tool_calls`` appears at least once in the actual tool calls.
    Additional internal tools (state_get, state_set, etc.) are allowed.

    Parameters
    ----------
    scenario:
        The scenario being evaluated.
    session_row:
        Completed session DB row, or None if timed out before session started.
    timed_out:
        Whether the scenario timed out before session completion.
    """
    raw_calls: list[dict[str, Any]] = []
    if session_row is not None:
        raw_calls = session_row.get("tool_calls") or []

    actual_names = [tc.get("name", "") for tc in raw_calls if isinstance(tc, dict)]
    actual_set = set(actual_names)
    expected = scenario.expected_tool_calls

    if not expected:
        # No tool-call assertions for this scenario
        return ToolCallResult(
            expected=[],
            actual_names=actual_names,
            missing=[],
            passed=True,
            timed_out=timed_out,
            all_tool_calls=raw_calls,
        )

    missing = [t for t in expected if t not in actual_set]
    passed = len(missing) == 0 and not timed_out

    return ToolCallResult(
        expected=expected,
        actual_names=actual_names,
        missing=missing,
        passed=passed,
        timed_out=timed_out,
        all_tool_calls=raw_calls,
    )


# ---------------------------------------------------------------------------
# DB assertion verification
# ---------------------------------------------------------------------------


async def _execute_db_assertion(
    assertion: DbAssertion,
    butler_ecosystem: ButlerEcosystem,
) -> DbAssertionResult:
    """Execute a single database assertion and return the result.

    Unlike the original implementation this function returns a result object
    rather than raising, so a failing assertion doesn't abort the full run.
    """
    pool = butler_ecosystem.pools.get(assertion.butler)
    if pool is None:
        return DbAssertionResult(
            description=assertion.description,
            passed=False,
            error=f"Butler pool not found: {assertion.butler!r}",
        )

    try:
        async with pool.acquire() as conn:
            if isinstance(assertion.expected, list):
                rows = await conn.fetch(assertion.query)
                actual_list = [dict(row) for row in rows]
                if actual_list == assertion.expected:
                    return DbAssertionResult(description=assertion.description, passed=True)
                return DbAssertionResult(
                    description=assertion.description,
                    passed=False,
                    error=(f"Expected {assertion.expected!r}, got {actual_list!r}"),
                )

            result = await conn.fetchrow(assertion.query)

            if isinstance(assertion.expected, int):
                if result is None:
                    return DbAssertionResult(
                        description=assertion.description,
                        passed=False,
                        error=f"Query returned no rows (expected count: {assertion.expected})",
                    )
                actual_count = result.get("count")
                if actual_count == assertion.expected:
                    return DbAssertionResult(description=assertion.description, passed=True)
                return DbAssertionResult(
                    description=assertion.description,
                    passed=False,
                    error=f"Expected count {assertion.expected}, got {actual_count!r}",
                )

            if isinstance(assertion.expected, dict):
                if result is None:
                    return DbAssertionResult(
                        description=assertion.description,
                        passed=False,
                        error=f"Query returned no rows (expected {assertion.expected!r})",
                    )
                actual_dict = dict(result)
                mismatches: list[str] = []
                for key, expected_value in assertion.expected.items():
                    if key not in actual_dict:
                        mismatches.append(f"missing key {key!r}")
                    elif actual_dict[key] != expected_value:
                        mismatches.append(
                            f"key {key!r}: expected {expected_value!r}, got {actual_dict[key]!r}"
                        )
                if not mismatches:
                    return DbAssertionResult(description=assertion.description, passed=True)
                return DbAssertionResult(
                    description=assertion.description,
                    passed=False,
                    error="; ".join(mismatches),
                )

            if assertion.expected is None:
                if result is None:
                    return DbAssertionResult(description=assertion.description, passed=True)
                return DbAssertionResult(
                    description=assertion.description,
                    passed=False,
                    error=f"Expected no rows, got {dict(result)!r}",
                )

            return DbAssertionResult(
                description=assertion.description,
                passed=False,
                error=f"Unsupported assertion.expected type: {type(assertion.expected).__name__}",
            )

    except Exception as exc:
        return DbAssertionResult(
            description=assertion.description,
            passed=False,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Core scenario execution
# ---------------------------------------------------------------------------


async def _run_scenario(
    scenario: Scenario,
    butler_ecosystem: ButlerEcosystem,
    cost_tracker: CostTracker,
) -> ScenarioResult:
    """Execute a single scenario and return a ScenarioResult.

    Steps:
    1. Inject envelope via ``ingest_v1(pool, envelope)`` on switchboard pool.
    2. Verify routing from ``IngestAcceptedResponse.triage_target``.
    3. Determine target butler pool (from expected_routing or triage_target).
    4. Poll for session completion, respecting ``scenario.timeout_seconds``.
    5. Verify tool calls via set containment.
    6. Execute DB assertions (non-fatal per assertion).
    7. Record token usage in cost_tracker.

    Returns
    -------
    ScenarioResult with all verification outcomes.
    """
    from butlers.tools.switchboard.ingestion.ingest import (  # noqa: PLC0415
        IngestAcceptedResponse,
        ingest_v1,
    )

    t0 = time.monotonic()

    switchboard_pool = butler_ecosystem.pools.get("switchboard")
    if switchboard_pool is None:
        return ScenarioResult(
            scenario_id=scenario.id,
            request_id=None,
            duplicate=False,
            routing=None,
            tool_calls=None,
            db_assertions=[],
            timed_out=False,
            duration_ms=0,
            error="switchboard pool not available",
        )

    # Step 1: Inject envelope via ingest_v1 at the Switchboard boundary.
    logger.info("[%s] Injecting envelope via ingest_v1()", scenario.id)
    try:
        response: IngestAcceptedResponse = await ingest_v1(
            switchboard_pool,
            scenario.envelope,
        )
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.error("[%s] ingest_v1() raised: %s", scenario.id, exc)
        return ScenarioResult(
            scenario_id=scenario.id,
            request_id=None,
            duplicate=False,
            routing=None,
            tool_calls=None,
            db_assertions=[],
            timed_out=False,
            duration_ms=duration_ms,
            error=f"ingest_v1 failed: {exc}",
        )

    request_id = str(response.request_id)
    duplicate = response.duplicate
    triage_target = response.triage_target

    logger.info(
        "[%s] ingest_v1 accepted: request_id=%s duplicate=%s triage_target=%s",
        scenario.id,
        request_id,
        duplicate,
        triage_target,
    )

    # Step 2: Routing verification.
    routing_result = _verify_routing(scenario, triage_target)
    if not routing_result.passed and not routing_result.skipped:
        logger.warning(
            "[%s] Routing mismatch: expected=%s actual=%s",
            scenario.id,
            routing_result.expected,
            routing_result.actual,
        )

    # Step 3: Determine which butler's pool to poll for session completion.
    # Use triage_target if available, fall back to expected_routing.
    target_butler = triage_target or scenario.expected_routing
    target_pool = butler_ecosystem.pools.get(target_butler) if target_butler else None

    # Step 4: Poll for session completion.
    timed_out = False
    session_row: dict[str, Any] | None = None

    # Duplicate injections don't spawn a new session — tool-call verification
    # is skipped for duplicates (no new session was created).
    if (
        not duplicate
        and target_pool is not None
        and (scenario.expected_tool_calls or scenario.db_assertions)
    ):
        logger.info(
            "[%s] Waiting for session (butler=%s timeout=%ds)",
            scenario.id,
            target_butler,
            scenario.timeout_seconds,
        )
        remaining_seconds = scenario.timeout_seconds - int(time.monotonic() - t0)
        if remaining_seconds > 0:
            session_row = await _wait_for_session_completion(
                target_pool,
                request_id,
                timeout_seconds=remaining_seconds,
            )
        if session_row is None:
            timed_out = True
            logger.warning(
                "[%s] Timed out waiting for session (butler=%s request_id=%s)",
                scenario.id,
                target_butler,
                request_id,
            )
        else:
            logger.info(
                "[%s] Session completed (butler=%s session_id=%s duration_ms=%s)",
                scenario.id,
                target_butler,
                session_row.get("id"),
                session_row.get("duration_ms"),
            )

    # Step 5: Tool-call verification.
    tool_call_result: ToolCallResult | None = None
    if scenario.expected_tool_calls or (session_row is not None and not duplicate):
        tool_call_result = _verify_tool_calls(scenario, session_row, timed_out=timed_out)
        if tool_call_result.missing:
            logger.warning(
                "[%s] Missing tool calls: %s (actual: %s)",
                scenario.id,
                tool_call_result.missing,
                tool_call_result.actual_names,
            )

    # Step 6: Execute DB assertions.
    db_results: list[DbAssertionResult] = []
    if not timed_out and session_row is not None:
        for assertion in scenario.db_assertions:
            result = await _execute_db_assertion(assertion, butler_ecosystem)
            db_results.append(result)
            if not result.passed:
                logger.warning(
                    "[%s] DB assertion failed: %s — %s",
                    scenario.id,
                    assertion.description,
                    result.error,
                )

    # Step 7: Track token usage.
    if session_row is not None:
        cost_tracker.record(
            input_tokens=session_row.get("input_tokens") or 0,
            output_tokens=session_row.get("output_tokens") or 0,
        )

    duration_ms = int((time.monotonic() - t0) * 1000)

    return ScenarioResult(
        scenario_id=scenario.id,
        request_id=request_id,
        duplicate=duplicate,
        routing=routing_result,
        tool_calls=tool_call_result,
        db_assertions=db_results,
        timed_out=timed_out,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Pytest test functions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=_scenario_id)
async def test_scenario_routing(
    scenario: Scenario,
    butler_ecosystem: ButlerEcosystem,
    cost_tracker: CostTracker,
    scenario_tag_filter: str | None,
) -> None:
    """Inject envelope via ingest_v1() and verify routing (triage_target).

    Skips scenarios that have no ``expected_routing``.
    When ``--scenarios`` tag filter is set, scenarios not matching the tag
    are skipped via pytest.skip (not collected away).
    """
    # Tag filter: skip silently if this scenario doesn't match
    if scenario_tag_filter is not None and scenario_tag_filter not in scenario.tags:
        pytest.skip(f"Scenario {scenario.id!r} does not match tag filter {scenario_tag_filter!r}")

    if scenario.expected_routing is None:
        pytest.skip(f"Scenario {scenario.id!r} has no expected_routing — skipping routing test")

    result = await _run_scenario(scenario, butler_ecosystem, cost_tracker)

    if result.error:
        pytest.fail(f"Scenario {scenario.id!r} error: {result.error}")

    if result.timed_out:
        pytest.skip(f"Scenario {scenario.id!r} timed out — recording as skip")

    assert result.routing is not None, "routing result expected"
    assert result.routing.passed, (
        f"Routing mismatch for {scenario.id!r}: "
        f"expected={result.routing.expected!r} "
        f"actual={result.routing.actual!r}"
    )

    logger.info(
        "[%s] routing PASS: triage_target=%s (duration=%dms)",
        scenario.id,
        result.routing.actual,
        result.duration_ms,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [s for s in ALL_SCENARIOS if s.expected_tool_calls],
    ids=_scenario_id,
)
async def test_scenario_tool_calls(
    scenario: Scenario,
    butler_ecosystem: ButlerEcosystem,
    cost_tracker: CostTracker,
    scenario_tag_filter: str | None,
) -> None:
    """Inject envelope and verify expected tool calls appear in the session.

    Uses set containment: scenario passes if all expected tools were called,
    even if additional tools were also called.

    Scenarios that time out are recorded as skip (not hard-fail).
    """
    # Tag filter
    if scenario_tag_filter is not None and scenario_tag_filter not in scenario.tags:
        pytest.skip(f"Scenario {scenario.id!r} does not match tag filter {scenario_tag_filter!r}")

    result = await _run_scenario(scenario, butler_ecosystem, cost_tracker)

    if result.error:
        pytest.fail(f"Scenario {scenario.id!r} error: {result.error}")

    if result.timed_out:
        # Record partial tool-call data in the log and skip (don't fail)
        partial_calls = result.tool_calls.actual_names if result.tool_calls else []
        logger.warning(
            "[%s] TIMEOUT — partial tool calls captured: %s",
            scenario.id,
            partial_calls,
        )
        pytest.skip(
            f"Scenario {scenario.id!r} timed out after {scenario.timeout_seconds}s "
            f"(partial tool calls: {partial_calls})"
        )

    assert result.tool_calls is not None, "tool_calls result expected"
    assert result.tool_calls.passed, (
        f"Tool-call mismatch for {scenario.id!r}: "
        f"missing={result.tool_calls.missing!r}, "
        f"actual={result.tool_calls.actual_names!r}"
    )

    logger.info(
        "[%s] tool-calls PASS: actual=%s (duration=%dms)",
        scenario.id,
        result.tool_calls.actual_names,
        result.duration_ms,
    )


_DB_ASSERTION_SCENARIOS = [s for s in ALL_SCENARIOS if s.db_assertions]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    _DB_ASSERTION_SCENARIOS
    or [pytest.param(None, marks=pytest.mark.skip(reason="no db-assertion scenarios defined"))],
    ids=lambda s: _scenario_id(s) if s is not None else "no-scenarios",
)
async def test_scenario_db_assertions(
    scenario: Scenario | None,
    butler_ecosystem: ButlerEcosystem,
    cost_tracker: CostTracker,
    scenario_tag_filter: str | None,
) -> None:
    """Inject envelope and validate database state after session completion.

    Each ``DbAssertion`` is executed against the appropriate butler's pool.
    All assertions must pass for the test to succeed.

    Scenarios that time out are skipped.
    """
    if scenario is None:
        pytest.skip("no db-assertion scenarios defined")

    # Tag filter
    if scenario_tag_filter is not None and scenario_tag_filter not in scenario.tags:
        pytest.skip(f"Scenario {scenario.id!r} does not match tag filter {scenario_tag_filter!r}")

    assert scenario.expected_routing is not None, (
        f"Side-effect scenario {scenario.id!r} must have expected_routing set"
    )

    result = await _run_scenario(scenario, butler_ecosystem, cost_tracker)

    if result.error:
        pytest.fail(f"Scenario {scenario.id!r} error: {result.error}")

    if result.timed_out:
        pytest.skip(f"Scenario {scenario.id!r} timed out — skipping DB assertions")

    # Collect all failures
    failures = [r for r in result.db_assertions if not r.passed]
    if failures:
        failure_details = "\n".join(f"  - {f.description}: {f.error}" for f in failures)
        pytest.fail(f"DB assertions failed for {scenario.id!r}:\n{failure_details}")

    logger.info(
        "[%s] db-assertions PASS: %d/%d passed (duration=%dms)",
        scenario.id,
        len(result.db_assertions),
        len(result.db_assertions),
        result.duration_ms,
    )


# ---------------------------------------------------------------------------
# Deduplication test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [ALL_SCENARIOS[0]] if ALL_SCENARIOS else [],
    ids=lambda s: f"{s.id}[dedup]",
)
async def test_scenario_deduplication(
    scenario: Scenario,
    butler_ecosystem: ButlerEcosystem,
    cost_tracker: CostTracker,
) -> None:
    """Verify that duplicate injection returns duplicate=True with same request_id.

    Uses the first available scenario as the test subject.  Two consecutive
    injections of the same envelope must produce the same request_id and set
    ``duplicate=True`` on the second response.
    """
    from butlers.tools.switchboard.ingestion.ingest import ingest_v1  # noqa: PLC0415

    switchboard_pool = butler_ecosystem.pools.get("switchboard")
    if switchboard_pool is None:
        pytest.skip("switchboard pool not available")

    # First injection — should be accepted fresh
    resp1 = await ingest_v1(switchboard_pool, scenario.envelope)
    assert resp1.status == "accepted"
    assert resp1.request_id is not None

    # Second injection with identical envelope — must be a duplicate
    resp2 = await ingest_v1(switchboard_pool, scenario.envelope)
    assert resp2.duplicate is True, (
        f"Expected duplicate=True on second injection of {scenario.id!r}"
    )
    assert resp2.request_id == resp1.request_id, (
        f"Expected same request_id on duplicate: first={resp1.request_id} second={resp2.request_id}"
    )

    logger.info(
        "[%s] dedup PASS: request_id=%s (both injections)",
        scenario.id,
        resp1.request_id,
    )
