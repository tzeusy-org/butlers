"""Tests for GET /api/calendar/workspace/search (bu-kowq7e).

Covers: ranked title/description/location matches, empty/blank query → empty,
lane + butler/source scoping, and the degraded (pg_trgm unavailable) fail-open
path (ILIKE fallback and skip-on-double-failure).

The search read-model issues its queries directly against ``db.pool(name)``
(rather than ``db.fan_out``) so it can fall back per-schema, so these tests mock
``pool(name).fetch`` instead of ``fan_out``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.calendar_workspace import _get_db_manager

pytestmark = pytest.mark.unit


def _search_row(
    *,
    title: str,
    search_rank: float,
    description: str | None = "desc",
    location: str | None = "loc",
    lane: str = "user",
    source_key: str = "provider:google:primary",
    butler_name: str | None = None,
    start: datetime | None = None,
) -> dict:
    start = start or datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
    end = datetime(2026, 2, 22, 15, 0, tzinfo=UTC)
    synced_at = datetime.now(tz=UTC)
    return {
        "instance_id": uuid4(),
        "source_id": uuid4(),
        "source_key": source_key,
        "source_kind": "provider_event" if lane == "user" else "internal_scheduler",
        "lane": lane,
        "provider": "google" if lane == "user" else "internal",
        "calendar_id": "primary" if lane == "user" else None,
        "butler_name": butler_name,
        "display_name": source_key,
        "writable": True,
        "source_metadata": {"projection": "test"},
        "event_id": uuid4(),
        "origin_ref": str(uuid4()),
        "origin_instance_ref": str(uuid4()),
        "title": title,
        "description": description,
        "location": location,
        "event_timezone": "UTC",
        "all_day": False,
        "event_status": "confirmed",
        "visibility": "default",
        "recurrence_rule": None,
        "event_metadata": {"source_type": "provider_event"} if lane == "user" else {},
        "instance_timezone": "UTC",
        "instance_starts_at": start,
        "instance_ends_at": end,
        "instance_status": "confirmed",
        "instance_metadata": {},
        "cursor_name": "provider_sync",
        "last_synced_at": synced_at,
        "last_success_at": synced_at,
        "last_error_at": None,
        "last_error": None,
        "full_sync_required": False,
        "search_rank": search_rank,
    }


def _build_app(
    app,
    *,
    rows_by_butler: dict[str, list[dict]] | None = None,
    calendar_butlers: list[str] | None = None,
    fail_trgm_for: set[str] | None = None,
    fail_all_for: set[str] | None = None,
) -> tuple:
    rows_by_butler = rows_by_butler or {}
    fail_trgm_for = fail_trgm_for or set()
    fail_all_for = fail_all_for or set()
    calendar_butlers = calendar_butlers or sorted(rows_by_butler)

    fetch_calls: list[tuple[str, str, tuple]] = []

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = calendar_butlers
    mock_db.butlers_with_module = MagicMock(return_value=calendar_butlers)

    def _pool_for(name: str):
        async def _fetch(sql: str, *args):
            fetch_calls.append((name, sql, args))
            is_trgm = "similarity(" in sql
            if name in fail_all_for:
                raise RuntimeError("relation pg_trgm unavailable")
            if name in fail_trgm_for and is_trgm:
                raise RuntimeError("function similarity(text, text) does not exist")
            return rows_by_butler.get(name, [])

        pool = MagicMock()
        pool.fetch = AsyncMock(side_effect=_fetch)
        return pool

    mock_db.pool = MagicMock(side_effect=_pool_for)

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app, mock_db, fetch_calls


async def _get(app, params):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/calendar/workspace/search", params=params)


# ---------------------------------------------------------------------------
# Empty / blank query → empty (never the whole calendar)
# ---------------------------------------------------------------------------


async def test_search_missing_query_returns_empty(app):
    app, mock_db, fetch_calls = _build_app(
        app, rows_by_butler={"general": [_search_row(title="Dentist", search_rank=0.9)]}
    )
    resp = await _get(app, {"view": "user"})
    assert resp.status_code == 200
    assert resp.json()["data"]["entries"] == []
    # No DB query should be issued for a blank query.
    assert fetch_calls == []


async def test_search_blank_query_returns_empty(app):
    app, _, fetch_calls = _build_app(
        app, rows_by_butler={"general": [_search_row(title="Dentist", search_rank=0.9)]}
    )
    resp = await _get(app, {"view": "user", "q": "   "})
    assert resp.status_code == 200
    assert resp.json()["data"]["entries"] == []
    assert fetch_calls == []


# ---------------------------------------------------------------------------
# Ranked matches across title/description/location
# ---------------------------------------------------------------------------


async def test_search_ranks_matches_by_relevance(app):
    high = _search_row(title="Dentist appointment", search_rank=0.91)
    mid = _search_row(title="Dental cleaning", description="dentist follow up", search_rank=0.42)
    low = _search_row(title="Lunch", location="Dentist Plaza", search_rank=0.18)
    app, _, _ = _build_app(
        app,
        rows_by_butler={"general": [mid, low], "relationship": [high]},
    )
    resp = await _get(app, {"view": "user", "q": "dentist"})
    assert resp.status_code == 200
    entries = resp.json()["data"]["entries"]
    assert [e["title"] for e in entries] == [
        "Dentist appointment",
        "Dental cleaning",
        "Lunch",
    ]
    # Each entry carries its match date so the UI can group by day + jump-to.
    assert all(e["start_at"].startswith("2026-02-22") for e in entries)


# ---------------------------------------------------------------------------
# Lane + butler/source scoping
# ---------------------------------------------------------------------------


async def test_search_respects_butler_scope(app):
    app, mock_db, fetch_calls = _build_app(
        app,
        rows_by_butler={
            "general": [_search_row(title="Dentist", search_rank=0.9)],
            "relationship": [_search_row(title="Dentist too", search_rank=0.8)],
        },
    )
    resp = await _get(app, {"view": "user", "q": "dentist", "butlers": "general"})
    assert resp.status_code == 200
    entries = resp.json()["data"]["entries"]
    assert [e["title"] for e in entries] == ["Dentist"]
    # Only the scoped butler schema is queried.
    queried = {name for name, _sql, _args in fetch_calls}
    assert queried == {"general"}


async def test_search_passes_lane_and_source_filters_to_query(app):
    app, _, fetch_calls = _build_app(
        app, rows_by_butler={"general": [_search_row(title="Dentist", search_rank=0.9)]}
    )
    resp = await _get(
        app,
        {"view": "user", "q": "dentist", "sources": "provider:google:primary"},
    )
    assert resp.status_code == 200
    assert len(fetch_calls) == 1
    _name, sql, args = fetch_calls[0]
    # Lane bound at $2; source filter present in SQL and bound in args.
    assert "s.lane = $2" in sql
    assert "s.source_key = ANY(" in sql
    assert "user" in args
    assert ["provider:google:primary"] in args


# ---------------------------------------------------------------------------
# Degraded path — fail-open when pg_trgm / the index is unavailable
# ---------------------------------------------------------------------------


async def test_search_falls_back_to_ilike_when_trigram_unavailable(app):
    """A schema lacking pg_trgm degrades to ILIKE rather than 500ing."""
    row = _search_row(title="Dentist appointment", search_rank=0.0)
    app, _, fetch_calls = _build_app(
        app,
        rows_by_butler={"general": [row]},
        fail_trgm_for={"general"},
    )
    resp = await _get(app, {"view": "user", "q": "dentist"})
    assert resp.status_code == 200
    entries = resp.json()["data"]["entries"]
    assert [e["title"] for e in entries] == ["Dentist appointment"]
    # The trigram query was attempted then the ILIKE fallback ran (2 fetches).
    sqls = [sql for _name, sql, _args in fetch_calls]
    assert any("similarity(" in s for s in sqls)
    assert any("similarity(" not in s for s in sqls)


async def test_search_skips_schema_when_both_queries_fail(app):
    """A fully-failing schema is skipped; healthy schemas still return.

    A *partial* failure is NOT degraded: at least one schema responded, so the
    envelope stays ``available=true`` with whatever matched.
    """
    healthy = _search_row(title="Dentist healthy", search_rank=0.7)
    app, _, _ = _build_app(
        app,
        rows_by_butler={"general": [healthy], "relationship": []},
        fail_all_for={"relationship"},
    )
    resp = await _get(app, {"view": "user", "q": "dentist"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert [e["title"] for e in data["entries"]] == ["Dentist healthy"]
    assert data["available"] is True


async def test_search_degrades_open_when_all_schemas_fail(app):
    """Fault injection: when EVERY targeted schema fails, signal ``available=false``.

    Empty ``entries`` then means the search could not run, NOT that nothing
    matched — the UI must render "search unavailable" rather than "no results".
    """
    app, _, _ = _build_app(
        app,
        rows_by_butler={"general": [], "relationship": []},
        calendar_butlers=["general", "relationship"],
        fail_all_for={"general", "relationship"},
    )
    resp = await _get(app, {"view": "user", "q": "dentist"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["entries"] == []
    assert data["available"] is False


async def test_search_available_true_on_genuine_empty(app):
    """A successful search with zero hits is honest (``available=true``)."""
    app, _, _ = _build_app(
        app,
        rows_by_butler={"general": []},
        calendar_butlers=["general"],
    )
    resp = await _get(app, {"view": "user", "q": "nonexistent"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["entries"] == []
    assert data["available"] is True
