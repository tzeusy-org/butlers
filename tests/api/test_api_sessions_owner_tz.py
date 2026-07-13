"""Owner-timezone day-window date filters for the sessions endpoints [bu-hmdqz.12].

The dashboard SessionsPage From/To inputs send bare ``YYYY-MM-DD`` day keys.
Before this fix, FastAPI coerced them to ``datetime`` at midnight in the DB
session timezone (UTC), so ``from=2026-07-11&to=2026-07-11`` compared
``started_at <= 2026-07-11T00:00:00Z`` and returned 0 of that owner-day's
sessions (live-confirmed: total=0 vs to=2026-07-12 → 97).

These tests assert the router now maps a bare day key onto the owner-timezone
calendar-day boundary — From → start of owner day, To → INCLUSIVE end of owner
day (23:59:59.999999) — while a full ISO-8601 timestamp still passes through
unchanged (the verdict opener's rolling-window cutoff path).

They are mocked-pool tests: the fix lives entirely in the router's param
parsing (the SQL predicate ``started_at >= $from AND started_at <= $to`` is
unchanged), so capturing the bound args threaded to ``fan_out_with_status`` is
the faithful contract surface — plus an assertion that a same-day session
timestamp falls inside ``[from, to]`` (the "a session on that day is returned"
contract at the predicate level).
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers import sessions as sessions_router
from butlers.api.routers.sessions import _get_db_manager as _sessions_get_db

pytestmark = pytest.mark.unit

OWNER_TZ = "Asia/Singapore"  # UTC+8, no DST
SGT = ZoneInfo(OWNER_TZ)


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


def _make_app_capturing_args(monkeypatch: pytest.MonkeyPatch) -> tuple[object, MagicMock]:
    """Wire an app whose fan-out records the args tuple it was called with.

    Patches ``resolve_general_timezone`` so the owner timezone is a known,
    non-UTC value (Asia/Singapore) regardless of DB state.
    """
    fan_out_return = ({"atlas": [_make_agg_record({"total": 1})]}, [])
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas"]
    mock_db.fan_out_with_status = AsyncMock(return_value=fan_out_return)

    async def _fake_resolve(_pool) -> str:
        return OWNER_TZ

    monkeypatch.setattr(sessions_router, "resolve_general_timezone", _fake_resolve)

    app = create_app()
    app.dependency_overrides[_sessions_get_db] = lambda: mock_db
    return app, mock_db


async def _get(app, url: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url)


async def test_from_equals_to_spans_the_full_owner_day(monkeypatch) -> None:
    """From=To=<day> must resolve to the full owner-tz day, not a UTC-midpoint zero."""
    app, mock_db = _make_app_capturing_args(monkeypatch)

    resp = await _get(app, "/api/sessions/aggregate?from_date=2026-07-11&to_date=2026-07-11")
    assert resp.status_code == 200

    # Aggregate binds exactly [from_bound, to_bound] into the WHERE clause.
    args = mock_db.fan_out_with_status.call_args.args[1]
    from_bound, to_bound = args

    assert from_bound == datetime(2026, 7, 11, 0, 0, 0, 0, tzinfo=SGT)
    assert to_bound == datetime(2026, 7, 11, 23, 59, 59, 999999, tzinfo=SGT)

    # Contract: a session logged midday on that owner-day is inside [from, to]
    # (the SQL predicate is started_at >= from AND started_at <= to). With the
    # pre-fix UTC-midnight upper bound it would have been excluded.
    midday = datetime(2026, 7, 11, 12, 0, 0, tzinfo=SGT)
    assert from_bound <= midday <= to_bound

    # And a session at 23:30 owner-local (15:30 UTC) — the exact class of row the
    # old inclusive UTC-midnight <= truncated — is still inside the window.
    late = datetime(2026, 7, 11, 23, 30, 0, tzinfo=SGT)
    assert from_bound <= late <= to_bound


async def test_list_and_aggregate_share_the_same_owner_day_bounds(monkeypatch) -> None:
    """KPI/window coherence: the list and aggregate resolve identical bounds."""
    app, mock_db = _make_app_capturing_args(monkeypatch)

    await _get(app, "/api/sessions/aggregate?from_date=2026-07-11&to_date=2026-07-11")
    agg_args = mock_db.fan_out_with_status.call_args.args[1]

    mock_db.fan_out_with_status.reset_mock()
    await _get(app, "/api/sessions?from_date=2026-07-11&to_date=2026-07-11&limit=20")
    # The list keyset path appends cursor/limit params after the WHERE args; the
    # leading two are the from/to bounds.
    list_args = mock_db.fan_out_with_status.call_args.args[1]

    assert list_args[0] == agg_args[0]  # from bound
    assert list_args[1] == agg_args[1]  # to bound
    assert agg_args[0] == datetime(2026, 7, 11, 0, 0, 0, 0, tzinfo=SGT)
    assert agg_args[1] == datetime(2026, 7, 11, 23, 59, 59, 999999, tzinfo=SGT)


async def test_full_iso_timestamp_passes_through_unchanged(monkeypatch) -> None:
    """A full ISO-8601 timestamp (verdict opener cutoff) is used as-is, not re-bucketed."""
    app, mock_db = _make_app_capturing_args(monkeypatch)

    # Naive ISO timestamp (no offset, so no '+' to URL-encode) — the verdict
    # opener sends `Date.toISOString()` ("...Z"); either way a full timestamp is
    # parsed and passed through verbatim, never re-bucketed into a day window. A
    # naive value stays naive (asyncpg then encodes it as UTC), matching prior
    # behavior.
    cutoff = "2026-07-11T06:30:00"
    await _get(app, f"/api/sessions/aggregate?from_date={cutoff}")

    args = mock_db.fan_out_with_status.call_args.args[1]
    assert args[0] == datetime(2026, 7, 11, 6, 30, 0)  # noqa: DTZ001 — naive-passthrough contract


async def test_unparseable_date_is_a_clean_422(monkeypatch) -> None:
    app, _ = _make_app_capturing_args(monkeypatch)
    resp = await _get(app, "/api/sessions/aggregate?from_date=not-a-date")
    assert resp.status_code == 422


async def test_day_key_shaped_invalid_calendar_date_is_a_clean_422(monkeypatch) -> None:
    """A value matching the YYYY-MM-DD shape but with an out-of-range month/day
    (e.g. month 13) must still be a clean 422, not fall through to the
    app-wide ValueError->400 handler via an uncaught datetime() ValueError."""
    app, _ = _make_app_capturing_args(monkeypatch)
    resp = await _get(app, "/api/sessions/aggregate?from_date=2026-13-40")
    assert resp.status_code == 422
