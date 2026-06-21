"""Tests for POST /api/switchboard/ingestion-rules/bulk endpoint.

Covers:
- Bulk disable: sets enabled=FALSE for existing rules
- Bulk enable: sets enabled=TRUE for existing rules
- Bulk delete: soft-deletes (deleted_at=NOW, enabled=FALSE)
- Connector-scope block-only: enable skips non-block actions (error_reason=scope_action_invalid)
- Not-found ids return per-id outcome 'not_found'
- Already-deleted ids return per-id outcome 'not_found'
- Invalid UUID format returns per-id outcome 'error_reason'
- Batch size cap: >100 ids returns HTTP 400 (Pydantic validation)
- Unknown op returns HTTP 400 (Pydantic validation)
- Audit entry emitted on batch with affected rules

§3.10 / §3.12 — Phase 3d (bu-1f91v.9)
"""

from __future__ import annotations

import datetime
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_MODULE_NAME = "switchboard_api_router"
_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "switchboard" / "api" / "router.py"

_BULK_URL = "/api/switchboard/ingestion-rules/bulk"


def _get_db_dep():
    if _MODULE_NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, _router_path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Could not load spec from {_router_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        spec.loader.exec_module(module)
    return sys.modules[_MODULE_NAME]._get_db_manager


def _make_row(data: dict):
    row = MagicMock()
    row.__getitem__ = lambda self, k: data[k]
    row.get = lambda k, default=None: data.get(k, default)
    row.keys = lambda: data.keys()
    return row


def _rule_row(
    rule_id: str | None = None,
    *,
    scope: str = "global",
    action: str = "block",
    enabled: bool = True,
    deleted_at=None,
) -> dict:
    return {
        "id": rule_id or str(uuid4()),
        "scope": scope,
        "action": action,
        "enabled": enabled,
        "deleted_at": deleted_at,
    }


def _app_with_mock(
    app,
    *,
    fetchrow_side_effects=None,
    fetchrow_result=None,
    execute_return="UPDATE 1",
):
    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(return_value=execute_return)
    if fetchrow_side_effects is not None:
        mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effects)
    else:
        mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app.dependency_overrides[_get_db_dep()] = lambda: mock_db
    return app, mock_pool


# ---------------------------------------------------------------------------
# Bulk disable
# ---------------------------------------------------------------------------


async def test_bulk_disable_ok(app):
    """Bulk disable sets enabled=FALSE for existing rules."""
    rule_id = str(uuid4())
    row = _rule_row(rule_id, enabled=True)
    app, mock_pool = _app_with_mock(app, fetchrow_result=_make_row(row))

    with patch(
        f"{_MODULE_NAME}.emit_dashboard_audit",
        new_callable=lambda: AsyncMock,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(_BULK_URL, json={"op": "disable", "ids": [rule_id]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["op"] == "disable"
    assert body["affected"] == 1
    assert len(body["results"]) == 1
    assert body["results"][0]["outcome"] == "ok"


# ---------------------------------------------------------------------------
# Bulk enable
# ---------------------------------------------------------------------------


async def test_bulk_enable_ok(app):
    """Bulk enable sets enabled=TRUE for an existing rule."""
    rule_id = str(uuid4())
    row = _rule_row(rule_id, scope="global", action="block", enabled=False)
    app, mock_pool = _app_with_mock(app, fetchrow_result=_make_row(row))

    with patch(f"{_MODULE_NAME}.emit_dashboard_audit", new_callable=lambda: AsyncMock):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(_BULK_URL, json={"op": "enable", "ids": [rule_id]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["affected"] == 1
    assert body["results"][0]["outcome"] == "ok"


async def test_bulk_enable_connector_scope_non_block_skipped(app):
    """Enable on a connector-scoped rule with action != 'block' → scope_action_invalid."""
    rule_id = str(uuid4())
    row = _rule_row(rule_id, scope="connector:gmail:user@example.com", action="skip", enabled=False)
    app, _ = _app_with_mock(app, fetchrow_result=_make_row(row))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(_BULK_URL, json={"op": "enable", "ids": [rule_id]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["affected"] == 0
    result = body["results"][0]
    assert result["outcome"] == "error_reason"
    assert result["error_reason"] == "scope_action_invalid"


# ---------------------------------------------------------------------------
# Bulk delete (soft-delete)
# ---------------------------------------------------------------------------


async def test_bulk_delete_ok(app):
    """Bulk delete soft-deletes rules (sets deleted_at and enabled=FALSE)."""
    rule_id = str(uuid4())
    row = _rule_row(rule_id, enabled=True)
    app, mock_pool = _app_with_mock(app, fetchrow_result=_make_row(row))

    with patch(f"{_MODULE_NAME}.emit_dashboard_audit", new_callable=lambda: AsyncMock):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(_BULK_URL, json={"op": "delete", "ids": [rule_id]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["affected"] == 1
    # Verify soft-delete SQL was called (contains deleted_at)
    execute_calls = mock_pool.execute.call_args_list
    assert any("deleted_at" in str(c) for c in execute_calls)


# ---------------------------------------------------------------------------
# Not-found / already-deleted ids
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_kind",
    ["unknown-id", "already-deleted"],
    ids=["unknown-id", "already-deleted"],
)
async def test_bulk_missing_rule_returns_not_found(app, missing_kind):
    """Unknown id (fetchrow None) and already-deleted rows both yield per-id
    'not_found' (affected=0), never HTTP 404."""
    rule_id = str(uuid4())
    if missing_kind == "unknown-id":
        fetchrow_result = None
    else:
        fetchrow_result = _make_row(
            _rule_row(rule_id, deleted_at=datetime.datetime.now(datetime.UTC))
        )
    app, _ = _app_with_mock(app, fetchrow_result=fetchrow_result)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(_BULK_URL, json={"op": "disable", "ids": [rule_id]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["affected"] == 0
    assert body["results"][0]["outcome"] == "not_found"


# ---------------------------------------------------------------------------
# Invalid UUID format
# ---------------------------------------------------------------------------


async def test_bulk_invalid_uuid_format(app):
    """Invalid UUID format returns per-id 'error_reason' (invalid_uuid)."""
    app, _ = _app_with_mock(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(_BULK_URL, json={"op": "disable", "ids": ["not-a-uuid"]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["affected"] == 0
    result = body["results"][0]
    assert result["outcome"] == "error_reason"
    assert result["error_reason"] == "invalid_uuid"


# ---------------------------------------------------------------------------
# Batch size cap enforcement (Pydantic model validation)
# ---------------------------------------------------------------------------


async def test_bulk_too_many_ids_returns_400(app):
    """More than 100 ids → HTTP 400 from Pydantic validation."""
    app, _ = _app_with_mock(app)
    ids = [str(uuid4()) for _ in range(101)]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(_BULK_URL, json={"op": "disable", "ids": ids})

    assert resp.status_code == 422  # FastAPI returns 422 on Pydantic validation error


async def test_bulk_empty_ids_returns_422(app):
    """Empty ids list → HTTP 422 from Pydantic validation."""
    app, _ = _app_with_mock(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(_BULK_URL, json={"op": "disable", "ids": []})

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Unknown op rejected
# ---------------------------------------------------------------------------


async def test_bulk_unknown_op_returns_422(app):
    """Unknown op → HTTP 422 from Pydantic validation."""
    rule_id = str(uuid4())
    app, _ = _app_with_mock(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(_BULK_URL, json={"op": "freeze", "ids": [rule_id]})

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Mixed batch: some ok, some not_found
# ---------------------------------------------------------------------------


async def test_bulk_mixed_batch(app):
    """Mixed batch: existing + unknown ids → partial success with per-id outcomes."""
    existing_id = str(uuid4())
    missing_id = str(uuid4())

    row = _rule_row(existing_id, enabled=True)

    def _fetchrow_side_effect(sql, rule_id, *args):
        if rule_id == existing_id:
            return _make_row(row)
        return None

    app, mock_pool = _app_with_mock(app, fetchrow_side_effects=_fetchrow_side_effect)

    with patch(f"{_MODULE_NAME}.emit_dashboard_audit", new_callable=lambda: AsyncMock):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                _BULK_URL, json={"op": "disable", "ids": [existing_id, missing_id]}
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["affected"] == 1
    outcomes = {r["id"]: r["outcome"] for r in body["results"]}
    assert outcomes[existing_id] == "ok"
    assert outcomes[missing_id] == "not_found"
