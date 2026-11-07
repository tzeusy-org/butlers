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

bu-rjdihn extends the same live-Postgres fixtures to cover the sibling
``retired_at`` soft-decommission signal (bu-6t8ix.3): retired rules must be
annotated (not hidden) in list/inspect, and excluded from active-rule counts
in stats with a separate ``retired_rules`` total.
"""

from __future__ import annotations

import shutil
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest

from butlers.api.deps import get_db
from butlers.testing.db_manager import db_manager
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


# Each test gets a fresh clone migrated by the real core + memory chains, so
# every table GET /api/memory/stats fans out to (episodes, facts, rules,
# public.consolidation_runs, ...) exists with its real shape. An
# UndefinedTableError on any one of them would drop the whole pool from the
# response as "no memory schema" (see _is_missing_memory_schema_error).


@asynccontextmanager
async def _app_client(db: object):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
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
    retired: bool = False,
) -> uuid.UUID:
    rule_id = uuid.uuid4()
    metadata = {"forgotten": True} if forgotten else {}
    await pool.execute(
        f"INSERT INTO rules (id, content, maturity, metadata, retired_at)"
        f" VALUES ($1, $2, $3, $4, {'now()' if retired else 'NULL'})",
        rule_id,
        content,
        maturity,
        metadata,
    )
    return rule_id


@pytest.mark.asyncio(loop_scope="session")
async def test_stats_maturity_buckets_exclude_forgotten_rules(db) -> None:
    """Each maturity bucket (incl. the Proven-rules KPI) excludes forgotten rules."""
    pool = await db("core", "memory")

    await _insert_rule(pool, content="live candidate", maturity="candidate")
    await _insert_rule(pool, content="forgotten candidate", maturity="candidate", forgotten=True)
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

    manager = db_manager({"memory": pool})
    async with _app_client(manager) as client:
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
async def test_list_rules_excludes_forgotten_by_default(db) -> None:
    """GET /api/memory/rules (the standing-orders register) hides forgotten rules
    unless ?forgotten=true is passed explicitly."""
    pool = await db("core", "memory")

    live_id = await _insert_rule(pool, content="live rule", maturity="proven")
    forgotten_id = await _insert_rule(
        pool, content="forgotten rule", maturity="proven", forgotten=True
    )

    manager = db_manager({"memory": pool})

    async with _app_client(manager) as client:
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
async def test_inspect_rule_search_excludes_forgotten(db) -> None:
    """GET /api/memory/inspect?kind=rule (the search bar) never surfaces a
    forgotten rule, matching the MCP recall/keyword_search convention."""
    pool = await db("core", "memory")

    live_id = await _insert_rule(pool, content="visible rule", maturity="proven")
    await _insert_rule(pool, content="hidden rule", maturity="proven", forgotten=True)

    manager = db_manager({"memory": pool})
    async with _app_client(manager) as client:
        resp = await client.get("/api/memory/inspect", params={"kind": "rule"})

    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()["data"]}
    assert ids == {str(live_id)}


@pytest.mark.asyncio(loop_scope="session")
async def test_inspect_rule_total_counts_exactly_the_rows_it_returns(
    db,
) -> None:
    """meta.total comes from a count(*) under the row filter, so a forgotten rule
    is neither returned nor counted (bu-kqnum.14.5)."""
    pool = await db("core", "memory")

    await _insert_rule(pool, content="visible rule", maturity="proven")
    await _insert_rule(pool, content="hidden rule", maturity="proven", forgotten=True)

    manager = db_manager({"memory": pool})
    async with _app_client(manager) as client:
        resp = await client.get("/api/memory/inspect", params={"kind": "rule"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == len(body["data"]) == 1


# ---------------------------------------------------------------------------
# bu-rjdihn — retired rules (retired_at, bu-6t8ix.3) are annotated, not
# hidden, in list/inspect, and excluded from active-rule counts in stats.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_stats_maturity_buckets_exclude_retired_rules(db) -> None:
    """Each maturity bucket excludes retired rules; a separate retired_rules
    field counts them instead of silently dropping them from any total."""
    pool = await db("core", "memory")

    await _insert_rule(pool, content="live candidate", maturity="candidate")
    await _insert_rule(pool, content="retired candidate", maturity="candidate", retired=True)
    await _insert_rule(pool, content="live proven", maturity="proven")
    await _insert_rule(pool, content="another live proven", maturity="proven")
    await _insert_rule(pool, content="retired proven", maturity="proven", retired=True)

    manager = db_manager({"memory": pool})
    async with _app_client(manager) as client:
        resp = await client.get("/api/memory/stats")

    assert resp.status_code == 200
    data = resp.json()["data"]

    # total_rules stays a raw table count (matches total_facts convention).
    assert data["total_rules"] == 5
    assert data["candidate_rules"] == 1
    assert data["proven_rules"] == 2
    assert data["retired_rules"] == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_list_rules_includes_retired_with_retired_at_populated(
    db,
) -> None:
    """GET /api/memory/rules (the standing-orders register) does NOT hide
    retired rules by default (unlike forgotten) — it annotates them with
    retired_at so the frontend can visually distinguish them."""
    pool = await db("core", "memory")

    live_id = await _insert_rule(pool, content="live rule", maturity="proven")
    retired_id = await _insert_rule(pool, content="retired rule", maturity="proven", retired=True)

    manager = db_manager({"memory": pool})
    async with _app_client(manager) as client:
        resp = await client.get("/api/memory/rules")

    assert resp.status_code == 200
    rows = {row["id"]: row for row in resp.json()["data"]}
    assert {str(live_id), str(retired_id)} == set(rows)
    assert rows[str(live_id)]["retired_at"] is None
    assert rows[str(retired_id)]["retired_at"] is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_inspect_rule_search_includes_retired_with_retired_at_populated(
    db,
) -> None:
    """GET /api/memory/inspect?kind=rule surfaces retired rules (unlike
    forgotten ones, which it hard-excludes) with retired_at populated."""
    pool = await db("core", "memory")

    live_id = await _insert_rule(pool, content="visible live rule", maturity="proven")
    retired_id = await _insert_rule(
        pool, content="visible retired rule", maturity="proven", retired=True
    )

    manager = db_manager({"memory": pool})
    async with _app_client(manager) as client:
        resp = await client.get("/api/memory/inspect", params={"kind": "rule"})

    assert resp.status_code == 200
    rows = {row["id"]: row for row in resp.json()["data"]}
    assert {str(live_id), str(retired_id)} == set(rows)
    assert rows[str(retired_id)]["rule"]["retired_at"] is not None
    assert rows[str(live_id)]["rule"]["retired_at"] is None
