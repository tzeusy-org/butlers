"""DB-level regression test: legacy dashboard_audit_log mutations are visible on
/audit-log (bu-isi4i / bu-isi4i.1).

Background
----------
Several mutation sites write ONLY to the Switchboard butler's
``dashboard_audit_log`` table (via ``log_audit_entry`` /
``emit_dashboard_audit``):

  * src/butlers/core/runtime_config.py        (runtime config / model changes)
  * src/butlers/api/routers/schedules.py      (schedule create/update/delete/…)
  * src/butlers/api/routers/state.py          (state set/delete)
  * src/butlers/api/routers/calendar_workspace.py
  * src/butlers/api/dashboard_audit_middleware.py (every non-GET mutation)

The /audit-log read endpoint reads ``public.audit_log`` and so these mutations
were INVISIBLE on the page — a split-brained audit log.

The unit tests in ``test_audit_log.py`` mock the DB pools and therefore cannot
catch this: they never exercise the real two-table topology.  This test runs the
*actual* read endpoint against a migrated Postgres (core + switchboard chains)
and asserts that a row written by the orphaned ``log_audit_entry`` writer (the
exact helper ``state.py`` / ``schedules.py`` call) shows up in the response.

Pre-fix: the row is invisible (endpoint reads only public.audit_log) → fails.
Post-fix: the endpoint UNIONs dashboard_audit_log → the row is returned.
"""

from __future__ import annotations

import shutil
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers import audit as audit_module
from butlers.api.routers.audit import log_audit_entry
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

AUDIT_PATH = "/api/audit-log"
BASE_URL = "http://test"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the core chain (public.audit_log) and the switchboard chain
    (dashboard_audit_log).  Both land in ``public`` (flat topology), mirroring
    the schemas the read endpoint queries."""
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
    await p.execute("TRUNCATE TABLE dashboard_audit_log CASCADE")
    yield p
    await p.close()


@pytest.fixture
def audit_app(pool: asyncpg.Pool) -> FastAPI:
    """FastAPI app whose audit router is wired to the real migrated pool.

    The credential-shared pool (public.audit_log) and the ``switchboard`` pool
    (dashboard_audit_log) both resolve to the same flat-public test DB, exactly
    as the read endpoint expects in a single-database deployment.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = pool
    mock_db.pool.return_value = pool

    application = create_app()
    application.dependency_overrides[audit_module._get_db_manager] = lambda: mock_db
    return application


async def _get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        return await client.get(path)


async def test_legacy_dashboard_audit_row_visible_on_audit_log(
    pool: asyncpg.Pool, audit_app: FastAPI
) -> None:
    """A mutation written via log_audit_entry (dashboard_audit_log) appears on
    /audit-log.  This is the bu-isi4i split-brain regression."""
    # Write a row exactly as state.py / schedules.py do — only to
    # dashboard_audit_log, never to public.audit_log.
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    await log_audit_entry(
        mock_db,
        butler="qa",
        operation="schedule.create",
        request_summary={"method": "POST", "path": "/api/qa/schedules"},
    )

    # Sanity: nothing was written to the canonical table.
    canonical_count = await pool.fetchval("SELECT count(*) FROM public.audit_log")
    assert canonical_count == 0
    legacy_count = await pool.fetchval("SELECT count(*) FROM dashboard_audit_log")
    assert legacy_count == 1

    resp = await _get(audit_app, AUDIT_PATH)
    assert resp.status_code == 200
    body = resp.json()

    # The legacy mutation must now be visible.
    actions = [e["action"] for e in body["data"]]
    assert "schedule.create" in actions, (
        "dashboard_audit_log mutation is invisible on /audit-log — "
        f"split-brain not fixed. Got actions={actions}"
    )
    assert body["meta"]["total"] == 1

    entry = next(e for e in body["data"] if e["action"] == "schedule.create")
    # log_audit_entry writes an empty user_context, so actor falls back to the
    # butler name (no user_context.principal to override it).
    assert entry["actor"] == "qa"
    assert entry["target"] == "/api/qa/schedules"


async def test_canonical_and_legacy_rows_merged_ts_desc(
    pool: asyncpg.Pool, audit_app: FastAPI
) -> None:
    """Canonical (public.audit_log) and legacy rows interleave by ts DESC and
    the combined total is correct."""
    # Canonical row (older).
    await pool.execute(
        "INSERT INTO public.audit_log (actor, action, ts) "
        "VALUES ($1, $2, now() - interval '1 hour')",
        "owner",
        "model_priority_change",
    )
    # Legacy row (newer).
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    await log_audit_entry(
        mock_db,
        butler="qa",
        operation="state.set",
        request_summary={"method": "PUT", "path": "/api/qa/state/foo"},
    )

    resp = await _get(audit_app, AUDIT_PATH)
    assert resp.status_code == 200
    body = resp.json()

    actions = [e["action"] for e in body["data"]]
    assert actions[0] == "state.set"  # newest first
    assert "model_priority_change" in actions
    assert body["meta"]["total"] == 2


async def test_action_filter_matches_legacy_operation(
    pool: asyncpg.Pool, audit_app: FastAPI
) -> None:
    """?action= filters legacy rows on their operation label."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    await log_audit_entry(mock_db, butler="qa", operation="schedule.delete", request_summary={})
    await log_audit_entry(mock_db, butler="qa", operation="state.delete", request_summary={})

    resp = await _get(audit_app, f"{AUDIT_PATH}?action=schedule.delete")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 1
    assert body["data"][0]["action"] == "schedule.delete"


async def test_key_filter_excludes_legacy_rows(pool: asyncpg.Pool, audit_app: FastAPI) -> None:
    """The credential-key filter (?key=) must never match legacy rows (they have
    no credential target), preserving its exact semantics."""
    # Legacy row — has no credential target.
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    await log_audit_entry(mock_db, butler="qa", operation="schedule.create", request_summary={})
    # Canonical row with a credential target.
    await pool.execute(
        "INSERT INTO public.audit_log (actor, action, target) VALUES ($1, $2, $3)",
        "owner",
        "rotated",
        "u:google",
    )

    resp = await _get(audit_app, f"{AUDIT_PATH}?key=u:google")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 1
    assert body["data"][0]["target"] == "u:google"
    assert "schedule.create" not in [e["action"] for e in body["data"]]
