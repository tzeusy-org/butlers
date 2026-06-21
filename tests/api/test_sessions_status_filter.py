"""Tests for the ?status= filter on the session list endpoints (bu-cmp28).

The frontend Sessions status dropdown sends ``?status=success|failed`` (and
omits the param for "all"), but the list route previously read only the legacy
``success: bool`` and discarded ``status`` entirely, so the dropdown changed
nothing. These tests assert that:

- ``?status=failed`` returns only ``success=False`` rows
- ``?status=success`` returns only ``success=True`` rows
- ``?status=all`` / absent applies no success filter
- ``status`` takes precedence over the legacy ``success`` bool param

The mocks here honor the resolved ``success`` boolean that the route puts into
the SQL WHERE args, so the assertions exercise real route behavior rather than a
hard-coded fixture.
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
from butlers.api.routers.sessions import _resolve_success_filter

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


def _make_session_row(*, success: bool) -> dict:
    return {
        "id": uuid4(),
        "prompt": "test prompt",
        "trigger_source": "api",
        "request_id": None,
        "success": success,
        "started_at": _NOW,
        "completed_at": _NOW,
        "duration_ms": 500,
        "model": "claude-sonnet",
        "complexity": None,
        "input_tokens": 10,
        "output_tokens": 20,
    }


def _make_record(row: dict):
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    return m


def _make_app_filtering_on_success(rows: list[dict]) -> object:
    """Wire an app whose fan_out honors the resolved ``success`` WHERE arg.

    The route builds ``WHERE success = $N`` and passes the boolean in ``args``.
    This mock filters ``rows`` by that boolean (when present) so the response
    reflects the filter the route actually applied — i.e. real behavior, not a
    fixed fixture.
    """

    def _matching(args: tuple) -> list[dict]:
        success_filter = next((a for a in args if isinstance(a, bool)), None)
        if success_filter is None:
            return rows
        return [r for r in rows if r["success"] is success_filter]

    def _side_effect(sql, args, **kw):
        matched = _matching(tuple(args))
        if "count" in sql:
            return {"atlas": [[len(matched)]]}
        return {"atlas": [_make_record(r) for r in matched]}

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas"]
    mock_db.fan_out = AsyncMock(side_effect=_side_effect)

    app = create_app()
    app.dependency_overrides[_sessions_get_db] = lambda: mock_db
    return app


def _make_butler_app_filtering_on_success(rows: list[dict]) -> object:
    """Butler-scoped equivalent: filter on the resolved success WHERE arg."""

    def _matching(args: tuple) -> list[dict]:
        success_filter = next((a for a in args if isinstance(a, bool)), None)
        if success_filter is None:
            return rows
        return [r for r in rows if r["success"] is success_filter]

    async def _fetchval(sql, *args):
        return len(_matching(tuple(args)))

    async def _fetch(sql, *args):
        return [_make_record(r) for r in _matching(tuple(args))]

    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(side_effect=_fetchval)
    mock_pool.fetch = AsyncMock(side_effect=_fetch)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_sessions_get_db] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# Unit tests for the resolver
# ---------------------------------------------------------------------------


def test_resolve_status_maps_and_falls_through_to_legacy_bool() -> None:
    # status=success|failed maps to the boolean; all/None falls through to legacy success bool
    assert _resolve_success_filter("success", None) is True
    assert _resolve_success_filter("failed", None) is False
    assert _resolve_success_filter("all", None) is None
    assert _resolve_success_filter("all", True) is True
    assert _resolve_success_filter(None, False) is False


def test_resolve_status_takes_precedence_over_success_bool() -> None:
    # status wins even if a conflicting legacy success bool is passed
    assert _resolve_success_filter("failed", True) is False
    assert _resolve_success_filter("success", False) is True


# ---------------------------------------------------------------------------
# Cross-butler GET /api/sessions
# ---------------------------------------------------------------------------


_MIXED_ROWS = [
    _make_session_row(success=True),
    _make_session_row(success=True),
    _make_session_row(success=False),
]


@pytest.mark.parametrize(
    ("query", "expected_len", "expected_success"),
    [
        ("?status=failed", 1, False),
        ("?status=success", 2, True),
        ("?status=all", 3, None),
        ("", 3, None),
    ],
)
async def test_sessions_status_filter(
    query: str, expected_len: int, expected_success: bool | None
) -> None:
    app = _make_app_filtering_on_success(_MIXED_ROWS)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/sessions{query}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == expected_len
    if expected_success is not None:
        assert all(item["success"] is expected_success for item in data)


async def test_sessions_legacy_success_bool_still_filters() -> None:
    app = _make_app_filtering_on_success(_MIXED_ROWS)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions?success=false")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert all(item["success"] is False for item in data)


async def test_sessions_rejects_invalid_status() -> None:
    app = _make_app_filtering_on_success(_MIXED_ROWS)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions?status=bogus")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Butler-scoped GET /api/butlers/{name}/sessions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected_len", "expected_success"),
    [("failed", 1, False), ("success", 2, True)],
)
async def test_butler_sessions_status_filter(
    status: str, expected_len: int, expected_success: bool
) -> None:
    app = _make_butler_app_filtering_on_success(_MIXED_ROWS)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/butlers/atlas/sessions?status={status}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == expected_len
    assert all(item["success"] is expected_success for item in data)
