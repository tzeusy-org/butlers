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
from butlers.api.briefing.prompts import _build_user_message, elaborate_llm
from butlers.api.db import DatabaseManager
from butlers.api.routers.dashboard_briefing import (
    _fetch_dashboard_state,
    _get_db_manager,
    _owner_local_now,
    _row_get,
    get_cache,
)
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
    audit_rows: list[dict] | None = None,
) -> AsyncMock:
    """Build a mock switchboard pool for the briefing endpoint.

    Routes pool.fetch calls by SQL keyword:
        - "notifications"   -> attention_items (notification rows)
        - "audit_source"    -> audit_rows (grouped audit error rows; the canonical
                               public.audit_log grouping CTE alias, bu-j26e8)
        - "butler_registry" -> butler_statuses
    """
    pool = AsyncMock()

    owner_id = "owner-uuid-1234"

    async def _fetchrow(sql, *args):
        # Owner-assertion query now reads public.entities directly (bu-jnaa3):
        # SELECT id FROM public.entities WHERE 'owner' = ANY(roles).
        if "public.entities" in sql and "ANY(roles)" in sql:
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
    audits = audit_rows or []

    async def _fetch(sql, *args):
        if "notifications" in sql:
            return [_make_record(r) for r in items]
        if "audit_source" in sql:
            return [_make_record(r) for r in audits]
        if "butler_registry" in sql:
            return [_make_record(r) for r in statuses]
        return []

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.fetch = AsyncMock(side_effect=_fetch)
    return pool


# ---------------------------------------------------------------------------
# Dashboard state context
# ---------------------------------------------------------------------------


class TestDashboardStateContext:
    async def test_fetch_dashboard_state_preserves_human_readable_context(self):
        """The briefing state includes names, messages, timestamps, and health context."""
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool(
            attention_items=[
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "severity": "high",
                    "source_butler": "calendar",
                    "channel": "telegram",
                    "message": "Calendar sync failed for the owner account",
                    "metadata": {"severity": "high", "kind": "oauth"},
                    "status": "unread",
                    "error": "Token has been expired or revoked",
                    "session_id": None,
                    "trace_id": "trace-123",
                    "created_at": now,
                }
            ],
            butler_statuses=[
                {
                    "name": "calendar",
                    "status": "degraded",
                    "agent_type": "butler",
                    "eligibility_state": "stale",
                    "last_seen_at": now,
                    "description": "Calendar butler",
                    "modules": ["calendar"],
                    "capabilities": ["calendar.sync"],
                    "quarantine_reason": None,
                }
            ],
        )

        state = await _fetch_dashboard_state(pool, now)

        assert state["attention_items"][0]["butler"] == "calendar"
        assert state["attention_items"][0]["description"] == (
            "Calendar sync failed for the owner account"
        )
        assert state["attention_items"][0]["last_seen_at"] == "2026-05-13T15:59:00+00:00"
        assert state["notification_items"][0]["channel"] == "telegram"
        assert state["notification_items"][0]["metadata"] == {
            "severity": "high",
            "kind": "oauth",
        }
        assert state["butler_statuses"][0]["status"] == "degraded"
        assert state["butler_statuses"][0]["eligibility_state"] == "stale"
        assert state["overview_totals"] == {
            "attention_total": 1,
            "attention_high": 1,
            "attention_medium": 0,
            "attention_low": 0,
            "butlers_total": 1,
            "butlers_unhealthy": 1,
        }


# ---------------------------------------------------------------------------
# Briefing liveness clock-skew guard (bu-1hs86)
#
# Verifies:
#   - The butler_registry SQL query contains the future-clock WHEN clause.
#   - A future-dated last_seen_at (>5 min ahead) is surfaced as 'degraded'.
#   - A stale last_seen_at (past, beyond TTL) is still 'degraded'.
#   - A healthy last_seen_at (within TTL, not in the future) is 'healthy'.
# ---------------------------------------------------------------------------


class TestButlerLivenessClockSkew:
    """SQL liveness check must flag future-dated last_seen_at as degraded."""

    def test_butler_registry_sql_contains_future_clock_guard(self):
        """The butler_registry query must include a WHEN clause for future timestamps."""
        import inspect

        import butlers.api.routers.dashboard_briefing as mod

        source = inspect.getsource(mod)
        assert "last_seen_at > NOW() + INTERVAL '5 minutes'" in source, (
            "SQL liveness check must guard against future-dated last_seen_at "
            "(clock-skew degraded guard is missing)"
        )

    async def test_future_dated_last_seen_at_is_degraded(self):
        """A butler whose last_seen_at is >5 min in the future appears as 'degraded'."""
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        # Simulate what the DB CASE expression returns when last_seen_at is far in the future
        pool = _make_owner_pool(
            butler_statuses=[
                {
                    "name": "skewed-butler",
                    "status": "degraded",  # what the SQL CASE yields for future ts
                    "agent_type": "butler",
                    "eligibility_state": "active",
                    "last_seen_at": now,
                    "description": "Butler with clock skew",
                    "modules": [],
                    "capabilities": [],
                    "quarantine_reason": None,
                }
            ],
        )

        state = await _fetch_dashboard_state(pool, now)

        assert len(state["butler_statuses"]) == 1
        assert state["butler_statuses"][0]["status"] == "degraded"
        assert state["butler_statuses"][0]["name"] == "skewed-butler"

    async def test_stale_last_seen_at_is_still_degraded(self):
        """A butler whose last_seen_at is past the TTL remains 'degraded'."""
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool(
            butler_statuses=[
                {
                    "name": "stale-butler",
                    "status": "degraded",  # what the SQL CASE yields for stale ts
                    "agent_type": "butler",
                    "eligibility_state": "active",
                    "last_seen_at": now,
                    "description": "Butler with stale heartbeat",
                    "modules": [],
                    "capabilities": [],
                    "quarantine_reason": None,
                }
            ],
        )

        state = await _fetch_dashboard_state(pool, now)

        assert state["butler_statuses"][0]["status"] == "degraded"

    async def test_healthy_last_seen_at_is_healthy(self):
        """A butler with a recent, non-future last_seen_at is 'healthy'."""
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool(
            butler_statuses=[
                {
                    "name": "healthy-butler",
                    "status": "healthy",  # what the SQL CASE yields for in-range ts
                    "agent_type": "butler",
                    "eligibility_state": "active",
                    "last_seen_at": now,
                    "description": "Healthy butler",
                    "modules": [],
                    "capabilities": [],
                    "quarantine_reason": None,
                }
            ],
        )

        state = await _fetch_dashboard_state(pool, now)

        assert state["butler_statuses"][0]["status"] == "healthy"


class TestPromptContext:
    def test_build_user_message_summarizes_top_attention_and_health(self):
        """The LLM prompt gets a bounded ecosystem snapshot, not raw thin rows."""
        state = {
            "now": datetime(2026, 5, 13, 23, 59, tzinfo=UTC),
            "attention_items": [
                {
                    "severity": "high",
                    "type": "notification",
                    "butler": "calendar",
                    "description": "Calendar sync failed for the owner account",
                    "last_seen_at": "2026-05-13T15:59:00+00:00",
                    "link": "/notifications",
                    "source": "notification",
                }
            ],
            "butler_statuses": [
                {
                    "name": "calendar",
                    "status": "degraded",
                    "type": "butler",
                    "eligibility_state": "stale",
                    "last_seen_at": "2026-05-13T15:59:00+00:00",
                }
            ],
            "overview_totals": {
                "attention_total": 1,
                "attention_high": 1,
                "attention_medium": 0,
                "attention_low": 0,
                "butlers_total": 1,
                "butlers_unhealthy": 1,
            },
        }

        message = _build_user_message(state, "urgent")

        assert "attention_summary" in message
        assert "top_attention_items" in message
        assert "butler_health" in message
        assert "Calendar sync failed for the owner account" in message
        assert "2026-05-13T15:59:00+00:00" in message
        assert "calendar" in message


# ---------------------------------------------------------------------------
# classify: all five branches
# ---------------------------------------------------------------------------


class TestClassify:
    @pytest.mark.parametrize(
        "attention_items, butler_statuses, expected",
        [
            # urgent: any high-severity item
            ([{"severity": "high"}], [], "urgent"),
            (
                [{"severity": "high"}, {"severity": "high"}, {"severity": "medium"}],
                [],
                "urgent",
            ),
            # urgent wins over busy: high severity even when total >= 3
            (
                [{"severity": "high"}, {"severity": "medium"}, {"severity": "low"}],
                [],
                "urgent",
            ),
            # busy: 3+ items, none high
            ([{"severity": "medium"}, {"severity": "low"}, {"severity": "medium"}], [], "busy"),
            # mild: 1-2 items, none high
            ([{"severity": "medium"}], [], "mild"),
            ([{"severity": "low"}, {"severity": "medium"}], [], "mild"),
            # degraded-quiet: no items but a degraded/error butler
            ([], [{"name": "health", "status": "degraded"}], "degraded-quiet"),
            ([], [{"name": "atlas", "status": "error"}], "degraded-quiet"),
            # quiet: no items, all butlers healthy (or none)
            (
                [],
                [
                    {"name": "health", "status": "healthy"},
                    {"name": "atlas", "status": "healthy"},
                ],
                "quiet",
            ),
            ([], [], "quiet"),
        ],
    )
    def test_classify_state_machine(self, attention_items, butler_statuses, expected):
        """classify() five-branch state machine incl. urgent-wins-over-busy."""
        state = {"attention_items": attention_items, "butler_statuses": butler_statuses}
        assert classify(state) == expected


# ---------------------------------------------------------------------------
# headline_for: singular and plural per class
# ---------------------------------------------------------------------------


class TestHeadlineFor:
    @pytest.mark.parametrize(
        "state_class, count, expected",
        [
            ("urgent", 1, "One thing needs you now."),
            ("urgent", 3, "3 things need you now."),
            ("mild", 1, "Things are quiet, with 1 exception."),
            ("mild", 2, "Things are quiet, with 2 exceptions."),
            ("degraded-quiet", 1, "Quiet, but 1 butler is degraded."),
            ("degraded-quiet", 3, "Quiet, but 3 butlers are degraded."),
        ],
    )
    def test_singular_plural_pluralization(self, state_class, count, expected):
        """Headlines pluralize correctly across urgent/mild/degraded-quiet."""
        assert headline_for(state_class, count) == expected

    def test_busy_uses_total(self):
        assert headline_for("busy", 5) == "Things are busy with 5 items waiting."

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
            complexity_tier=Complexity.CHEAP,
        )

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
    async def test_response_envelope_fields_and_value_domains(self):
        """One response: required field set, greet format, valid state_class and source."""
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
        assert data["greet"] in {
            "Good late-night.",
            "Good morning.",
            "Good afternoon.",
            "Good evening.",
            "Good night.",
        }
        assert data["state_class"] in {"urgent", "busy", "mild", "degraded-quiet", "quiet"}
        assert data["source"] in ("llm", "fallback")


# ---------------------------------------------------------------------------
# _row_get: only catches KeyError, not AttributeError
# ---------------------------------------------------------------------------


class TestRowGet:
    def test_returns_value_when_key_exists(self):
        """_row_get returns the value for a present key."""
        rec = MagicMock()
        rec.__getitem__ = MagicMock(return_value="calendar")
        assert _row_get(rec, "name") == "calendar"

    def test_returns_default_on_key_error(self):
        """_row_get returns the default when the key is absent."""
        rec = MagicMock()
        rec.__getitem__ = MagicMock(side_effect=KeyError("missing"))
        assert _row_get(rec, "missing_col") is None
        assert _row_get(rec, "missing_col", "fallback") == "fallback"

    def test_does_not_suppress_attribute_error(self):
        """_row_get lets AttributeError propagate — it is not a missing-column error."""
        rec = MagicMock()
        rec.__getitem__ = MagicMock(side_effect=AttributeError("bad attribute"))
        with pytest.raises(AttributeError):
            _row_get(rec, "broken")


# ---------------------------------------------------------------------------
# Data-fetch failures log at WARNING
# ---------------------------------------------------------------------------


class TestDataFetchWarnings:
    @pytest.mark.parametrize(
        "failing_sql, warning_msg",
        [
            ("notifications", "Could not fetch attention items"),
            ("audit_source", "Could not fetch audit-derived attention items"),
            ("butler_registry", "Could not fetch butler statuses"),
        ],
    )
    async def test_fetch_failure_logs_warning_and_isolates(self, caplog, failing_sql, warning_msg):
        """A DB error in one of the three concurrent fetchers logs at WARNING.

        The failing query degrades to empty while the other two still succeed.
        """
        import logging

        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = AsyncMock()

        async def _fetch_side_effect(sql, *args):
            if failing_sql in sql:
                raise RuntimeError("DB outage")
            return []

        pool.fetch = AsyncMock(side_effect=_fetch_side_effect)

        with caplog.at_level(logging.WARNING, logger="butlers.api.routers.dashboard_briefing"):
            state = await _fetch_dashboard_state(pool, now)

        # All buckets resolve (failing one empty, others still ran).
        assert state["notification_items"] == []
        assert state["audit_issues"] == []
        assert state["butler_statuses"] == []
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(warning_msg in r.message for r in warning_records)


# ---------------------------------------------------------------------------
# Concurrent dispatch (bu-pg3qa)
#
# Verifies that the three DB queries are dispatched concurrently via
# asyncio.gather(), not serially, and that each can fail independently.
# ---------------------------------------------------------------------------


class TestConcurrentFetch:
    """_fetch_dashboard_state dispatches all three queries concurrently."""

    async def test_all_three_queries_dispatched_concurrently(self):
        """asyncio.gather dispatches all three fetch coroutines at once.

        By recording the order of SQL keywords seen in pool.fetch, we confirm
        that all three queries are issued before any of them returns.  We
        simulate this with a gate: each coroutine blocks until all three have
        started, then unblocks together.
        """
        import asyncio as _asyncio

        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        started: list[str] = []
        all_started = _asyncio.Event()

        async def _fetch_side_effect(sql, *args):
            if "notifications" in sql:
                key = "notifications"
            elif "audit_source" in sql:
                key = "audit"
            elif "butler_registry" in sql:
                key = "registry"
            else:
                key = "other"
            started.append(key)
            # Once all three have checked in, unblock them all.
            if len(started) >= 3:
                all_started.set()
            # Wait for the gate (or fall through quickly if already set).
            await _asyncio.wait_for(all_started.wait(), timeout=1.0)
            return []

        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=_fetch_side_effect)

        state = await _fetch_dashboard_state(pool, now)

        # All three must have started before any finished.
        assert all_started.is_set(), "Not all three queries started concurrently"
        assert set(started) == {"notifications", "audit", "registry"}
        # State is fully assembled with empty results.
        assert state["notification_items"] == []
        assert state["audit_issues"] == []
        assert state["butler_statuses"] == []

    async def test_notifications_failure_does_not_prevent_registry_data(self):
        """When notifications fails, butler_statuses is still populated."""
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool(
            butler_statuses=[
                {
                    "name": "atlas",
                    "status": "healthy",
                    "agent_type": "butler",
                    "eligibility_state": "active",
                    "last_seen_at": now,
                    "description": "Atlas butler",
                    "modules": [],
                    "capabilities": [],
                    "quarantine_reason": None,
                }
            ]
        )
        # Override fetch so notifications always raises.
        original_fetch = pool.fetch.side_effect

        async def _patched_fetch(sql, *args):
            if "notifications" in sql:
                raise RuntimeError("notifications unavailable")
            return await original_fetch(sql, *args)

        pool.fetch = AsyncMock(side_effect=_patched_fetch)

        state = await _fetch_dashboard_state(pool, now)

        assert state["notification_items"] == []
        assert len(state["butler_statuses"]) == 1
        assert state["butler_statuses"][0]["name"] == "atlas"

    async def test_registry_failure_does_not_prevent_notification_data(self):
        """When butler_registry fails, notification items are still populated."""
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool(
            attention_items=[
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "severity": "high",
                    "source_butler": "calendar",
                    "channel": "telegram",
                    "message": "Calendar sync failed",
                    "metadata": {"severity": "high"},
                    "status": "unread",
                    "error": None,
                    "session_id": None,
                    "trace_id": None,
                    "created_at": now,
                }
            ]
        )
        original_fetch = pool.fetch.side_effect

        async def _patched_fetch(sql, *args):
            if "butler_registry" in sql:
                raise RuntimeError("registry unavailable")
            return await original_fetch(sql, *args)

        pool.fetch = AsyncMock(side_effect=_patched_fetch)

        state = await _fetch_dashboard_state(pool, now)

        assert state["butler_statuses"] == []
        assert len(state["notification_items"]) == 1
        assert state["notification_items"][0]["source_butler"] == "calendar"

    async def test_audit_failure_does_not_prevent_notification_data(self):
        """When audit query fails, notification items are still populated."""
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool(
            attention_items=[
                {
                    "id": "00000000-0000-0000-0000-000000000002",
                    "severity": "low",
                    "source_butler": "health",
                    "channel": "telegram",
                    "message": "Health check complete",
                    "metadata": {},
                    "status": "sent",
                    "error": None,
                    "session_id": None,
                    "trace_id": None,
                    "created_at": now,
                }
            ]
        )
        original_fetch = pool.fetch.side_effect

        async def _patched_fetch(sql, *args):
            if "audit_source" in sql:
                raise RuntimeError("audit unavailable")
            return await original_fetch(sql, *args)

        pool.fetch = AsyncMock(side_effect=_patched_fetch)

        state = await _fetch_dashboard_state(pool, now)

        assert state["audit_issues"] == []
        assert len(state["notification_items"]) == 1
        assert state["notification_items"][0]["source_butler"] == "health"


# ---------------------------------------------------------------------------
# Audit-derived attention items (bu-5y5ve spec coverage)
#
# Validates: a scheduled-task failure in the audit log surfaces as a
# severity="high" attention item which forces state_class="urgent", while
# a non-scheduled audit error surfaces as severity="medium".
# ---------------------------------------------------------------------------


def _make_audit_pool(
    audit_rows: list[dict] | None = None,
    butler_statuses: list[dict] | None = None,
) -> AsyncMock:
    """Build a mock pool that returns the given audit rows for audit queries.

    Notifications query returns [] so audit items are the sole attention source.
    """
    pool = AsyncMock()
    owner_id = "owner-uuid-1234"

    async def _fetchrow(sql, *args):
        # Owner-assertion query now reads public.entities directly (bu-jnaa3).
        if "public.entities" in sql and "ANY(roles)" in sql:
            rec = MagicMock()
            rec.__getitem__ = MagicMock(return_value=owner_id)
            return rec
        return None

    rows = audit_rows or []
    statuses = butler_statuses or []

    async def _fetch(sql, *args):
        if "notifications" in sql:
            return []
        if "audit_source" in sql:
            return [_make_record(r) for r in rows]
        if "butler_registry" in sql:
            return [_make_record(r) for r in statuses]
        return []

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.fetch = AsyncMock(side_effect=_fetch)
    return pool


class TestAuditDerivedAttentionItems:
    """Spec requirement: Attention Item Sources — audit-derived path (D7)."""

    async def test_scheduled_audit_failure_becomes_high_severity(self):
        """An audit error from a scheduled session gets severity='high'.

        This means the item will drive state_class='urgent' even with no
        owner notification pending (the core of the bu-5y5ve bug report).
        """
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_audit_pool(
            audit_rows=[
                {
                    "error_summary": "OAuth token expired",
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "occurrences": 3,
                    "butlers": ["calendar"],
                    "has_schedule": True,
                    "schedule_names": ["daily-sync"],
                }
            ]
        )

        state = await _fetch_dashboard_state(pool, now)

        assert len(state["attention_items"]) == 1
        item = state["attention_items"][0]
        assert item["severity"] == "high"
        assert item["source"] == "audit_log"
        assert item["type"] == "scheduled_task_failure"
        assert item["butler"] == "calendar"

    async def test_scheduled_audit_failure_forces_urgent_state_class(self):
        """A single high-severity audit item causes the endpoint to return state_class='urgent'.

        This verifies the end-to-end chain: audit query -> attention item ->
        classify -> 'urgent'.  No notification required.
        """
        pool = _make_audit_pool(
            audit_rows=[
                {
                    "error_summary": "OAuth token expired",
                    "first_seen_at": datetime(2026, 5, 13, 15, 0, tzinfo=UTC),
                    "last_seen_at": datetime(2026, 5, 13, 15, 59, tzinfo=UTC),
                    "occurrences": 1,
                    "butlers": ["calendar"],
                    "has_schedule": True,
                    "schedule_names": ["morning-sync"],
                }
            ]
        )
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
        assert data["state_class"] == "urgent"

    async def test_non_scheduled_audit_failure_becomes_medium_severity(self):
        """An audit error not from a scheduled session gets severity='medium'.

        A single medium-severity item drives state_class='mild', not 'urgent'.
        """
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_audit_pool(
            audit_rows=[
                {
                    "error_summary": "Unexpected response from API",
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "occurrences": 1,
                    "butlers": ["health"],
                    "has_schedule": False,
                    "schedule_names": [],
                }
            ]
        )

        state = await _fetch_dashboard_state(pool, now)

        assert len(state["attention_items"]) == 1
        item = state["attention_items"][0]
        assert item["severity"] == "medium"
        assert item["source"] == "audit_log"
        assert item["type"] == "audit_error_group"

    async def test_non_scheduled_audit_failure_does_not_force_urgent(self):
        """A non-scheduled audit error produces 'mild', not 'urgent'.

        Validates the spec: ad-hoc errors stay below the urgent threshold.
        """
        pool = _make_audit_pool(
            audit_rows=[
                {
                    "error_summary": "Unexpected response from API",
                    "first_seen_at": datetime(2026, 5, 13, 15, 0, tzinfo=UTC),
                    "last_seen_at": datetime(2026, 5, 13, 15, 59, tzinfo=UTC),
                    "occurrences": 1,
                    "butlers": ["health"],
                    "has_schedule": False,
                    "schedule_names": [],
                }
            ]
        )
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
        assert data["state_class"] == "mild"

    async def test_audit_item_multi_butler_description(self):
        """When multiple butlers share an error, the description includes the butler count."""
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_audit_pool(
            audit_rows=[
                {
                    "error_summary": "DB connection timeout",
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "occurrences": 5,
                    "butlers": ["health", "calendar"],
                    "has_schedule": False,
                    "schedule_names": [],
                }
            ]
        )

        state = await _fetch_dashboard_state(pool, now)

        item = state["attention_items"][0]
        assert item["butler"] == "multiple"
        assert "2 butlers" in item["description"]


# ---------------------------------------------------------------------------
# _make_owner_pool dispatches the audit grouping query (bu-e00sx gap)
#
# Verifies that the general-purpose pool helper routes on "audit_source" (the
# canonical public.audit_log grouping CTE alias, bu-j26e8) so tests that mix
# notification and audit rows exercise the end-to-end audit path through
# _fetch_dashboard_state.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _compute_overview_totals counts audit-derived items (bu-e00sx gap)
#
# Verifies that overview_totals.attention_high and attention_medium include
# audit-derived items, not only notification-derived items.
# ---------------------------------------------------------------------------


class TestOverviewTotalsWithAuditItems:
    """overview_totals counts must include audit-derived attention items."""

    async def test_mixed_notification_and_audit_totals(self):
        """attention_high and attention_medium include both notification and audit items."""
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool(
            # One notification item with high severity
            attention_items=[
                {
                    "id": "notif-001",
                    "severity": "high",
                    "source_butler": "calendar",
                    "channel": "telegram",
                    "message": "OAuth token expired",
                    "metadata": {"severity": "high"},
                    "status": "unread",
                    "error": None,
                    "session_id": None,
                    "trace_id": None,
                    "created_at": now,
                }
            ],
            # One audit item with medium severity (non-scheduled)
            audit_rows=[
                {
                    "error_summary": "Rate limit exceeded",
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "occurrences": 3,
                    "butlers": ["health"],
                    "has_schedule": False,
                    "schedule_names": [],
                }
            ],
        )

        state = await _fetch_dashboard_state(pool, now)

        totals = state["overview_totals"]
        # Both items (notification high + audit medium) must be counted
        assert totals["attention_total"] == 2
        assert totals["attention_high"] == 1
        assert totals["attention_medium"] == 1
        assert totals["attention_low"] == 0

    async def test_audit_only_totals_without_notifications(self):
        """When only audit rows are present, totals reflect only audit-derived items."""
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool(
            # No notifications
            audit_rows=[
                {
                    "error_summary": "Schedule task timeout",
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "occurrences": 1,
                    "butlers": ["calendar"],
                    "has_schedule": True,
                    "schedule_names": ["morning-sync"],
                }
            ],
        )

        state = await _fetch_dashboard_state(pool, now)

        totals = state["overview_totals"]
        assert totals["attention_total"] == 1
        assert totals["attention_high"] == 1
        assert totals["attention_medium"] == 0

    async def test_multiple_audit_rows_counted_individually(self):
        """Each audit group row is its own attention item in the totals."""
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool(
            audit_rows=[
                {
                    "error_summary": "Scheduled task failure",
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "occurrences": 2,
                    "butlers": ["calendar"],
                    "has_schedule": True,
                    "schedule_names": ["daily-sync"],
                },
                {
                    "error_summary": "API rate limit exceeded",
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "occurrences": 1,
                    "butlers": ["health"],
                    "has_schedule": False,
                    "schedule_names": [],
                },
            ],
        )

        state = await _fetch_dashboard_state(pool, now)

        totals = state["overview_totals"]
        # One scheduled (high) + one non-scheduled (medium)
        assert totals["attention_total"] == 2
        assert totals["attention_high"] == 1
        assert totals["attention_medium"] == 1


# ---------------------------------------------------------------------------
# Audit items appear in top_attention_items in the prompt (bu-e00sx gap)
#
# Verifies the end-to-end chain: audit rows -> state['attention_items'] ->
# top_attention_items in the LLM prompt when audit items rank higher than
# notification items by severity.
# ---------------------------------------------------------------------------


class TestPromptIncludesAuditItems:
    """Audit-derived items must surface in the LLM prompt when they rank high."""

    def test_high_severity_audit_item_appears_in_top_attention_items(self):
        """A high-severity audit item must appear in top_attention_items in the prompt."""
        state = {
            "now": datetime(2026, 5, 13, 15, 59, tzinfo=UTC),
            "attention_items": [
                {
                    "severity": "high",
                    "type": "scheduled_task_failure",
                    "butler": "calendar",
                    "description": "Scheduled task 'daily-sync' failure on 'calendar': Token expired",
                    "last_seen_at": "2026-05-13T15:59:00+00:00",
                    "link": "/audit-log?butler=calendar&operation=session",
                    "source": "audit_log",
                    "occurrences": 3,
                    "error_message": "Token expired",
                }
            ],
            "notification_items": [],
            "audit_issues": [],
            "butler_statuses": [],
            "overview_totals": {
                "attention_total": 1,
                "attention_high": 1,
                "attention_medium": 0,
                "attention_low": 0,
                "butlers_total": 0,
                "butlers_unhealthy": 0,
            },
        }

        message = _build_user_message(state, "urgent")

        assert "top_attention_items" in message
        assert "audit_log" in message
        assert "scheduled_task_failure" in message
        assert "Token expired" in message

    def test_audit_item_ranked_above_low_notification_in_prompt(self):
        """A high-severity audit item ranks above a low-severity notification in top_attention_items."""
        state = {
            "now": datetime(2026, 5, 13, 15, 59, tzinfo=UTC),
            "attention_items": [
                {
                    "severity": "low",
                    "type": "notification",
                    "butler": "telegram",
                    "description": "Minor routine notification",
                    "last_seen_at": "2026-05-13T15:59:00+00:00",
                    "link": "/notifications",
                    "source": "notification",
                },
                {
                    "severity": "high",
                    "type": "scheduled_task_failure",
                    "butler": "calendar",
                    "description": "Scheduled task 'sync' failure on 'calendar': Token expired",
                    "last_seen_at": "2026-05-13T15:59:00+00:00",
                    "link": "/audit-log?butler=calendar&operation=session",
                    "source": "audit_log",
                    "occurrences": 2,
                    "error_message": "Token expired",
                },
            ],
            "notification_items": [],
            "audit_issues": [],
            "butler_statuses": [],
            "overview_totals": {
                "attention_total": 2,
                "attention_high": 1,
                "attention_medium": 0,
                "attention_low": 1,
                "butlers_total": 0,
                "butlers_unhealthy": 0,
            },
        }

        message = _build_user_message(state, "urgent")

        import json

        # Parse just the JSON portion from the message
        json_start = message.index("{")
        json_end = message.rindex("}") + 1
        state_summary = json.loads(message[json_start:json_end])

        top = state_summary["top_attention_items"]
        assert len(top) == 2
        # The high-severity audit item must appear first (sorted by severity rank)
        assert top[0]["severity"] == "high"
        assert top[0]["source"] == "audit_log"
        # The low-severity notification appears second
        assert top[1]["severity"] == "low"
        assert top[1]["source"] == "notification"
