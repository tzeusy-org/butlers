"""End-to-end test: a consolidation run writes ``public.consolidation_runs``
and ``GET /api/memory/stats`` then populates its consolidation fields from it.

This closes the gap from the /memory closeout audit (bu-ezg4r): the existing
suite has unit coverage for the stats fan-out (mocked pools) and for the
``consolidation_runs`` migration shape, but nothing that exercises the full
write → read path against a live PostgreSQL instance:

    real _record_consolidation_run() INSERT
        → public.consolidation_runs row
        → real GET /api/memory/stats handler SELECT
        → populated last_consolidation_* / dead_letter_episodes fields

The endpoint reads the unqualified ``episodes`` / ``facts`` / ``rules`` tables
(search-path scoped) plus ``public.consolidation_runs``. Each test runs on a
fresh clone migrated by the real core + memory chains, so the rows seeded here
satisfy the real columns and constraints.
"""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager

import httpx
import pytest

from butlers.api.deps import get_db
from butlers.modules.memory.consolidation import _record_consolidation_run
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


class _SinglePoolDB:
    """DatabaseManager stand-in exposing one real pool under one butler name."""

    def __init__(self, butler: str, pool: object) -> None:
        self._butler = butler
        self._pool = pool
        self.butler_names = [butler]

    def pool(self, name: str) -> object:
        if name != self._butler:
            raise KeyError(f"No pool for butler: {name}")
        return self._pool


@asynccontextmanager
async def _app_client(db: object):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio(loop_scope="session")
async def test_consolidation_run_populates_stats_fields(db) -> None:
    """A recorded consolidation run surfaces through GET /api/memory/stats."""
    pool = await db("core", "memory")

    # Seed memory rows so the non-consolidation stats are non-trivial and a
    # dead_letter episode is present for the dead_letter_episodes count.
    await pool.execute(
        "INSERT INTO episodes (butler, content, consolidated, consolidation_status) VALUES "
        "('memory', 'e1', true, 'consolidated'), ('memory', 'e2', false, 'pending'), "
        "('memory', 'e3', false, 'dead_letter')"
    )
    await pool.execute(
        "INSERT INTO facts (subject, predicate, content, validity) VALUES "
        "('owner', 'stats_active', 'a', 'active'), ('owner', 'stats_fading', 'f', 'fading')"
    )
    await pool.execute("INSERT INTO rules (content, maturity) VALUES ('r1', 'candidate')")

    manager = _SinglePoolDB("memory", pool)

    # Baseline: no consolidation runs yet → consolidation fields are null,
    # while the seeded counts already populate.
    async with _app_client(manager) as client:
        resp = await client.get("/api/memory/stats")
    assert resp.status_code == 200
    baseline = resp.json()["data"]
    assert baseline["last_consolidation_at"] is None
    assert baseline["last_consolidation_facts_produced"] is None
    assert baseline["total_episodes"] == 3
    assert baseline["dead_letter_episodes"] == 1
    assert baseline["total_facts"] == 2
    assert baseline["active_facts"] == 1
    assert baseline["fading_facts"] == 1
    assert baseline["total_rules"] == 1

    # Record a real consolidation run through the production writer.
    await _record_consolidation_run(
        pool,
        butler="memory",
        episodes_processed=4,
        facts_produced=9,
        facts_updated=2,
        rules_created=1,
        confirmations_made=3,
        errors=0,
    )

    # The audit row must actually exist (writer is best-effort / swallows).
    run_count = await pool.fetchval("SELECT count(*) FROM public.consolidation_runs")
    assert run_count == 1, "consolidation run was not recorded"

    # Now the stats endpoint must surface that run.
    async with _app_client(manager) as client:
        resp = await client.get("/api/memory/stats")
    assert resp.status_code == 200
    after = resp.json()["data"]

    assert after["last_consolidation_at"] is not None
    assert after["last_consolidation_facts_produced"] == 9
    # Stable counts are unchanged by recording a run.
    assert after["dead_letter_episodes"] == 1
    assert after["total_episodes"] == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_stats_reports_complete_expired_retention_observation(
    db,
) -> None:
    """The read-only stats route counts exactly the cleanup population.

    ``expired_retained_episodes`` reports genuine un-reaped lag — the same
    consolidation-aware set ``run_episode_cleanup`` would delete — so an episode
    the sweep is deliberately still holding (expired but ``pending`` within the
    grace window) must NOT be counted, even though it is retention-eligible.
    """
    pool = await db("core", "memory")
    await pool.execute(
        "INSERT INTO episodes (butler, content, consolidation_status, expires_at) VALUES "
        # Reapable expired lag (non-pending) → counted.
        "('memory', 'e1', 'consolidated', now() - interval '1 hour'), "
        "('memory', 'e2', 'failed', now() - interval '1 hour'), "
        # Expired but pending within grace → intentionally retained, NOT counted.
        "('memory', 'e3', 'pending', now() - interval '1 hour'), "
        # Not expired → eligible but not retained.
        "('memory', 'e4', 'consolidated', now() + interval '1 hour'), "
        # No TTL → not eligible.
        "('memory', 'e5', 'pending', NULL)"
    )
    manager = _SinglePoolDB("memory", pool)

    async with _app_client(manager) as client:
        resp = await client.get("/api/memory/stats")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["expired_retained_episodes"] == 2
    assert body["data"]["retention_eligible_episodes"] == 4
    assert body["data"]["expired_retained_ratio"] == 0.5
    assert body["meta"]["retention_status"] == "degraded"
    assert body["meta"]["retention_sources"] == [
        {
            "source_butler": "memory",
            "source_schema": None,
            "expired_retained_episodes": 2,
            "retention_eligible_episodes": 4,
            "expired_retained_ratio": 0.5,
        }
    ]
