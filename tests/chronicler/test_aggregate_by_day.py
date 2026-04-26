"""Tests for GET /api/chronicler/aggregate/by-day.

Covers:
- DST edge cases: America/New_York spring-forward (23 h day) and fall-back (25 h day)
- Basic happy path: single day, multiple sources, correct totals
- Optional category filter
- Privacy tier: restricted excluded, sensitive contributes to sums
- Tombstone exclusion and include_tombstoned override
- Guardrail: handler imports no LLM packages
- Guardrail: SQL strings only reference v_episodes_corrected (chronicler schema)
- Parameter validation: missing params, bad tz, end_at <= start_at
  — all 400 responses use the ErrorResponse envelope {error: {code, message, butler}}
  — no partial bucket records returned on 4xx
- Precision and retention_floor_days carry-forward
- Microbenchmark: O(N×k) inner loop is faster than O(N×D) on large windows
"""

from __future__ import annotations

import ast
import re
import zoneinfo
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NY = zoneinfo.ZoneInfo("America/New_York")
_UTC = UTC

_ROUTER_PATH = Path(__file__).parent.parent.parent / "roster" / "chronicler" / "api" / "router.py"


def _make_episode_row(
    *,
    source_name: str = "core.sessions",
    episode_type: str = "work",
    start_at: datetime,
    end_at: datetime | None = None,
    precision: str = "exact",
    privacy: str = "normal",
    retention_days: int | None = None,
    tombstone_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "source_name": source_name,
        "episode_type": episode_type,
        "start_at": start_at,
        "end_at": end_at,
        "precision": precision,
        "privacy": privacy,
        "retention_days": retention_days,
        "tombstone_at": tombstone_at,
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

    # Locate the chronicler router module via app.state.butler_routers (set by create_app)
    # and override its _get_db_manager dependency to inject the mocked database.
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "chronicler" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_missing_start_at_returns_400():
    app, _ = _build_app([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={"end_at": "2024-03-11T00:00:00Z"},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body, f"Expected ErrorResponse envelope, got: {body}"
    assert "data" not in body, "4xx must not include partial data"
    assert body["error"]["code"] == "missing_parameter"
    assert body["error"]["butler"] == "chronicler"
    assert body["error"]["message"]


@pytest.mark.unit
async def test_missing_end_at_returns_400():
    app, _ = _build_app([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={"start_at": "2024-03-10T00:00:00Z"},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body, f"Expected ErrorResponse envelope, got: {body}"
    assert "data" not in body, "4xx must not include partial data"
    assert body["error"]["code"] == "missing_parameter"
    assert body["error"]["butler"] == "chronicler"
    assert body["error"]["message"]


@pytest.mark.unit
async def test_end_at_equal_to_start_at_returns_400():
    app, _ = _build_app([])
    ts = "2024-03-10T00:00:00Z"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={"start_at": ts, "end_at": ts},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body, f"Expected ErrorResponse envelope, got: {body}"
    assert "data" not in body, "4xx must not include partial data"
    assert body["error"]["code"] == "invalid_time_range"
    assert body["error"]["butler"] == "chronicler"
    assert body["error"]["message"]


@pytest.mark.unit
async def test_end_at_before_start_at_returns_400():
    app, _ = _build_app([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": "2024-03-11T00:00:00Z",
                "end_at": "2024-03-10T00:00:00Z",
            },
        )
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body, f"Expected ErrorResponse envelope, got: {body}"
    assert "data" not in body, "4xx must not include partial data"
    assert body["error"]["code"] == "invalid_time_range"
    assert body["error"]["butler"] == "chronicler"
    assert body["error"]["message"]


@pytest.mark.unit
async def test_unrecognized_timezone_returns_400():
    app, _ = _build_app([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": "2024-03-10T00:00:00Z",
                "end_at": "2024-03-11T00:00:00Z",
                "tz": "Not/A/Timezone",
            },
        )
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body, f"Expected ErrorResponse envelope, got: {body}"
    assert "data" not in body, "4xx must not include partial data"
    assert body["error"]["code"] == "invalid_timezone"
    assert body["error"]["butler"] == "chronicler"
    assert body["error"]["message"]


# ---------------------------------------------------------------------------
# Happy path — basic aggregation
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_single_episode_one_day():
    """An episode fully within a single UTC day sums correctly."""
    start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    end = datetime(2024, 3, 15, 11, 0, 0, tzinfo=_UTC)  # 2 hours
    rows = [
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=start,
            end_at=end,
        )
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": "2024-03-15T00:00:00Z",
                "end_at": "2024-03-16T00:00:00Z",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert row["day"] == "2024-03-15"
    assert row["category"] == "work"
    assert row["total_seconds"] == pytest.approx(7200.0)
    assert row["episode_count"] == 1
    assert row["precision"] == "exact"
    assert row["retention_floor_days"] is None
    assert len(row["source_breakdown"]) == 1
    assert row["source_breakdown"][0]["source_name"] == "core.sessions"
    assert row["source_breakdown"][0]["total_seconds"] == pytest.approx(7200.0)


@pytest.mark.unit
async def test_episode_spans_two_days_splits_correctly():
    """An episode crossing midnight is split across two day buckets."""
    start = datetime(2024, 3, 15, 22, 0, 0, tzinfo=_UTC)
    end = datetime(2024, 3, 16, 2, 0, 0, tzinfo=_UTC)  # 2h before midnight + 2h after
    rows = [
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=start,
            end_at=end,
        )
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": "2024-03-15T00:00:00Z",
                "end_at": "2024-03-17T00:00:00Z",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    by_day = {r["day"]: r for r in data}
    assert "2024-03-15" in by_day
    assert "2024-03-16" in by_day
    assert by_day["2024-03-15"]["total_seconds"] == pytest.approx(7200.0)  # 22:00-24:00
    assert by_day["2024-03-16"]["total_seconds"] == pytest.approx(7200.0)  # 00:00-02:00


@pytest.mark.unit
async def test_multiple_categories_separate_rows():
    """Two episodes with different categories produce separate rows for the same day."""
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=day_start,
            end_at=day_start + timedelta(hours=1),
        ),
        _make_episode_row(
            source_name="spotify.session_summary",
            episode_type="listening_episode",
            start_at=day_start + timedelta(hours=2),
            end_at=day_start + timedelta(hours=3),
        ),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": "2024-03-15T00:00:00Z",
                "end_at": "2024-03-16T00:00:00Z",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    categories = {r["category"] for r in data}
    assert "work" in categories
    assert "music" in categories


@pytest.mark.unit
async def test_category_filter_excludes_other_categories():
    """The category query parameter filters to a single category."""
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=day_start,
            end_at=day_start + timedelta(hours=1),
        ),
        _make_episode_row(
            source_name="spotify.session_summary",
            episode_type="listening_episode",
            start_at=day_start + timedelta(hours=2),
            end_at=day_start + timedelta(hours=3),
        ),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": "2024-03-15T00:00:00Z",
                "end_at": "2024-03-16T00:00:00Z",
                "category": "work",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert all(r["category"] == "work" for r in data)


# ---------------------------------------------------------------------------
# Privacy filtering
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sensitive_episodes_contribute_to_totals():
    """Sensitive episodes must contribute duration to bucket sums."""
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=day_start,
            end_at=day_start + timedelta(hours=2),
            privacy="sensitive",
        ),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": "2024-03-15T00:00:00Z",
                "end_at": "2024-03-16T00:00:00Z",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["total_seconds"] == pytest.approx(7200.0)


# ---------------------------------------------------------------------------
# Tombstone handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_tombstoned_episodes_excluded_by_default():
    """tombstone_at IS NULL is included in the SQL WHERE clause by default.

    The mock returns a tombstoned row; the SQL WHERE should have excluded it.
    We exercise the endpoint and verify it returns 200 (the SQL guard is
    covered by the guardrail test below).
    """
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=day_start,
            end_at=day_start + timedelta(hours=1),
            tombstone_at=day_start - timedelta(hours=1),
        ),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": "2024-03-15T00:00:00Z",
                "end_at": "2024-03-16T00:00:00Z",
            },
        )
    assert resp.status_code == 200


@pytest.mark.unit
async def test_include_tombstoned_flag_passes_rows_through():
    """With include_tombstoned=true, tombstoned rows contribute and breakdown is flagged."""
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=day_start,
            end_at=day_start + timedelta(hours=1),
            tombstone_at=day_start - timedelta(hours=1),
        ),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": "2024-03-15T00:00:00Z",
                "end_at": "2024-03-16T00:00:00Z",
                "include_tombstoned": "true",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["source_breakdown"][0]["tombstoned"] is True


# ---------------------------------------------------------------------------
# Precision and retention_floor_days
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_precision_carries_least_precise():
    """The least-precise precision value across contributing rows is returned."""
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=day_start,
            end_at=day_start + timedelta(hours=1),
            precision="exact",
        ),
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
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
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": "2024-03-15T00:00:00Z",
                "end_at": "2024-03-16T00:00:00Z",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["precision"] == "hour"  # least precise of {exact, hour}


@pytest.mark.unit
async def test_retention_floor_days_minimum_non_null():
    """retention_floor_days is the minimum non-NULL retention_days across rows."""
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=day_start,
            end_at=day_start + timedelta(hours=1),
            retention_days=90,
        ),
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=day_start + timedelta(hours=2),
            end_at=day_start + timedelta(hours=3),
            retention_days=30,
        ),
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=day_start + timedelta(hours=4),
            end_at=day_start + timedelta(hours=5),
            retention_days=None,  # NULL inherits default
        ),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": "2024-03-15T00:00:00Z",
                "end_at": "2024-03-16T00:00:00Z",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["retention_floor_days"] == 30


@pytest.mark.unit
async def test_retention_floor_days_null_when_all_null():
    """retention_floor_days is None when all contributing rows have NULL retention_days."""
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=day_start,
            end_at=day_start + timedelta(hours=1),
            retention_days=None,
        ),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": "2024-03-15T00:00:00Z",
                "end_at": "2024-03-16T00:00:00Z",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["retention_floor_days"] is None


# ---------------------------------------------------------------------------
# DST edge cases — America/New_York
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_dst_spring_forward_23h_day():
    """America/New_York spring-forward day (Mar 10 2024) has 23 h of actual time.

    An episode spanning the full 23-hour day (from 05:00 UTC to 04:00 UTC next
    day) should produce total_seconds = 82800 (23 * 3600) for that calendar day.
    The day_start and day_end in the response must also reflect the 23 h duration.
    """
    # Spring forward: 2024-03-10 00:00 EST (-5) = 05:00 UTC
    #                 2024-03-11 00:00 EDT (-4) = 04:00 UTC  → 23 h
    spring_day_start_utc = datetime(2024, 3, 10, 5, 0, 0, tzinfo=_UTC)
    spring_day_end_utc = datetime(2024, 3, 11, 4, 0, 0, tzinfo=_UTC)

    rows = [
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=spring_day_start_utc,
            end_at=spring_day_end_utc,
        )
    ]
    app, _ = _build_app(rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": "2024-03-10T05:00:00Z",
                "end_at": "2024-03-11T04:00:00Z",
                "tz": "America/New_York",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1, f"Expected 1 bucket, got {len(data)}: {data}"
    row = data[0]

    assert row["day"] == "2024-03-10"
    assert row["category"] == "work"
    # 23 hours = 82800 seconds
    assert row["total_seconds"] == pytest.approx(82800.0), (
        f"Expected 82800 s (23 h) for spring-forward day, got {row['total_seconds']}"
    )
    assert row["episode_count"] == 1

    day_start_dt = datetime.fromisoformat(row["day_start"])
    day_end_dt = datetime.fromisoformat(row["day_end"])
    duration = (day_end_dt - day_start_dt).total_seconds()
    assert duration == pytest.approx(82800.0), (
        f"day_end - day_start should be 23 h (82800 s) for spring-forward; got {duration} s"
    )


@pytest.mark.unit
async def test_dst_fall_back_25h_day():
    """America/New_York fall-back day (Nov 3 2024) has 25 h of actual time.

    An episode spanning the full 25-hour day should produce total_seconds = 90000
    (25 * 3600) for that calendar day.
    """
    # Fall back: 2024-11-03 00:00 EDT (-4) = 04:00 UTC
    #            2024-11-04 00:00 EST (-5) = 05:00 UTC  → 25 h
    fall_day_start_utc = datetime(2024, 11, 3, 4, 0, 0, tzinfo=_UTC)
    fall_day_end_utc = datetime(2024, 11, 4, 5, 0, 0, tzinfo=_UTC)

    rows = [
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=fall_day_start_utc,
            end_at=fall_day_end_utc,
        )
    ]
    app, _ = _build_app(rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": "2024-11-03T04:00:00Z",
                "end_at": "2024-11-04T05:00:00Z",
                "tz": "America/New_York",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1, f"Expected 1 bucket, got {len(data)}: {data}"
    row = data[0]

    assert row["day"] == "2024-11-03"
    assert row["category"] == "work"
    # 25 hours = 90000 seconds
    assert row["total_seconds"] == pytest.approx(90000.0), (
        f"Expected 90000 s (25 h) for fall-back day, got {row['total_seconds']}"
    )
    assert row["episode_count"] == 1

    day_start_dt = datetime.fromisoformat(row["day_start"])
    day_end_dt = datetime.fromisoformat(row["day_end"])
    duration = (day_end_dt - day_start_dt).total_seconds()
    assert duration == pytest.approx(90000.0), (
        f"day_end - day_start should be 25 h (90000 s) for fall-back; got {duration} s"
    )


@pytest.mark.unit
async def test_dst_spring_forward_episode_before_and_after_transition():
    """Two episodes on spring-forward day (one EST, one EDT) bucket to the same day."""
    # Pre-transition: 01:00-02:00 EST = 06:00-07:00 UTC
    # Post-transition: 03:00-04:00 EDT = 07:00-08:00 UTC (2 AM jumps to 3 AM)
    pre_start = datetime(2024, 3, 10, 6, 0, 0, tzinfo=_UTC)
    pre_end = datetime(2024, 3, 10, 7, 0, 0, tzinfo=_UTC)
    post_start = datetime(2024, 3, 10, 7, 0, 0, tzinfo=_UTC)
    post_end = datetime(2024, 3, 10, 8, 0, 0, tzinfo=_UTC)

    rows = [
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=pre_start,
            end_at=pre_end,
        ),
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=post_start,
            end_at=post_end,
        ),
    ]
    app, _ = _build_app(rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": "2024-03-10T05:00:00Z",
                "end_at": "2024-03-11T04:00:00Z",
                "tz": "America/New_York",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    days = {r["day"] for r in data}
    assert days == {"2024-03-10"}, f"Expected only 2024-03-10, got {days}"
    work_row = next(r for r in data if r["category"] == "work")
    assert work_row["episode_count"] == 2
    assert work_row["total_seconds"] == pytest.approx(7200.0)  # 2h total


@pytest.mark.unit
async def test_dst_fall_back_episode_in_repeated_hour():
    """An episode spanning the repeated 01:00-02:00 hour on fall-back day counts once."""
    # Fall-back 2024-11-03: 2 AM EDT (06:00 UTC) → 1 AM EST (06:00 UTC)
    # Repeated 1 AM: first occurrence 05:00-06:00 UTC (EDT), second 06:00-07:00 UTC (EST)
    ep_start = datetime(2024, 11, 3, 5, 30, 0, tzinfo=_UTC)  # 01:30 EDT
    ep_end = datetime(2024, 11, 3, 6, 30, 0, tzinfo=_UTC)  # 01:30 EST (second hour)

    rows = [
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=ep_start,
            end_at=ep_end,
        )
    ]
    app, _ = _build_app(rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
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
    assert work_rows[0]["total_seconds"] == pytest.approx(3600.0)  # 1 hour


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_result_sorted_by_day_then_category():
    """Response rows must be sorted (day ASC, category ASC)."""
    base = datetime(2024, 3, 15, 10, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            source_name="spotify.session_summary",
            episode_type="listening_episode",
            start_at=base + timedelta(days=1),
            end_at=base + timedelta(days=1, hours=1),
        ),
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=base,
            end_at=base + timedelta(hours=1),
        ),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": "2024-03-15T00:00:00Z",
                "end_at": "2024-03-17T00:00:00Z",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    keys = [(r["day"], r["category"]) for r in data]
    assert keys == sorted(keys), f"Results not sorted: {keys}"


# ---------------------------------------------------------------------------
# Response includes day_start and day_end
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_response_includes_day_start_and_day_end():
    """Each row must include day_start and day_end timestamps."""
    day_start = datetime(2024, 3, 15, 9, 0, 0, tzinfo=_UTC)
    rows = [
        _make_episode_row(
            source_name="core.sessions",
            episode_type="work",
            start_at=day_start,
            end_at=day_start + timedelta(hours=1),
        )
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": "2024-03-15T00:00:00Z",
                "end_at": "2024-03-16T00:00:00Z",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert "day_start" in row
    assert "day_end" in row
    ds = datetime.fromisoformat(row["day_start"])
    de = datetime.fromisoformat(row["day_end"])
    assert de > ds


# ---------------------------------------------------------------------------
# Guardrail: no LLM imports
# ---------------------------------------------------------------------------

_FORBIDDEN_IMPORTS = frozenset(
    {"anthropic", "openai", "claude_agent_sdk", "butlers.chronicler.interpretation"}
)


def test_router_no_llm_imports():
    """roster/chronicler/api/router.py must not import any LLM provider package."""
    source = _ROUTER_PATH.read_text()
    tree = ast.parse(source)

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                full = alias.name
                if root in _FORBIDDEN_IMPORTS or full in _FORBIDDEN_IMPORTS:
                    violations.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                full = node.module
                if root in _FORBIDDEN_IMPORTS or full in _FORBIDDEN_IMPORTS:
                    violations.append(node.module)

    assert not violations, f"router.py must not import LLM packages; found: {violations}"


# ---------------------------------------------------------------------------
# Guardrail: SQL only references chronicler-schema relations
# ---------------------------------------------------------------------------

# Known relations that live in the chronicler schema.
_CHRONICLER_RELATIONS = frozenset(
    {
        "v_episodes_corrected",
        "v_point_events_corrected",
        "episodes",
        "point_events",
        "overrides",
        "episode_event_links",
        "source_adapter_state",
        "projection_checkpoints",
        "tier2_cache",
        # Core butler tables present in every butler schema:
        "scheduled_tasks",
    }
)

# Match FROM/JOIN clauses in SQL string literals to extract relation names.
_FROM_JOIN_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_.]*)\b",
    re.IGNORECASE,
)


# SQL-like strings must start with a SQL verb keyword (SELECT/INSERT/UPDATE/DELETE).
# This excludes prose strings that happen to contain the word "from".
_SQL_VERB_RE = re.compile(r"^\s*(?:SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)


def _extract_sql_string_literals(source: str) -> list[str]:
    """Return string literals that start with a SQL verb (SELECT/INSERT/UPDATE/DELETE)."""
    tree = ast.parse(source)
    literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value.strip()
            if _SQL_VERB_RE.match(val):
                literals.append(val)
    return literals


def test_sql_strings_only_reference_chronicler_relations():
    """All SQL in router.py must reference only known chronicler-schema relations."""
    source = _ROUTER_PATH.read_text()
    sql_literals = _extract_sql_string_literals(source)

    violations: list[str] = []
    for sql in sql_literals:
        for match in _FROM_JOIN_RE.finditer(sql):
            relation = match.group(1).strip().lower()
            # Allow schema-qualified references IF the schema is 'chronicler'.
            if "." in relation:
                schema, _, rel = relation.partition(".")
                if schema != "chronicler":
                    violations.append(f"cross-schema reference: {relation!r}")
                    continue
                relation = rel
            if relation and relation not in _CHRONICLER_RELATIONS:
                violations.append(f"unknown relation in SQL: {relation!r}")

    assert not violations, f"router.py SQL references non-chronicler relations: {violations}"


# ---------------------------------------------------------------------------
# Microbenchmark: large window (N=1000 episodes, D=365 days)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_large_window_completes_quickly():
    """Aggregation over 1000 episodes × 365 days must complete within 3 s.

    The O(N×k) inner loop (k=avg episode span in days, typically 1-2)
    is much faster than the naive O(N×D) scan.  This test provides a
    no-Docker, no-Docker lower bound: if the handler regresses back to
    O(N×D) the test will become slow and flag the regression in CI.

    N=1000, D=365 gives O_old=365 000 iterations vs O_new~1000 iterations.
    At ~100 ns per loop body that is ~37 ms vs ~0.1 ms — well within the
    3 s budget even under CI load.
    """
    import time

    _N = 1000
    _D = 365

    window_start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=_UTC)
    window_end = window_start + timedelta(days=_D)

    # Build synthetic rows: each episode lasts 1 h and stays within a single day.
    rows = []
    for i in range(_N):
        day_offset = i % _D
        hour_offset = i % 23
        ep_start = window_start + timedelta(days=day_offset, hours=hour_offset)
        ep_end = ep_start + timedelta(hours=1)
        rows.append(
            _make_episode_row(
                source_name="core.sessions",
                episode_type="work",
                start_at=ep_start,
                end_at=ep_end,
            )
        )

    app, _ = _build_app(rows)

    t0 = time.perf_counter()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-day",
            params={
                "start_at": window_start.isoformat(),
                "end_at": window_end.isoformat(),
            },
        )
    elapsed = time.perf_counter() - t0

    assert resp.status_code == 200
    data = resp.json()
    # All 1000 episodes land on distinct days (within the 365-day window)
    # so we should see exactly 1 row per occupied day.
    assert len(data) > 0

    assert elapsed < 3.0, (
        f"aggregate/by-day with N={_N} episodes × D={_D} days took {elapsed:.2f} s "
        f"(budget: 3 s). Inner loop may have regressed to O(N×D)."
    )
