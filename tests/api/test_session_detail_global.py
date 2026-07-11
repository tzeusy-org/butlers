"""Tests for the cross-butler session detail route GET /api/sessions/{id}.

The global session detail path must resolve a session by id across all
butler schemas without requiring a ``?butler=`` hint, mirroring how the
cross-butler LIST endpoint fans out. It is the ONLY single-session detail
route (the butler-scoped ``GET /api/butlers/{name}/sessions/{id}`` path was
deleted in bu-tpudw.2).

It also splits not-found from source-degraded: a miss over a fully-reachable
fan-out is a 404, but a miss while one or more pools were unreachable is a 503
(the session may live in a pool that could not be queried) — never a false
404.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.sessions import _get_db_manager as _sessions_get_db

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


def _make_detail_row(session_id) -> dict:
    return {
        "id": session_id,
        "prompt": "test prompt",
        "trigger_source": "api",
        "result": "ok",
        "tool_calls": [],
        "duration_ms": 500,
        "trace_id": "trace-1",
        "request_id": None,
        "cost": None,
        "started_at": _NOW,
        "completed_at": _NOW,
        "success": True,
        "error": None,
        "model": "claude-sonnet",
        "input_tokens": 1234,
        "output_tokens": 567,
        "parent_session_id": None,
        "complexity": None,
        "resolution_source": None,
    }


def _make_record(row: dict):
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    return m


def _make_app(*, owning_butler: str, row: dict | None, degraded: list[str] | None = None) -> object:
    """Wire an app whose fan_out returns ``row`` only for ``owning_butler``.

    ``degraded`` names any pool the fan-out reports as failed (the second
    element of ``fan_out_with_status``'s ``(results, failed)`` tuple). The
    owning butler's pool answers the best-effort process-log / correction-count
    follow-up queries with no extra data.
    """

    async def _fan_out(sql, args, **kw):
        result = {"atlas": [], "general": []}
        if row is not None:
            result[owning_butler] = [_make_record(row)]
        return result, list(degraded or [])

    owning_pool = AsyncMock()
    owning_pool.fetchrow = AsyncMock(return_value=None)
    owning_pool.fetchval = AsyncMock(return_value=0)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas", "general"]
    mock_db.fan_out_with_status = AsyncMock(side_effect=_fan_out)
    mock_db.pool.return_value = owning_pool

    app = create_app()
    app.dependency_overrides[_sessions_get_db] = lambda: mock_db
    return app


async def test_global_session_detail_resolves_across_schemas() -> None:
    """GET /api/sessions/{id} (no ?butler=) finds the session via fan-out."""
    session_id = uuid4()
    row = _make_detail_row(session_id)
    app = _make_app(owning_butler="general", row=row)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/sessions/{session_id}")

    assert resp.status_code == 200
    data = resp.json()["data"]
    # Detail shape is preserved and the resolved butler is attached.
    assert data["id"] == str(session_id)
    assert data["butler"] == "general"
    assert data["input_tokens"] == 1234
    assert data["output_tokens"] == 567
    assert data["prompt"] == "test prompt"


async def test_global_session_detail_404_when_no_butler_has_it() -> None:
    """GET /api/sessions/{id} returns 404 when no schema owns the session."""
    session_id = uuid4()
    app = _make_app(owning_butler="general", row=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/sessions/{session_id}")

    assert resp.status_code == 404


async def test_global_session_detail_503_when_a_pool_is_unreachable() -> None:
    """A miss while a pool errored is a 503 naming the pool, not a false 404."""
    session_id = uuid4()
    app = _make_app(owning_butler="general", row=None, degraded=["general"])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/sessions/{session_id}")

    assert resp.status_code == 503
    # The down pool is named so the drawer can render a distinct pool-down state.
    assert "general" in resp.json()["detail"]


async def test_global_session_detail_prefers_hit_over_degraded() -> None:
    """A found row wins even if a sibling pool errored — 200, not 503."""
    session_id = uuid4()
    row = _make_detail_row(session_id)
    app = _make_app(owning_butler="general", row=row, degraded=["atlas"])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/sessions/{session_id}")

    assert resp.status_code == 200
    assert resp.json()["data"]["butler"] == "general"


async def test_global_session_detail_rejects_non_uuid() -> None:
    """The {session_id} path is typed as UUID; a non-UUID is a 422, not a 404."""
    app = _make_app(owning_butler="general", row=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions/not-a-uuid")

    assert resp.status_code == 422
