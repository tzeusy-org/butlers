"""Unit tests for the health dashboard routes added in bu-sqjc7.

Covers three routes, all of which read/write the ``facts`` table via the
Health butler's existing MCP tools (no new schema):

- POST /api/health/medications/{id}/doses  -> medication_log_dose (took_dose fact)
- GET  /api/health/medications/{id}/adherence -> medication_history (aggregate)
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
    """Adherence route reports total/taken/skipped counts plus the rate."""
    med_id = str(uuid.uuid4())

    async def fake_history(pool, medication_id, *, start_date, end_date):
        assert medication_id == med_id
        return {
            "medication": {"id": med_id, "name": "Metformin"},
            "doses": [
                {"skipped": False},
                {"skipped": False},
                {"skipped": True},
            ],
            "adherence_rate": 66.7,
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
    assert body["adherence_rate"] == 66.7


async def test_adherence_empty_doses(monkeypatch):
    """No doses -> zero counts and null adherence_rate."""
    med_id = str(uuid.uuid4())

    async def fake_history(pool, medication_id, *, start_date, end_date):
        return {"medication": {}, "doses": [], "adherence_rate": None}

    monkeypatch.setattr(health_tools, "medication_history", fake_history)

    app, _ = _make_app()
    async with _client(app) as client:
        resp = await client.get(f"/api/health/medications/{med_id}/adherence")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_doses"] == 0
    assert body["taken_doses"] == 0
    assert body["skipped_doses"] == 0
    assert body["adherence_rate"] is None


async def test_adherence_forwards_window(monkeypatch):
    """start/end query params are forwarded to medication_history."""
    med_id = str(uuid.uuid4())
    seen: dict = {}

    async def fake_history(pool, medication_id, *, start_date, end_date):
        seen["start"] = start_date
        seen["end"] = end_date
        return {"medication": {}, "doses": [], "adherence_rate": None}

    monkeypatch.setattr(health_tools, "medication_history", fake_history)

    app, _ = _make_app()
    async with _client(app) as client:
        resp = await client.get(
            f"/api/health/medications/{med_id}/adherence",
            params={"start": "2026-01-01T00:00:00Z", "end": "2026-01-31T00:00:00Z"},
        )
    assert resp.status_code == 200
    assert seen["start"] is not None
    assert seen["end"] is not None


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
