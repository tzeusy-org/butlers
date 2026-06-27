"""Real-Postgres tests for the education analytics -> curriculum_replan feedback loop.

Spec: ``module-education-analytics`` "Feedback Loop Trigger". The nightly analytics
job (``_run_education_compute_analytics_snapshots_job``) must wire a
``curriculum_replan`` callback into ``analytics_compute_all`` so that a freshly
computed snapshot triggers a re-plan when ``len(struggling_nodes) >= 3`` OR
``retention_rate_7d < 0.60``.

These tests exercise the real wiring
``scheduled_jobs -> analytics_compute_all -> callback`` against real Postgres
(the analytics queries run under the ``education`` schema). The terminal
``curriculum_replan`` tool is stubbed so the assertion is about whether the
wiring *invokes* it with the right ``(mind_map_id, metrics)`` — the tool itself
has its own (separately tracked) schema dependencies and is not the unit under
test here.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.scheduled_jobs import _run_education_compute_analytics_snapshots_job
from butlers.testing.migration import create_migrated_test_db, migration_db_name

_docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
]

_REPLAN_TARGET = "butlers.tools.education.curriculum.curriculum_replan"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core + education migrations applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "education"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    """Return an asyncpg pool; truncate education tables between tests."""
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    # Child tables first so FK cascades are satisfied.
    await p.execute(
        "TRUNCATE TABLE education.quiz_responses, education.analytics_snapshots, "
        "education.mind_map_edges, education.mind_map_nodes, education.mind_maps CASCADE"
    )
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_map(pool: asyncpg.Pool, *, status: str = "active") -> str:
    return await pool.fetchval(
        "INSERT INTO education.mind_maps (title, status) VALUES ($1, $2) RETURNING id::text",
        "Test Topic",
        status,
    )


async def _insert_node(
    pool: asyncpg.Pool,
    map_id: str,
    *,
    label: str,
    mastery_status: str = "learning",
) -> str:
    return await pool.fetchval(
        """
        INSERT INTO education.mind_map_nodes (mind_map_id, label, mastery_status)
        VALUES ($1, $2, $3)
        RETURNING id::text
        """,
        map_id,
        label,
        mastery_status,
    )


async def _insert_reviews(
    pool: asyncpg.Pool,
    map_id: str,
    node_id: str,
    qualities: list[int],
    *,
    days_ago: int,
) -> None:
    """Insert review-type responses for a node, anchored ``days_ago`` in the past."""
    responded_at = datetime.now(tz=UTC) - timedelta(days=days_ago)
    for q in qualities:
        await pool.execute(
            """
            INSERT INTO education.quiz_responses
                (node_id, mind_map_id, question_text, quality, response_type, responded_at)
            VALUES ($1, $2, $3, $4, 'review', $5)
            """,
            node_id,
            map_id,
            "q?",
            q,
            responded_at,
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_feedback_loop_fires_when_struggling_nodes_reach_threshold(pool):
    """3+ struggling nodes -> the nightly job invokes curriculum_replan for that map.

    Reviews are placed 30 days in the past so ``retention_rate_7d`` is None (no
    reviews in the 7-day window); the trigger here is the struggling-node count
    alone, isolating the first branch of the spec's OR condition.
    """
    map_id = await _insert_map(pool)
    for i in range(3):
        node_id = await _insert_node(pool, map_id, label=f"concept-{i}")
        # last-5 review avg = 1.0 (< 2.5) with cnt == 5 -> struggling
        await _insert_reviews(pool, map_id, node_id, [1, 1, 1, 1, 1], days_ago=30)

    with patch(_REPLAN_TARGET, new=AsyncMock()) as mock_replan:
        result = await _run_education_compute_analytics_snapshots_job(pool, None)

    assert result["snapshots_computed"] == 1
    assert result["replans_triggered"] == 1
    mock_replan.assert_awaited_once()
    call = mock_replan.await_args
    # curriculum_replan(pool, mind_map_id, reason=...)
    assert call.args[1] == map_id


async def test_feedback_loop_fires_when_retention_below_threshold(pool):
    """retention_rate_7d < 0.60 with <3 struggling nodes still fires the loop."""
    map_id = await _insert_map(pool)
    node_id = await _insert_node(pool, map_id, label="concept-0")
    # Recent reviews (within 7d): passed (q>=3) = 1 of 5 -> retention 0.2 < 0.60.
    # mean quality 1.8 (< 2.5) -> this is the ONLY struggling node (count == 1 < 3).
    await _insert_reviews(pool, map_id, node_id, [1, 2, 2, 1, 3], days_ago=1)

    with patch(_REPLAN_TARGET, new=AsyncMock()) as mock_replan:
        result = await _run_education_compute_analytics_snapshots_job(pool, None)

    assert result["replans_triggered"] == 1
    mock_replan.assert_awaited_once()
    call = mock_replan.await_args
    assert call.args[1] == map_id
    # The trigger context (the metric values that breached the threshold) is
    # threaded into curriculum_replan's ``reason`` for observability; the tool
    # itself re-reads mastery state from the DB.
    reason = call.kwargs["reason"]
    assert "struggling_nodes=1" in reason
    assert "retention_rate_7d=0.2" in reason


async def test_feedback_loop_does_not_fire_when_thresholds_not_breached(pool):
    """Healthy map (no struggling nodes, no failing retention) computes a snapshot
    but does NOT invoke curriculum_replan."""
    map_id = await _insert_map(pool)
    node_id = await _insert_node(pool, map_id, label="concept-0")
    # 5 strong recent reviews: struggling avg 4.4 (>= 2.5), retention 1.0 (>= 0.60).
    await _insert_reviews(pool, map_id, node_id, [4, 5, 4, 5, 4], days_ago=1)

    with patch(_REPLAN_TARGET, new=AsyncMock()) as mock_replan:
        result = await _run_education_compute_analytics_snapshots_job(pool, None)

    assert result["snapshots_computed"] == 1
    assert result["replans_triggered"] == 0
    mock_replan.assert_not_awaited()


async def test_replan_failure_does_not_abort_remaining_maps(pool):
    """A curriculum_replan error for one map is swallowed so the job still completes
    (and reports zero successful replans) rather than crashing the nightly run."""
    map_id = await _insert_map(pool)
    for i in range(3):
        node_id = await _insert_node(pool, map_id, label=f"concept-{i}")
        await _insert_reviews(pool, map_id, node_id, [1, 1, 1, 1, 1], days_ago=30)

    boom = AsyncMock(side_effect=RuntimeError("replan blew up"))
    with patch(_REPLAN_TARGET, new=boom):
        result = await _run_education_compute_analytics_snapshots_job(pool, None)

    # Snapshot still computed; failed replan counted as not-triggered, no exception.
    assert result["snapshots_computed"] == 1
    assert result["replans_triggered"] == 0
    boom.assert_awaited_once()
