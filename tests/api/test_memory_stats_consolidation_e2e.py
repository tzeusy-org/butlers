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
(search-path scoped) plus ``public.consolidation_runs``. We create the minimal
tables the handler touches so the assertion is about the stats wiring, not the
full memory migration chain.
"""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.routers.memory import _get_db_manager
from butlers.modules.memory.consolidation import _record_consolidation_run

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


# Minimal subset of the memory schema that GET /api/memory/stats reads. These
# mirror the only columns the stats fan-out queries reference.
_STATS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS episodes (
    id                   BIGSERIAL PRIMARY KEY,
    consolidated         BOOLEAN NOT NULL DEFAULT false,
    consolidation_status TEXT    NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS facts (
    id       BIGSERIAL PRIMARY KEY,
    validity TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS rules (
    id       BIGSERIAL PRIMARY KEY,
    maturity TEXT NOT NULL DEFAULT 'candidate'
);
CREATE TABLE IF NOT EXISTS public.consolidation_runs (
    id                 BIGSERIAL PRIMARY KEY,
    butler             TEXT NOT NULL,
    consolidated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    episodes_processed INT NOT NULL DEFAULT 0,
    facts_produced     INT NOT NULL DEFAULT 0,
    facts_updated      INT NOT NULL DEFAULT 0,
    rules_created      INT NOT NULL DEFAULT 0,
    confirmations_made INT NOT NULL DEFAULT 0,
    errors             INT NOT NULL DEFAULT 0
);
"""


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
    app.dependency_overrides[_get_db_manager] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio(loop_scope="session")
async def test_consolidation_run_populates_stats_fields(provisioned_postgres_pool) -> None:
    """A recorded consolidation run surfaces through GET /api/memory/stats."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_STATS_SCHEMA_SQL)

        # Seed memory rows so the non-consolidation stats are non-trivial and a
        # dead_letter episode is present for the dead_letter_episodes count.
        await pool.execute(
            "INSERT INTO episodes (consolidated, consolidation_status) VALUES "
            "(true, 'consolidated'), (false, 'pending'), (false, 'dead_letter')"
        )
        await pool.execute("INSERT INTO facts (validity) VALUES ('active'), ('fading')")
        await pool.execute("INSERT INTO rules (maturity) VALUES ('candidate')")

        db = _SinglePoolDB("memory", pool)

        # Baseline: no consolidation runs yet → consolidation fields are null,
        # while the seeded counts already populate.
        async with _app_client(db) as client:
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
        async with _app_client(db) as client:
            resp = await client.get("/api/memory/stats")
        assert resp.status_code == 200
        after = resp.json()["data"]

        assert after["last_consolidation_at"] is not None
        assert after["last_consolidation_facts_produced"] == 9
        # Stable counts are unchanged by recording a run.
        assert after["dead_letter_episodes"] == 1
        assert after["total_episodes"] == 3
