"""Tests for the fleet case file read API (bu-8cdl1.7 Slice 2, RFC 0032).

Covers:
- ``GET /api/switchboard/cases`` — cursor-paginated list, state/posture
  filters, cursor encode/decode round trip, degraded (table-missing) fallback
- ``GET /api/switchboard/cases/{case_id}`` — single case with evidence/links,
  404, 422 (malformed uuid), 503 (pool unavailable / lookup failure)
"""

from __future__ import annotations

import base64
import datetime
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_MODULE_NAME = "switchboard_api_router"
_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "switchboard" / "api" / "router.py"


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
    row.__iter__ = lambda self: iter(data)
    return row


def _app_with_mock(
    app,
    *,
    fetch_rows=None,
    fetch_side_effect=None,
    fetchrow_result=None,
    fetchrow_side_effect=None,
    pool_available=True,
):
    mock_pool = AsyncMock()
    if fetch_side_effect is not None:
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])

    if fetchrow_side_effect is not None:
        mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        mock_db.pool.return_value = mock_pool
    else:
        mock_db.pool.side_effect = KeyError("No pool")

    app.dependency_overrides[_get_db_dep()] = lambda: mock_db
    return app, mock_pool


def _sample_case_row(**overrides):
    base = {
        "id": "22222222-2222-2222-2222-222222222222",
        "correlation_key": "health:owner:respiratory-illness",
        "state": "open",
        "posture": "routine",
        "outcome": None,
        "opened_at": datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC),
        "updated_at": datetime.datetime(2026, 9, 3, tzinfo=datetime.UTC),
        "closed_at": None,
    }
    base.update(overrides)
    return base


def _sample_evidence_row(**overrides):
    base = {
        "id": "33333333-3333-3333-3333-333333333333",
        "case_id": "22222222-2222-2222-2222-222222222222",
        "contributor": "butler_health_rw",
        "kind": "candidate",
        "ref": "insight-42",
        "payload": {"note": "fever reported"},
        "contributed_at": datetime.datetime(2026, 9, 1, 12, 0, tzinfo=datetime.UTC),
    }
    base.update(overrides)
    return base


def _sample_link_row(**overrides):
    base = {
        "id": "44444444-4444-4444-4444-444444444444",
        "case_id": "22222222-2222-2222-2222-222222222222",
        "link_kind": "insight_candidate",
        "ref": "insight-42",
        "metadata": None,
        "linked_at": datetime.datetime(2026, 9, 1, 12, 5, tzinfo=datetime.UTC),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# GET /cases — happy path
# ---------------------------------------------------------------------------


async def test_list_cases_happy_path_maps_all_fields(app):
    app, _ = _app_with_mock(app, fetch_rows=[_make_row(_sample_case_row())])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/cases")

    assert resp.status_code == 200
    body = resp.json()
    data = body["data"]
    assert len(data) == 1
    item = data[0]
    assert item["id"] == "22222222-2222-2222-2222-222222222222"
    assert item["correlation_key"] == "health:owner:respiratory-illness"
    assert item["state"] == "open"
    assert item["posture"] == "routine"
    assert item["outcome"] is None
    assert item["opened_at"] is not None
    assert item["updated_at"] is not None
    assert item["closed_at"] is None
    assert body["meta"]["has_more"] is False
    assert body["meta"]["next_cursor"] is None


async def test_list_cases_state_filter_adds_condition(app):
    app, mock_pool = _app_with_mock(app, fetch_rows=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/cases", params={"state": "open,watching"})

    assert resp.status_code == 200
    call = mock_pool.fetch.call_args
    sql = call[0][0]
    assert "state = ANY($1::text[])" in sql
    assert call[0][1] == ["open", "watching"]


async def test_list_cases_posture_filter_adds_condition(app):
    app, mock_pool = _app_with_mock(app, fetch_rows=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/cases", params={"posture": "urgent"})

    assert resp.status_code == 200
    call = mock_pool.fetch.call_args
    sql = call[0][0]
    assert "posture = ANY($1::text[])" in sql
    assert call[0][1] == ["urgent"]


async def test_list_cases_has_more_emits_next_cursor(app):
    rows = [
        _make_row(_sample_case_row(id=f"11111111-1111-1111-1111-11111111111{i}")) for i in range(3)
    ]
    app, _ = _app_with_mock(app, fetch_rows=rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/cases", params={"limit": 2})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 2
    assert body["meta"]["has_more"] is True
    assert body["meta"]["next_cursor"] is not None


async def test_list_cases_cursor_round_trips_into_where_clause(app):
    app, mock_pool = _app_with_mock(app, fetch_rows=[])
    cursor_payload = {"ua": "2026-09-01T00:00:00+00:00", "id": "some-id"}
    cursor = base64.urlsafe_b64encode(json.dumps(cursor_payload).encode()).decode()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/cases", params={"cursor": cursor})

    assert resp.status_code == 200
    call = mock_pool.fetch.call_args
    sql = call[0][0]
    assert "(updated_at, id) < ($1, $2)" in sql


# ---------------------------------------------------------------------------
# GET /cases — validation and degraded modes
# ---------------------------------------------------------------------------


async def test_list_cases_invalid_state_422(app):
    _app_with_mock(app, fetch_rows=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/cases", params={"state": "bogus"})
    assert resp.status_code == 422


async def test_list_cases_invalid_posture_422(app):
    _app_with_mock(app, fetch_rows=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/cases", params={"posture": "bogus"})
    assert resp.status_code == 422


async def test_list_cases_invalid_cursor_422(app):
    _app_with_mock(app, fetch_rows=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/cases", params={"cursor": "not-valid-base64!!"})
    assert resp.status_code == 422


async def test_list_cases_limit_out_of_range_422(app):
    _app_with_mock(app, fetch_rows=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/cases", params={"limit": 99999})
    assert resp.status_code == 422


async def test_list_cases_graceful_degrade_when_table_missing(app):
    _app_with_mock(app, fetch_side_effect=RuntimeError("relation does not exist"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/cases")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["has_more"] is False
    assert body["meta"]["next_cursor"] is None


# ---------------------------------------------------------------------------
# GET /cases/{case_id} — happy path
# ---------------------------------------------------------------------------


async def test_get_case_happy_path_includes_evidence_and_links(app):
    app, mock_pool = _app_with_mock(
        app,
        fetchrow_result=_make_row(_sample_case_row()),
        fetch_side_effect=[
            [_make_row(_sample_evidence_row())],
            [_make_row(_sample_link_row())],
        ],
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/cases/22222222-2222-2222-2222-222222222222")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == "22222222-2222-2222-2222-222222222222"
    assert data["correlation_key"] == "health:owner:respiratory-illness"
    assert len(data["evidence"]) == 1
    assert data["evidence"][0]["contributor"] == "butler_health_rw"
    assert data["evidence"][0]["payload"] == {"note": "fever reported"}
    assert len(data["links"]) == 1
    assert data["links"][0]["link_kind"] == "insight_candidate"


# ---------------------------------------------------------------------------
# GET /cases/{case_id} — validation and degraded modes
# ---------------------------------------------------------------------------


async def test_get_case_not_found_404(app):
    _app_with_mock(app, fetchrow_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/cases/22222222-2222-2222-2222-222222222222")
    assert resp.status_code == 404


async def test_get_case_invalid_uuid_422(app):
    _app_with_mock(app, fetchrow_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/cases/not-a-uuid")
    assert resp.status_code == 422


async def test_get_case_503_when_pool_unavailable(app):
    _app_with_mock(app, pool_available=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/cases/22222222-2222-2222-2222-222222222222")
    assert resp.status_code == 503


async def test_get_case_503_when_lookup_fails(app):
    _app_with_mock(app, fetchrow_side_effect=RuntimeError("relation does not exist"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/cases/22222222-2222-2222-2222-222222222222")
    assert resp.status_code == 503


async def test_get_case_503_when_evidence_or_links_lookup_fails(app):
    _app_with_mock(
        app,
        fetchrow_result=_make_row(_sample_case_row()),
        fetch_side_effect=RuntimeError("relation does not exist"),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/cases/22222222-2222-2222-2222-222222222222")
    assert resp.status_code == 503
