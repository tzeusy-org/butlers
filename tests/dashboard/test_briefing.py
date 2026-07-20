"""Tests for the dashboard briefing endpoint and supporting modules.

Coverage:
    - classify: all six branches (urgent / busy / mild / degraded-quiet /
      degraded / quiet), incl. priority ordering against degraded_sources
    - headline_for: singular and plural variants for each class
    - LLM happy path returns source: "llm"
    - LLM timeout, error, and empty response each return source: "fallback"
    - Voice lint rejects responses containing each banned token
    - Voice lint does not reject "factually" for the "actually" word-boundary case
    - Cache TTL: hit preserves generated_at, miss regenerates
    - HTTP 403 path for non-owner access
    - HTTP 401 path for unauthenticated (API-key middleware)
    - Classification exception falls through to the degraded paragraph
    - State sources (bu-gcz9e.1): board liveness, approvals, failed
      notifications, and QA state feed the SAME composed attention model the
      Overview dashboard page renders; a source fetch failure surfaces via
      degraded_sources and can never compose "quiet"
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.briefing.cache import BriefingCache
from butlers.api.briefing.classify import classify, headline_for, time_of_day
from butlers.api.briefing.fallback import elaborate_fallback
from butlers.api.briefing.lint import voice_lint_passes
from butlers.api.briefing.prompts import _build_user_message, elaborate_llm
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_butler_configs, get_mcp_manager, get_pricing
from butlers.api.routers.dashboard_briefing import (
    _fetch_approvals_state,
    _fetch_audit_issues,
    _fetch_board_state,
    _fetch_dashboard_state,
    _fetch_notifications_state,
    _fetch_qa_state,
    _get_db_manager,
    _is_missing_relation_error,
    _map_board_rows,
    _owner_local_now,
    _qa_attention_item,
    get_cache,
    get_dashboard_briefing,
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


def _board_row(
    name: str = "calendar",
    *,
    type: str = "butler",  # noqa: A002 -- mirrors BoardRow's field name
    activity: str = "idle",
    eligibility: str = "active",
    last_heartbeat_at: str | None = "2026-05-13T15:59:00+00:00",
    quarantine_reason: str | None = None,
) -> SimpleNamespace:
    """A minimal stand-in for a BoardRow -- only the fields _map_board_rows reads."""
    return SimpleNamespace(
        name=name,
        type=type,
        activity=activity,
        eligibility=eligibility,
        last_heartbeat_at=last_heartbeat_at,
        quarantine_reason=quarantine_reason,
    )


def _board_response(
    rows: list[SimpleNamespace], *, registry_source_error: bool = False
) -> SimpleNamespace:
    """A minimal stand-in for ApiResponse[BoardResponse]."""
    return SimpleNamespace(
        data=SimpleNamespace(
            rows=rows,
            aggregates=SimpleNamespace(registry_source_error=registry_source_error),
        )
    )


def _make_owner_pool(
    has_owner: bool = True,
    owner_fails: bool = False,
    audit_rows: list[dict] | None = None,
) -> AsyncMock:
    """Build a mock switchboard pool for the briefing endpoint.

    Routes pool.fetch calls by SQL keyword:
        - "audit_source"    -> audit_rows (grouped audit error rows; the canonical
                               public.audit_log grouping CTE alias, bu-j26e8)
    Notification-stats queries are answered generically (count/sum queries
    return 0 via ``fetchval``'s AsyncMock default of ``None``, and ``fetch``
    returns ``[]`` for anything else) so the switchboard pool used for
    ``notification_stats`` never errors out by default.
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

    audits = audit_rows or []

    async def _fetch(sql, *args):
        if "audit_source" in sql:
            return [_make_record(r) for r in audits]
        return []

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.fetch = AsyncMock(side_effect=_fetch)
    pool.fetchval = AsyncMock(return_value=0)
    return pool


def _make_app(pool: AsyncMock, cache: BriefingCache | None = None) -> object:
    """Build a FastAPI test app with the briefing DB and cache overridden.

    Also overrides the butler-roster/MCP/pricing dependencies the board fetch
    needs (cheap, real-ish stand-ins -- no network I/O happens until
    ``get_butlers_board`` is actually called, and that call is patched to an
    empty board by default via ``_default_board_patch`` in each test that
    does not care about board specifics).
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    mock_db.credential_shared_pool.return_value = pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_butler_configs] = lambda: []
    app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
    app.dependency_overrides[get_pricing] = lambda: MagicMock()
    if cache is not None:
        app.dependency_overrides[get_cache] = lambda: cache
    return app


def _empty_board_patch():
    """Patch get_butlers_board to return an empty, fully-healthy board."""
    return patch(
        "butlers.api.routers.butlers.get_butlers_board",
        new=AsyncMock(return_value=_board_response([])),
    )


# ---------------------------------------------------------------------------
# classify: all six branches
# ---------------------------------------------------------------------------


class TestClassify:
    @pytest.mark.parametrize(
        "attention_items, butler_statuses, degraded_sources, expected",
        [
            # urgent: any high-severity item
            ([{"severity": "high"}], [], [], "urgent"),
            (
                [{"severity": "high"}, {"severity": "high"}, {"severity": "medium"}],
                [],
                [],
                "urgent",
            ),
            # urgent wins over busy: high severity even when total >= 3
            (
                [{"severity": "high"}, {"severity": "medium"}, {"severity": "low"}],
                [],
                [],
                "urgent",
            ),
            # urgent wins over degraded_sources too
            ([{"severity": "high"}], [], ["board"], "urgent"),
            # busy: 3+ items, none high
            ([{"severity": "medium"}, {"severity": "low"}, {"severity": "medium"}], [], [], "busy"),
            # mild: 1-2 items, none high
            ([{"severity": "medium"}], [], [], "mild"),
            ([{"severity": "low"}, {"severity": "medium"}], [], [], "mild"),
            # degraded-quiet: no items but a degraded/error butler
            ([], [{"name": "health", "status": "degraded"}], [], "degraded-quiet"),
            ([], [{"name": "atlas", "status": "error"}], [], "degraded-quiet"),
            # degraded-quiet wins over degraded when both are true
            ([], [{"name": "atlas", "status": "error"}], ["qa"], "degraded-quiet"),
            # degraded: no items, no known-degraded butler, but a source failed
            ([], [], ["board"], "degraded"),
            ([], [{"name": "atlas", "status": "healthy"}], ["notifications", "qa"], "degraded"),
            # quiet: no items, all butlers healthy (or none), every source answered
            (
                [],
                [
                    {"name": "health", "status": "healthy"},
                    {"name": "atlas", "status": "healthy"},
                ],
                [],
                "quiet",
            ),
            ([], [], [], "quiet"),
        ],
    )
    def test_classify_state_machine(
        self, attention_items, butler_statuses, degraded_sources, expected
    ):
        """classify() six-branch state machine incl. priority ordering."""
        state = {
            "attention_items": attention_items,
            "butler_statuses": butler_statuses,
            "degraded_sources": degraded_sources,
        }
        assert classify(state) == expected

    def test_missing_degraded_sources_key_defaults_to_quiet(self):
        """A state dict without a degraded_sources key behaves like an empty list."""
        assert classify({"attention_items": [], "butler_statuses": []}) == "quiet"


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
            ("degraded", 1, "One source could not be reached, so this may be incomplete."),
            ("degraded", 2, "2 sources could not be reached, so this may be incomplete."),
        ],
    )
    def test_singular_plural_pluralization(self, state_class, count, expected):
        """Headlines pluralize correctly across urgent/mild/degraded-quiet/degraded."""
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

_ALL_STATE_CLASSES = ["urgent", "busy", "mild", "degraded-quiet", "degraded", "quiet"]


class TestElaborateFallback:
    @pytest.mark.parametrize("state_class", _ALL_STATE_CLASSES)
    def test_returns_string_for_all_classes(self, state_class):
        result = elaborate_fallback({}, state_class)
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.parametrize("state_class", _ALL_STATE_CLASSES)
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
# _map_board_rows: board rows -> (attention_items, butler_statuses)
# ---------------------------------------------------------------------------


class TestMapBoardRows:
    """_map_board_rows is the core of the board-verdict reuse (bu-gcz9e.1)."""

    @pytest.mark.parametrize(
        "activity, expected_severity, expected_status",
        [
            ("offline", "high", "down"),
            ("quarantined", "high", "quarantined"),
            ("overdue", "medium", "stale"),
            ("unknown", "medium", "degraded"),
        ],
    )
    def test_needs_attention_activities_produce_items(
        self, activity, expected_severity, expected_status
    ):
        rows = [_board_row("calendar", activity=activity)]
        attention_items, butler_statuses = _map_board_rows(rows, registry_source_error=False)

        assert len(attention_items) == 1
        item = attention_items[0]
        assert item["severity"] == expected_severity
        assert item["butler"] == "calendar"
        assert item["source"] == "board"

        assert butler_statuses == [
            {
                "name": "calendar",
                "status": expected_status,
                "type": "butler",
                "eligibility_state": "active",
                "last_seen_at": "2026-05-13T15:59:00+00:00",
                "quarantine_reason": None,
            }
        ]

    @pytest.mark.parametrize("activity", ["running", "idle"])
    def test_healthy_activities_produce_no_attention_item(self, activity):
        rows = [_board_row("calendar", activity=activity)]
        attention_items, butler_statuses = _map_board_rows(rows, registry_source_error=False)

        assert attention_items == []
        assert butler_statuses[0]["status"] == "healthy"

    def test_staffer_rows_are_excluded(self):
        """Only type == 'butler' rows are considered (mirrors deriveOverviewTriageModel)."""
        rows = [_board_row("qa", type="staffer", activity="offline")]
        attention_items, butler_statuses = _map_board_rows(rows, registry_source_error=False)

        assert attention_items == []
        assert butler_statuses == []

    def test_systemic_registry_failure_suppresses_fabricated_items(self):
        """A total registry outage must not fabricate one attention item per butler.

        Every row degrades to activity 'unknown' uniformly when the board's
        registry query failed -- that is one systemic outage (surfaced via
        the caller's "board" degraded source), not N independent butler
        problems, so no attention item is fabricated and the row reads as
        neutral ("healthy"), not "degraded".
        """
        rows = [
            _board_row("calendar", activity="unknown"),
            _board_row("health", activity="unknown"),
        ]
        attention_items, butler_statuses = _map_board_rows(rows, registry_source_error=True)

        assert attention_items == []
        assert all(status["status"] == "healthy" for status in butler_statuses)

    def test_per_butler_unknown_without_registry_failure_still_flags(self):
        """A single butler's own schema being unreachable IS a real, isolated signal."""
        rows = [_board_row("calendar", activity="unknown")]
        attention_items, _ = _map_board_rows(rows, registry_source_error=False)

        assert len(attention_items) == 1
        assert attention_items[0]["butler"] == "calendar"


# ---------------------------------------------------------------------------
# _fetch_board_state: wraps GET /api/butlers/board
# ---------------------------------------------------------------------------


class TestFetchBoardState:
    async def test_reuses_the_canonical_board_computation(self):
        rows = [_board_row("calendar", activity="offline")]
        board_fn = AsyncMock(return_value=_board_response(rows, registry_source_error=False))

        with patch("butlers.api.routers.butlers.get_butlers_board", new=board_fn):
            attention_items, butler_statuses, degraded = await _fetch_board_state(
                configs=[], mgr=MagicMock(), db=MagicMock(), pricing=MagicMock()
            )

        assert board_fn.await_count == 1
        assert len(attention_items) == 1
        assert attention_items[0]["butler"] == "calendar"
        assert butler_statuses[0]["status"] == "down"
        assert degraded is False

    async def test_registry_source_error_is_surfaced_as_degraded(self):
        board_fn = AsyncMock(
            return_value=_board_response(
                [_board_row("calendar", activity="unknown")], registry_source_error=True
            )
        )

        with patch("butlers.api.routers.butlers.get_butlers_board", new=board_fn):
            attention_items, _, degraded = await _fetch_board_state(
                configs=[], mgr=MagicMock(), db=MagicMock(), pricing=MagicMock()
            )

        assert degraded is True
        assert attention_items == []

    async def test_board_fetch_exception_is_caught_and_degraded(self):
        board_fn = AsyncMock(side_effect=RuntimeError("board outage"))

        with patch("butlers.api.routers.butlers.get_butlers_board", new=board_fn):
            attention_items, butler_statuses, degraded = await _fetch_board_state(
                configs=[], mgr=MagicMock(), db=MagicMock(), pricing=MagicMock()
            )

        assert attention_items == []
        assert butler_statuses == []
        assert degraded is True


# ---------------------------------------------------------------------------
# _fetch_approvals_state
# ---------------------------------------------------------------------------


class TestFetchApprovalsState:
    async def test_sums_pending_across_pools(self):
        pool_a = AsyncMock()
        pool_a.fetchval = AsyncMock(return_value=2)
        pool_b = AsyncMock()
        pool_b.fetchval = AsyncMock(return_value=3)

        with patch(
            "butlers.api.routers.approvals._find_all_approvals_pools",
            new=AsyncMock(return_value=[pool_a, pool_b]),
        ):
            pending, degraded = await _fetch_approvals_state(MagicMock())

        assert pending == 5
        assert degraded is False

    async def test_pool_resolution_failure_is_degraded(self):
        with patch(
            "butlers.api.routers.approvals._find_all_approvals_pools",
            new=AsyncMock(side_effect=RuntimeError("catalog query failed")),
        ):
            pending, degraded = await _fetch_approvals_state(MagicMock())

        assert pending == 0
        assert degraded is True

    async def test_one_pool_failing_is_degraded_but_others_still_count(self):
        good_pool = AsyncMock()
        good_pool.fetchval = AsyncMock(return_value=4)
        bad_pool = AsyncMock()
        bad_pool.fetchval = AsyncMock(side_effect=RuntimeError("connection dropped"))

        with patch(
            "butlers.api.routers.approvals._find_all_approvals_pools",
            new=AsyncMock(return_value=[good_pool, bad_pool]),
        ):
            pending, degraded = await _fetch_approvals_state(MagicMock())

        assert pending == 4
        assert degraded is True


# ---------------------------------------------------------------------------
# _fetch_notifications_state
# ---------------------------------------------------------------------------


class TestFetchNotificationsState:
    async def test_excludes_all_time_failures_outside_the_exact_24_hour_window(self):
        """The briefing's cutoff is a precise request boundary, not a hint.

        The stub deliberately returns the old all-time total if the caller
        forgets ``since``.  That makes this a regression for the exact failure
        mode that kept historical delivery pressure in the briefing.
        """
        now = datetime(2026, 7, 19, 0, 37, tzinfo=UTC)

        async def _notification_stats(*, since, until, db):
            failed = 0 if (since, until) == (now - timedelta(hours=24), now) else 87
            return SimpleNamespace(data=SimpleNamespace(failed=failed, source_available=True))

        with patch(
            "butlers.api.routers.notifications.notification_stats",
            new=AsyncMock(side_effect=_notification_stats),
        ) as notification_stats:
            failed, degraded = await _fetch_notifications_state(MagicMock(), now=now)

        notification_stats.assert_awaited_once()
        since = notification_stats.await_args.kwargs["since"]
        assert since == now - timedelta(hours=24)
        assert notification_stats.await_args.kwargs["until"] == now
        assert failed == 0
        assert degraded is False

    async def test_source_unavailable_is_degraded(self):
        response = SimpleNamespace(data=SimpleNamespace(failed=0, source_available=False))

        with patch(
            "butlers.api.routers.notifications.notification_stats",
            new=AsyncMock(return_value=response),
        ):
            failed, degraded = await _fetch_notifications_state(MagicMock())

        assert failed == 0
        assert degraded is True

    async def test_exception_is_degraded(self):
        with patch(
            "butlers.api.routers.notifications.notification_stats",
            new=AsyncMock(side_effect=RuntimeError("db outage")),
        ):
            failed, degraded = await _fetch_notifications_state(MagicMock())

        assert failed == 0
        assert degraded is True


# ---------------------------------------------------------------------------
# _is_missing_relation_error
# ---------------------------------------------------------------------------


class TestIsMissingRelationError:
    def test_undefined_table_error_class_name(self):
        class UndefinedTableError(Exception):
            pass

        assert _is_missing_relation_error(UndefinedTableError("boom"), "qa_patrols") is True

    def test_message_based_detection(self):
        exc = RuntimeError('relation "qa_patrols" does not exist')
        assert _is_missing_relation_error(exc, "qa_patrols") is True

    def test_unrelated_error_is_not_missing_relation(self):
        assert _is_missing_relation_error(RuntimeError("connection reset"), "qa_patrols") is False


# ---------------------------------------------------------------------------
# _fetch_qa_state
# ---------------------------------------------------------------------------


class TestFetchQaState:
    async def test_no_shared_pool_is_legitimately_absent(self):
        db = MagicMock()
        db.credential_shared_pool.side_effect = KeyError("no shared pool")

        qa_state, degraded = await _fetch_qa_state(db)

        assert qa_state is None
        assert degraded is False

    async def test_missing_table_is_legitimately_absent(self):
        db = MagicMock()
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=RuntimeError('relation "qa_patrols" does not exist'))
        db.credential_shared_pool.return_value = pool

        qa_state, degraded = await _fetch_qa_state(db)

        assert qa_state is None
        assert degraded is False

    async def test_generic_failure_is_degraded(self):
        db = MagicMock()
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=RuntimeError("connection dropped"))
        db.credential_shared_pool.return_value = pool

        qa_state, degraded = await _fetch_qa_state(db)

        assert qa_state is None
        assert degraded is True

    async def test_error_patrol_without_detail_reads_as_failure_and_active_case_count(self):
        db = MagicMock()
        pool = AsyncMock()

        pool.fetchrow = AsyncMock(
            return_value=_make_record({"status": "error", "error_detail": None})
        )
        pool.fetch = AsyncMock(return_value=[])
        pool.fetchval = AsyncMock(return_value=2)
        db.credential_shared_pool.return_value = pool

        qa_state, degraded = await _fetch_qa_state(db)

        assert degraded is False
        assert qa_state == {
            "circuit_breaker_tripped": False,
            "circuit_breaker_consecutive_failures": 0,
            "last_patrol_failed": True,
            "active_cases_now": 2,
        }
        assert (
            "started_at >= NOW() - INTERVAL '24 hours'" in pool.fetchrow.await_args_list[0].args[0]
        )
        assert "started_at <= NOW()" in pool.fetchrow.await_args_list[0].args[0]

    @pytest.mark.parametrize(
        "status",
        ["running", "clean", "findings_dispatched", "suppressed", "skipped_overlap", "failed"],
    )
    async def test_non_error_patrol_never_infers_failure_from_error_detail(self, status: str):
        db = MagicMock()
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            return_value=_make_record({"status": status, "error_detail": "legacy detail"})
        )
        pool.fetch = AsyncMock(return_value=[])
        pool.fetchval = AsyncMock(return_value=0)
        db.credential_shared_pool.return_value = pool

        qa_state, degraded = await _fetch_qa_state(db)

        assert degraded is False
        assert qa_state is not None
        assert qa_state["last_patrol_failed"] is False

    async def test_circuit_breaker_tripped_feeds_qa_state(self):
        """bu-y2xqi: a tripped breaker with no failed patrol / novel signal
        must still surface -- this is the exact drift the bead pins."""
        db = MagicMock()
        pool = AsyncMock()

        pool.fetchrow = AsyncMock(
            return_value=_make_record({"status": "clean", "error_detail": None})
        )
        pool.fetch = AsyncMock(return_value=[_make_record({"status": "failed"}) for _ in range(5)])
        pool.fetchval = AsyncMock(return_value=0)
        db.credential_shared_pool.return_value = pool

        qa_state, degraded = await _fetch_qa_state(db)

        assert degraded is False
        assert qa_state == {
            "circuit_breaker_tripped": True,
            "circuit_breaker_consecutive_failures": 5,
            "last_patrol_failed": False,
            "active_cases_now": 0,
        }

    async def test_circuit_breaker_untripped_feeds_qa_state(self):
        db = MagicMock()
        pool = AsyncMock()

        pool.fetchrow = AsyncMock(
            return_value=_make_record({"status": "clean", "error_detail": None})
        )
        pool.fetch = AsyncMock(return_value=[_make_record({"status": "failed"}) for _ in range(2)])
        pool.fetchval = AsyncMock(return_value=0)
        db.credential_shared_pool.return_value = pool

        qa_state, degraded = await _fetch_qa_state(db)

        assert degraded is False
        assert qa_state["circuit_breaker_tripped"] is False
        assert qa_state["circuit_breaker_consecutive_failures"] == 2

    async def test_circuit_breaker_query_failure_degrades_without_discarding_patrol_signal(self):
        """A breaker-query failure (distinct table from qa_patrols) must not
        blank out the patrol-derived signal already successfully fetched."""
        db = MagicMock()
        pool = AsyncMock()

        pool.fetchrow = AsyncMock(
            return_value=_make_record({"status": "error", "error_detail": None})
        )
        pool.fetch = AsyncMock(side_effect=RuntimeError("connection dropped"))
        pool.fetchval = AsyncMock(return_value=0)
        db.credential_shared_pool.return_value = pool

        qa_state, degraded = await _fetch_qa_state(db)

        assert degraded is True
        assert qa_state == {
            "circuit_breaker_tripped": False,
            "circuit_breaker_consecutive_failures": 0,
            "last_patrol_failed": True,
            "active_cases_now": 0,
        }

    async def test_circuit_breaker_missing_table_is_legitimately_absent(self):
        """healing_attempts un-provisioned is legitimately absent, not degraded."""
        db = MagicMock()
        pool = AsyncMock()

        pool.fetchrow = AsyncMock(
            return_value=_make_record({"status": "clean", "error_detail": None})
        )
        pool.fetch = AsyncMock(
            side_effect=RuntimeError('relation "healing_attempts" does not exist')
        )
        pool.fetchval = AsyncMock(return_value=0)
        db.credential_shared_pool.return_value = pool

        qa_state, degraded = await _fetch_qa_state(db)

        assert degraded is False
        assert qa_state["circuit_breaker_tripped"] is False

    async def test_active_case_query_failure_keeps_a_successful_patrol_signal(self):
        """A partial QA source outage must not turn an error patrol into a quiet state."""
        db = MagicMock()
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            return_value=_make_record({"status": "error", "error_detail": None})
        )
        pool.fetchval = AsyncMock(side_effect=RuntimeError("healing attempts unavailable"))
        pool.fetch = AsyncMock(return_value=[])
        db.credential_shared_pool.return_value = pool

        qa_state, degraded = await _fetch_qa_state(db)

        assert degraded is True
        assert qa_state == {
            "circuit_breaker_tripped": False,
            "circuit_breaker_consecutive_failures": 0,
            "last_patrol_failed": True,
            "active_cases_now": 0,
        }
        # The breaker query remains independently attempted after the
        # active-case query fails, rather than returning before it can add a
        # stronger signal.
        pool.fetch.assert_awaited_once()


# ---------------------------------------------------------------------------
# _qa_attention_item: mirrors model.ts::summarizeQaState's priority order
# ---------------------------------------------------------------------------


class TestQaAttentionItem:
    def test_none_state_returns_none(self):
        assert _qa_attention_item(None) is None

    def test_all_quiet_returns_none(self):
        state = {
            "last_patrol_failed": False,
            "novel_findings": 0,
            "dispatched_investigations": 0,
            "active_cases_now": 0,
        }
        assert _qa_attention_item(state) is None

    def test_failed_patrol_wins_over_everything(self):
        state = {
            "last_patrol_failed": True,
            "novel_findings": 5,
            "dispatched_investigations": 5,
        }
        item = _qa_attention_item(state)
        assert item["severity"] == "high"
        assert item["description"] == "QA patrol failed"

    def test_completed_dispatches_are_activity_not_attention(self):
        state = {
            "last_patrol_failed": False,
            "novel_findings": 3,
            "dispatched_investigations": 2,
            "active_cases_now": 0,
        }
        assert _qa_attention_item(state) is None

    def test_novel_findings_without_active_case_are_not_attention(self):
        state = {
            "last_patrol_failed": False,
            "novel_findings": 1,
            "dispatched_investigations": 0,
            "active_cases_now": 0,
        }
        assert _qa_attention_item(state) is None

    def test_active_cases_surface_as_attention(self):
        state = {
            "last_patrol_failed": False,
            "novel_findings": 0,
            "dispatched_investigations": 0,
            "active_cases_now": 2,
        }
        item = _qa_attention_item(state)
        assert item["severity"] == "medium"
        assert item["description"] == "2 active QA investigations"
        assert item["occurrences"] == 2

    def test_circuit_breaker_tripped_wins_over_everything(self):
        """bu-y2xqi: mirrors model.ts::summarizeQaState checking
        circuit_breaker.tripped BEFORE last_patrol_failed."""
        state = {
            "circuit_breaker_tripped": True,
            "circuit_breaker_consecutive_failures": 5,
            "last_patrol_failed": True,
            "novel_findings": 5,
            "dispatched_investigations": 5,
            "active_cases_now": 2,
        }
        item = _qa_attention_item(state)
        assert item["severity"] == "high"
        assert "circuit breaker tripped" in item["description"]
        assert "5 consecutive failures" in item["description"]
        assert item["occurrences"] == 5

    def test_circuit_breaker_tripped_singular_failure_count(self):
        state = {
            "circuit_breaker_tripped": True,
            "circuit_breaker_consecutive_failures": 1,
            "last_patrol_failed": False,
            "novel_findings": 0,
            "dispatched_investigations": 0,
            "active_cases_now": 0,
        }
        item = _qa_attention_item(state)
        assert "1 consecutive failure)" in item["description"]

    def test_circuit_breaker_untripped_falls_through_to_failed_patrol(self):
        state = {
            "circuit_breaker_tripped": False,
            "circuit_breaker_consecutive_failures": 0,
            "last_patrol_failed": True,
            "novel_findings": 0,
            "dispatched_investigations": 0,
            "active_cases_now": 0,
        }
        item = _qa_attention_item(state)
        assert item["description"] == "QA patrol failed"

    def test_missing_circuit_breaker_key_is_treated_as_untripped(self):
        """Backward-compat: a qa_state dict without the new keys (e.g. an
        older cached shape) must not raise and must fall through normally."""
        state = {
            "last_patrol_failed": False,
            "novel_findings": 0,
            "dispatched_investigations": 0,
            "active_cases_now": 0,
        }
        assert _qa_attention_item(state) is None


# ---------------------------------------------------------------------------
# _fetch_audit_issues (unchanged shared CTE, now returns a degraded flag)
# ---------------------------------------------------------------------------


class TestFetchAuditIssues:
    async def test_success_path(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[
                _make_record(
                    {
                        "error_summary": "OAuth token expired",
                        "first_seen_at": datetime(2026, 5, 13, 15, 59, tzinfo=UTC),
                        "last_seen_at": datetime(2026, 5, 13, 15, 59, tzinfo=UTC),
                        "occurrences": 3,
                        "butlers": ["calendar"],
                        "has_schedule": True,
                        "schedule_names": ["daily-sync"],
                    }
                )
            ]
        )

        audit_issues, attention_items, degraded = await _fetch_audit_issues(pool)

        assert degraded is False
        assert len(audit_issues) == 1
        assert len(attention_items) == 1
        assert attention_items[0]["severity"] == "high"

    async def test_excludes_a_group_last_seen_more_than_twelve_hours_ago(self):
        fresh = _make_record(
            {
                "error_summary": "Fresh runtime error",
                "first_seen_at": datetime(2026, 5, 13, 12, 0, tzinfo=UTC),
                "last_seen_at": datetime(2026, 5, 13, 15, 0, tzinfo=UTC),
                "occurrences": 1,
                "butlers": ["general"],
                "has_schedule": False,
                "schedule_names": [],
            }
        )
        historical = _make_record(
            {
                "error_summary": "Historical model error",
                "first_seen_at": datetime(2026, 5, 12, 12, 0, tzinfo=UTC),
                "last_seen_at": datetime(2026, 5, 12, 23, 0, tzinfo=UTC),
                "occurrences": 18,
                "butlers": ["chronicler"],
                "has_schedule": False,
                "schedule_names": [],
            }
        )
        future = _make_record(
            {
                "error_summary": "Future clock-skewed error",
                "first_seen_at": datetime(2026, 5, 14, 16, 0, tzinfo=UTC),
                "last_seen_at": datetime(2026, 5, 14, 16, 0, tzinfo=UTC),
                "occurrences": 1,
                "butlers": ["general"],
                "has_schedule": False,
                "schedule_names": [],
            }
        )

        async def fetch(query: str):
            return (
                [fresh]
                if "INTERVAL '12 hours'" in query and "created_at <= NOW()" in query
                else [fresh, historical, future]
            )

        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=fetch)

        audit_issues, attention_items, degraded = await _fetch_audit_issues(pool)

        assert degraded is False
        assert [item["description"] for item in audit_issues] == ["Fresh runtime error (general)"]
        assert [item["description"] for item in attention_items] == [
            "Fresh runtime error (general)"
        ]

    async def test_failure_is_degraded(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=RuntimeError("db outage"))

        audit_issues, attention_items, degraded = await _fetch_audit_issues(pool)

        assert audit_issues == []
        assert attention_items == []
        assert degraded is True


# ---------------------------------------------------------------------------
# _fetch_dashboard_state: end-to-end composition of the five sources
# ---------------------------------------------------------------------------


def _patch_all_sources(
    *,
    board=([], [], False),
    approvals=(0, False),
    notifications=(0, False),
    qa=(None, False),
):
    """Patch all four new-source fetchers at once; audit still hits the pool."""
    return (
        patch(
            "butlers.api.routers.dashboard_briefing._fetch_board_state",
            new=AsyncMock(return_value=board),
        ),
        patch(
            "butlers.api.routers.dashboard_briefing._fetch_approvals_state",
            new=AsyncMock(return_value=approvals),
        ),
        patch(
            "butlers.api.routers.dashboard_briefing._fetch_notifications_state",
            new=AsyncMock(return_value=notifications),
        ),
        patch(
            "butlers.api.routers.dashboard_briefing._fetch_qa_state",
            new=AsyncMock(return_value=qa),
        ),
    )


class TestFetchDashboardState:
    async def test_all_sources_quiet_produces_empty_state(self):
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool()

        patches = _patch_all_sources()
        with patches[0], patches[1], patches[2], patches[3]:
            state = await _fetch_dashboard_state(
                pool, now, db=MagicMock(), configs=[], mgr=MagicMock(), pricing=MagicMock()
            )

        assert state["attention_items"] == []
        assert state["butler_statuses"] == []
        assert state["degraded_sources"] == []
        assert state["overview_totals"]["attention_total"] == 0

    async def test_failed_notifications_produce_one_medium_item(self):
        """Only FAILED notifications count -- replaces the old every-SENT-counts feed."""
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool()

        patches = _patch_all_sources(notifications=(3, False))
        with patches[0], patches[1], patches[2], patches[3]:
            state = await _fetch_dashboard_state(
                pool, now, db=MagicMock(), configs=[], mgr=MagicMock(), pricing=MagicMock()
            )

        assert len(state["attention_items"]) == 1
        item = state["attention_items"][0]
        assert item["severity"] == "medium"
        assert item["type"] == "notification"
        assert "3 failed notifications" in item["description"]
        window = parse_qs(urlparse(item["link"]).query)
        assert window == {
            "status": ["failed"],
            "since": [(now - timedelta(hours=24)).isoformat()],
            "until": [now.isoformat()],
        }

    async def test_approvals_pending_produces_one_medium_item(self):
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool()

        patches = _patch_all_sources(approvals=(2, False))
        with patches[0], patches[1], patches[2], patches[3]:
            state = await _fetch_dashboard_state(
                pool, now, db=MagicMock(), configs=[], mgr=MagicMock(), pricing=MagicMock()
            )

        assert len(state["attention_items"]) == 1
        assert state["attention_items"][0]["type"] == "approval"

    async def test_qa_state_feeds_an_attention_item(self):
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool()
        qa_state = {
            "last_patrol_failed": True,
            "novel_findings": 0,
            "dispatched_investigations": 0,
        }

        patches = _patch_all_sources(qa=(qa_state, False))
        with patches[0], patches[1], patches[2], patches[3]:
            state = await _fetch_dashboard_state(
                pool, now, db=MagicMock(), configs=[], mgr=MagicMock(), pricing=MagicMock()
            )

        assert len(state["attention_items"]) == 1
        assert state["attention_items"][0]["type"] == "qa"
        assert state["attention_items"][0]["severity"] == "high"

    async def test_board_attention_and_statuses_are_threaded_through(self):
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool()
        board_attention = [{"severity": "high", "type": "runtime", "butler": "calendar"}]
        board_statuses = [{"name": "calendar", "status": "down"}]

        patches = _patch_all_sources(board=(board_attention, board_statuses, False))
        with patches[0], patches[1], patches[2], patches[3]:
            state = await _fetch_dashboard_state(
                pool, now, db=MagicMock(), configs=[], mgr=MagicMock(), pricing=MagicMock()
            )

        assert state["attention_items"] == board_attention
        assert state["butler_statuses"] == board_statuses

    @pytest.mark.parametrize(
        "kwargs, expected_source",
        [
            ({"board": ([], [], True)}, "board"),
            ({"approvals": (0, True)}, "approvals"),
            ({"notifications": (0, True)}, "notifications"),
            ({"qa": (None, True)}, "qa"),
        ],
    )
    async def test_each_source_failure_is_named_in_degraded_sources(self, kwargs, expected_source):
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool()

        patches = _patch_all_sources(**kwargs)
        with patches[0], patches[1], patches[2], patches[3]:
            state = await _fetch_dashboard_state(
                pool, now, db=MagicMock(), configs=[], mgr=MagicMock(), pricing=MagicMock()
            )

        assert expected_source in state["degraded_sources"]

    async def test_audit_failure_is_named_in_degraded_sources(self):
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=RuntimeError("audit outage"))
        pool.fetchrow = AsyncMock(return_value=None)

        patches = _patch_all_sources()
        with patches[0], patches[1], patches[2], patches[3]:
            state = await _fetch_dashboard_state(
                pool, now, db=MagicMock(), configs=[], mgr=MagicMock(), pricing=MagicMock()
            )

        assert "audit" in state["degraded_sources"]

    async def test_a_failed_source_with_no_other_signal_classifies_as_degraded_not_quiet(self):
        """The core acceptance criterion: 'All quiet.' must be unreachable
        when any source failed."""
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool()

        patches = _patch_all_sources(board=([], [], True))
        with patches[0], patches[1], patches[2], patches[3]:
            state = await _fetch_dashboard_state(
                pool, now, db=MagicMock(), configs=[], mgr=MagicMock(), pricing=MagicMock()
            )

        assert classify(state) == "degraded"
        assert classify(state) != "quiet"

    async def test_all_five_fetches_run_concurrently(self):
        """The five state fetches are dispatched via asyncio.gather, not serially."""
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        started: list[str] = []
        all_started = asyncio.Event()

        async def _gate(name, result):
            started.append(name)
            if len(started) >= 5:
                all_started.set()
            await asyncio.wait_for(all_started.wait(), timeout=1.0)
            return result

        pool = AsyncMock()

        async def _pool_fetch(sql, *args):
            return await _gate("audit", [])

        pool.fetch = AsyncMock(side_effect=_pool_fetch)
        pool.fetchrow = AsyncMock(return_value=None)

        async def _board(*a, **k):
            return await _gate("board", ([], [], False))

        async def _approvals(*a, **k):
            return await _gate("approvals", (0, False))

        async def _notifications(*a, **k):
            return await _gate("notifications", (0, False))

        async def _qa(*a, **k):
            return await _gate("qa", (None, False))

        with (
            patch(
                "butlers.api.routers.dashboard_briefing._fetch_board_state",
                new=AsyncMock(side_effect=_board),
            ),
            patch(
                "butlers.api.routers.dashboard_briefing._fetch_approvals_state",
                new=AsyncMock(side_effect=_approvals),
            ),
            patch(
                "butlers.api.routers.dashboard_briefing._fetch_notifications_state",
                new=AsyncMock(side_effect=_notifications),
            ),
            patch(
                "butlers.api.routers.dashboard_briefing._fetch_qa_state",
                new=AsyncMock(side_effect=_qa),
            ),
        ):
            state = await _fetch_dashboard_state(
                pool, now, db=MagicMock(), configs=[], mgr=MagicMock(), pricing=MagicMock()
            )

        assert all_started.is_set(), "Not all five fetches started concurrently"
        assert set(started) == {"audit", "board", "approvals", "notifications", "qa"}
        assert state["attention_items"] == []


# ---------------------------------------------------------------------------
# Audit-derived attention items (bu-5y5ve spec coverage, unchanged behavior)
# ---------------------------------------------------------------------------


class TestAuditDerivedAttentionItems:
    """Spec requirement: Attention Item Sources — audit-derived path (D7)."""

    async def test_scheduled_audit_failure_becomes_high_severity(self):
        """An audit error from a scheduled session gets severity='high'.

        This means the item will drive state_class='urgent' even with no
        other attention source pending (the core of the bu-5y5ve bug report).
        """
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool(
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

        patches = _patch_all_sources()
        with patches[0], patches[1], patches[2], patches[3]:
            state = await _fetch_dashboard_state(
                pool, now, db=MagicMock(), configs=[], mgr=MagicMock(), pricing=MagicMock()
            )

        assert len(state["attention_items"]) == 1
        item = state["attention_items"][0]
        assert item["severity"] == "high"
        assert item["source"] == "audit_log"
        assert item["type"] == "scheduled_task_failure"
        assert item["butler"] == "calendar"

    async def test_scheduled_audit_failure_forces_urgent_state_class(self):
        """A single high-severity audit item causes the endpoint to return state_class='urgent'.

        This verifies the end-to-end chain: audit query -> attention item ->
        classify -> 'urgent'. No other source required.
        """
        pool = _make_owner_pool(
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

        patches = _patch_all_sources()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
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
        assert data["state_class"] == "urgent"

    async def test_non_scheduled_audit_failure_becomes_medium_severity(self):
        """An audit error not from a scheduled session gets severity='medium'.

        A single medium-severity item drives state_class='mild', not 'urgent'.
        """
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool(
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

        patches = _patch_all_sources()
        with patches[0], patches[1], patches[2], patches[3]:
            state = await _fetch_dashboard_state(
                pool, now, db=MagicMock(), configs=[], mgr=MagicMock(), pricing=MagicMock()
            )

        assert len(state["attention_items"]) == 1
        item = state["attention_items"][0]
        assert item["severity"] == "medium"
        assert item["source"] == "audit_log"
        assert item["type"] == "audit_error_group"

    async def test_audit_item_multi_butler_description(self):
        """When multiple butlers share an error, the description includes the butler count."""
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool(
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

        patches = _patch_all_sources()
        with patches[0], patches[1], patches[2], patches[3]:
            state = await _fetch_dashboard_state(
                pool, now, db=MagicMock(), configs=[], mgr=MagicMock(), pricing=MagicMock()
            )

        item = state["attention_items"][0]
        assert item["butler"] == "multiple"
        assert "2 butlers" in item["description"]


# ---------------------------------------------------------------------------
# _compute_overview_totals counts every attention source
# ---------------------------------------------------------------------------


class TestOverviewTotals:
    async def test_totals_include_audit_board_approvals_notifications_qa(self):
        now = datetime(2026, 5, 13, 15, 59, tzinfo=UTC)
        pool = _make_owner_pool(
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

        patches = _patch_all_sources(
            board=([{"severity": "high", "type": "runtime", "butler": "calendar"}], [], False),
            approvals=(1, False),
            notifications=(2, False),
        )
        with patches[0], patches[1], patches[2], patches[3]:
            state = await _fetch_dashboard_state(
                pool, now, db=MagicMock(), configs=[], mgr=MagicMock(), pricing=MagicMock()
            )

        totals = state["overview_totals"]
        # audit (medium) + board (high) + approvals (medium) + notifications (medium)
        assert totals["attention_total"] == 4
        assert totals["attention_high"] == 1
        assert totals["attention_medium"] == 3


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

        with (
            _empty_board_patch(),
            patch(
                "butlers.api.routers.dashboard_briefing.elaborate_llm",
                new=AsyncMock(return_value="All butlers are healthy and the queue is empty."),
            ),
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

        with (
            _empty_board_patch(),
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
        assert data["source"] == "fallback"

    async def test_llm_exception_triggers_fallback(self):
        """An unhandled exception from elaborate_llm produces source: fallback."""
        pool = _make_owner_pool()
        cache = BriefingCache(ttl_seconds=300)
        app = _make_app(pool, cache)

        with (
            _empty_board_patch(),
            patch(
                "butlers.api.routers.dashboard_briefing.elaborate_llm",
                new=AsyncMock(side_effect=RuntimeError("network failure")),
            ),
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
        with (
            _empty_board_patch(),
            patch(
                "butlers.api.routers.dashboard_briefing.elaborate_llm",
                new=AsyncMock(return_value="We checked all systems today!"),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/dashboard/briefing")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source"] == "fallback"

    async def test_degraded_class_skips_llm_entirely(self):
        """The degraded class never calls the LLM -- deterministic fallback only."""
        pool = _make_owner_pool()
        cache = BriefingCache(ttl_seconds=300)
        app = _make_app(pool, cache)

        llm_mock = AsyncMock(return_value="This should never be called.")
        board_fn = AsyncMock(return_value=_board_response([], registry_source_error=True))

        with (
            patch("butlers.api.routers.butlers.get_butlers_board", new=board_fn),
            patch("butlers.api.routers.dashboard_briefing.elaborate_llm", new=llm_mock),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/dashboard/briefing")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["state_class"] == "degraded"
        assert data["source"] == "fallback"
        llm_mock.assert_not_awaited()


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

        with (
            _empty_board_patch(),
            patch("butlers.api.routers.dashboard_briefing.elaborate_llm", new=_llm_stub),
        ):
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

        with (
            _empty_board_patch(),
            patch("butlers.api.routers.dashboard_briefing.elaborate_llm", new=_llm_stub),
        ):
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

    async def test_inflight_briefing_fill_cannot_repopulate_after_invalidation(self):
        """An invalidation during composition fences the stale cache write.

        A reset can arrive after the request's cache miss but before its
        expensive composition returns.  The in-flight response may finish,
        but it must not reinsert the pre-reset state for the next caller.
        """
        pool = _make_owner_pool()
        cache = BriefingCache(ttl_seconds=300)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = pool
        mock_db.credential_shared_pool.return_value = pool
        composition_started = asyncio.Event()
        allow_composition = asyncio.Event()
        now = datetime(2026, 7, 19, 0, 37, tzinfo=UTC)

        async def _slow_state(*args, **kwargs):
            composition_started.set()
            await allow_composition.wait()
            return {
                "now": now,
                "attention_items": [
                    {
                        "severity": "high",
                        "type": "qa",
                        "description": "Pre-reset QA breaker state",
                    }
                ],
                "butler_statuses": [],
                "degraded_sources": [],
            }

        with (
            patch(
                "butlers.api.routers.dashboard_briefing._owner_local_now",
                new=AsyncMock(return_value=now),
            ),
            patch(
                "butlers.api.routers.dashboard_briefing._fetch_dashboard_state",
                new=AsyncMock(side_effect=_slow_state),
            ),
            patch(
                "butlers.api.routers.dashboard_briefing.elaborate_llm",
                new=AsyncMock(return_value=None),
            ),
        ):
            request = asyncio.create_task(
                get_dashboard_briefing(
                    db=mock_db,
                    cache=cache,
                    configs=[],
                    mgr=MagicMock(),
                    pricing=MagicMock(),
                )
            )
            composition_wait = asyncio.create_task(composition_started.wait())
            try:
                done, _ = await asyncio.wait(
                    {request, composition_wait},
                    timeout=5.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if request in done:
                    await request
                assert composition_wait in done, (
                    "Briefing composition did not start within five seconds"
                )

                # Mirrors the committed reset's cache invalidation between
                # cache-miss and cache-fill.
                cache.invalidate_all()
                allow_composition.set()
                response = await request
            finally:
                for task in (request, composition_wait):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(request, composition_wait, return_exceptions=True)

        assert response.data.state_class == "urgent"
        assert cache.get("owner-uuid-1234") is None


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
# Classification exception falls through to the degraded paragraph
# ---------------------------------------------------------------------------


class TestClassificationExceptionFallback:
    async def test_classification_exception_returns_degraded(self):
        """When classify raises, the endpoint returns state_class=degraded
        with the degraded templated paragraph and source=fallback.

        A classifier bug is itself a swallowed failure -- it must not
        compose "quiet" any more than a swallowed fetch failure may
        (bu-gcz9e.1).
        """
        pool = _make_owner_pool()
        cache = BriefingCache(ttl_seconds=300)
        app = _make_app(pool, cache)

        with (
            _empty_board_patch(),
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
        assert data["state_class"] == "degraded"
        assert data["source"] == "fallback"
        assert data["elaboration"] == elaborate_fallback({}, "degraded")


# ---------------------------------------------------------------------------
# Response shape contract
# ---------------------------------------------------------------------------


class TestResponseShape:
    async def test_response_envelope_fields_and_value_domains(self):
        """One response: required field set, greet format, valid state_class and source."""
        pool = _make_owner_pool()
        cache = BriefingCache(ttl_seconds=300)
        app = _make_app(pool, cache)

        with (
            _empty_board_patch(),
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

        required = {"greet", "headline", "elaboration", "source", "state_class", "generated_at"}
        assert set(data.keys()) == required
        assert data["greet"] in {
            "Good late-night.",
            "Good morning.",
            "Good afternoon.",
            "Good evening.",
            "Good night.",
        }
        assert data["state_class"] in {
            "urgent",
            "busy",
            "mild",
            "degraded-quiet",
            "degraded",
            "quiet",
        }
        assert data["source"] in ("llm", "fallback")


# ---------------------------------------------------------------------------
# Prompt context: attention items feed the LLM prompt regardless of source
# ---------------------------------------------------------------------------


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
