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
switchboard chains) and asserts the canonical-only read contract: a row written
via ``log_audit_entry`` lands in (and is read from) ``public.audit_log``.

The legacy ``dashboard_audit_log`` table is dropped by switchboard migration
sw_026 (bu-o699b), so the earlier "a legacy-only row is NOT read live" negative
case is now guaranteed structurally by the table's non-existence rather than by
a live-UNION-absent assertion — those negative tests were removed with the drop.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers import audit as audit_module
from butlers.api.routers.audit import log_audit_entry
from butlers.core.state import state_set
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
    """Provision the core chain (public.audit_log) and the switchboard chain.

    Flat topology (both chains land in ``public``), mirroring the single-DB
    deployment the read endpoint queries. The switchboard chain creates the
    legacy ``dashboard_audit_log`` (migration 001) and then drops it (sw_026),
    so the migrated DB exposes only ``public.audit_log`` — matching production."""
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

    # The writer lands the row in the canonical table.
    canonical_count = await pool.fetchval("SELECT count(*) FROM public.audit_log")
    assert canonical_count == 1

    resp = await _get(audit_app, AUDIT_PATH)
    assert resp.status_code == 200
    body = resp.json()

    actions = [e["action"] for e in body["data"]]
    assert "schedule.create" in actions, f"Got actions={actions}"
    assert body["meta"]["total"] == 1

    entry = next(e for e in body["data"] if e["action"] == "schedule.create")
    assert entry["actor"] == "qa"  # actor <- butler
    assert entry["target"] == "/api/qa/schedules"  # target <- request_summary.path


# NOTE: the pre-sw_026 "genuinely legacy dashboard_audit_log row not read live"
# negative-contract test was removed by bu-o699b: the legacy table is dropped by
# switchboard migration sw_026, so a legacy-only row can no longer be
# constructed — the canonical-only read contract is now guaranteed structurally
# by the table's non-existence rather than by a live-UNION-absent assertion.


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


async def test_append_metadata_roundtrips_as_object_not_double_encoded_string(
    pool: asyncpg.Pool, audit_app: FastAPI
) -> None:
    """Real-Postgres regression for the append() jsonb double-encoding fix
    (JARVIS audit move 6, bu-qvnce.6).

    append() used to ``json.dumps()`` metadata into a string and bind it with
    an explicit ``$N::jsonb`` cast. Every asyncpg pool in this codebase also
    registers a JSONB type codec (``register_jsonb_codec``, src/butlers/db.py)
    whose encoder calls ``json.dumps()`` itself — so the old code path
    double-encoded metadata into a jsonb-typed STRING instead of an OBJECT
    (the same class of regression as bu-qki26 / bu-aaacv, see
    ``tests/relationship/test_jsonb_codec.py``). The mocked-pool unit tests in
    ``tests/api/test_audit_log.py`` only assert on the Python value handed to
    ``pool.fetchval`` — they cannot prove what actually lands in a real jsonb
    column. This test writes via the real ``append()`` code path against a
    migrated Postgres and reads the row back two ways: directly off the
    connection (proving the stored type is a dict, not a JSON string) and via
    the ``GET /api/audit-log`` read path (proving ``AuditLogEntry.from_record``
    surfaces the same structured object end-to-end).
    """
    non_json_safe_id = uuid.uuid4()
    row_id = await audit_module.append(
        pool,
        "owner",
        "model_priority_change",
        metadata={"request_id": non_json_safe_id, "nested": {"nums": [1, 2, 3]}},
        result="success",
    )
    assert row_id > 0

    row = await pool.fetchrow(
        "SELECT metadata, result, error FROM public.audit_log WHERE id = $1", row_id
    )
    stored_metadata = row["metadata"]
    assert isinstance(stored_metadata, dict), (
        f"metadata arrived as {type(stored_metadata).__name__!r}, not a dict — "
        "the jsonb column was double-encoded into a string."
    )
    assert stored_metadata == {"request_id": str(non_json_safe_id), "nested": {"nums": [1, 2, 3]}}
    assert row["result"] == "success"
    assert row["error"] is None

    resp = await _get(audit_app, f"{AUDIT_PATH}?action=model_priority_change")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 1
    entry = body["data"][0]
    assert entry["metadata"] == {
        "request_id": str(non_json_safe_id),
        "nested": {"nums": [1, 2, 3]},
    }
    assert entry["result"] == "success"


async def test_privileged_filter_is_a_consequence_allowlist(
    pool: asyncpg.Pool, audit_app: FastAPI
) -> None:
    """The real SQL path selects consequential rows and every explicit error.

    This covers the semantic distinction a mocked SQL-shape assertion cannot:
    successful machine cadence is absent, while an error from an otherwise
    unknown action family remains an operator-visible failure.
    """
    rows = [
        ("approval.approve", "success"),
        ("approvals.policy", "success"),
        ("model.update", "success"),
        ("permission.grant", "success"),
        ("data.export", "success"),
        ("webhook.create", "success"),
        ("rotated", "success"),
        ("GET /api/health", "success"),
        ("butler_heartbeat", "success"),
        ("models.verify_all", "success"),
        ("runtime.heartbeat", "error"),
    ]
    await pool.executemany(
        "INSERT INTO public.audit_log (actor, action, result) VALUES ('owner', $1, $2)",
        rows,
    )

    resp = await _get(audit_app, f"{AUDIT_PATH}?kind=privileged&limit=100")

    assert resp.status_code == 200
    actions = {entry["action"] for entry in resp.json()["data"]}
    assert {
        "approval.approve",
        "approvals.policy",
        "model.update",
        "permission.grant",
        "data.export",
        "webhook.create",
        "rotated",
        "runtime.heartbeat",
    } <= actions
    assert "GET /api/health" not in actions
    assert "butler_heartbeat" not in actions
    assert "models.verify_all" not in actions


async def test_audit_owner_timezone_day_bounds_include_the_entire_owner_day(
    pool: asyncpg.Pool, audit_app: FastAPI
) -> None:
    """From=To is an inclusive owner-local day, independent of server UTC."""
    owner_tz = ZoneInfo("Asia/Singapore")
    await state_set(pool, "settings.general", {"timezone": owner_tz.key})
    same_day_early = await pool.fetchval(
        """
        INSERT INTO public.audit_log (ts, actor, action, result)
        VALUES ($1, 'owner', 'model.update', 'success') RETURNING id
        """,
        datetime(2026, 7, 10, 16, 15, tzinfo=UTC),  # 00:15 on 2026-07-11 SGT
    )
    same_day_late = await pool.fetchval(
        """
        INSERT INTO public.audit_log (ts, actor, action, result)
        VALUES ($1, 'owner', 'model.update', 'success') RETURNING id
        """,
        datetime(2026, 7, 11, 15, 45, tzinfo=UTC),  # 23:45 on 2026-07-11 SGT
    )
    next_day = await pool.fetchval(
        """
        INSERT INTO public.audit_log (ts, actor, action, result)
        VALUES ($1, 'owner', 'model.update', 'success') RETURNING id
        """,
        datetime(2026, 7, 11, 16, 0, tzinfo=UTC),  # midnight on 2026-07-12 SGT
    )

    resp = await _get(
        audit_app,
        f"{AUDIT_PATH}?from_date=2026-07-11&to_date=2026-07-11&limit=100",
    )

    assert resp.status_code == 200
    ids = {entry["id"] for entry in resp.json()["data"]}
    assert {same_day_early, same_day_late} <= ids
    assert next_day not in ids
