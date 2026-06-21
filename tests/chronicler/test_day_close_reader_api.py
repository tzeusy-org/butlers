"""Tests for GET /api/chronicler/aggregate/day-close.

Covers:
- Cache miss returns 404.
- Fresh cache (no staleness signals) returns DayCloseFreshResponse.
- Stale due to episodes.tombstone_at > cache_built_at.
- Stale due to episodes.updated_at > cache_built_at.
- Stale due to point_events.tombstone_at > cache_built_at.
- Stale due to point_events.updated_at > cache_built_at.
- Stale due to overrides.created_at > cache_built_at (episode override).
- Stale due to provenance-ref episode updated outside cached window.
- Stale due to provenance-ref point_event updated outside cached window.
- Stale due to override corrected_start_at moving episode into cached window (signal 8).
- Stale due to override corrected_start_at moving point_event into cached window (signal 9).
- target_kind filtering: episode branch does not catch point_event overrides and vice versa.
- Stale tie-break: last_invalidating_event_at is the MAX of all signals.
- Staleness query passes cache_key as 4th parameter.

(The no-LLM-import and cross-schema-relation guardrails for router.py are
authoritative in tests/contracts/test_chronicler_no_llm.py and
tests/contracts/test_chronicler_no_cross_schema.py.)
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_ROUTER_PATH = Path(__file__).resolve().parents[2] / "roster" / "chronicler" / "api" / "router.py"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_T_CACHE_BUILT = datetime(2026, 4, 24, 2, 0, 0, tzinfo=UTC)
_T_BEFORE = datetime(2026, 4, 24, 1, 0, 0, tzinfo=UTC)
_T_AFTER = datetime(2026, 4, 24, 3, 0, 0, tzinfo=UTC)
_T_AFTER_2 = datetime(2026, 4, 24, 4, 0, 0, tzinfo=UTC)

_CACHE_START = datetime(2026, 4, 23, 0, 0, 0, tzinfo=UTC)
_CACHE_END = datetime(2026, 4, 24, 0, 0, 0, tzinfo=UTC)


class _Row(dict):
    """dict subclass that mimics asyncpg Record."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def get(self, key: str, default: Any = None) -> Any:
        return super().get(key, default)


def _row(data: dict) -> _Row:
    return _Row(data)


def _cache_row(
    *,
    prose: str = "Yesterday was a productive day.",
    provenance_refs: list[str] | None = None,
    cache_built_at: datetime = _T_CACHE_BUILT,
) -> _Row:
    refs = provenance_refs if provenance_refs is not None else ["core.sessions:abc123"]
    return _row(
        {
            "cache_key": "day_close:2026-04-23",
            "start_at": _CACHE_START,
            "end_at": _CACHE_END,
            "cache_built_at": cache_built_at,
            "prose": prose,
            "provenance_refs": refs,
        }
    )


def _mock_pool(*, fetchrow_side_effect=None, fetchrow_returns=None):
    """Create an asyncpg pool mock.

    ``fetchrow_side_effect`` provides successive return values for
    sequential ``pool.fetchrow`` calls: [cache_row, staleness_row].
    """
    pool = AsyncMock()
    if fetchrow_side_effect is not None:
        pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        pool.fetchrow = AsyncMock(return_value=fetchrow_returns)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock(return_value="OK")
    return pool


def _mock_db(pool):
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    return db


# ---------------------------------------------------------------------------
# Dynamic module loading for the chronicler router
# ---------------------------------------------------------------------------


def _load_chronicler_router():
    module_name = "chronicler_api_router"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, _ROUTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_app(pool):
    chronicler_mod = _load_chronicler_router()
    db = _mock_db(pool)
    app = create_app(api_key="")
    app.dependency_overrides[chronicler_mod._get_db_manager] = lambda: db
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDayCloseReaderCacheMiss:
    async def test_cache_miss_returns_404(self):
        """No tier2_cache row for the requested date → 404."""
        # fetchrow returns None (cache miss) — staleness query never reached.
        pool = _mock_pool(fetchrow_side_effect=[None])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        assert resp.status_code == 404
        assert "2026-04-23" in resp.json()["detail"]


class TestDayCloseReaderValidation:
    """400 error envelopes for missing / malformed parameters."""

    async def test_missing_date_returns_400_envelope(self):
        """Omitting the required date param → 400 with missing_parameter envelope."""
        pool = _mock_pool(fetchrow_side_effect=[None])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close")
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "missing_parameter"
        assert body["error"]["butler"] == "chronicler"
        assert "data" not in body

    async def test_invalid_date_format_returns_400_envelope(self):
        """Supplying a non-YYYY-MM-DD date string → 400 with invalid_date_format envelope."""
        pool = _mock_pool(fetchrow_side_effect=[None])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=not-a-date")
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "invalid_date_format"
        assert body["error"]["butler"] == "chronicler"
        assert "data" not in body

    async def test_invalid_date_format_partial_date(self):
        """Supplying a partial date (MM-DD) → 400 with invalid_date_format envelope."""
        pool = _mock_pool(fetchrow_side_effect=[None])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=04-23")
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "invalid_date_format"
        assert body["error"]["butler"] == "chronicler"

    async def test_400_response_has_no_details_field_when_none(self):
        """ErrorResponse with no details → details key absent from serialized output (exclude_none)."""
        pool = _mock_pool(fetchrow_side_effect=[None])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close")
        assert resp.status_code == 400
        body = resp.json()
        # details field must not appear when None (exclude_none=True)
        assert "details" not in body["error"]


class TestDayCloseReaderFreshCache:
    async def test_fresh_cache_returns_prose_and_provenance(self):
        """No staleness signals → fresh response with prose + provenance_refs."""
        cr = _cache_row()
        # staleness query returns MAX(ts) = NULL (no invalidators)
        stale_row = _row({"last_invalidating_event_at": None})
        pool = _mock_pool(fetchrow_side_effect=[cr, stale_row])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        assert resp.status_code == 200
        body = resp.json()
        assert body["prose"] == "Yesterday was a productive day."
        assert body["provenance_refs"] == ["core.sessions:abc123"]
        assert "cache_built_at" in body
        assert "stale" not in body

    async def test_fresh_cache_provenance_refs_as_json_string(self):
        """provenance_refs stored as JSON string is decoded correctly."""
        refs_json = json.dumps(["spotify.session_summary:s1"])
        cr = _row(
            {
                "cache_key": "day_close:2026-04-23",
                "start_at": _CACHE_START,
                "end_at": _CACHE_END,
                "cache_built_at": _T_CACHE_BUILT,
                "prose": "Music day.",
                "provenance_refs": refs_json,
            }
        )
        stale_row = _row({"last_invalidating_event_at": None})
        pool = _mock_pool(fetchrow_side_effect=[cr, stale_row])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        assert resp.status_code == 200
        assert resp.json()["provenance_refs"] == ["spotify.session_summary:s1"]


class TestDayCloseReaderStaleness:
    """Each test covers one invalidation signal independently."""

    def _stale_row(self, ts: datetime) -> _Row:
        return _row({"last_invalidating_event_at": ts})

    async def _assert_stale(self, invalidating_ts: datetime) -> dict:
        """Helper: build app, call endpoint, assert stale response."""
        cr = _cache_row()
        pool = _mock_pool(fetchrow_side_effect=[cr, self._stale_row(invalidating_ts)])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stale"] is True
        assert "cache_built_at" in body
        assert "last_invalidating_event_at" in body
        assert "prose" not in body
        return body

    async def test_stale_due_to_invalidating_signal(self):
        """Any invalidator with ts > cache_built_at (episode/point tombstone or
        updated_at, override created_at) surfaces as a single MAX signal that
        flips the response to stale. The reader does not branch per signal —
        it consumes the staleness query's MAX(ts), so one behavioral guard
        covers signals 1-5 identically."""
        body = await self._assert_stale(_T_AFTER)
        assert body["last_invalidating_event_at"] is not None

    async def test_stale_tiebreak_last_invalidating_event_at_is_max(self):
        """last_invalidating_event_at is the MAX across all invalidators."""
        # The staleness query returns MAX — simulate two signals where MAX = _T_AFTER_2
        cr = _cache_row()
        # The DB MAX query already returns the tiebreak; just return the larger ts.
        stale_row = _row({"last_invalidating_event_at": _T_AFTER_2})
        pool = _mock_pool(fetchrow_side_effect=[cr, stale_row])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stale"] is True
        # Verify the larger timestamp is surfaced
        assert "2026-04-24T04:00:00" in body["last_invalidating_event_at"]

    async def test_cache_built_at_preserved_in_stale_response(self):
        """cache_built_at in the stale response matches the stored cache row value."""
        cr = _cache_row(cache_built_at=_T_CACHE_BUILT)
        stale_row = self._stale_row(_T_AFTER)
        pool = _mock_pool(fetchrow_side_effect=[cr, stale_row])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        body = resp.json()
        assert body["stale"] is True
        # cache_built_at should reflect _T_CACHE_BUILT (2026-04-24T02:00:00)
        assert "2026-04-24T02:00:00" in body["cache_built_at"]


class TestDayCloseProvenanceRefStaleness:
    """Staleness signal: cited rows updated to move outside the cached window.

    The provenance-ref branches (signals 6 and 7) detect when an episode or
    point_event cited in tier2_cache.provenance_refs has been updated after
    cache_built_at — even if its current time range is now outside the cached
    window so the window-scoped branches would miss it.
    """

    def _stale_row(self, ts: datetime) -> _Row:
        return _row({"last_invalidating_event_at": ts})

    async def test_stale_due_to_provenance_episode_updated_outside_window(self):
        """Episode cited in provenance_refs updated outside cached window triggers stale.

        This is the core regression scenario: the episode was originally in
        the window (and thus cited), but was later updated to move its
        start_at/end_at outside [cache_start, cache_end).  The window-scoped
        branches miss it; the provenance-ref branch catches it.
        """
        # Cache row cites an episode source_ref.
        cr = _cache_row(provenance_refs=["core.sessions:session-abc"])
        # Staleness query returns a non-null timestamp (the updated_at of the
        # cited episode, now outside the window).
        stale_row = self._stale_row(_T_AFTER)
        pool = _mock_pool(fetchrow_side_effect=[cr, stale_row])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stale"] is True
        assert "last_invalidating_event_at" in body
        assert "prose" not in body

    async def test_stale_due_to_provenance_point_event_updated_outside_window(self):
        """Point event cited in provenance_refs updated outside cached window triggers stale."""
        cr = _cache_row(provenance_refs=["spotify.track_play:play-xyz"])
        stale_row = self._stale_row(_T_AFTER)
        pool = _mock_pool(fetchrow_side_effect=[cr, stale_row])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stale"] is True
        assert "prose" not in body

    async def test_staleness_query_passes_cache_key_as_fourth_parameter(self):
        """Staleness fetchrow is called with cache_key as the 4th positional argument.

        The provenance-ref branches join against tier2_cache by cache_key ($4).
        This test verifies the correct argument is passed.
        """
        cr = _cache_row(provenance_refs=["core.sessions:abc"])
        stale_row = _row({"last_invalidating_event_at": None})
        pool = _mock_pool(fetchrow_side_effect=[cr, stale_row])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")

        # pool.fetchrow called twice: once for cache lookup, once for staleness.
        assert pool.fetchrow.call_count == 2
        staleness_call = pool.fetchrow.call_args_list[1]
        # positional args after the SQL string: start_at, end_at, cache_built_at, cache_key
        call_args = staleness_call[0]  # positional args tuple
        assert len(call_args) == 5, f"Expected SQL + 4 args, got {len(call_args)} args"
        # 5th element (index 4) is cache_key
        assert call_args[4] == "day_close:2026-04-23"

    async def test_fresh_cache_with_provenance_refs_no_stale(self):
        """Provenance-ref branches do not trigger stale when no updates occurred."""
        cr = _cache_row(provenance_refs=["core.sessions:session-abc"])
        # MAX returns NULL → fresh
        stale_row = _row({"last_invalidating_event_at": None})
        pool = _mock_pool(fetchrow_side_effect=[cr, stale_row])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        assert resp.status_code == 200
        body = resp.json()
        assert "stale" not in body
        assert body["prose"] == "Yesterday was a productive day."


class TestDayCloseCorrectedStartAtStaleness:
    """Staleness signal 8: override sets corrected_start_at inside the cached window.

    An override created after cache_built_at that moves an episode INTO the
    cached window via corrected_start_at triggers staleness.  The episode's
    original start_at lies outside [cache_start, cache_end), so signals 1-5
    (which scope via the episode's current window position) would miss it.
    """

    def _stale_row(self, ts: datetime) -> _Row:
        return _row({"last_invalidating_event_at": ts})

    async def test_stale_due_to_corrected_start_at_inside_window(self):
        """Override created after cache_built_at with corrected_start_at inside [start, end) → stale.

        Scenario:
        - Episode originally starts outside [_CACHE_START, _CACHE_END).
        - After the cache was built, an override sets corrected_start_at to a
          timestamp inside the window, pulling the episode into scope.
        - The staleness query must detect this via corrected_start_at.
        """
        cr = _cache_row()
        # Simulate the DB returning a non-null MAX from the corrected_start_at branch.
        stale_row = self._stale_row(_T_AFTER)
        pool = _mock_pool(fetchrow_side_effect=[cr, stale_row])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stale"] is True
        assert "last_invalidating_event_at" in body
        assert "prose" not in body

    async def test_no_stale_when_corrected_start_at_outside_window(self):
        """Override with corrected_start_at outside the window does not trigger stale."""
        cr = _cache_row()
        # MAX returns NULL — no corrected_start_at override falls inside the window.
        stale_row = _row({"last_invalidating_event_at": None})
        pool = _mock_pool(fetchrow_side_effect=[cr, stale_row])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        assert resp.status_code == 200
        body = resp.json()
        assert "stale" not in body
        assert body["prose"] == "Yesterday was a productive day."


class TestDayCloseStalenessQueryShape:
    """Source-shape guard for the staleness CTE in get_day_close_cache.

    The staleness query is a UNION of nine sub-SELECTs feeding MAX(ts). The
    behavioral tests above mock the staleness fetchrow's MAX result, so they
    cannot catch a refactor that silently drops one of the nine signal
    branches (the mock would still return a non-null MAX from whatever branch
    remains). This single source-text guard re-pins the CTE window shape so a
    refactor that drops the corrected_start_at branches or collapses the
    episode/point_event target_kind split fails here rather than silently in
    production.
    """

    def test_staleness_query_contains_signal_branch_markers(self):
        source = _ROUTER_PATH.read_text()
        # corrected_start_at signals (8 + 9) move an episode/point_event INTO
        # the window via an override; without this column the override-window
        # signals collapse back to the start_at/occurred_at scoped branches.
        assert "corrected_start_at" in source
        # episode-scoped override branch must filter on target_kind = 'episode'
        # so episode overrides do not catch point_event targets and vice versa.
        assert "o.target_kind = 'episode'" in source
        # signal-9 marker: the point_event corrected_start_at branch is the only
        # place the `point_events pe` join alias appears. Its absence means the
        # point_event corrected_start_at signal was dropped.
        assert "JOIN point_events pe ON pe.id = o.target_id" in source
        assert "o.target_kind = 'point_event'" in source


class TestDayCloseCorrectedStartAtPointEventStaleness:
    """Staleness signal 9: override sets corrected_start_at on a point_event inside the cached window.

    An override created after cache_built_at that moves a point_event INTO the
    cached window via corrected_start_at triggers staleness.  The point_event's
    original occurred_at lies outside [cache_start, cache_end), so signals 1-5
    (which scope via the point_event's current occurred_at) would miss it.

    This is the sibling signal to signal 8 (episode corrected_start_at) and
    ensures target_kind='point_event' overrides are caught independently.
    """

    def _stale_row(self, ts: datetime) -> _Row:
        return _row({"last_invalidating_event_at": ts})

    async def test_stale_due_to_point_event_corrected_start_at_inside_window(self):
        """Override on a point_event with corrected_start_at inside [start, end) → stale.

        Scenario:
        - Point event originally has occurred_at outside [_CACHE_START, _CACHE_END).
        - After the cache was built, an override sets corrected_start_at to a
          timestamp inside the window, pulling the point_event into scope.
        - The staleness query must detect this via the point_event corrected_start_at branch.
        """
        cr = _cache_row()
        # Simulate the DB returning a non-null MAX from the point_event corrected_start_at branch.
        stale_row = self._stale_row(_T_AFTER)
        pool = _mock_pool(fetchrow_side_effect=[cr, stale_row])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stale"] is True
        assert "last_invalidating_event_at" in body
        assert "prose" not in body

    async def test_no_stale_when_point_event_corrected_start_at_outside_window(self):
        """Override on a point_event with corrected_start_at outside window does not stale."""
        cr = _cache_row()
        # MAX returns NULL — no corrected_start_at override on point_event falls inside the window.
        stale_row = _row({"last_invalidating_event_at": None})
        pool = _mock_pool(fetchrow_side_effect=[cr, stale_row])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        assert resp.status_code == 200
        body = resp.json()
        assert "stale" not in body
        assert body["prose"] == "Yesterday was a productive day."
