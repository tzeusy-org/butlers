"""Unit tests for the health Voice briefing route added in bu-sqjc7.4.

GET /api/health/briefing mirrors GET /api/dashboard/briefing: it is an
owner-only composer over the health summary + the proactive insight feed.

Covered:
- templated-only by default (cost flag off -> no LLM invoked, source=fallback)
- LLM happy path (flag on, clean text -> source=llm)
- non-diagnostic voice-lint rejection -> source=fallback (never raises)
- never-raises on LLM timeout/error -> HTTP 200 templated fallback
- owner-only 403 (no cache read or write)
- per-owner 5-minute TTL cache behaviour (hit preserves generated_at; expiry recomputes)
- source field is exactly "llm" or "fallback"
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
from butlers.api.briefing.cache import BriefingCache
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)

# Trigger router discovery so the dependency + module hooks are importable.
_APP_SEED = create_app(api_key="")
_health_router = sys.modules["health_api_router"]
_health_get_db_manager = _health_router._get_db_manager


@pytest.fixture(autouse=True)
def _fresh_cache_and_flag(monkeypatch):
    """Reset the briefing cache and clear the cost flag before every test."""
    _health_router.replace_health_briefing_cache(BriefingCache())
    monkeypatch.delenv("HEALTH_BRIEFING_LLM_ENABLED", raising=False)
    # Keep timezone resolution off the mocked pool and deterministic.
    monkeypatch.setattr(
        _health_router,
        "load_general_settings",
        AsyncMock(return_value={"timezone": "UTC"}),
    )
    yield


def _insight_row(priority: int = 1, message: str = "noted", category: str = "trend") -> dict:
    return {
        "id": uuid.uuid4(),
        "category": category,
        "priority": priority,
        "message": message,
        "metadata": {},
        "created_at": _NOW,
        "status": "pending",
        "expires_at": None,
    }


def _make_app(
    *,
    owner: bool = True,
    insights: list[dict] | None = None,
    summary: dict | None = None,
    monkeypatch=None,
):
    """Build a test app with switchboard + health pools mocked.

    - switchboard pool: owner assertion (fetchrow) + insight feed (fetch)
    - health pool: health_summary() reads (patched via butlers.tools.health)
    """
    sw_pool = AsyncMock()
    sw_pool.fetchrow.return_value = {"id": "owner-1"} if owner else None
    sw_pool.fetch.return_value = list(insights or [])

    health_pool = AsyncMock()

    if monkeypatch is not None:

        async def fake_health_summary(pool):
            return summary or {
                "recent_measurements": [],
                "active_medications": [],
                "active_conditions": [],
            }

        monkeypatch.setattr(health_tools, "health_summary", fake_health_summary)

    def pool_for(name):
        return sw_pool if name == "switchboard" else health_pool

    db = MagicMock(spec=DatabaseManager)
    db.pool.side_effect = pool_for

    app = create_app(api_key="")
    app.dependency_overrides[_health_get_db_manager] = lambda: db
    return app, sw_pool, health_pool


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Templated-only by default (cost flag off)
# ---------------------------------------------------------------------------


async def test_templated_default_invokes_no_llm(monkeypatch):
    """Flag off: source=fallback and the LLM elaboration is never invoked."""
    spy = AsyncMock(return_value="should not be used")
    monkeypatch.setattr(_health_router, "elaborate_health_llm", spy)

    app, _, _ = _make_app(insights=[_insight_row()], monkeypatch=monkeypatch)
    async with _client(app) as client:
        resp = await client.get("/api/health/briefing")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "fallback"
    assert set(data) == {
        "greet",
        "headline",
        "elaboration",
        "source",
        "state_class",
        "generated_at",
    }
    spy.assert_not_called()


# ---------------------------------------------------------------------------
# LLM happy path (flag on)
# ---------------------------------------------------------------------------


async def test_llm_clean_text_sets_source_llm(monkeypatch):
    """Flag on + voice-clean elaboration -> source=llm."""
    monkeypatch.setenv("HEALTH_BRIEFING_LLM_ENABLED", "1")
    clean = "The health log holds two medications. The insight feed has one item pending review."
    monkeypatch.setattr(_health_router, "elaborate_health_llm", AsyncMock(return_value=clean))

    app, _, _ = _make_app(insights=[_insight_row()], monkeypatch=monkeypatch)
    async with _client(app) as client:
        resp = await client.get("/api/health/briefing")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "llm"
    assert data["elaboration"] == clean


# ---------------------------------------------------------------------------
# Non-diagnostic voice-lint rejection
# ---------------------------------------------------------------------------


async def test_diagnostic_line_falls_back(monkeypatch):
    """A diagnosis/advice token is rejected -> source=fallback, never raises."""
    monkeypatch.setenv("HEALTH_BRIEFING_LLM_ENABLED", "1")
    diagnostic = "The readings may indicate elevated risk of a condition."
    monkeypatch.setattr(_health_router, "elaborate_health_llm", AsyncMock(return_value=diagnostic))

    app, _, _ = _make_app(insights=[_insight_row()], monkeypatch=monkeypatch)
    async with _client(app) as client:
        resp = await client.get("/api/health/briefing")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "fallback"
    assert "risk of" not in data["elaboration"]


# ---------------------------------------------------------------------------
# Never raises on LLM timeout/error
# ---------------------------------------------------------------------------


async def test_llm_timeout_returns_templated(monkeypatch):
    """An LLM timeout/transport error -> HTTP 200 templated fallback (never raises)."""
    monkeypatch.setenv("HEALTH_BRIEFING_LLM_ENABLED", "1")

    async def boom(pool, state, state_class):
        raise TimeoutError("transport timed out")

    monkeypatch.setattr(_health_router, "elaborate_health_llm", boom)

    app, _, _ = _make_app(insights=[_insight_row()], monkeypatch=monkeypatch)
    async with _client(app) as client:
        resp = await client.get("/api/health/briefing")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "fallback"


async def test_llm_empty_text_returns_templated(monkeypatch):
    """An LLM returning None -> source=fallback."""
    monkeypatch.setenv("HEALTH_BRIEFING_LLM_ENABLED", "1")
    monkeypatch.setattr(_health_router, "elaborate_health_llm", AsyncMock(return_value=None))

    app, _, _ = _make_app(monkeypatch=monkeypatch)
    async with _client(app) as client:
        resp = await client.get("/api/health/briefing")

    assert resp.status_code == 200
    assert resp.json()["data"]["source"] == "fallback"


# ---------------------------------------------------------------------------
# Owner-only access
# ---------------------------------------------------------------------------


async def test_non_owner_403_no_cache_write(monkeypatch):
    """A non-owner session -> HTTP 403 and no cache entry is read or written."""
    app, _, _ = _make_app(owner=False, monkeypatch=monkeypatch)
    async with _client(app) as client:
        resp = await client.get("/api/health/briefing")

    assert resp.status_code == 403
    assert len(_health_router.get_health_briefing_cache()._store) == 0


async def test_owner_query_failure_403(monkeypatch):
    """A failing owner-assertion query -> HTTP 403 (not 500)."""
    app, sw_pool, _ = _make_app(monkeypatch=monkeypatch)
    sw_pool.fetchrow.side_effect = RuntimeError("db down")
    async with _client(app) as client:
        resp = await client.get("/api/health/briefing")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Per-owner 5-minute TTL cache
# ---------------------------------------------------------------------------


async def test_cache_hit_preserves_generated_at(monkeypatch):
    """Within TTL, the second call is served from cache (same generated_at)."""
    _health_router.replace_health_briefing_cache(BriefingCache(ttl_seconds=300.0))

    app, sw_pool, _ = _make_app(insights=[_insight_row()], monkeypatch=monkeypatch)
    async with _client(app) as client:
        first = (await client.get("/api/health/briefing")).json()["data"]
        second = (await client.get("/api/health/briefing")).json()["data"]

    assert first["generated_at"] == second["generated_at"]
    # The insight feed (sw_pool.fetch) is queried only on the cache miss.
    assert sw_pool.fetch.await_count == 1


async def test_cache_expiry_recomposes(monkeypatch):
    """With a zero TTL, each call recomposes (cache always expired)."""
    _health_router.replace_health_briefing_cache(BriefingCache(ttl_seconds=0.0))

    app, sw_pool, _ = _make_app(insights=[_insight_row()], monkeypatch=monkeypatch)
    async with _client(app) as client:
        await client.get("/api/health/briefing")
        await client.get("/api/health/briefing")

    assert sw_pool.fetch.await_count == 2


# ---------------------------------------------------------------------------
# Classification headline (drives state_class)
# ---------------------------------------------------------------------------


async def test_state_class_quiet_when_no_insights(monkeypatch):
    """No pending insights -> state_class=quiet, source=fallback."""
    app, _, _ = _make_app(insights=[], monkeypatch=monkeypatch)
    async with _client(app) as client:
        data = (await client.get("/api/health/briefing")).json()["data"]
    assert data["state_class"] == "quiet"
    assert data["source"] in ("llm", "fallback")


async def test_state_class_attention_on_high_priority(monkeypatch):
    """A high-priority pending insight -> state_class=attention."""
    app, _, _ = _make_app(insights=[_insight_row(priority=3)], monkeypatch=monkeypatch)
    async with _client(app) as client:
        data = (await client.get("/api/health/briefing")).json()["data"]
    assert data["state_class"] == "attention"


# ---------------------------------------------------------------------------
# Non-diagnostic voice lint (unit-level)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "This may indicate a condition.",
        "There is a risk of complications.",
        "The number is elevated.",
        "This is a symptom of something.",
        "Readings rose because of the medication.",
        "The trend will continue tomorrow.",
        "Great job keeping the streak going!",
        "You may have an issue.",
        "Consistent with a known pattern.",
    ],
)
def test_health_lint_rejects_non_descriptive_copy(bad):
    assert _health_router._health_voice_lint_passes(bad) is False


@pytest.mark.parametrize(
    "good",
    [
        "The health log holds two active medications and one tracked condition.",
        "The insight feed has three items pending review.",
        "One measurement type carries recent readings.",
    ],
)
def test_health_lint_accepts_descriptive_copy(good):
    assert _health_router._health_voice_lint_passes(good) is True
