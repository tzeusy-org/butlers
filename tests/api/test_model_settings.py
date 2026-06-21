"""Tests for model catalog and butler model override endpoints.

Condensed from test_model_settings.py (53) + test_model_settings_discretion_tier.py (8)
+ test_model_settings_self_healing_tier.py (9) → ~12 tests (bu-egmz6) → 3 tests (bu-2yw2d).
Keeps: list/503 fallback, create 201 + conflict 409 + invalid-tier 422 (parametrized),
       resolve-model 200.

Phase 2 additions (bu-q2nz3): priority stepper, verify-all rate-limit + concurrency,
server sort order, failures tail.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.model_settings import _get_db_manager

pytestmark = pytest.mark.unit


def _make_catalog_row(
    *,
    entry_id=None,
    alias="claude-sonnet",
    runtime_type="claude",
    model_id="claude-sonnet-4-6",
    complexity_tier="workhorse",
    enabled=True,
    priority=0,
    session_timeout_s=1800,
    extra_args=None,
):
    return {
        "id": entry_id or uuid.uuid4(),
        "alias": alias,
        "runtime_type": runtime_type,
        "model_id": model_id,
        "extra_args": json.dumps(extra_args or []),
        "complexity_tier": complexity_tier,
        # effective_tier mirrors complexity_tier in mock rows (SQL alias for
        # COALESCE(bmo.complexity_tier, mc.complexity_tier) in _RESOLVE_SQL).
        "effective_tier": complexity_tier,
        "enabled": enabled,
        "priority": priority,
        "session_timeout_s": session_timeout_s,
        # Verification columns added by core_093 migration
        "last_verified_at": None,
        "last_verified_latency_ms": None,
        "last_verified_ok": None,
    }


def _mock_record(row: dict[str, Any]) -> MagicMock:
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    for k, v in row.items():
        setattr(m, k, v)
    return m


def _app_with_pool(
    app,
    *,
    fetch_rows=None,
    fetchrow_result=None,
    fetchval_result=None,
    execute_result="DELETE 1",
    pool_raises=None,
    fetchrow_side_effect=None,
):
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[_mock_record(r) for r in (fetch_rows or [])])
    if fetchrow_side_effect is not None:
        mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        mock_pool.fetchrow = AsyncMock(
            return_value=_mock_record(fetchrow_result) if fetchrow_result else None
        )
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.execute = AsyncMock(return_value=execute_result)
    mock_conn = AsyncMock()
    mock_conn.transaction = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=None)
        )
    )
    mock_conn.fetchrow = mock_pool.fetchrow
    mock_pool.acquire = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=None)
        )
    )
    mock_db = MagicMock(spec=DatabaseManager)
    if pool_raises:
        mock_db.credential_shared_pool.side_effect = pool_raises
    else:
        mock_db.credential_shared_pool.return_value = mock_pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app, mock_pool


# ---------------------------------------------------------------------------
# Catalog list + 503 fallback
# ---------------------------------------------------------------------------


async def test_catalog_list_and_503(app):
    rows = [
        _make_catalog_row(alias="claude-haiku", complexity_tier="cheap"),
        _make_catalog_row(alias="claude-sonnet", complexity_tier="workhorse"),
    ]
    # Happy path
    _app_with_pool(app, fetch_rows=rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/settings/models")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 2

    # 503 when pool unavailable
    _app_with_pool(app, pool_raises=KeyError("No shared pool"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp_503 = await client.get("/api/settings/models")
    assert resp_503.status_code == 503


# ---------------------------------------------------------------------------
# Catalog CRUD error paths (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload,fetchrow_side_effect,execute_result,expected",
    [
        # Create 201
        (
            {
                "alias": "new-model",
                "runtime_type": "codex",
                "model_id": "gpt-5",
                "complexity_tier": "workhorse",
                "enabled": True,
                "priority": 0,
            },
            None,
            "INSERT 1",
            201,
        ),
        # Create 409 duplicate alias
        (
            {
                "alias": "claude-sonnet",
                "runtime_type": "claude",
                "model_id": "claude-sonnet-4-6",
                "complexity_tier": "workhorse",
            },
            asyncpg.UniqueViolationError("uq_model_catalog_alias"),
            "INSERT 1",
            409,
        ),
        # Create 422 invalid complexity tier
        (
            {
                "alias": "x",
                "runtime_type": "claude",
                "model_id": "y",
                "complexity_tier": "invalid_tier",
            },
            None,
            "INSERT 1",
            422,
        ),
    ],
    ids=["create-201", "create-409-duplicate", "create-422-bad-tier"],
)
async def test_catalog_create_error_paths(
    app, payload, fetchrow_side_effect, execute_result, expected
):
    created_row = _make_catalog_row(alias=payload.get("alias", "x"))
    _app_with_pool(
        app,
        fetchrow_side_effect=fetchrow_side_effect,
        fetchrow_result=created_row if fetchrow_side_effect is None else None,
        execute_result=execute_result,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/settings/models", json=payload)
    assert resp.status_code == expected


# ---------------------------------------------------------------------------
# Resolve-model preview
# ---------------------------------------------------------------------------


async def test_resolve_model_preview_200_and_422_for_invalid(app):
    catalog_row = _make_catalog_row(complexity_tier="workhorse")
    app2, mock_pool = _app_with_pool(app)
    mock_pool.fetchrow = AsyncMock(side_effect=[_mock_record(catalog_row), None])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r200 = await client.get("/api/butlers/general/resolve-model?complexity=workhorse")
        r422 = await client.get("/api/butlers/general/resolve-model?complexity=invalid")
    assert r200.status_code == 200
    assert r422.status_code == 422


# ---------------------------------------------------------------------------
# §3.4  Priority stepper
# ---------------------------------------------------------------------------


async def test_priority_stepper_200_and_clamp_at_zero(app, audit_append_spy):
    """PUT /api/settings/models/{id}/priority adjusts priority and calls audit.append."""
    entry_id = uuid.uuid4()
    updated_row = _make_catalog_row(entry_id=entry_id, priority=5)
    _, mock_pool = _app_with_pool(app, fetchrow_result=updated_row)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/api/settings/models/{entry_id}/priority",
            json={"delta": 5},
        )
    assert resp.status_code == 200
    assert resp.json()["data"]["priority"] == 5
    # The route emits an explicit audit entry with action "model.priority"; the
    # dashboard_audit_middleware ALSO routes through the same canonical
    # audit.append() spy as a fire-and-forget task, so the total count races
    # between 1 and 2.  Assert on the route's specific call rather than the count.
    route_calls = [
        c
        for c in audit_append_spy.call_args_list
        if len(c.args) >= 3 and c.args[2] == "model.priority"
    ]
    assert len(route_calls) == 1, (
        f"expected exactly one route audit.append with action 'model.priority', "
        f"got call list: {audit_append_spy.call_args_list}"
    )
    assert route_calls[0].kwargs["note"] == "5"


async def test_priority_stepper_404_on_missing(app, audit_append_spy):
    """PUT /api/settings/models/{id}/priority returns 404 when catalog entry missing."""
    _, mock_pool = _app_with_pool(app, fetchrow_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/api/settings/models/{uuid.uuid4()}/priority",
            json={"delta": 1},
        )
    assert resp.status_code == 404
    audit_append_spy.assert_not_awaited()


# ---------------------------------------------------------------------------
# §3.5  Verify-all rate limit
# ---------------------------------------------------------------------------


async def test_verify_all_rate_limit(app, audit_append_spy, monkeypatch):
    """POST /api/settings/models/verify-all returns 429 on second call within 60s."""
    import butlers.api.routers.model_settings as _ms

    # Reset the sentinel to allow the first call
    monkeypatch.setattr(_ms, "_verify_all_last_run", 0.0)

    _, mock_pool = _app_with_pool(app)
    # Return an empty enabled-models list so no actual verification is attempted
    mock_pool.fetch = AsyncMock(return_value=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r1 = await client.post("/api/settings/models/verify-all")
        r429 = await client.post("/api/settings/models/verify-all")

    assert r1.status_code == 200
    assert r429.status_code == 429
    # The accepted run emits an explicit audit entry with action
    # "models.verify_all".  The dashboard_audit_middleware ALSO routes through
    # the same canonical audit.append() spy as a fire-and-forget task (once per
    # POST, including the 429), so the total count races.  Assert on the route's
    # specific calls rather than the total count.
    route_calls = [
        c
        for c in audit_append_spy.call_args_list
        if len(c.args) >= 3 and c.args[2] == "models.verify_all"
    ]
    assert len(route_calls) == 1, (
        f"expected exactly one route audit.append with action 'models.verify_all', "
        f"got call list: {audit_append_spy.call_args_list}"
    )


async def test_verify_all_accepted_after_interval(app, audit_append_spy, monkeypatch):
    """POST /api/settings/models/verify-all is accepted once the 60s window passes."""
    import butlers.api.routers.model_settings as _ms

    # Simulate last run well in the past
    monkeypatch.setattr(_ms, "_verify_all_last_run", time.monotonic() - 120.0)

    _, mock_pool = _app_with_pool(app)
    mock_pool.fetch = AsyncMock(return_value=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/settings/models/verify-all")

    assert resp.status_code == 200
    assert resp.json()["data"]["accepted"] is True


# ---------------------------------------------------------------------------
# §3.6  Failures tail — filters by since, graceful on missing table
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# §4.1  PUT /api/settings/models/{id} — full edit endpoint
# ---------------------------------------------------------------------------


async def test_update_catalog_entry_200_writes_audit(app, audit_append_spy):
    """PUT /api/settings/models/{id} returns 200 and calls audit.append('model.update')."""
    entry_id = uuid.uuid4()
    updated_row = _make_catalog_row(
        entry_id=entry_id, alias="renamed", complexity_tier="cheap", priority=3
    )
    _, mock_pool = _app_with_pool(app, fetchrow_result=updated_row)

    payload = {
        "alias": "renamed",
        "model_id": "claude-haiku-4",
        "complexity_tier": "cheap",
        "priority": 3,
        "enabled": True,
        "extra_args": [],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(f"/api/settings/models/{entry_id}", json=payload)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["alias"] == "renamed"
    # The route emits an explicit audit entry with action "model.update"; the
    # dashboard_audit_middleware ALSO routes through the same canonical
    # audit.append() spy as a fire-and-forget task, so the total count races
    # between 1 and 2.  Assert on the route's specific call rather than the count.
    route_calls = [
        c
        for c in audit_append_spy.call_args_list
        if len(c.args) >= 3 and c.args[2] == "model.update"
    ]
    assert len(route_calls) == 1, (
        f"expected exactly one route audit.append with action 'model.update', "
        f"got call list: {audit_append_spy.call_args_list}"
    )
    assert route_calls[0].kwargs["target"] == str(entry_id)


async def test_update_catalog_entry_422_invalid_tier(app, audit_append_spy):
    """PUT /api/settings/models/{id} returns 422 when complexity_tier is not canonical."""
    entry_id = uuid.uuid4()
    _app_with_pool(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/api/settings/models/{entry_id}",
            json={"complexity_tier": "ultra"},
        )
    assert resp.status_code == 422
    audit_append_spy.assert_not_awaited()


async def test_update_catalog_entry_404_missing_entry(app, audit_append_spy):
    """PUT /api/settings/models/{id} returns 404 when catalog entry does not exist."""
    entry_id = uuid.uuid4()
    # fetchrow returns None → 404
    _app_with_pool(app, fetchrow_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/api/settings/models/{entry_id}",
            json={"alias": "ghost"},
        )
    assert resp.status_code == 404
    audit_append_spy.assert_not_awaited()


async def test_update_catalog_entry_422_no_fields(app, audit_append_spy):
    """PUT /api/settings/models/{id} returns 422 when no fields are provided."""
    entry_id = uuid.uuid4()
    _app_with_pool(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(f"/api/settings/models/{entry_id}", json={})
    assert resp.status_code == 422
    audit_append_spy.assert_not_awaited()


async def test_model_failures_404_on_missing_entry(app):
    """GET /api/settings/models/{id}/failures returns 404 when catalog entry absent."""
    _, mock_pool = _app_with_pool(app, fetchval_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/settings/models/{uuid.uuid4()}/failures")
    assert resp.status_code == 404


async def test_model_failures_empty_on_missing_table(app):
    """GET /api/settings/models/{id}/failures returns empty list if dispatch_failures absent."""
    import asyncpg.exceptions

    entry_id = uuid.uuid4()
    _, mock_pool = _app_with_pool(app, fetchval_result=entry_id)
    # fetchval returns entry exists (first call), then since_ts (second call)
    mock_pool.fetchval = AsyncMock(side_effect=[entry_id, "2026-01-01 00:00:00+00"])
    mock_pool.fetch = AsyncMock(
        side_effect=asyncpg.exceptions.UndefinedTableError("dispatch_failures")
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/settings/models/{entry_id}/failures?since=24h")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_model_failures_422_on_bad_since(app):
    """GET /api/settings/models/{id}/failures returns 422 on unrecognised 'since' value."""
    entry_id = uuid.uuid4()
    _, mock_pool = _app_with_pool(app, fetchval_result=entry_id)
    mock_pool.fetchval = AsyncMock(return_value=entry_id)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/settings/models/{entry_id}/failures?since=badvalue")
    assert resp.status_code == 422


async def test_model_failures_returns_real_rows(app):
    """GET /api/settings/models/{id}/failures returns real rows from dispatch_failures."""
    from datetime import UTC, datetime

    entry_id = uuid.uuid4()
    session_id = uuid.uuid4()
    failure_ts = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)

    failure_row = {
        "ts": failure_ts,
        "error_code": "TimeoutError",
        "error_message": "Session timed out after 30s",
        "butler": "general",
        "session_id": session_id,
    }

    _, mock_pool = _app_with_pool(app)
    # fetchval: first call → entry exists (entry_id), second call → since_ts, third → total count
    mock_pool.fetchval = AsyncMock(side_effect=[entry_id, failure_ts, 1])
    mock_pool.fetch = AsyncMock(return_value=[_mock_record(failure_row)])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/settings/models/{entry_id}/failures?since=24h&limit=10")

    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 1
    rows = body["data"]
    assert len(rows) == 1
    assert rows[0]["error_code"] == "TimeoutError"
    assert rows[0]["error_message"] == "Session timed out after 30s"
    assert rows[0]["butler"] == "general"
    assert rows[0]["session_id"] == str(session_id)


# ---------------------------------------------------------------------------
# GET /api/settings/models/{id}/attempts — failover attempt provenance
# ---------------------------------------------------------------------------


async def test_model_attempts_404_on_missing_entry(app):
    """GET /api/settings/models/{id}/attempts returns 404 when catalog entry absent."""
    _, mock_pool = _app_with_pool(app, fetchval_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/settings/models/{uuid.uuid4()}/attempts")
    assert resp.status_code == 404


async def test_model_attempts_empty_on_missing_table(app):
    """GET /api/settings/models/{id}/attempts returns empty list if table absent."""
    import asyncpg.exceptions

    entry_id = uuid.uuid4()
    _, mock_pool = _app_with_pool(app, fetchval_result=entry_id)
    mock_pool.fetchval = AsyncMock(side_effect=[entry_id, "2026-01-01 00:00:00+00"])
    mock_pool.fetch = AsyncMock(
        side_effect=asyncpg.exceptions.UndefinedTableError("model_dispatch_attempts")
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/settings/models/{entry_id}/attempts?since=24h")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_model_attempts_422_on_bad_since(app):
    """GET /api/settings/models/{id}/attempts returns 422 on unrecognised 'since' value."""
    entry_id = uuid.uuid4()
    _, mock_pool = _app_with_pool(app, fetchval_result=entry_id)
    mock_pool.fetchval = AsyncMock(return_value=entry_id)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/settings/models/{entry_id}/attempts?since=badvalue")
    assert resp.status_code == 422


async def test_model_attempts_returns_real_rows(app):
    """GET /api/settings/models/{id}/attempts returns provenance rows."""
    from datetime import UTC, datetime

    entry_id = uuid.uuid4()
    attempt_ts = datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)

    attempt_row = {
        "ts": attempt_ts,
        "butler": "general",
        "outcome": "quota_skip",
        "attempt_index": 0,
        "failure_reason": "Token quota exhausted for catalog entry 'claude-sonnet': 24h",
        "error_code": None,
        "error_message": None,
        "tool_call_count": 0,
        "session_id": None,
        "logical_session_id": "req-abc-123",
    }

    _, mock_pool = _app_with_pool(app)
    mock_pool.fetchval = AsyncMock(side_effect=[entry_id, attempt_ts, 1])
    mock_pool.fetch = AsyncMock(return_value=[_mock_record(attempt_row)])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/settings/models/{entry_id}/attempts?since=24h&limit=10")

    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 1
    rows = body["data"]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "quota_skip"
    assert rows[0]["attempt_index"] == 0
    assert rows[0]["butler"] == "general"
    assert rows[0]["logical_session_id"] == "req-abc-123"
    assert rows[0]["tool_call_count"] == 0
    assert rows[0]["session_id"] is None


# ---------------------------------------------------------------------------
# GET /api/dispatch/attempts — session-scoped failover provenance
# ---------------------------------------------------------------------------


async def test_dispatch_attempts_422_when_no_filter(app):
    """GET /api/dispatch/attempts returns 422 when neither session_id nor logical_session_id given."""
    _, _mock_pool = _app_with_pool(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/dispatch/attempts")
    assert resp.status_code == 422


async def test_dispatch_attempts_empty_on_missing_table(app):
    """GET /api/dispatch/attempts returns empty list if table absent."""
    import asyncpg.exceptions

    session_id = uuid.uuid4()
    _, mock_pool = _app_with_pool(app)
    mock_pool.fetch = AsyncMock(
        side_effect=asyncpg.exceptions.UndefinedTableError("model_dispatch_attempts")
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/dispatch/attempts?session_id={session_id}")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_dispatch_attempts_by_session_id(app):
    """GET /api/dispatch/attempts?session_id=... returns attempt rows ordered by attempt_index."""
    from datetime import UTC, datetime

    session_id = uuid.uuid4()
    attempt_ts = datetime(2026, 5, 25, 9, 0, 0, tzinfo=UTC)

    rows_data = [
        {
            "ts": attempt_ts,
            "butler": "general",
            "outcome": "runtime_failure",
            "attempt_index": 0,
            "failure_reason": "cli_missing",
            "error_code": "RuntimeError",
            "error_message": "binary not found",
            "tool_call_count": 0,
            "session_id": session_id,
            "logical_session_id": "req-abc-001",
        },
        {
            "ts": attempt_ts,
            "butler": "general",
            "outcome": "success",
            "attempt_index": 1,
            "failure_reason": None,
            "error_code": None,
            "error_message": None,
            "tool_call_count": 3,
            "session_id": session_id,
            "logical_session_id": "req-abc-001",
        },
    ]

    _, mock_pool = _app_with_pool(app)
    mock_pool.fetch = AsyncMock(return_value=[_mock_record(r) for r in rows_data])
    mock_pool.fetchval = AsyncMock(return_value=2)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/dispatch/attempts?session_id={session_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 2
    data = body["data"]
    assert len(data) == 2
    assert data[0]["outcome"] == "runtime_failure"
    assert data[0]["attempt_index"] == 0
    assert data[1]["outcome"] == "success"
    assert data[1]["attempt_index"] == 1
    assert data[0]["logical_session_id"] == "req-abc-001"


async def test_dispatch_attempts_by_logical_session_id(app):
    """GET /api/dispatch/attempts?logical_session_id=... uses the logical_session_id=$1 branch.

    The endpoint has a distinct branch for the logical-session filter (no
    session_id::uuid cast): ``WHERE logical_session_id = $1``. Assert both the
    behavioral output and the SQL shape of the recorded mock-pool fetch call so
    a regression that routes through the session_id cast branch is caught.
    """
    from datetime import UTC, datetime

    logical_id = "req-xyz-999"
    attempt_ts = datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC)

    rows_data = [
        {
            "ts": attempt_ts,
            "butler": "general",
            "outcome": "quota_skip",
            "attempt_index": 0,
            "failure_reason": "Token quota exhausted: 24h",
            "error_code": None,
            "error_message": None,
            "tool_call_count": 0,
            "session_id": None,
            "logical_session_id": logical_id,
        },
    ]

    _, mock_pool = _app_with_pool(app)
    mock_pool.fetch = AsyncMock(return_value=[_mock_record(r) for r in rows_data])
    mock_pool.fetchval = AsyncMock(return_value=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/dispatch/attempts?logical_session_id={logical_id}")

    assert resp.status_code == 200
    body = resp.json()
    data = body["data"]
    assert len(data) == 1
    assert data[0]["outcome"] == "quota_skip"
    assert data[0]["session_id"] is None
    assert data[0]["logical_session_id"] == logical_id

    # SQL-shape: the recorded fetch uses the logical-only WHERE clause (no
    # session_id::uuid cast) and binds the logical id as the first positional arg.
    fetch_call = mock_pool.fetch.call_args
    sql = fetch_call.args[0]
    assert "WHERE logical_session_id = $1" in sql
    assert "session_id = $1::uuid" not in sql
    assert fetch_call.args[1] == logical_id
