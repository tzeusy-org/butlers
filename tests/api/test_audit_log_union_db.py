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

As of the audit-unify epic, every writer routes through ``public.audit_log``
(.3 / bu-h47nm) and the read endpoint reads ``public.audit_log`` ALONE — the
live legacy UNION arm was removed (bu-j26e8) after migration core_124 backfilled
the historical ``dashboard_audit_log`` rows into the canonical table.

The unit tests in ``test_audit_log.py`` mock the DB pools and therefore cannot
catch read-topology regressions: they never exercise the real two-table layout.
This module runs the *actual* read endpoint against a migrated Postgres (core +
switchboard chains) and asserts the canonical-only read contract:

  * a row written via ``log_audit_entry`` lands in (and is read from)
    ``public.audit_log``;
  * a row living ONLY in the legacy ``dashboard_audit_log`` is NOT read live.
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


async def test_log_audit_entry_lands_in_canonical_audit_log(
    pool: asyncpg.Pool, audit_app: FastAPI
) -> None:
    """As of bu-h47nm, log_audit_entry routes to ``public.audit_log`` (not the
    legacy ``dashboard_audit_log``); the row appears on /audit-log directly.

    Field mapping: butler->actor, operation->action, request_summary.path->target.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    await log_audit_entry(
        mock_db,
        butler="qa",
        operation="schedule.create",
        request_summary={"method": "POST", "path": "/api/qa/schedules"},
    )

    # The writer now lands the row in the canonical table, NOT the legacy one.
    canonical_count = await pool.fetchval("SELECT count(*) FROM public.audit_log")
    assert canonical_count == 1
    legacy_count = await pool.fetchval("SELECT count(*) FROM dashboard_audit_log")
    assert legacy_count == 0

    resp = await _get(audit_app, AUDIT_PATH)
    assert resp.status_code == 200
    body = resp.json()

    actions = [e["action"] for e in body["data"]]
    assert "schedule.create" in actions, f"Got actions={actions}"
    assert body["meta"]["total"] == 1

    entry = next(e for e in body["data"] if e["action"] == "schedule.create")
    assert entry["actor"] == "qa"  # actor <- butler
    assert entry["target"] == "/api/qa/schedules"  # target <- request_summary.path


async def test_genuinely_legacy_dashboard_row_not_read_live(
    pool: asyncpg.Pool, audit_app: FastAPI
) -> None:
    """Read-side contract (bu-j26e8): a row living ONLY in the legacy
    ``dashboard_audit_log`` must NOT surface on /audit-log — the live UNION arm
    was removed.  Such pre-cutover history becomes visible only after the
    core_124 backfill copies it into ``public.audit_log`` (covered by the
    migration test), not via a live cross-table read here."""
    # The pool registers the JSONB codec, so hand request_summary / user_context
    # as plain dicts (matching how the legacy log_audit_entry writer wrote them).
    await pool.execute(
        "INSERT INTO dashboard_audit_log "
        "(butler, operation, request_summary, result, error, user_context) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        "qa",
        "schedule.create",
        {"method": "POST", "path": "/api/qa/schedules"},
        "success",
        None,
        {},
    )

    canonical_count = await pool.fetchval("SELECT count(*) FROM public.audit_log")
    assert canonical_count == 0
    legacy_count = await pool.fetchval("SELECT count(*) FROM dashboard_audit_log")
    assert legacy_count == 1

    resp = await _get(audit_app, AUDIT_PATH)
    assert resp.status_code == 200
    body = resp.json()

    actions = [e["action"] for e in body["data"]]
    assert "schedule.create" not in actions, (
        "legacy-only dashboard_audit_log row leaked onto /audit-log — the live "
        f"UNION arm should be gone. Got actions={actions}"
    )
    assert body["meta"]["total"] == 0


async def test_canonical_and_legacy_rows_merged_ts_desc(
    pool: asyncpg.Pool, audit_app: FastAPI
) -> None:
    """Two canonical rows (one inserted directly, one via the rerouted
    log_audit_entry writer) interleave by ts DESC and the total is correct."""
    # Direct canonical row (older).
    await pool.execute(
        "INSERT INTO public.audit_log (actor, action, ts) "
        "VALUES ($1, $2, now() - interval '1 hour')",
        "owner",
        "model_priority_change",
    )
    # Newer row via log_audit_entry (also canonical, post bu-h47nm).
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
    """?action= filters canonical rows by their action label (operation maps to
    action via the log_audit_entry shim)."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    await log_audit_entry(mock_db, butler="qa", operation="schedule.delete", request_summary={})
    await log_audit_entry(mock_db, butler="qa", operation="state.delete", request_summary={})

    resp = await _get(audit_app, f"{AUDIT_PATH}?action=schedule.delete")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 1
    assert body["data"][0]["action"] == "schedule.delete"


async def test_key_filter_excludes_rows_without_credential_target(
    pool: asyncpg.Pool, audit_app: FastAPI
) -> None:
    """The credential-key filter (?key=) must never match rows with no credential
    target, preserving its exact semantics."""
    # Row with no credential target (shim writer leaves target NULL).
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
