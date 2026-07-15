"""DB-level regression for the canonical-only audit-log read path (bu-j26e8).

The audit-unify epic finished by making every read surface query the canonical
``public.audit_log`` primitive ALONE.  The historical Switchboard
``dashboard_audit_log`` rows were copied into ``public.audit_log`` by migration
core_124, so the live legacy UNION arm was removed (bu-j26e8) — reads no longer
touch ``dashboard_audit_log`` at all.  (Earlier in the epic, .2/bu-fyal7 had
these surfaces UNION both tables during the writer transition; this module
supersedes those live-UNION assertions.)

The unit tests in ``test_audit_grouping.py`` only assert on the SQL string shape;
they never execute it.  This module runs the *actual* grouping query and the
*actual* egress-catalog query against a migrated Postgres (core + switchboard
chains, flat ``public`` topology) and asserts:

  1. An error row in ``public.audit_log`` appears in the issues/briefing
     grouping.
  2. Error-grouping semantics:
     - ``result='error'`` rows are included, ``result='success'`` rows excluded.
     - schedule detection (``operation='session'`` +
       ``request_summary->>'trigger_source' LIKE 'schedule:%'``) classifies a
       canonical row as a scheduled failure.
  3. A row present ONLY in the legacy ``dashboard_audit_log`` is NOT read live
     (it would surface only after the core_124 backfill — covered by the
     migration test, not a live UNION here).
  4. The egress catalog counts operations from ``public.audit_log`` only
     (``action`` -> ``operation``); legacy-only rows are not counted live.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.audit_grouping import (
    build_audit_group_occurrences_query,
    build_audit_group_query,
    issue_from_audit_group_row,
)
from butlers.api.db import DatabaseManager
from butlers.api.routers import system as system_module
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

BASE_URL = "http://test"
EGRESS_PATH = "/api/system/egress"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the core chain (public.audit_log + metadata/result/error from
    core_122) and the switchboard chain (dashboard_audit_log).  Both land in
    ``public`` (flat topology), mirroring what the read surfaces query."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
    )


@pytest.fixture
async def pool(postgres_container, migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE public.audit_log CASCADE")
    # The owner-singleton partial unique index on public.entities persists across
    # tests; clear identity rows so each test can seed a fresh owner.
    await p.execute("TRUNCATE TABLE public.entities CASCADE")
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Insert helpers (exact column shapes for each table)
# ---------------------------------------------------------------------------


async def _insert_canonical(
    pool: asyncpg.Pool,
    *,
    actor: str,
    action: str,
    result: str | None = None,
    error: str | None = None,
    metadata: dict | None = None,
    ts: datetime | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO public.audit_log (actor, action, result, error, metadata, ts)
        VALUES ($1, $2, $3, $4, $5::jsonb, COALESCE($6, now()))
        """,
        actor,
        action,
        result,
        error,
        metadata,
        ts,
    )


# ---------------------------------------------------------------------------
# Grouping query (issues + briefing)
# ---------------------------------------------------------------------------


async def test_canonical_only_error_appears_in_grouping(pool: asyncpg.Pool) -> None:
    """An error row written ONLY to public.audit_log must surface in the grouping
    that previously read dashboard_audit_log exclusively."""
    await _insert_canonical(
        pool,
        actor="health",
        action="session",
        result="error",
        error="DB connection timeout",
    )

    rows = await pool.fetch(build_audit_group_query())
    issues = [issue_from_audit_group_row(r) for r in rows]
    assert any("DB connection timeout" in i.error_message for i in issues), (
        f"canonical-only error invisible in grouping; got {[i.error_message for i in issues]}"
    )


# NOTE: the pre-sw_026 "legacy-only error not read live" negative-contract test
# was removed by bu-o699b together with the ``_insert_legacy`` helper: switchboard
# migration sw_026 drops the legacy dashboard_audit_log table, so a legacy-only
# row can no longer be written — the canonical-only grouping contract is now
# guaranteed structurally by the table's non-existence.


async def test_grouping_reads_canonical_only(pool: asyncpg.Pool) -> None:
    """Only public.audit_log error rows are returned; success rows are filtered.

    (The legacy dashboard_audit_log table was dropped by sw_026, so there is no
    live UNION to leak legacy rows — the canonical-only contract is structural.)
    """
    await _insert_canonical(
        pool, actor="health", action="session", result="error", error="canonical-err"
    )
    await _insert_canonical(pool, actor="health", action="rotated", result="success", error=None)

    rows = await pool.fetch(build_audit_group_query())
    summaries = {r["error_summary"] for r in rows}
    assert "canonical-err" in summaries
    # success rows contribute no group
    assert all(s not in {"", None} for s in summaries)
    assert len(rows) == 1


async def test_canonical_scheduled_failure_classified_critical(pool: asyncpg.Pool) -> None:
    """Schedule detection (operation='session' + trigger_source 'schedule:NAME')
    survives the UNION when the row comes from public.audit_log's metadata."""
    await _insert_canonical(
        pool,
        actor="calendar",
        action="session",
        result="error",
        error="OAuth token expired",
        metadata={"trigger_source": "schedule:daily-sync"},
    )
    rows = await pool.fetch(build_audit_group_query())
    issues = [issue_from_audit_group_row(r) for r in rows]
    target = next(i for i in issues if "OAuth token expired" in i.error_message)
    assert target.severity == "critical"
    assert target.type == "scheduled_task_failure:daily-sync"


async def test_where_extra_window_filters_unified_source(pool: asyncpg.Pool) -> None:
    """The created_at window filter injected by the briefing applies across the
    UNION (ts maps to created_at on the canonical side)."""
    old = datetime(2020, 1, 1, tzinfo=UTC)
    await _insert_canonical(
        pool,
        actor="health",
        action="session",
        result="error",
        error="stale-canonical-err",
        ts=old,
    )
    await _insert_canonical(
        pool,
        actor="health",
        action="session",
        result="error",
        error="fresh-canonical-err",
    )
    sql = build_audit_group_query(
        where_extra="\n                  AND created_at >= NOW() - INTERVAL '24 hours'"
    )
    rows = await pool.fetch(sql)
    summaries = {r["error_summary"] for r in rows}
    assert "fresh-canonical-err" in summaries
    assert "stale-canonical-err" not in summaries


# ---------------------------------------------------------------------------
# Egress catalog (system.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def egress_app(pool: asyncpg.Pool) -> FastAPI:
    """Wire the system router's switchboard pool to the migrated flat-public DB."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    application = create_app()
    application.dependency_overrides[system_module._get_db_manager] = lambda: mock_db
    return application


async def _seed_owner(pool: asyncpg.Pool) -> None:
    """The egress endpoint gates on an owner entity; create one.

    post-core_134: public.contacts is dropped. The owner assertion reads
    public.entities.roles directly ('owner' = ANY(roles)).
    """
    await pool.execute(
        "INSERT INTO public.entities (canonical_name, roles) VALUES ($1, ARRAY['owner'])",
        "Owner",
    )


async def _get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        return await client.get(path)


async def test_egress_counts_canonical_only(pool: asyncpg.Pool, egress_app: FastAPI) -> None:
    """The egress catalog aggregates llm_api_call counts from public.audit_log
    ONLY (action -> operation).  The legacy dashboard_audit_log table was dropped
    by sw_026, so there is no live UNION arm to over-count (bu-j26e8 removed the
    read arm; bu-o699b dropped the table)."""
    await _seed_owner(pool)
    # 3 canonical rows whose action maps onto the operation grouping key.
    for _ in range(3):
        await _insert_canonical(pool, actor="x", action="llm_api_call", result="success")

    resp = await _get(egress_app, EGRESS_PATH)
    assert resp.status_code == 200
    actors = resp.json()["data"]["actors"]
    claude = next((a for a in actors if a["actor_id"] == "anthropic.claude"), None)
    assert claude is not None
    assert claude["total_calls"] == 3, (
        f"egress should count canonical rows only; expected 3, got {claude['total_calls']}"
    )


async def test_egress_canonical_only_operation_visible(
    pool: asyncpg.Pool, egress_app: FastAPI
) -> None:
    """An operation present only in public.audit_log appears in the catalog."""
    await _seed_owner(pool)
    await _insert_canonical(pool, actor="x", action="llm_api_call", result="success")

    resp = await _get(egress_app, EGRESS_PATH)
    assert resp.status_code == 200
    actors = resp.json()["data"]["actors"]
    assert any(a["actor_id"] == "anthropic.claude" for a in actors)


# ---------------------------------------------------------------------------
# Occurrences drill-down (JARVIS audit move 6, bu-qvnce.6)
# ---------------------------------------------------------------------------


async def test_occurrences_query_does_not_undercount_mixed_schedule_group(
    pool: asyncpg.Pool,
) -> None:
    """Real-Postgres regression: a group can straddle both scheduled and
    non-scheduled rows behind the same normalized ``error_summary`` --
    ``grouped_errors`` groups by ``error_summary`` ALONE, and ``has_schedule``
    is only a ``BOOL_OR`` aggregate over that group, not part of its identity.

    Filtering the occurrences drill-down on the group's aggregated
    ``has_schedule`` flag (the pre-fix behavior) silently drops the rows on
    the other side of that flag, while the group's reported ``occurrences``
    count keeps including them -- the drill-down page permanently undercounts
    relative to the total it claims, no matter how many pages are fetched.
    """
    await _insert_canonical(
        pool,
        actor="calendar",
        action="session",
        result="error",
        error="Connection timeout",
        metadata={"trigger_source": "schedule:daily-sync"},
    )
    await _insert_canonical(
        pool,
        actor="calendar",
        action="manual_trigger",
        result="error",
        error="Connection timeout",
        metadata={},
    )

    group_rows = await pool.fetch(build_audit_group_query())
    assert len(group_rows) == 1
    group = group_rows[0]
    assert group["error_summary"] == "Connection timeout"
    total = int(group["occurrences"])
    assert total == 2
    butlers = [str(b) for b in group["butlers"]]

    occurrences_sql = build_audit_group_occurrences_query()
    occ_rows = await pool.fetch(occurrences_sql, str(group["error_summary"]), butlers, 50, 0)
    assert len(occ_rows) == total, (
        f"occurrences drill-down returned {len(occ_rows)} rows but the group "
        f"reports occurrences={total} -- a scheduled/non-scheduled mix under "
        "the same error_summary is being undercounted"
    )
