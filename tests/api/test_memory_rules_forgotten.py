"""bu-5ud8p.2 — forgotten rules must not count as live beliefs.

Rules have no ``validity`` column (unlike facts, which got one in bu-5ud8p.1);
the sole soft-delete signal is the JSONB ``metadata->>'forgotten'`` flag
written by ``forget_memory`` and the decay sweep's terminal expiry branch
(``src/butlers/modules/memory/storage.py``). Several readers disagreed on
whether to honor it:

- ``GET /api/memory/stats`` (candidate/established/proven/anti_pattern_rules,
  including the dashboard's "Proven rules" KPI) counted forgotten rules as
  live.
- ``GET /api/memory/rules`` (the standing-orders register / list surface)
  returned forgotten rules mixed in with live ones.
- ``GET /api/memory/inspect?kind=rule`` (the search bar) surfaced forgotten
  rules in search results.
- The ``memory_stats`` MCP tool's ``anti_pattern`` bucket didn't exclude
  forgotten rules even though its sibling maturity buckets already did
  (covered by ``tests/modules/memory/test_tools_management.py``).

This exercises the real query text against a live Postgres instance (not the
substring-matching mocks in test_memory.py) so a regression that silently
drops the ``metadata->>'forgotten'`` predicate is actually caught.
"""

from __future__ import annotations

import shutil
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.routers.memory import _get_db_manager

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


# Minimal subset of the real rules schema (001_memory_schema.py) covering
# every column the stats/list/inspect handlers select or filter on, plus the
# episodes/facts/consolidation_runs tables GET /api/memory/stats also queries
# in the same fan-out (an UndefinedTableError on any one of them drops the
# whole pool from the response as "no memory schema" — see
# _is_missing_memory_schema_error — so all four must exist for a meaningful
# stats assertion, mirroring test_memory_stats_consolidation_e2e.py).
_RULES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS episodes (
    id                   BIGSERIAL PRIMARY KEY,
    consolidated         BOOLEAN NOT NULL DEFAULT false,
    consolidation_status TEXT    NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS facts (
    id       BIGSERIAL PRIMARY KEY,
    validity TEXT NOT NULL DEFAULT 'active'
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
CREATE TABLE IF NOT EXISTS rules (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content              TEXT NOT NULL,
    search_vector        tsvector,
    scope                TEXT NOT NULL DEFAULT 'global',
    maturity             TEXT NOT NULL DEFAULT 'candidate',
    confidence           FLOAT NOT NULL DEFAULT 0.5,
    decay_rate           FLOAT NOT NULL DEFAULT 0.008,
    permanence           TEXT NOT NULL DEFAULT 'standard',
    effectiveness_score  FLOAT NOT NULL DEFAULT 0.0,
    applied_count        INTEGER NOT NULL DEFAULT 0,
    success_count        INTEGER NOT NULL DEFAULT 0,
    harmful_count        INTEGER NOT NULL DEFAULT 0,
    source_episode_id    UUID,
    source_butler        TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_applied_at      TIMESTAMPTZ,
    last_evaluated_at    TIMESTAMPTZ,
    tags                 JSONB DEFAULT '[]'::jsonb,
    metadata             JSONB DEFAULT '{}'::jsonb
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


async def _insert_rule(
    pool,
    *,
    content: str,
    maturity: str,
    forgotten: bool = False,
) -> uuid.UUID:
    rule_id = uuid.uuid4()
    metadata = {"forgotten": True} if forgotten else {}
    await pool.execute(
        "INSERT INTO rules (id, content, maturity, metadata) VALUES ($1, $2, $3, $4)",
        rule_id,
        content,
        maturity,
        metadata,
    )
    return rule_id


@pytest.mark.asyncio(loop_scope="session")
async def test_stats_maturity_buckets_exclude_forgotten_rules(provisioned_postgres_pool) -> None:
    """Each maturity bucket (incl. the Proven-rules KPI) excludes forgotten rules."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_RULES_SCHEMA_SQL)

        await _insert_rule(pool, content="live candidate", maturity="candidate")
        await _insert_rule(
            pool, content="forgotten candidate", maturity="candidate", forgotten=True
        )
        await _insert_rule(pool, content="live established", maturity="established")
        await _insert_rule(
            pool, content="forgotten established", maturity="established", forgotten=True
        )
        await _insert_rule(pool, content="live proven", maturity="proven")
        await _insert_rule(pool, content="another live proven", maturity="proven")
        await _insert_rule(pool, content="forgotten proven", maturity="proven", forgotten=True)
        await _insert_rule(pool, content="live anti-pattern", maturity="anti_pattern")
        await _insert_rule(
            pool, content="forgotten anti-pattern", maturity="anti_pattern", forgotten=True
        )

        db = _SinglePoolDB("memory", pool)
        async with _app_client(db) as client:
            resp = await client.get("/api/memory/stats")

        assert resp.status_code == 200
        data = resp.json()["data"]

        # total_rules stays a raw table count (matches total_facts convention).
        assert data["total_rules"] == 9
        assert data["candidate_rules"] == 1
        assert data["established_rules"] == 1
        # The dashboard "Proven rules" KPI (MemoryOverture) reads this field.
        assert data["proven_rules"] == 2
        assert data["anti_pattern_rules"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_list_rules_excludes_forgotten_by_default(provisioned_postgres_pool) -> None:
    """GET /api/memory/rules (the standing-orders register) hides forgotten rules
    unless ?forgotten=true is passed explicitly."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_RULES_SCHEMA_SQL)

        live_id = await _insert_rule(pool, content="live rule", maturity="proven")
        forgotten_id = await _insert_rule(
            pool, content="forgotten rule", maturity="proven", forgotten=True
        )

        db = _SinglePoolDB("memory", pool)

        async with _app_client(db) as client:
            default_resp = await client.get("/api/memory/rules")
            forgotten_resp = await client.get("/api/memory/rules", params={"forgotten": "true"})

        assert default_resp.status_code == 200
        default_ids = {row["id"] for row in default_resp.json()["data"]}
        assert str(live_id) in default_ids
        assert str(forgotten_id) not in default_ids
        assert default_resp.json()["meta"]["total"] == 1

        assert forgotten_resp.status_code == 200
        forgotten_ids = {row["id"] for row in forgotten_resp.json()["data"]}
        assert forgotten_ids == {str(forgotten_id)}


@pytest.mark.asyncio(loop_scope="session")
async def test_inspect_rule_search_excludes_forgotten(provisioned_postgres_pool) -> None:
    """GET /api/memory/inspect?kind=rule (the search bar) never surfaces a
    forgotten rule, matching the MCP recall/keyword_search convention."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_RULES_SCHEMA_SQL)

        live_id = await _insert_rule(pool, content="visible rule", maturity="proven")
        await _insert_rule(pool, content="hidden rule", maturity="proven", forgotten=True)

        db = _SinglePoolDB("memory", pool)
        async with _app_client(db) as client:
            resp = await client.get("/api/memory/inspect", params={"kind": "rule"})

        assert resp.status_code == 200
        ids = {row["id"] for row in resp.json()["data"]}
        assert ids == {str(live_id)}
