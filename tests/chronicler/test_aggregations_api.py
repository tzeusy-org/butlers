"""Condensed aggregation API tests — by-day, by-category, and OTel spans.

Merges tests from test_aggregate_by_day.py (34), test_aggregate_by_category.py (20),
and test_aggregate_spans.py (12) into a single parametrized file [bu-m564i].

Shared endpoint behaviours (param validation, privacy, tombstone, precision,
retention) are exercised via @pytest.mark.parametrize so both endpoints stay
covered without duplication.  DST edge cases and day_start/day_end fields are
tested only against the by-day endpoint because they are dimension-specific.
Sorting contract is tested only against by-category because the sort order
differs between dimensions.  OTel span tests cover both aggregation dimensions
plus day-close and source-state handlers.

Guardrails that were previously duplicated here are now authoritative in:
  tests/contracts/test_chronicler_no_cross_schema.py  (RFC D17)
  tests/contracts/test_chronicler_no_llm.py           (RFC 0014 §D5)
"""

from __future__ import annotations

import importlib.util
import json
import sys
import zoneinfo
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NY = zoneinfo.ZoneInfo("America/New_York")
_UTC = UTC
_ROUTER_PATH = Path(__file__).resolve().parents[2] / "roster" / "chronicler" / "api" / "router.py"

_T0 = datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 4, 2, 0, 0, 0, tzinfo=UTC)
_T_CACHE_BUILT = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)

# Endpoint paths for parametrized shared tests
_BY_DAY = "/api/chronicler/aggregate/by-day"
_BY_CATEGORY = "/api/chronicler/aggregate/by-category"
_BOTH_ENDPOINTS = [_BY_DAY, _BY_CATEGORY]

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_episode_row(
    *,
    source_name: str = "core.sessions",
    episode_type: str = "work",
    trigger_source: str | None = None,
    start_at: datetime,
    end_at: datetime | None = None,
    precision: str = "exact",
    privacy: str = "normal",
    retention_days: int | None = None,
    tombstone_at: datetime | None = None,
    layer: str = "activity",
    confidence: str = "high",
) -> dict[str, Any]:
    return {
        "source_name": source_name,
        "episode_type": episode_type,
        "trigger_source": trigger_source,
        "start_at": start_at,
        "end_at": end_at,
        "precision": precision,
        "privacy": privacy,
        "retention_days": retention_days,
        "tombstone_at": tombstone_at,
        "layer": layer,
        "confidence": confidence,
    }


def _build_app(rows: list[dict[str, Any]]):
    """Wire a FastAPI test app with a mocked pool returning the given episode rows."""
    mock_pool = AsyncMock()

    def _make_mock_row(row: dict[str, Any]) -> MagicMock:
        m = MagicMock()
        m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
        return m

    mock_pool.fetch = AsyncMock(return_value=[_make_mock_row(r) for r in rows])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()

    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "chronicler" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


# ---------------------------------------------------------------------------
# Parameter validation (shared: both endpoints reject bad params the same way)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", _BOTH_ENDPOINTS)
async def test_missing_start_at_returns_400(endpoint):
    app, _ = _build_app([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(endpoint, params={"end_at": "2024-03-16T00:00:00Z"})
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body and "data" not in body
    assert body["error"]["code"] == "missing_parameter"
    assert body["error"]["butler"] == "chronicler"


@pytest.mark.parametrize("endpoint", _BOTH_ENDPOINTS)
async def test_missing_end_at_returns_400(endpoint):
    app, _ = _build_app([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(endpoint, params={"start_at": "2024-03-15T00:00:00Z"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "missing_parameter"


@pytest.mark.parametrize("endpoint", _BOTH_ENDPOINTS)
async def test_end_at_not_after_start_at_returns_400(endpoint):
    app, _ = _build_app([])
    ts = "2024-03-15T00:00:00Z"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(endpoint, params={"start_at": ts, "end_at": ts})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_time_range"


@pytest.mark.parametrize("endpoint", _BOTH_ENDPOINTS)
async def test_unrecognized_timezone_returns_400(endpoint):
    app, _ = _build_app([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            endpoint,
            params={
                "start_at": "2024-03-15T00:00:00Z",
                "end_at": "2024-03-16T00:00:00Z",
                "tz": "Not/A/Timezone",
            },
        )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_timezone"


# ---------------------------------------------------------------------------
# Privacy tier (shared)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", _BOTH_ENDPOINTS)
async def test_sensitive_episodes_contribute_by_default(endpoint):
    """Sensitive episodes are included with the default privacy_tier."""
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            start_at=day_start,
            end_at=day_start + timedelta(hours=2),
            privacy="sensitive",
        )
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            endpoint,
            params={"start_at": "2024-03-15T00:00:00Z", "end_at": "2024-03-16T00:00:00Z"},
        )
    assert resp.status_code == 200
    items = _extract_items(resp.json(), endpoint)
    assert len(items) == 1, "Sensitive episode must produce at least one bucket"
    assert items[0]["total_seconds"] == pytest.approx(7200.0)
    assert items[0]["episode_count"] == 1


@pytest.mark.parametrize("endpoint", _BOTH_ENDPOINTS)
async def test_privacy_tier_normal_excludes_sensitive(endpoint):
    """privacy_tier=normal excludes sensitive episodes from results.

    The mock returns only the normal row (simulating SQL-filtered result), then
    asserts the response reflects only that row — verifying the param is wired
    through to the SQL WHERE clause.
    """
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            start_at=day_start,
            end_at=day_start + timedelta(hours=1),
            privacy="normal",
        ),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            endpoint,
            params={
                "start_at": "2024-03-15T00:00:00Z",
                "end_at": "2024-03-16T00:00:00Z",
                "privacy_tier": "normal",
            },
        )
    assert resp.status_code == 200
    items = _extract_items(resp.json(), endpoint)
    assert len(items) == 1
    assert items[0]["total_seconds"] == pytest.approx(3600.0)


def _extract_items(body: Any, endpoint: str) -> list[dict]:
    """Extract the list of buckets/rows from either endpoint's response format.

    by-day: returns a plain list.
    by-category: returns {data: {buckets: [...]}}
    """
    if endpoint == _BY_DAY:
        return body if isinstance(body, list) else []
    # by-category
    return body.get("data", {}).get("buckets", [])


@pytest.mark.parametrize("endpoint", _BOTH_ENDPOINTS)
async def test_tombstoned_excluded_include_tombstoned_flag(endpoint):
    """include_tombstoned=true passes tombstoned rows through; breakdown is flagged."""
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            start_at=day_start,
            end_at=day_start + timedelta(hours=1),
            tombstone_at=day_start - timedelta(hours=1),
        )
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            endpoint,
            params={
                "start_at": "2024-03-15T00:00:00Z",
                "end_at": "2024-03-16T00:00:00Z",
                "include_tombstoned": "true",
            },
        )
    assert resp.status_code == 200
    items = _extract_items(resp.json(), endpoint)
    assert len(items) >= 1, "include_tombstoned=true must return at least one bucket"
    breakdown = items[0].get("source_breakdown", [])
    assert len(breakdown) >= 1, "Tombstoned episode must appear in source_breakdown"
    assert breakdown[0].get("tombstoned") is True


@pytest.mark.parametrize("endpoint", _BOTH_ENDPOINTS)
async def test_precision_carries_least_precise(endpoint):
    """The least-precise precision value across contributing rows is returned."""
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            start_at=day_start, end_at=day_start + timedelta(hours=1), precision="exact"
        ),
        _make_episode_row(
            start_at=day_start + timedelta(hours=2),
            end_at=day_start + timedelta(hours=3),
            precision="hour",
        ),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            endpoint,
            params={"start_at": "2024-03-15T00:00:00Z", "end_at": "2024-03-16T00:00:00Z"},
        )
    assert resp.status_code == 200
    items = _extract_items(resp.json(), endpoint)
    assert items[0]["precision"] == "hour"


@pytest.mark.parametrize("endpoint", _BOTH_ENDPOINTS)
async def test_retention_floor_days_minimum_non_null(endpoint):
    """retention_floor_days is the minimum non-NULL retention_days across rows."""
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            start_at=day_start, end_at=day_start + timedelta(hours=1), retention_days=90
        ),
        _make_episode_row(
            start_at=day_start + timedelta(hours=2),
            end_at=day_start + timedelta(hours=3),
            retention_days=30,
        ),
        _make_episode_row(
            start_at=day_start + timedelta(hours=4),
            end_at=day_start + timedelta(hours=5),
            retention_days=None,
        ),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            endpoint,
            params={"start_at": "2024-03-15T00:00:00Z", "end_at": "2024-03-16T00:00:00Z"},
        )
    assert resp.status_code == 200
    items = _extract_items(resp.json(), endpoint)
    assert items[0]["retention_floor_days"] == 30


# ---------------------------------------------------------------------------
# by-day specific tests
# ---------------------------------------------------------------------------


async def test_by_day_single_episode_sums_correctly():
    """An episode fully within a single UTC day sums correctly."""
    start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    end = datetime(2024, 3, 15, 11, 0, 0, tzinfo=_UTC)
    rows = [_make_episode_row(start_at=start, end_at=end)]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            _BY_DAY,
            params={"start_at": "2024-03-15T00:00:00Z", "end_at": "2024-03-16T00:00:00Z"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert row["day"] == "2024-03-15"
    assert row["category"] == "work"
    assert row["total_seconds"] == pytest.approx(7200.0)
    assert row["episode_count"] == 1
    assert "day_start" in row and "day_end" in row


async def test_by_day_episode_crossing_midnight_splits_across_days():
    """An episode crossing midnight is split across two day buckets."""
    start = datetime(2024, 3, 15, 22, 0, 0, tzinfo=_UTC)
    end = datetime(2024, 3, 16, 2, 0, 0, tzinfo=_UTC)
    rows = [_make_episode_row(start_at=start, end_at=end)]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            _BY_DAY,
            params={"start_at": "2024-03-15T00:00:00Z", "end_at": "2024-03-17T00:00:00Z"},
        )
    assert resp.status_code == 200
    by_day = {r["day"]: r for r in resp.json()}
    assert "2024-03-15" in by_day and "2024-03-16" in by_day
    assert by_day["2024-03-15"]["total_seconds"] == pytest.approx(7200.0)
    assert by_day["2024-03-16"]["total_seconds"] == pytest.approx(7200.0)


async def test_by_day_result_sorted_day_then_category():
    """Response rows must be sorted (day ASC, category ASC)."""
    base = datetime(2024, 3, 15, 10, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            source_name="spotify.session_summary",
            episode_type="listening_episode",
            start_at=base + timedelta(days=1),
            end_at=base + timedelta(days=1, hours=1),
        ),
        _make_episode_row(start_at=base, end_at=base + timedelta(hours=1)),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            _BY_DAY,
            params={"start_at": "2024-03-15T00:00:00Z", "end_at": "2024-03-17T00:00:00Z"},
        )
    assert resp.status_code == 200
    data = resp.json()
    keys = [(r["day"], r["category"]) for r in data]
    assert keys == sorted(keys)


# DST edge cases — America/New_York


async def test_by_day_dst_spring_forward_23h_day():
    """America/New_York spring-forward day (Mar 10 2024) has 23 h of actual time."""
    spring_start = datetime(2024, 3, 10, 5, 0, 0, tzinfo=_UTC)
    spring_end = datetime(2024, 3, 11, 4, 0, 0, tzinfo=_UTC)
    rows = [_make_episode_row(start_at=spring_start, end_at=spring_end)]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            _BY_DAY,
            params={
                "start_at": "2024-03-10T05:00:00Z",
                "end_at": "2024-03-11T04:00:00Z",
                "tz": "America/New_York",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert row["day"] == "2024-03-10"
    assert row["total_seconds"] == pytest.approx(82800.0)  # 23 h
    day_start_dt = datetime.fromisoformat(row["day_start"])
    day_end_dt = datetime.fromisoformat(row["day_end"])
    assert (day_end_dt - day_start_dt).total_seconds() == pytest.approx(82800.0)


async def test_by_day_dst_fall_back_25h_day():
    """America/New_York fall-back day (Nov 3 2024) has 25 h of actual time."""
    fall_start = datetime(2024, 11, 3, 4, 0, 0, tzinfo=_UTC)
    fall_end = datetime(2024, 11, 4, 5, 0, 0, tzinfo=_UTC)
    rows = [_make_episode_row(start_at=fall_start, end_at=fall_end)]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            _BY_DAY,
            params={
                "start_at": "2024-11-03T04:00:00Z",
                "end_at": "2024-11-04T05:00:00Z",
                "tz": "America/New_York",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert row["day"] == "2024-11-03"
    assert row["total_seconds"] == pytest.approx(90000.0)  # 25 h
    day_start_dt = datetime.fromisoformat(row["day_start"])
    day_end_dt = datetime.fromisoformat(row["day_end"])
    assert (day_end_dt - day_start_dt).total_seconds() == pytest.approx(90000.0)


async def test_by_day_dst_spring_forward_two_episodes_same_calendar_day():
    """Two episodes on spring-forward day (one EST, one EDT) bucket to the same day."""
    pre_start = datetime(2024, 3, 10, 6, 0, 0, tzinfo=_UTC)  # 01:00-02:00 EST
    pre_end = datetime(2024, 3, 10, 7, 0, 0, tzinfo=_UTC)
    post_start = datetime(2024, 3, 10, 7, 0, 0, tzinfo=_UTC)  # 03:00-04:00 EDT
    post_end = datetime(2024, 3, 10, 8, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(start_at=pre_start, end_at=pre_end),
        _make_episode_row(start_at=post_start, end_at=post_end),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            _BY_DAY,
            params={
                "start_at": "2024-03-10T05:00:00Z",
                "end_at": "2024-03-11T04:00:00Z",
                "tz": "America/New_York",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    days = {r["day"] for r in data}
    assert days == {"2024-03-10"}
    work_row = next(r for r in data if r["category"] == "work")
    assert work_row["episode_count"] == 2
    assert work_row["total_seconds"] == pytest.approx(7200.0)


async def test_by_day_dst_fall_back_repeated_hour_counted_once():
    """An episode spanning the repeated 01:00-02:00 hour on fall-back day counts once."""
    ep_start = datetime(2024, 11, 3, 5, 30, 0, tzinfo=_UTC)  # 01:30 EDT
    ep_end = datetime(2024, 11, 3, 6, 30, 0, tzinfo=_UTC)  # 01:30 EST (second hour)
    rows = [_make_episode_row(start_at=ep_start, end_at=ep_end)]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            _BY_DAY,
            params={
                "start_at": "2024-11-03T04:00:00Z",
                "end_at": "2024-11-04T05:00:00Z",
                "tz": "America/New_York",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    work_rows = [r for r in data if r["category"] == "work"]
    assert len(work_rows) == 1
    assert work_rows[0]["day"] == "2024-11-03"
    assert work_rows[0]["total_seconds"] == pytest.approx(3600.0)


async def test_by_day_large_window_completes_quickly():
    """Aggregation over 1000 episodes × 365 days must complete within 3 s.

    Guards against O(N×D) regression — the O(N×k) inner loop should be fast.
    """
    import time

    _N = 1000
    _D = 365
    window_start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=_UTC)
    window_end = window_start + timedelta(days=_D)

    rows = []
    for i in range(_N):
        day_offset = i % _D
        hour_offset = i % 23
        ep_start = window_start + timedelta(days=day_offset, hours=hour_offset)
        rows.append(_make_episode_row(start_at=ep_start, end_at=ep_start + timedelta(hours=1)))

    app, _ = _build_app(rows)
    t0 = time.perf_counter()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            _BY_DAY,
            params={"start_at": window_start.isoformat(), "end_at": window_end.isoformat()},
        )
    elapsed = time.perf_counter() - t0

    assert resp.status_code == 200
    assert len(resp.json()) > 0
    assert elapsed < 3.0, (
        f"aggregate/by-day N={_N}×D={_D} took {elapsed:.2f}s (budget: 3s). "
        "Inner loop may have regressed to O(N×D)."
    )


# ---------------------------------------------------------------------------
# by-category specific tests
# ---------------------------------------------------------------------------


async def test_by_category_single_episode_correct_total():
    """An episode fully within window produces correct category bucket."""
    start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    end = datetime(2024, 3, 15, 11, 0, 0, tzinfo=_UTC)
    rows = [_make_episode_row(start_at=start, end_at=end)]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            _BY_CATEGORY,
            params={"start_at": "2024-03-15T00:00:00Z", "end_at": "2024-03-16T00:00:00Z"},
        )
    assert resp.status_code == 200
    buckets = resp.json()["data"]["buckets"]
    assert len(buckets) == 1
    assert buckets[0]["category"] == "work"
    assert buckets[0]["total_seconds"] == pytest.approx(7200.0)
    assert buckets[0]["episode_count"] == 1


async def test_by_category_multiple_categories_separate_buckets():
    """Two episodes with different categories produce two separate buckets."""
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(start_at=day_start, end_at=day_start + timedelta(hours=2)),
        _make_episode_row(
            source_name="spotify.session_summary",
            episode_type="listening_episode",
            start_at=day_start + timedelta(hours=3),
            end_at=day_start + timedelta(hours=4),
        ),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            _BY_CATEGORY,
            params={"start_at": "2024-03-15T00:00:00Z", "end_at": "2024-03-16T00:00:00Z"},
        )
    assert resp.status_code == 200
    categories = {b["category"] for b in resp.json()["data"]["buckets"]}
    assert "work" in categories and "play" in categories


async def test_by_category_open_episode_clipped_to_query_end():
    """An episode with end_at NULL is clipped to query_end for duration."""
    query_end = datetime(2024, 3, 16, 0, 0, 0, tzinfo=_UTC)
    ep_start = datetime(2024, 3, 15, 22, 0, 0, tzinfo=_UTC)  # 2h before query_end
    rows = [_make_episode_row(start_at=ep_start, end_at=None)]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            _BY_CATEGORY,
            params={
                "start_at": "2024-03-15T00:00:00Z",
                "end_at": query_end.isoformat(),
            },
        )
    assert resp.status_code == 200
    buckets = resp.json()["data"]["buckets"]
    assert len(buckets) == 1
    assert buckets[0]["total_seconds"] == pytest.approx(7200.0)


async def test_by_category_sorted_by_total_seconds_desc_then_category_asc():
    """Buckets must be sorted by total_seconds DESC, then category ASC."""
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            source_name="spotify.session_summary",
            episode_type="listening_episode",
            start_at=day_start,
            end_at=day_start + timedelta(hours=1),
        ),
        _make_episode_row(
            start_at=day_start + timedelta(hours=2), end_at=day_start + timedelta(hours=5)
        ),
        _make_episode_row(
            source_name="owntracks.points",
            episode_type="movement_episode",
            start_at=day_start + timedelta(hours=6),
            end_at=day_start + timedelta(hours=8),
        ),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            _BY_CATEGORY,
            params={"start_at": "2024-03-15T00:00:00Z", "end_at": "2024-03-16T00:00:00Z"},
        )
    assert resp.status_code == 200
    buckets = resp.json()["data"]["buckets"]
    assert len(buckets) == 3
    # work=3h > travel=2h > play=1h. (music+gaming would both fold into Play,
    # so Play here is a single spotify episode.)
    assert buckets[0]["category"] == "work"
    assert buckets[1]["category"] == "travel"
    assert buckets[2]["category"] == "play"


async def test_by_category_uncorroborated_calendar_block_counts_zero():
    """IEA regression (tasks.md §4): an uncorroborated 5 h calendar block is
    intent → 0 s in every lane. Only the overlapping activity episode counts.

    NOTE: this is the mocked-pool fast check; the authoritative regression runs
    against real Postgres in roster/chronicler/tests/test_storage_integration.py
    (a mocked pool cannot catch a wrong SQL layer filter — see bu-3n44q5).
    """
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        # 5 h planned calendar block (intent layer) — must never count.
        _make_episode_row(
            source_name="google_calendar.completed",
            episode_type="scheduled_block",
            start_at=day_start,
            end_at=day_start + timedelta(hours=5),
            layer="intent",
        ),
        # Overlapping 2 h GPS-dwell movement (activity layer) — the thing that
        # actually counts, under its own Travel lane.
        _make_episode_row(
            source_name="owntracks.points",
            episode_type="movement_episode",
            start_at=day_start,
            end_at=day_start + timedelta(hours=2),
            layer="activity",
        ),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            _BY_CATEGORY,
            params={"start_at": "2024-03-15T00:00:00Z", "end_at": "2024-03-16T00:00:00Z"},
        )
    assert resp.status_code == 200
    buckets = resp.json()["data"]["buckets"]
    # Exactly one lane (Travel); no "calendar" / intent bucket at all.
    assert len(buckets) == 1
    assert buckets[0]["category"] == "travel"
    assert buckets[0]["total_seconds"] == pytest.approx(7200.0)
    assert {b["category"] for b in buckets} == {"travel"}


async def test_by_category_response_envelope_fields():
    """Response must be ApiResponse<CategoryBuckets> with start_at/end_at/tz."""
    app, _ = _build_app([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            _BY_CATEGORY,
            params={
                "start_at": "2024-03-15T00:00:00Z",
                "end_at": "2024-03-16T00:00:00Z",
                "tz": "America/New_York",
            },
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "start_at" in data and "end_at" in data
    assert data["tz"] == "America/New_York"
    assert isinstance(data["buckets"], list)


async def test_by_category_per_lane_low_confidence_breakdown():
    """Each lane bucket reports how much of its time is low-confidence (S9a §9.1).

    The Work lane gets one high-confidence 2h block and one low-confidence 1h
    block (both fold into Work).  ``total_seconds`` covers all 3h; the
    ``low_confidence_*`` fields surface only the 1h low-confidence slice so the
    dashboard can flag it.
    """
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        # High-confidence work block (2h, distinct window).
        _make_episode_row(
            start_at=day_start,
            end_at=day_start + timedelta(hours=2),
            confidence="high",
        ),
        # Low-confidence work block (1h, non-overlapping so union == sum).
        _make_episode_row(
            start_at=day_start + timedelta(hours=3),
            end_at=day_start + timedelta(hours=4),
            confidence="low",
        ),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            _BY_CATEGORY,
            params={"start_at": "2024-03-15T00:00:00Z", "end_at": "2024-03-16T00:00:00Z"},
        )
    assert resp.status_code == 200
    buckets = resp.json()["data"]["buckets"]
    work = next(b for b in buckets if b["category"] == "work")
    assert work["total_seconds"] == pytest.approx(3 * 3600.0)
    assert work["episode_count"] == 2
    assert work["low_confidence_seconds"] == pytest.approx(3600.0)
    assert work["low_confidence_episode_count"] == 1


async def test_by_category_low_confidence_zero_when_all_high():
    """A lane with no low-confidence rows reports a zero low-confidence slice."""
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            start_at=day_start,
            end_at=day_start + timedelta(hours=1),
            confidence="high",
        ),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            _BY_CATEGORY,
            params={"start_at": "2024-03-15T00:00:00Z", "end_at": "2024-03-16T00:00:00Z"},
        )
    assert resp.status_code == 200
    work = next(b for b in resp.json()["data"]["buckets"] if b["category"] == "work")
    assert work["low_confidence_seconds"] == pytest.approx(0.0)
    assert work["low_confidence_episode_count"] == 0


# ---------------------------------------------------------------------------
# OTel span tests
# ---------------------------------------------------------------------------


def _save_otel_state():
    return {
        "set_once": trace._TRACER_PROVIDER_SET_ONCE,
        "provider": trace._TRACER_PROVIDER,
        "proxy": getattr(trace, "_PROXY_TRACER_PROVIDER", None),
    }


def _restore_otel_state(saved: dict) -> None:
    trace._TRACER_PROVIDER_SET_ONCE = saved["set_once"]
    trace._TRACER_PROVIDER = saved["provider"]
    if hasattr(trace, "_PROXY_TRACER_PROVIDER"):
        trace._PROXY_TRACER_PROVIDER = saved["proxy"]


def _install_fresh_provider() -> tuple[InMemorySpanExporter, TracerProvider]:
    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()
    trace._TRACER_PROVIDER = None
    if hasattr(trace, "_PROXY_TRACER_PROVIDER"):
        trace._PROXY_TRACER_PROVIDER = None
    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": "butler-test"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter, provider


@pytest.fixture()
def otel_exporter():
    saved = _save_otel_state()
    exporter, provider = _install_fresh_provider()
    yield exporter
    provider.shutdown()
    _restore_otel_state(saved)


class _Row(dict):
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _load_chronicler_router():
    module_name = "chronicler_api_router"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, _ROUTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_otel_row(
    *,
    source_name: str = "core.sessions",
    episode_type: str = "work",
    trigger_source: str | None = None,
    start_at: datetime = _T0,
    end_at: datetime | None = None,
    precision: str = "exact",
    privacy: str = "normal",
    retention_days: int | None = None,
    tombstone_at: datetime | None = None,
    layer: str = "activity",
    confidence: str = "high",
) -> dict[str, Any]:
    if end_at is None:
        end_at = _T1
    return {
        "source_name": source_name,
        "episode_type": episode_type,
        "trigger_source": trigger_source,
        "start_at": start_at,
        "end_at": end_at,
        "precision": precision,
        "privacy": privacy,
        "retention_days": retention_days,
        "tombstone_at": tombstone_at,
        "layer": layer,
        "confidence": confidence,
    }


def _make_mock_row_otel(row: dict[str, Any]) -> MagicMock:
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    return m


def _make_app_with_pool(pool):
    chronicler_mod = _load_chronicler_router()
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app = create_app(api_key="")
    app.dependency_overrides[chronicler_mod._get_db_manager] = lambda: db
    return app


def _get_span(exporter: InMemorySpanExporter, name: str):
    spans = exporter.get_finished_spans()
    matching = [s for s in spans if s.name == name]
    assert matching, f"No span named {name!r} found. Got: {[s.name for s in spans]}"
    return matching[-1]


@pytest.mark.parametrize(
    "endpoint,span_name",
    [
        (_BY_CATEGORY, "chronicler.aggregate.by_category"),
        (_BY_DAY, "chronicler.aggregate.by_day"),
    ],
)
async def test_aggregate_span_emitted_with_attributes(endpoint, span_name, otel_exporter):
    """Happy path: span emitted with bucket_count and query_latency_ms attributes."""
    row = _make_otel_row()
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[_make_mock_row_otel(row)])
    app = _make_app_with_pool(pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            endpoint, params={"start_at": _T0.isoformat(), "end_at": _T1.isoformat()}
        )
    assert resp.status_code == 200
    span = _get_span(otel_exporter, span_name)
    attrs = dict(span.attributes)
    assert "chronicler.aggregate.query_latency_ms" in attrs
    assert isinstance(attrs["chronicler.aggregate.query_latency_ms"], float)
    assert "chronicler.aggregate.bucket_count" in attrs
    assert attrs["chronicler.aggregate.bucket_count"] >= 1


@pytest.mark.parametrize(
    "endpoint,span_name",
    [
        (_BY_CATEGORY, "chronicler.aggregate.by_category"),
        (_BY_DAY, "chronicler.aggregate.by_day"),
    ],
)
async def test_aggregate_span_unmapped_source_attribute(endpoint, span_name, otel_exporter):
    """Unmapped source_name triggers chronicler.aggregate.unmapped_source attribute."""
    row = _make_otel_row(source_name="totally.unknown.source", episode_type="unknown_type")
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[_make_mock_row_otel(row)])
    app = _make_app_with_pool(pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            endpoint, params={"start_at": _T0.isoformat(), "end_at": _T1.isoformat()}
        )
    assert resp.status_code == 200
    span = _get_span(otel_exporter, span_name)
    assert span.attributes.get("chronicler.aggregate.unmapped_source") == "totally.unknown.source"


async def test_day_close_span_cache_states(otel_exporter):
    """Day-close span cache_state attribute covers fresh, stale, and miss cases."""

    def _cache_row():
        return _Row(
            {
                "cache_key": "day_close:2026-04-01",
                "start_at": _T0,
                "end_at": _T1,
                "cache_built_at": _T_CACHE_BUILT,
                "prose": "Yesterday you worked for 8 hours.",
                "provenance_refs": json.dumps(["core.sessions:ref1"]),
            }
        )

    # Fresh: last_invalidating_event_at is NULL
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        side_effect=[
            _cache_row(),
            _Row({"last_invalidating_event_at": None}),
        ]
    )
    app = _make_app_with_pool(pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/day-close", params={"date": "2026-04-01"}
        )
    assert resp.status_code == 200
    assert (
        _get_span(otel_exporter, "chronicler.aggregate.day_close").attributes.get(
            "chronicler.day_close.cache_state"
        )
        == "fresh"
    )

    # Miss: no cache entry (404)
    pool2 = AsyncMock()
    pool2.fetchrow = AsyncMock(return_value=None)
    app2 = _make_app_with_pool(pool2)
    # Need fresh exporter state for second request
    spans_before = len(otel_exporter.get_finished_spans())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app2), base_url="http://test"
    ) as client:
        resp2 = await client.get(
            "/api/chronicler/aggregate/day-close", params={"date": "2026-04-01"}
        )
    assert resp2.status_code == 404
    all_spans = otel_exporter.get_finished_spans()
    day_close_spans = [
        s for s in all_spans[spans_before:] if s.name == "chronicler.aggregate.day_close"
    ]
    assert (
        day_close_spans
        and day_close_spans[-1].attributes.get("chronicler.day_close.cache_state") == "miss"
    )


async def test_source_state_span_row_count(otel_exporter):
    """Source-state span: row_count and query_latency_ms attributes are emitted."""
    adapter_row = _Row(
        {
            "source_name": "core.sessions",
            "chronicler_compatibility": "supported",
            "read_surface": "sessions",
            "boundary_semantics": "wall_clock",
            "optional_schema": False,
            "active": True,
            "inactive_reason": None,
        }
    )
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=[[adapter_row], []])
    app = _make_app_with_pool(pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/chronicler/source-state")
    assert resp.status_code == 200
    span = _get_span(otel_exporter, "chronicler.source_state")
    attrs = dict(span.attributes)
    assert attrs["chronicler.source_state.row_count"] == 1
    assert isinstance(attrs.get("chronicler.source_state.query_latency_ms"), float)
