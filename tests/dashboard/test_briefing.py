"""Tests for the dashboard briefing endpoint and supporting modules.

Coverage:
    - classify: all five branches (urgent / busy / mild / degraded-quiet / quiet)
    - headline_for: singular and plural variants for each class
    - LLM happy path returns source: "llm"
    - LLM timeout, error, and empty response each return source: "fallback"
    - Voice lint rejects responses containing each banned token
    - Voice lint does not reject "factually" for the "actually" word-boundary case
    - Cache TTL: hit preserves generated_at, miss regenerates
    - HTTP 403 path for non-owner access
    - HTTP 401 path for unauthenticated (API-key middleware)
    - Classification exception falls through to the quiet paragraph
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.briefing.cache import BriefingCache
from butlers.api.briefing.classify import classify, headline_for, time_of_day
from butlers.api.briefing.fallback import elaborate_fallback
from butlers.api.briefing.lint import voice_lint_passes
from butlers.api.briefing.prompts import elaborate_llm
from butlers.api.db import DatabaseManager
from butlers.api.routers.dashboard_briefing import _get_db_manager, _owner_local_now, get_cache
from butlers.core.model_routing import Complexity

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(row: dict) -> MagicMock:
    rec = MagicMock()
    rec.__getitem__ = MagicMock(side_effect=lambda k: row[k])
    rec.get = MagicMock(side_effect=lambda k, default=None: row.get(k, default))
    for k, v in row.items():
        setattr(rec, k, v)
    return rec


def _make_app(pool: AsyncMock, cache: BriefingCache | None = None) -> object:
    """Build a FastAPI test app with the briefing DB and cache overridden."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    mock_db.credential_shared_pool.return_value = pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    if cache is not None:
        app.dependency_overrides[get_cache] = lambda: cache
    return app


def _make_owner_pool(
    has_owner: bool = True,
    owner_fails: bool = False,
    attention_items: list[dict] | None = None,
    butler_statuses: list[dict] | None = None,
) -> AsyncMock:
    """Build a mock switchboard pool for the briefing endpoint."""
    pool = AsyncMock()

    owner_id = "owner-uuid-1234"

    async def _fetchrow(sql, *args):
        if "public.contacts" in sql and "public.entities" in sql:
            if owner_fails:
                raise RuntimeError("DB error")
            if not has_owner:
                return None
            rec = MagicMock()
            rec.__getitem__ = MagicMock(return_value=owner_id)
            return rec
        return None

    items = attention_items or []
    statuses = butler_statuses or []

    async def _fetch(sql, *args):
        if "notifications" in sql:
            return [_make_record(r) for r in items]
        if "butler_registry" in sql:
            return [_make_record(r) for r in statuses]
        return []

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.fetch = AsyncMock(side_effect=_fetch)
    return pool


# ---------------------------------------------------------------------------
# classify: all five branches
# ---------------------------------------------------------------------------


class TestClassify:
    def test_urgent_single_high(self):
        state = {"attention_items": [{"severity": "high"}], "butler_statuses": []}
        assert classify(state) == "urgent"

    def test_urgent_multiple_high(self):
        state = {
            "attention_items": [{"severity": "high"}, {"severity": "high"}, {"severity": "medium"}],
            "butler_statuses": [],
        }
        assert classify(state) == "urgent"

    def test_busy_three_or_more_no_high(self):
        state = {
            "attention_items": [
                {"severity": "medium"},
                {"severity": "low"},
                {"severity": "medium"},
            ],
            "butler_statuses": [],
        }
        assert classify(state) == "busy"

    def test_mild_one_item(self):
        state = {"attention_items": [{"severity": "medium"}], "butler_statuses": []}
        assert classify(state) == "mild"

    def test_mild_two_items(self):
        state = {
            "attention_items": [{"severity": "low"}, {"severity": "medium"}],
            "butler_statuses": [],
        }
        assert classify(state) == "mild"

    def test_degraded_quiet_single_degraded(self):
        state = {
            "attention_items": [],
            "butler_statuses": [{"name": "health", "status": "degraded"}],
        }
        assert classify(state) == "degraded-quiet"

    def test_degraded_quiet_error_status(self):
        state = {
            "attention_items": [],
            "butler_statuses": [{"name": "atlas", "status": "error"}],
        }
        assert classify(state) == "degraded-quiet"

    def test_quiet_all_healthy(self):
        state = {
            "attention_items": [],
            "butler_statuses": [
                {"name": "health", "status": "healthy"},
                {"name": "atlas", "status": "healthy"},
            ],
        }
        assert classify(state) == "quiet"

    def test_quiet_no_butlers(self):
        state = {"attention_items": [], "butler_statuses": []}
        assert classify(state) == "quiet"

    def test_urgent_wins_over_busy(self):
        """High severity triggers urgent even when total items >= 3."""
        state = {
            "attention_items": [
                {"severity": "high"},
                {"severity": "medium"},
                {"severity": "low"},
            ],
            "butler_statuses": [],
        }
        assert classify(state) == "urgent"


# ---------------------------------------------------------------------------
# headline_for: singular and plural per class
# ---------------------------------------------------------------------------


class TestHeadlineFor:
    def test_urgent_singular(self):
        assert headline_for("urgent", 1) == "One thing needs you now."

    def test_urgent_plural(self):
        assert headline_for("urgent", 3) == "3 things need you now."

    def test_busy_uses_total(self):
        assert headline_for("busy", 5) == "Things are busy with 5 items waiting."

    def test_mild_singular(self):
        assert headline_for("mild", 1) == "Things are quiet, with 1 exception."

    def test_mild_plural(self):
        assert headline_for("mild", 2) == "Things are quiet, with 2 exceptions."

    def test_degraded_quiet_singular(self):
        assert headline_for("degraded-quiet", 1) == "Quiet, but 1 butler is degraded."

    def test_degraded_quiet_plural(self):
        assert headline_for("degraded-quiet", 3) == "Quiet, but 3 butlers are degraded."

    def test_quiet(self):
        assert headline_for("quiet", 0) == "Everything is in hand."


# ---------------------------------------------------------------------------
# time_of_day
# ---------------------------------------------------------------------------


class TestTimeOfDay:
    @pytest.mark.parametrize(
        "hour,expected",
        [
            (0, "late-night"),
            (4, "late-night"),
            (5, "morning"),
            (11, "morning"),
            (12, "afternoon"),
            (16, "afternoon"),
            (17, "evening"),
            (20, "evening"),
            (21, "night"),
            (23, "night"),
        ],
    )
    def test_buckets(self, hour, expected):
        assert time_of_day(hour) == expected

    async def test_owner_local_now_uses_general_settings_timezone(self):
        pool = _make_owner_pool()
        utc_now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)

        with patch(
            "butlers.api.routers.dashboard_briefing.load_general_settings",
            new=AsyncMock(return_value={"timezone": "Asia/Singapore"}),
        ):
            local_now = await _owner_local_now(pool, utc_now=utc_now)

        assert local_now.hour == 23
        assert local_now.tzinfo is not None


# ---------------------------------------------------------------------------
# Voice lint
# ---------------------------------------------------------------------------


class TestVoiceLint:
    def test_clean_text_passes(self):
        assert voice_lint_passes("The system ran without issues.") is True

    def test_rejects_exclamation_mark(self):
        assert voice_lint_passes("Everything is fine!") is False

    def test_rejects_em_dash(self):
        assert voice_lint_passes("The butler ran — all good.") is False

    @pytest.mark.parametrize("pronoun", ["I", "we", "us", "our"])
    def test_rejects_first_person_pronouns(self, pronoun):
        assert voice_lint_passes(f"{pronoun} checked the queue.") is False

    def test_rejects_will_be(self):
        assert voice_lint_passes("The butler will be ready soon.") is False

    def test_rejects_is_going_to(self):
        assert voice_lint_passes("The system is going to finish soon.") is False

    @pytest.mark.parametrize("adverb", ["currently", "presently", "just", "simply", "basically"])
    def test_rejects_hedging_adverbs(self, adverb):
        assert voice_lint_passes(f"The butler is {adverb} processing.") is False

    def test_word_boundary_does_not_reject_factually(self):
        """'factually' must not be rejected as a match for 'actually'."""
        assert voice_lint_passes("The data is factually accurate.") is True

    def test_our_boundary_does_not_reject_iour(self):
        """'honour' must not be rejected as a match for 'our'."""
        assert voice_lint_passes("The system acted with honour.") is True

    def test_just_boundary_does_not_reject_adjustment(self):
        """'adjustment' must not be rejected as a match for 'just'."""
        assert voice_lint_passes("An adjustment was made to the queue.") is True


# ---------------------------------------------------------------------------
# elaborate_fallback
# ---------------------------------------------------------------------------


class TestElaborateFallback:
    @pytest.mark.parametrize("state_class", ["urgent", "busy", "mild", "degraded-quiet", "quiet"])
    def test_returns_string_for_all_classes(self, state_class):
        result = elaborate_fallback({}, state_class)
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.parametrize("state_class", ["urgent", "busy", "mild", "degraded-quiet", "quiet"])
    def test_fallbacks_pass_voice_lint(self, state_class):
        """Every fallback paragraph must comply with the voice rules."""
        result = elaborate_fallback({}, state_class)
        assert voice_lint_passes(result), (
            f"Fallback for {state_class!r} failed voice lint: {result!r}"
        )

    def test_unknown_class_returns_quiet_paragraph(self):
        result = elaborate_fallback({}, "nonexistent-class")
        quiet_result = elaborate_fallback({}, "quiet")
        assert result == quiet_result


# ---------------------------------------------------------------------------
# LLM happy path: source = "llm"
# ---------------------------------------------------------------------------


class TestLlmHappyPath:
    async def test_elaboration_uses_local_runtime_dispatcher(self):
        """elaborate_llm uses the catalog-backed local runtime dispatcher."""
        pool = _make_owner_pool()

        dispatcher = MagicMock()
        dispatcher.call = AsyncMock(return_value="The local runtime wrote this paragraph.")

        with patch(
            "butlers.api.briefing.prompts.DiscretionDispatcher",
            return_value=dispatcher,
        ) as dispatcher_cls:
            text = await elaborate_llm(
                pool,
                {"attention_items": [], "butler_statuses": []},
                "quiet",
            )

        assert text == "The local runtime wrote this paragraph."
        dispatcher_cls.assert_called_once_with(
            pool,
            butler_name="__dashboard_briefing__",
            complexity_tier=Complexity.TRIVIAL,
        )
        dispatcher.call.assert_awaited_once()

    async def test_llm_happy_path_returns_llm_source(self):
        """When LLM returns a voice-clean response, source is 'llm'."""
        pool = _make_owner_pool()
        cache = BriefingCache(ttl_seconds=300)
        app = _make_app(pool, cache)

        with patch(
            "butlers.api.routers.dashboard_briefing.elaborate_llm",
            new=AsyncMock(return_value="All butlers are healthy and the queue is empty."),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/dashboard/briefing")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source"] == "llm"
        assert data["elaboration"] == "All butlers are healthy and the queue is empty."


# ---------------------------------------------------------------------------
# LLM failures fall back to templated paragraph
# ---------------------------------------------------------------------------


class TestLlmFailureFallback:
    async def test_llm_returns_none_triggers_fallback(self):
        """elaborate_llm returning None results in source: fallback."""
        pool = _make_owner_pool()
        cache = BriefingCache(ttl_seconds=300)
        app = _make_app(pool, cache)

        with patch(
            "butlers.api.routers.dashboard_briefing.elaborate_llm",
            new=AsyncMock(return_value=None),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/dashboard/briefing")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source"] == "fallback"

    async def test_llm_exception_triggers_fallback(self):
        """An unhandled exception from elaborate_llm produces source: fallback."""
        pool = _make_owner_pool()
        cache = BriefingCache(ttl_seconds=300)
        app = _make_app(pool, cache)

        with patch(
            "butlers.api.routers.dashboard_briefing.elaborate_llm",
            new=AsyncMock(side_effect=RuntimeError("network failure")),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/dashboard/briefing")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source"] == "fallback"

    async def test_llm_voice_lint_rejection_triggers_fallback(self):
        """A response that fails voice lint produces source: fallback."""
        pool = _make_owner_pool()
        cache = BriefingCache(ttl_seconds=300)
        app = _make_app(pool, cache)

        # "We" is a first-person pronoun: should be rejected.
        with patch(
            "butlers.api.routers.dashboard_briefing.elaborate_llm",
            new=AsyncMock(return_value="We checked all systems today!"),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/dashboard/briefing")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source"] == "fallback"


# ---------------------------------------------------------------------------
# Cache TTL: hit preserves generated_at, miss regenerates
# ---------------------------------------------------------------------------


class TestCacheTTL:
    async def test_cache_hit_preserves_generated_at(self):
        """A second request within TTL returns the same generated_at."""
        pool = _make_owner_pool()
        cache = BriefingCache(ttl_seconds=300)
        app = _make_app(pool, cache)

        call_count = 0

        async def _llm_stub(pool, state, state_class):
            nonlocal call_count
            call_count += 1
            return "The system is running without issues."

        with patch("butlers.api.routers.dashboard_briefing.elaborate_llm", new=_llm_stub):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp1 = await client.get("/api/dashboard/briefing")
                resp2 = await client.get("/api/dashboard/briefing")

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        data1 = resp1.json()["data"]
        data2 = resp2.json()["data"]
        # generated_at must be identical (cache hit returns original timestamp)
        assert data1["generated_at"] == data2["generated_at"]
        # LLM should only have been called once
        assert call_count == 1

    async def test_cache_miss_after_ttl_regenerates(self):
        """After TTL expiry the briefing is recomposed with a new generated_at."""
        pool = _make_owner_pool()
        # Very short TTL so the entry expires immediately.
        cache = BriefingCache(ttl_seconds=0.001)
        app = _make_app(pool, cache)

        call_count = 0

        async def _llm_stub(pool, state, state_class):
            nonlocal call_count
            call_count += 1
            return "The system is running without issues."

        with patch("butlers.api.routers.dashboard_briefing.elaborate_llm", new=_llm_stub):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp1 = await client.get("/api/dashboard/briefing")
                # Sleep long enough for the TTL to expire.
                await asyncio.sleep(0.05)
                resp2 = await client.get("/api/dashboard/briefing")

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        data1 = resp1.json()["data"]
        data2 = resp2.json()["data"]
        # generated_at should differ after expiry and recomposition.
        assert data1["generated_at"] != data2["generated_at"]
        # LLM should have been called twice (once per composition).
        assert call_count == 2


# ---------------------------------------------------------------------------
# HTTP 403 path for non-owner access
# ---------------------------------------------------------------------------


class TestNonOwnerAccess:
    async def test_403_when_no_owner_in_db(self):
        pool = _make_owner_pool(has_owner=False)
        cache = BriefingCache(ttl_seconds=300)
        app = _make_app(pool, cache)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/dashboard/briefing")

        assert resp.status_code == 403

    async def test_403_when_owner_query_fails(self):
        pool = _make_owner_pool(has_owner=True, owner_fails=True)
        cache = BriefingCache(ttl_seconds=300)
        app = _make_app(pool, cache)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/dashboard/briefing")

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# HTTP 401 path for unauthenticated
# ---------------------------------------------------------------------------


class TestUnauthenticated:
    async def test_401_when_api_key_required_and_missing(self):
        """ApiKeyMiddleware returns 401 when DASHBOARD_API_KEY is set
        and the request lacks the X-API-Key header."""
        pool = _make_owner_pool()
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = pool

        # Create app with an explicit API key to enable auth.
        app = create_app(api_key="test-secret-key")
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/dashboard/briefing")  # no X-API-Key header

        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Classification exception falls through to quiet paragraph
# ---------------------------------------------------------------------------


class TestClassificationExceptionFallback:
    async def test_classification_exception_returns_quiet(self):
        """When classify raises, the endpoint returns state_class=quiet
        with the quiet templated paragraph and source=fallback."""
        pool = _make_owner_pool()
        cache = BriefingCache(ttl_seconds=300)
        app = _make_app(pool, cache)

        with (
            patch(
                "butlers.api.routers.dashboard_briefing.classify",
                side_effect=RuntimeError("schema drift"),
            ),
            patch(
                "butlers.api.routers.dashboard_briefing.elaborate_llm",
                new=AsyncMock(return_value=None),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/dashboard/briefing")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["state_class"] == "quiet"
        assert data["source"] == "fallback"
        # The quiet fallback paragraph must be non-empty.
        assert data["elaboration"] == elaborate_fallback({}, "quiet")


# ---------------------------------------------------------------------------
# Response shape contract
# ---------------------------------------------------------------------------


class TestResponseShape:
    async def test_response_has_six_required_fields(self):
        """The response body must contain exactly the six specified fields."""
        pool = _make_owner_pool()
        cache = BriefingCache(ttl_seconds=300)
        app = _make_app(pool, cache)

        with patch(
            "butlers.api.routers.dashboard_briefing.elaborate_llm",
            new=AsyncMock(return_value=None),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/dashboard/briefing")

        assert resp.status_code == 200
        data = resp.json()["data"]
        required = {"greet", "headline", "elaboration", "source", "state_class", "generated_at"}
        assert set(data.keys()) == required

    async def test_greet_matches_time_of_day_format(self):
        """greet must be 'Good {time_of_day}.'"""
        pool = _make_owner_pool()
        cache = BriefingCache(ttl_seconds=300)
        app = _make_app(pool, cache)

        valid_greets = {
            "Good late-night.",
            "Good morning.",
            "Good afternoon.",
            "Good evening.",
            "Good night.",
        }

        with patch(
            "butlers.api.routers.dashboard_briefing.elaborate_llm",
            new=AsyncMock(return_value=None),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/dashboard/briefing")

        data = resp.json()["data"]
        assert data["greet"] in valid_greets

    async def test_state_class_is_valid(self):
        """state_class must be one of the five valid values."""
        pool = _make_owner_pool()
        cache = BriefingCache(ttl_seconds=300)
        app = _make_app(pool, cache)

        valid_classes = {"urgent", "busy", "mild", "degraded-quiet", "quiet"}

        with patch(
            "butlers.api.routers.dashboard_briefing.elaborate_llm",
            new=AsyncMock(return_value=None),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/dashboard/briefing")

        data = resp.json()["data"]
        assert data["state_class"] in valid_classes

    async def test_source_is_valid(self):
        """source must be 'llm' or 'fallback'."""
        pool = _make_owner_pool()
        cache = BriefingCache(ttl_seconds=300)
        app = _make_app(pool, cache)

        with patch(
            "butlers.api.routers.dashboard_briefing.elaborate_llm",
            new=AsyncMock(return_value=None),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/dashboard/briefing")

        data = resp.json()["data"]
        assert data["source"] in ("llm", "fallback")
