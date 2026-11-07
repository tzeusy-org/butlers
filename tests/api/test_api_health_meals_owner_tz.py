"""Integration tests: owner-timezone day boundaries for health date filters [bu-jlzxf].

GET /api/health/meals (and the sibling health read endpoints) accept bare
``YYYY-MM-DD`` day keys — the shape the dashboard sends (see
frontend/src/lib/day-window.ts).  Historically those bare dates were compared
against the ``valid_at`` timestamptz column, so Postgres coerced them to
midnight in the DB *session* timezone (UTC), not the owner's day boundary:

    * a meal logged at 23:30 owner-local landed on the wrong calendar day, and
    * an inclusive ``valid_at <= <until-date>`` truncated every entry logged
      later that same owner-day.

These tests exercise the full write -> read path against a live PostgreSQL
instance (testcontainers, NOT a mocked pool) with meals straddling
owner-midnight on both sides, proving the window is interpreted in the owner's
configured timezone (``Asia/Singapore``, UTC+8).
"""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest

from butlers.api.deps import get_db
from butlers.core.general_settings import save_general_settings
from butlers.testing.db_manager import db_manager
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

# Trigger router discovery so the health dependency fn is importable from
# sys.modules (FastAPI dependency_overrides keys on object identity).
_APP_SEED = create_app(api_key="")
_health_get_db_manager = get_db

OWNER_TZ = "Asia/Singapore"  # UTC+8, no DST

# The health read endpoints touch the memory chain's ``facts`` table (meals are
# temporal facts) and the core chain's ``state`` table (general settings, which
# is where the owner timezone is resolved from); both migrate into ``public``.


@asynccontextmanager
async def _app_client(db: object):
    app = create_app(api_key="")
    app.dependency_overrides[_health_get_db_manager] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def _insert_meal(
    pool: object,
    *,
    description: str,
    valid_at_utc: datetime,
    calories: int | None = None,
) -> None:
    """Insert a single meal fact at an explicit UTC instant."""
    metadata: dict[str, object] = {}
    if calories is not None:
        metadata = {"estimated_calories": calories, "macros": {"protein_g": 1}}
    await pool.execute(
        """
        INSERT INTO facts (subject, predicate, content, scope, validity, valid_at, metadata)
        VALUES ('owner', 'meal_dinner', $1, 'health', 'active', $2, $3)
        """,
        description,
        valid_at_utc,
        metadata,
    )


async def _seed(pool: object) -> None:
    """Seed the owner timezone and insert the boundary meals.

    Owner timezone is Asia/Singapore (UTC+8). The five meals straddle the
    2026-07-11 owner-day boundaries; each ``valid_at`` is given as the exact UTC
    instant it maps to, with the owner-local wall-clock time in the description.
    """
    await save_general_settings(
        pool,
        timezone=OWNER_TZ,
        language="en-US",
        date_format="YYYY-mm-dd",
        time_format="HH:MM",
        week_starts_on="Monday",
        currency="USD",
    )

    # 2026-07-11 00:30 +08  == 2026-07-10 16:30 UTC  (just after owner-midnight)
    await _insert_meal(
        pool,
        description="d11-0030",
        valid_at_utc=datetime(2026, 7, 10, 16, 30, tzinfo=UTC),
        calories=100,
    )
    # 2026-07-11 08:00 +08  == 2026-07-11 00:00 UTC  (midday, owner day 11)
    await _insert_meal(
        pool,
        description="d11-0800",
        valid_at_utc=datetime(2026, 7, 11, 0, 0, tzinfo=UTC),
        calories=200,
    )
    # 2026-07-11 23:30 +08  == 2026-07-11 15:30 UTC  (late, same owner day 11)
    await _insert_meal(
        pool,
        description="d11-2330",
        valid_at_utc=datetime(2026, 7, 11, 15, 30, tzinfo=UTC),
        calories=400,
    )
    # 2026-07-12 00:30 +08  == 2026-07-11 16:30 UTC  (just after NEXT owner-midnight)
    await _insert_meal(
        pool,
        description="d12-0030",
        valid_at_utc=datetime(2026, 7, 11, 16, 30, tzinfo=UTC),
        calories=800,
    )
    # 2026-07-10 23:30 +08  == 2026-07-10 15:30 UTC  (previous owner day 10)
    await _insert_meal(
        pool,
        description="d10-2330",
        valid_at_utc=datetime(2026, 7, 10, 15, 30, tzinfo=UTC),
        calories=1600,
    )


def _descriptions(payload: dict) -> set[str]:
    return {row["description"] for row in payload["data"]}


@pytest.mark.asyncio(loop_scope="session")
async def test_meals_day_window_uses_owner_timezone(db) -> None:
    """A bare since/until day key frames the owner-tz calendar day, not UTC."""
    pool = await db("core", "memory")
    await _seed(pool)
    manager = db_manager({"health": pool})

    async with _app_client(manager) as client:
        resp = await client.get("/api/health/meals?since=2026-07-11&until=2026-07-11")
    assert resp.status_code == 200, resp.text
    got = _descriptions(resp.json())

    # (a) a meal at 00:30 owner-tz lands in that day (owner-tz start-of-day
    #     lower bound, not UTC midnight which would exclude 16:30 UTC).
    assert "d11-0030" in got
    # midday meal is in the day.
    assert "d11-0800" in got
    # (c) a bare-date `until` no longer truncates a same-owner-day 23:30 meal.
    assert "d11-2330" in got
    # (b) a meal at 00:30 the NEXT owner-day is excluded.
    assert "d12-0030" not in got
    # the previous owner-day's late meal is excluded.
    assert "d10-2330" not in got

    assert got == {"d11-0030", "d11-0800", "d11-2330"}


@pytest.mark.asyncio(loop_scope="session")
async def test_meals_next_owner_day_key_round_trips(db) -> None:
    """The next owner-day's key returns exactly the meal that spilled over."""
    pool = await db("core", "memory")
    await _seed(pool)
    manager = db_manager({"health": pool})

    async with _app_client(manager) as client:
        resp = await client.get("/api/health/meals?since=2026-07-12&until=2026-07-12")
    assert resp.status_code == 200, resp.text
    # The 2026-07-12 00:30 owner-local meal belongs to day 12, and nothing
    # from day 11 leaks forward.
    assert _descriptions(resp.json()) == {"d12-0030"}


@pytest.mark.asyncio(loop_scope="session")
async def test_nutrition_summary_window_uses_owner_timezone(db) -> None:
    """GET /nutrition/summary shares the owner-tz day-boundary interpretation."""
    pool = await db("core", "memory")
    await _seed(pool)
    manager = db_manager({"health": pool})

    async with _app_client(manager) as client:
        resp = await client.get("/api/health/nutrition/summary?start=2026-07-11&end=2026-07-11")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # day-11 meals: 100 + 200 + 400 = 700 kcal across 3 meals; the next
    # owner-day's 800-kcal meal and the prior day's 1600-kcal meal are out.
    assert body["meal_count"] == 3
    assert body["total_calories"] == 700
