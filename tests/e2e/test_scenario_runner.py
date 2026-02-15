"""Parametrized scenario runner for declarative E2E tests.

This module executes scenarios defined in scenarios.py via pytest.mark.parametrize.
Each scenario is automatically converted into a test case without code changes.

Test functions:
- test_scenario_side_effects: Triggers butler spawner and validates DB state

Tag-based filtering works via pytest -k:
  pytest -k 'smoke'              # Run only smoke tests
  pytest -k 'health and smoke'   # Run health smoke tests
"""

from __future__ import annotations

import logging

import pytest
from fastmcp import Client as MCPClient

from tests.e2e.conftest import ButlerEcosystem, CostTracker
from tests.e2e.scenarios import ALL_SCENARIOS, DbAssertion, E2EScenario

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Scenario Parametrization
# ---------------------------------------------------------------------------


def _scenario_id(scenario: E2EScenario) -> str:
    """Generate pytest ID from scenario.id and tags."""
    tags_str = "-".join(sorted(scenario.tags)) if scenario.tags else "untagged"
    return f"{scenario.id}[{tags_str}]"


# Filter scenarios for side-effect tests (those with db_assertions)
SIDE_EFFECT_SCENARIOS = [s for s in ALL_SCENARIOS if s.db_assertions]


# ---------------------------------------------------------------------------
# Side-Effect Tests
# ---------------------------------------------------------------------------


async def _execute_db_assertion(
    assertion: DbAssertion,
    butler_ecosystem: ButlerEcosystem,
) -> None:
    """Execute a single database assertion and validate result."""
    pool = butler_ecosystem.pools.get(assertion.butler)
    assert pool is not None, f"Butler {assertion.butler} pool not found in ecosystem"

    async with pool.acquire() as conn:
        # Handle different expected types with appropriate query method
        if isinstance(assertion.expected, list):
            # Multi-row result: fetch all and compare
            rows = await conn.fetch(assertion.query)
            actual_list = [dict(row) for row in rows]
            assert actual_list == assertion.expected, (
                f"Assertion failed: {assertion.description}\n"
                f"Expected: {assertion.expected}\n"
                f"Got: {actual_list}"
            )
            return

        # For int, dict, and None, we expect a single row or no rows
        result = await conn.fetchrow(assertion.query)

        if isinstance(assertion.expected, int):
            # Count queries: expect single row with 'count' column
            assert result is not None, (
                f"Assertion failed: {assertion.description}\n"
                f"Query returned no rows (expected count: {assertion.expected})"
            )
            actual_count = result.get("count")
            assert actual_count == assertion.expected, (
                f"Assertion failed: {assertion.description}\n"
                f"Expected count: {assertion.expected}, got: {actual_count}"
            )

        elif isinstance(assertion.expected, dict):
            # Single-row result: compare as dict
            assert result is not None, (
                f"Assertion failed: {assertion.description}\n"
                f"Query returned no rows (expected: {assertion.expected})"
            )
            actual_dict = dict(result)
            for key, expected_value in assertion.expected.items():
                assert key in actual_dict, (
                    f"Assertion failed: {assertion.description}\n"
                    f"Missing key '{key}' in result: {actual_dict}"
                )
                assert actual_dict[key] == expected_value, (
                    f"Assertion failed: {assertion.description}\n"
                    f"Key '{key}': expected {expected_value}, got {actual_dict[key]}"
                )

        elif assertion.expected is None:
            # Expect no rows
            assert result is None, (
                f"Assertion failed: {assertion.description}\nExpected no rows, but got: {result}"
            )

        else:
            raise TypeError(f"Unsupported assertion.expected type: {type(assertion.expected)}")


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", SIDE_EFFECT_SCENARIOS, ids=_scenario_id)
async def test_scenario_side_effects(
    scenario: E2EScenario,
    butler_ecosystem: ButlerEcosystem,
    cost_tracker: CostTracker,
) -> None:
    """Trigger butler spawner and validate DB assertions.

    For each scenario with db_assertions:
    1. Determine target butler from expected_butler
    2. Call execute_prompt() via butler's MCP client to trigger spawner
    3. Wait for execution to complete
    4. Execute all db_assertions and validate results
    5. Track LLM usage in cost_tracker
    """
    assert scenario.expected_butler is not None, (
        f"Side-effect scenario {scenario.id} must have expected_butler set"
    )

    target_butler = scenario.expected_butler
    daemon = butler_ecosystem.butlers.get(target_butler)
    assert daemon is not None, f"Butler {target_butler} not found in ecosystem"

    port = daemon.config.port
    url = f"http://localhost:{port}/sse"

    logger.info(
        "Running side-effect scenario: %s (butler: %s, assertions: %d)",
        scenario.id,
        target_butler,
        len(scenario.db_assertions),
    )

    async with MCPClient(url) as client:
        # Trigger spawner via execute_prompt tool
        result = await client.call_tool(
            "execute_prompt",
            {"prompt": scenario.input_prompt},
        )

        logger.info("Spawner completed for %s: %s", scenario.id, result)

        # TODO: Track LLM usage from result when telemetry is available
        cost_tracker.record(input_tokens=0, output_tokens=0)

    # Execute all database assertions
    for assertion in scenario.db_assertions:
        logger.info(
            "Executing assertion for %s: %s",
            scenario.id,
            assertion.description,
        )
        await _execute_db_assertion(assertion, butler_ecosystem)

    logger.info("All assertions passed for %s", scenario.id)
