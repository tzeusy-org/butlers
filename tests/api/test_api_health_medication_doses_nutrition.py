"""Unit tests for the health dashboard routes added in bu-sqjc7.

Covers three routes, all of which read/write the ``facts`` table via the
Health butler's existing MCP tools (no new schema):

- POST /api/health/medications/{id}/doses  -> medication_log_dose (took_dose fact)
- GET  /api/health/medications/{id}/adherence -> medication_history (aggregate)
  - expected_doses is computed from prescribed frequency (bu-ve69q)
- GET  /api/health/nutrition/summary       -> nutrition_summary (meal_* facts)
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import butlers.tools.health as health_tools
from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)

# Trigger router discovery so the dependency function is importable from
# sys.modules (FastAPI dependency_overrides keys on object identity).
_APP_SEED = create_app(api_key="")
_health_get_db_manager = sys.modules["health_api_router"]._get_db_manager


def _make_app():
    """Build a test app with the health DB pool mocked."""
    pool = AsyncMock()
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool

    app = create_app(api_key="")
    app.dependency_overrides[_health_get_db_manager] = lambda: db
    return app, pool


def _make_app_unavailable():
    """Build a test app where the health pool lookup raises KeyError (503)."""
    db = MagicMock(spec=DatabaseManager)
    db.pool.side_effect = KeyError("health")

    app = create_app(api_key="")
    app.dependency_overrides[_health_get_db_manager] = lambda: db
    return app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# POST /medications/{id}/doses
# ---------------------------------------------------------------------------


async def test_log_dose_returns_201_and_dose(monkeypatch):
    """POST a dose persists via medication_log_dose and returns the Dose shape."""
    med_id = str(uuid.uuid4())
    fact_id = uuid.uuid4()

    async def fake_log_dose(pool, medication_id, *, taken_at, skipped, notes):
        assert medication_id == med_id
        assert skipped is True
        assert notes == "felt nauseous"
        return {
            "id": fact_id,
            "medication_id": uuid.UUID(med_id),
            "skipped": skipped,
            "notes": notes,
            "taken_at": _NOW,
            "created_at": _NOW,
        }

    monkeypatch.setattr(health_tools, "medication_log_dose", fake_log_dose)

    app, _ = _make_app()
    async with _client(app) as client:
        resp = await client.post(
            f"/api/health/medications/{med_id}/doses",
            json={"skipped": True, "notes": "felt nauseous"},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == str(fact_id)
    assert body["medication_id"] == med_id
    assert body["skipped"] is True
    assert body["notes"] == "felt nauseous"
    assert body["taken_at"] == _NOW.isoformat()


async def test_log_dose_invalidates_briefing_cache(monkeypatch):
    """A successful dose write invalidates the per-owner health-briefing cache.

    Spec (butler-health delta, "Logging a dose writes a took_dose fact"): the
    route MUST invalidate the per-owner briefing cache so the next briefing
    reflects the dose rather than serving a stale pre-dose paragraph for the
    5-minute TTL.
    """
    import health_api_router as health_router

    med_id = str(uuid.uuid4())

    async def fake_log_dose(pool, medication_id, *, taken_at, skipped, notes):
        return {
            "id": uuid.uuid4(),
            "medication_id": uuid.UUID(med_id),
            "skipped": skipped,
            "notes": notes,
            "taken_at": _NOW,
            "created_at": _NOW,
        }

    monkeypatch.setattr(health_tools, "medication_log_dose", fake_log_dose)

    cache = MagicMock()
    app, _ = _make_app()
    app.dependency_overrides[health_router.get_health_briefing_cache] = lambda: cache
    async with _client(app) as client:
        resp = await client.post(f"/api/health/medications/{med_id}/doses", json={})

    assert resp.status_code == 201
    cache.invalidate_all.assert_called_once()


async def test_log_dose_defaults_skipped_false(monkeypatch):
    """Omitting skipped logs a taken dose (skipped defaults to False)."""
    med_id = str(uuid.uuid4())

    async def fake_log_dose(pool, medication_id, *, taken_at, skipped, notes):
        assert skipped is False
        return {
            "id": uuid.uuid4(),
            "medication_id": uuid.UUID(med_id),
            "skipped": skipped,
            "notes": notes,
            "taken_at": _NOW,
            "created_at": _NOW,
        }

    monkeypatch.setattr(health_tools, "medication_log_dose", fake_log_dose)

    app, _ = _make_app()
    async with _client(app) as client:
        resp = await client.post(f"/api/health/medications/{med_id}/doses", json={})
    assert resp.status_code == 201
    assert resp.json()["skipped"] is False


async def test_log_dose_404_when_medication_missing(monkeypatch):
    """A ValueError from the tool (unknown medication) maps to 404."""
    med_id = str(uuid.uuid4())

    async def fake_log_dose(pool, medication_id, *, taken_at, skipped, notes):
        raise ValueError(f"Medication {medication_id} not found")

    monkeypatch.setattr(health_tools, "medication_log_dose", fake_log_dose)

    app, _ = _make_app()
    async with _client(app) as client:
        resp = await client.post(f"/api/health/medications/{med_id}/doses", json={})
    assert resp.status_code == 404


async def test_log_dose_503_when_pool_unavailable():
    """POST returns 503 when the health DB pool is unavailable."""
    app = _make_app_unavailable()
    async with _client(app) as client:
        resp = await client.post(f"/api/health/medications/{uuid.uuid4()}/doses", json={})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /medications/{id}/adherence
# ---------------------------------------------------------------------------


async def test_adherence_aggregates_counts(monkeypatch):
    """Adherence route reports total/taken/skipped/expected counts.

    expected_doses is derived from prescribed frequency, NOT from len(doses).
    For a "daily" medication over the default 30-day window: expected = 30.
    adherence_rate = taken / expected * 100 = 2/30 * 100 ≈ 6.7.
    """
    med_id = str(uuid.uuid4())

    async def fake_history(pool, medication_id, *, start_date, end_date):
        assert medication_id == med_id
        return {
            "medication": {"id": med_id, "name": "Metformin", "frequency": "daily"},
            "doses": [
                {"skipped": False},
                {"skipped": False},
                {"skipped": True},
            ],
            "adherence_rate": 66.7,  # naive ratio — route ignores this now
        }

    monkeypatch.setattr(health_tools, "medication_history", fake_history)

    app, _ = _make_app()
    async with _client(app) as client:
        resp = await client.get(f"/api/health/medications/{med_id}/adherence")
    assert resp.status_code == 200
    body = resp.json()
    assert body["medication_id"] == med_id
    assert body["total_doses"] == 3
    assert body["taken_doses"] == 2
    assert body["skipped_doses"] == 1
    # expected_doses = 1.0 dose/day × 30 days = 30
    assert body["expected_doses"] == 30
    # adherence_rate = 2/30 * 100 ≈ 6.7
    assert body["adherence_rate"] == pytest.approx(6.7, abs=0.1)


async def test_adherence_twice_daily_frequency(monkeypatch):
    """expected_doses scales with the prescribed frequency.

    "twice daily" → 2 doses/day × 30 days = 60 expected.
    """
    med_id = str(uuid.uuid4())

    async def fake_history(pool, medication_id, *, start_date, end_date):
        return {
            "medication": {"id": med_id, "name": "Metformin", "frequency": "twice daily"},
            "doses": [{"skipped": False}] * 10,
            "adherence_rate": None,
        }

    monkeypatch.setattr(health_tools, "medication_history", fake_history)

    app, _ = _make_app()
    async with _client(app) as client:
        resp = await client.get(f"/api/health/medications/{med_id}/adherence")
    assert resp.status_code == 200
    body = resp.json()
    assert body["expected_doses"] == 60  # 2/day × 30 days
    assert body["taken_doses"] == 10
    assert body["adherence_rate"] == pytest.approx(10 / 60 * 100, abs=0.1)


async def test_adherence_window_days_param(monkeypatch):
    """?window_days overrides the default 30-day window."""
    med_id = str(uuid.uuid4())

    async def fake_history(pool, medication_id, *, start_date, end_date):
        return {
            "medication": {"id": med_id, "name": "X", "frequency": "daily"},
            "doses": [{"skipped": False}] * 7,
            "adherence_rate": None,
        }

    monkeypatch.setattr(health_tools, "medication_history", fake_history)

    app, _ = _make_app()
    async with _client(app) as client:
        resp = await client.get(
            f"/api/health/medications/{med_id}/adherence",
            params={"window_days": "7"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["expected_doses"] == 7  # 1/day × 7 days
    assert body["adherence_rate"] == pytest.approx(100.0, abs=0.1)


async def test_adherence_window_from_start_end(monkeypatch):
    """When start/end are given, window_days is derived from the span."""
    med_id = str(uuid.uuid4())

    async def fake_history(pool, medication_id, *, start_date, end_date):
        return {
            "medication": {"id": med_id, "name": "X", "frequency": "daily"},
            "doses": [{"skipped": False}] * 14,
            "adherence_rate": None,
        }

    monkeypatch.setattr(health_tools, "medication_history", fake_history)

    app, _ = _make_app()
    async with _client(app) as client:
        # 14-day span
        resp = await client.get(
            f"/api/health/medications/{med_id}/adherence",
            params={"start": "2026-01-01T00:00:00Z", "end": "2026-01-15T00:00:00Z"},
        )
    assert resp.status_code == 200
    body = resp.json()
    # (Jan 15 - Jan 1).days == 14 → expected = 14
    assert body["expected_doses"] == 14
    assert body["adherence_rate"] == pytest.approx(100.0, abs=0.1)


async def test_adherence_empty_doses(monkeypatch):
    """No doses -> zero counts; expected_doses still computed; null adherence_rate
    only when expected_doses is zero (which can't happen with a valid frequency)."""
    med_id = str(uuid.uuid4())

    async def fake_history(pool, medication_id, *, start_date, end_date):
        return {
            "medication": {"id": med_id, "frequency": "daily"},
            "doses": [],
            "adherence_rate": None,
        }

    monkeypatch.setattr(health_tools, "medication_history", fake_history)

    app, _ = _make_app()
    async with _client(app) as client:
        resp = await client.get(f"/api/health/medications/{med_id}/adherence")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_doses"] == 0
    assert body["taken_doses"] == 0
    assert body["skipped_doses"] == 0
    # daily × 30 days = 30 expected even when 0 logged
    assert body["expected_doses"] == 30
    # adherence_rate = 0 / 30 * 100 = 0.0 (not null, because expected > 0)
    assert body["adherence_rate"] == pytest.approx(0.0, abs=0.1)


async def test_adherence_empty_medication_dict(monkeypatch):
    """If medication dict is empty/missing frequency, defaults to 'daily'."""
    med_id = str(uuid.uuid4())

    async def fake_history(pool, medication_id, *, start_date, end_date):
        return {"medication": {}, "doses": [], "adherence_rate": None}

    monkeypatch.setattr(health_tools, "medication_history", fake_history)

    app, _ = _make_app()
    async with _client(app) as client:
        resp = await client.get(f"/api/health/medications/{med_id}/adherence")
    assert resp.status_code == 200
    body = resp.json()
    # Defaults to "daily" → 30 expected
    assert body["expected_doses"] == 30
    assert body["adherence_rate"] == pytest.approx(0.0, abs=0.1)


async def test_adherence_naive_start_with_no_end(monkeypatch):
    """Naive start param + omitted end must not raise TypeError (timezone mismatch).

    When ``start`` lacks timezone info and ``end`` is omitted, ``effective_end``
    defaults to ``datetime.now(UTC)`` (timezone-aware).  Without normalisation the
    subtraction raises ``TypeError: can't subtract offset-naive and offset-aware
    datetimes``, producing a 500.  The route must normalise both sides to UTC and
    return 200.
    """
    med_id = str(uuid.uuid4())

    async def fake_history(pool, medication_id, *, start_date, end_date):
        return {
            "medication": {"id": med_id, "frequency": "daily"},
            "doses": [],
            "adherence_rate": None,
        }

    monkeypatch.setattr(health_tools, "medication_history", fake_history)

    app, _ = _make_app()
    async with _client(app) as client:
        # Naive ISO-8601 (no trailing Z or offset) — FastAPI parses as naive datetime
        resp = await client.get(
            f"/api/health/medications/{med_id}/adherence",
            params={"start": "2026-01-01T00:00:00"},
        )
    # Must not 500; effective window is today-minus-jan1 days (≥1)
    assert resp.status_code == 200
    body = resp.json()
    assert body["expected_doses"] >= 1


async def test_adherence_start_after_end_returns_400(monkeypatch):
    """start > end must return 400 Bad Request."""
    med_id = str(uuid.uuid4())

    async def fake_history(pool, medication_id, *, start_date, end_date):  # pragma: no cover
        return {"medication": {}, "doses": [], "adherence_rate": None}

    monkeypatch.setattr(health_tools, "medication_history", fake_history)

    app, _ = _make_app()
    async with _client(app) as client:
        resp = await client.get(
            f"/api/health/medications/{med_id}/adherence",
            params={"start": "2026-01-31T00:00:00Z", "end": "2026-01-01T00:00:00Z"},
        )
    assert resp.status_code == 400
    assert "start" in resp.json()["detail"].lower()


async def test_adherence_404_when_medication_missing(monkeypatch):
    """A ValueError from the tool maps to 404."""
    med_id = str(uuid.uuid4())

    async def fake_history(pool, medication_id, *, start_date, end_date):
        raise ValueError(f"Medication {medication_id} not found")

    monkeypatch.setattr(health_tools, "medication_history", fake_history)

    app, _ = _make_app()
    async with _client(app) as client:
        resp = await client.get(f"/api/health/medications/{med_id}/adherence")
    assert resp.status_code == 404


async def test_adherence_shared_helper_parity(monkeypatch):
    """Route and insight-scan job produce the same expected_doses denominator.

    Verifies the spec's "Shared denominator with insight job" requirement:
    both callers import the same ``frequency_to_doses_per_day`` helper, so for
    a given (frequency, window_days) pair the expected-dose count is identical.
    """
    from butlers.jobs._roster_loader import load_roster_jobs
    from butlers.tools.health._medication_utils import frequency_to_doses_per_day

    health_jobs = load_roster_jobs("health")
    job_helper = health_jobs._frequency_to_doses_per_day

    # Both must resolve to the same function (shared import).
    test_cases = [
        ("daily", 30),
        ("twice daily", 7),
        ("weekly", 30),
        ("every other day", 14),
        ("prn", 30),
        ("unknown_frequency", 10),
    ]
    for freq, days in test_cases:
        route_expected = round(frequency_to_doses_per_day(freq) * days)
        job_expected = round(job_helper(freq) * days)
        assert route_expected == job_expected, (
            f"Denominator mismatch for freq={freq!r}, days={days}: "
            f"route={route_expected}, job={job_expected}"
        )


# ---------------------------------------------------------------------------
# GET /nutrition/summary
# ---------------------------------------------------------------------------


async def test_nutrition_summary_reshapes_tool_output(monkeypatch):
    """Nutrition route reshapes the flat tool dict into totals + daily_avg + days."""

    async def fake_summary(pool, start_date, end_date):
        return {
            "total_calories": 4000.0,
            "daily_avg_calories": 2000.0,
            "total_protein_g": 300.0,
            "daily_avg_protein_g": 150.0,
            "total_carbs_g": 400.0,
            "daily_avg_carbs_g": 200.0,
            "total_fat_g": 120.0,
            "daily_avg_fat_g": 60.0,
            "meal_count": 6,
        }

    monkeypatch.setattr(health_tools, "nutrition_summary", fake_summary)

    app, _ = _make_app()
    async with _client(app) as client:
        resp = await client.get(
            "/api/health/nutrition/summary",
            params={"start": "2026-01-01T00:00:00Z", "end": "2026-01-03T00:00:00Z"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_calories"] == 4000.0
    assert body["total_protein_g"] == 300.0
    assert body["total_carbs_g"] == 400.0
    assert body["total_fat_g"] == 120.0
    assert body["meal_count"] == 6
    assert body["days"] == 2  # (Jan 3 - Jan 1) = 2 days
    assert body["daily_avg"] == {
        "calories": 2000.0,
        "protein_g": 150.0,
        "carbs_g": 200.0,
        "fat_g": 60.0,
    }


async def test_nutrition_summary_requires_start_and_end():
    """start and end are required query params -> 422 when missing."""
    app, _ = _make_app()
    async with _client(app) as client:
        resp = await client.get("/api/health/nutrition/summary")
    assert resp.status_code == 422


async def test_nutrition_summary_503_when_pool_unavailable():
    """GET returns 503 when the health DB pool is unavailable."""
    app = _make_app_unavailable()
    async with _client(app) as client:
        resp = await client.get(
            "/api/health/nutrition/summary",
            params={"start": "2026-01-01T00:00:00Z", "end": "2026-01-03T00:00:00Z"},
        )
    assert resp.status_code == 503
