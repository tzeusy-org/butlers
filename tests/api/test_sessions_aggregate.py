"""Tests for GET /api/sessions/aggregate and the ?status=running filter.

The aggregate endpoint is a window-scoped, filter-aware rollup across all
butlers (no pagination).  It powers the Sessions KPI strip and must:

- sum scalar counts/tokens across butlers
- compute success_rate = success / (success + failed), or null when denom == 0
- expose running_count (success IS NULL)
- return by_butler sorted by count desc, count > 0 only
- pass the shared list filters (status/butler/since/until/request_id) through

The ?status=running filter is also exercised on GET /api/sessions: it maps to
the ``success IS NULL`` predicate (no bound bool arg).
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agg_record(values: dict):
    base = {
        "total": 0,
        "success_count": 0,
        "failed_count": 0,
        "running_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    base.update(values)
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: base[key])
    return m


def _make_app_with_aggregate(per_butler: dict[str, dict]) -> object:
    """Wire an app whose fan_out returns one aggregate row per butler."""
    fan_out_return = {name: [_make_agg_record(vals)] for name, vals in per_butler.items()}

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = list(per_butler.keys())
    mock_db.fan_out = AsyncMock(return_value=fan_out_return)

    app = create_app()
    app.dependency_overrides[_sessions_get_db] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# Aggregate endpoint
# ---------------------------------------------------------------------------


async def test_aggregate_combines_counts_across_butlers() -> None:
    app = _make_app_with_aggregate(
        {
            "health": {
                "total": 612,
                "success_count": 600,
                "failed_count": 10,
                "running_count": 2,
                "input_tokens": 2_000_000,
                "output_tokens": 700_000,
            },
            "finance": {
                "total": 401,
                "success_count": 390,
                "failed_count": 6,
                "running_count": 5,
                "input_tokens": 1_100_000,
                "output_tokens": 350_000,
            },
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions/aggregate")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 1013
    assert data["success_count"] == 990
    assert data["failed_count"] == 16
    assert data["running_count"] == 7
    assert data["input_tokens"] == 3_100_000
    assert data["output_tokens"] == 1_050_000
    # by_butler sorted by count desc
    assert data["by_butler"] == [
        {"butler": "health", "count": 612},
        {"butler": "finance", "count": 401},
    ]
    # success_rate = 990 / (990 + 16)
    assert data["success_rate"] == pytest.approx(990 / 1006)
    # cost is intentionally omitted
    assert "cost" not in data


async def test_aggregate_success_rate_null_when_denominator_zero() -> None:
    """No completed sessions (only running) -> success_rate is null, not 0."""
    app = _make_app_with_aggregate(
        {"health": {"total": 3, "success_count": 0, "failed_count": 0, "running_count": 3}}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions/aggregate")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["success_rate"] is None
    assert data["running_count"] == 3


async def test_aggregate_by_butler_omits_zero_count() -> None:
    app = _make_app_with_aggregate(
        {
            "health": {"total": 5, "success_count": 5},
            "idle": {"total": 0},
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions/aggregate")
    data = resp.json()["data"]
    assert [b["butler"] for b in data["by_butler"]] == ["health"]


async def test_aggregate_passes_filters_through_to_sql() -> None:
    """status/since/until/request_id filters reach the aggregate WHERE clause."""
    captured: dict = {}

    def _side_effect(sql, args, **kw):
        captured["sql"] = sql
        captured["args"] = args
        captured["butler_names"] = kw.get("butler_names")
        return {"health": [_make_agg_record({"total": 1, "success_count": 1})]}

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["health"]
    mock_db.fan_out = AsyncMock(side_effect=_side_effect)

    app = create_app()
    app.dependency_overrides[_sessions_get_db] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/sessions/aggregate"
            "?status=failed&butler=health&trigger_source=schedule&request_id=req-1"
        )
    assert resp.status_code == 200
    # success=False bound for status=failed; trigger_source + request_id bound too.
    assert "success = $" in captured["sql"]
    assert "trigger_source = $" in captured["sql"]
    assert "request_id = $" in captured["sql"]
    assert False in captured["args"]
    assert captured["butler_names"] == ["health"]


async def test_aggregate_status_running_uses_success_is_null() -> None:
    """status=running adds the success IS NULL predicate (no bound bool)."""
    captured: dict = {}

    def _side_effect(sql, args, **kw):
        captured["sql"] = sql
        captured["args"] = args
        return {"health": [_make_agg_record({"total": 2, "running_count": 2})]}

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["health"]
    mock_db.fan_out = AsyncMock(side_effect=_side_effect)

    app = create_app()
    app.dependency_overrides[_sessions_get_db] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions/aggregate?status=running")
    assert resp.status_code == 200
    assert "success IS NULL" in captured["sql"]
    # No success bool should be bound for the running filter.
    assert not any(isinstance(a, bool) for a in captured["args"])
    assert resp.json()["data"]["running_count"] == 2


# ---------------------------------------------------------------------------
# ?status=running on GET /api/sessions (list)
# ---------------------------------------------------------------------------


def _make_summary_record(*, success):
    row = {
        "id": uuid4(),
        "prompt": "p",
        "trigger_source": "api",
        "request_id": None,
        "success": success,
        "started_at": _NOW,
        "completed_at": None,
        "duration_ms": None,
        "model": None,
        "complexity": None,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    return m


async def test_list_status_running_filters_to_null_success() -> None:
    """GET /api/sessions?status=running emits the success IS NULL predicate."""
    captured: dict = {}

    def _side_effect(sql, args, **kw):
        captured["sql"] = sql
        captured["args"] = args
        return {"atlas": [_make_summary_record(success=None)]}

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas"]
    mock_db.fan_out = AsyncMock(side_effect=_side_effect)

    app = create_app()
    app.dependency_overrides[_sessions_get_db] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions?status=running")
    assert resp.status_code == 200
    assert "success IS NULL" in captured["sql"]
    assert not any(isinstance(a, bool) for a in captured["args"])
    assert resp.json()["data"][0]["success"] is None


async def test_list_status_running_is_accepted_literal() -> None:
    """'running' is a valid status literal (not a 422)."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas"]
    mock_db.fan_out = AsyncMock(return_value={"atlas": []})

    app = create_app()
    app.dependency_overrides[_sessions_get_db] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/sessions?status=running")
    assert resp.status_code == 200
