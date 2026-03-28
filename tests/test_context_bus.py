"""Tests for butlers.context_bus — unit and integration tests [bu-pidq].

Coverage:
  - ContextSignal enum membership
  - _check_write_permission()
  - _clamp_ttl()
  - format_context_preamble() (unit, no DB)
  - get_active_context() (unit with mock pool)
  - is_user_in_context() (unit with mock pool)
  - set_context() (unit with mock pool + write-path validation)
  - clear_context() (unit with mock pool)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from butlers.context_bus import (
    ContextEntry,
    ContextSignal,
    _check_write_permission,
    _clamp_ttl,
    clear_context,
    format_context_preamble,
    get_active_context,
    is_user_in_context,
    set_context,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _entry(
    signal_type: str = "traveling",
    value: str | None = "Paris",
    set_by_butler: str = "travel",
    confidence: float = 1.0,
    *,
    now: datetime | None = None,
) -> ContextEntry:
    t = now or _now()
    return ContextEntry(
        signal_type=signal_type,
        value=value,
        set_by_butler=set_by_butler,
        set_at=t,
        expires_at=t + timedelta(hours=1),
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# ContextSignal enum
# ---------------------------------------------------------------------------


class TestContextSignal:
    def test_all_expected_members_present(self):
        expected = {
            "traveling",
            "sleeping",
            "meeting",
            "focused",
            "exercising",
            "sick",
            "socializing",
            "commuting",
            "at_home",
            "away",
            "dnd",
        }
        actual = {s.value for s in ContextSignal}
        assert actual == expected

    def test_enum_values_match_names(self):
        for signal in ContextSignal:
            assert signal.value == signal.name

    def test_lookup_by_value(self):
        assert ContextSignal("traveling") is ContextSignal.traveling
        assert ContextSignal("dnd") is ContextSignal.dnd

    def test_invalid_value_raises_value_error(self):
        with pytest.raises(ValueError):
            ContextSignal("partying")


# ---------------------------------------------------------------------------
# _check_write_permission
# ---------------------------------------------------------------------------


class TestCheckWritePermission:
    def test_authorized_butler_raises_nothing(self):
        # health is authorized for exercising
        _check_write_permission("health", "exercising")

    def test_general_butler_authorized_for_most_signal_types(self):
        # Per spec permission table, general is authorized for all signals EXCEPT exercising
        general_signals = {
            "traveling",
            "sleeping",
            "meeting",
            "focused",
            "sick",
            "socializing",
            "commuting",
            "at_home",
            "away",
            "dnd",
        }
        for signal_value in general_signals:
            _check_write_permission("general", signal_value)

    def test_general_butler_not_authorized_for_exercising(self):
        # Per spec: exercising is health-only; general is not listed
        with pytest.raises(PermissionError):
            _check_write_permission("general", "exercising")

    def test_unauthorized_butler_raises_permission_error(self):
        with pytest.raises(PermissionError) as exc_info:
            _check_write_permission("finance", "exercising")
        assert "finance" in str(exc_info.value)
        assert "exercising" in str(exc_info.value)

    def test_permission_error_lists_authorized_butlers(self):
        with pytest.raises(PermissionError) as exc_info:
            _check_write_permission("gaming", "sleeping")
        # authorized for sleeping: health, general
        msg = str(exc_info.value)
        assert "health" in msg or "general" in msg

    def test_travel_authorized_for_traveling(self):
        _check_write_permission("travel", "traveling")

    def test_travel_authorized_for_commuting(self):
        _check_write_permission("travel", "commuting")

    def test_home_authorized_for_at_home(self):
        _check_write_permission("home", "at_home")

    def test_switchboard_authorized_for_dnd(self):
        _check_write_permission("switchboard", "dnd")

    def test_relationship_authorized_for_socializing(self):
        _check_write_permission("relationship", "socializing")

    def test_health_not_authorized_for_traveling(self):
        with pytest.raises(PermissionError):
            _check_write_permission("health", "traveling")

    def test_finance_not_authorized_for_any_signal(self):
        for signal in ContextSignal:
            with pytest.raises(PermissionError):
                _check_write_permission("finance", signal.value)


# ---------------------------------------------------------------------------
# _clamp_ttl
# ---------------------------------------------------------------------------


class TestClampTtl:
    def test_expires_at_within_max_returned_unchanged(self):
        now = _now()
        # meeting max is 4 hours; request 2 hours -> no clamping
        expires_at = now + timedelta(hours=2)
        result = _clamp_ttl("meeting", now, expires_at)
        assert result == expires_at

    def test_expires_at_exceeding_max_clamped(self):
        now = _now()
        # meeting max is 4 hours; request 10 hours -> clamped to 4h
        expires_at = now + timedelta(hours=10)
        result = _clamp_ttl("meeting", now, expires_at)
        expected = now + timedelta(hours=4)
        # Allow 1-second tolerance for timing
        assert abs((result - expected).total_seconds()) < 1

    def test_expires_at_exactly_max_returned_unchanged(self):
        now = _now()
        # meeting max = 4h; request exactly 4h
        expires_at = now + timedelta(hours=4)
        result = _clamp_ttl("meeting", now, expires_at)
        assert result == expires_at

    def test_sleeping_max_is_12h(self):
        now = _now()
        expires_at = now + timedelta(hours=20)  # exceeds 12h max
        result = _clamp_ttl("sleeping", now, expires_at)
        expected = now + timedelta(hours=12)
        assert abs((result - expected).total_seconds()) < 1

    def test_traveling_max_is_30_days(self):
        now = _now()
        expires_at = now + timedelta(days=31)  # exceeds 30d max
        result = _clamp_ttl("traveling", now, expires_at)
        expected = now + timedelta(days=30)
        assert abs((result - expected).total_seconds()) < 1

    def test_commuting_max_is_3h(self):
        now = _now()
        expires_at = now + timedelta(hours=5)
        result = _clamp_ttl("commuting", now, expires_at)
        expected = now + timedelta(hours=3)
        assert abs((result - expected).total_seconds()) < 1

    def test_short_expiry_not_clamped(self):
        now = _now()
        # exercising max = 3h; request 30 minutes -> fine
        expires_at = now + timedelta(minutes=30)
        result = _clamp_ttl("exercising", now, expires_at)
        assert result == expires_at


# ---------------------------------------------------------------------------
# get_active_context (unit: mock pool)
# ---------------------------------------------------------------------------


class TestGetActiveContext:
    async def test_returns_context_entries_from_pool(self):
        now = _now()
        fake_row = {
            "signal_type": "traveling",
            "value": "Paris",
            "set_by_butler": "travel",
            "set_at": now,
            "expires_at": now + timedelta(hours=12),
            "confidence": 1.0,
            "metadata": None,
        }
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[fake_row])

        result = await get_active_context(pool)

        assert len(result) == 1
        assert result[0].signal_type == "traveling"
        assert result[0].value == "Paris"
        assert result[0].set_by_butler == "travel"
        assert result[0].confidence == 1.0
        assert result[0].metadata is None

    async def test_returns_empty_list_when_no_active_signals(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        result = await get_active_context(pool)
        assert result == []

    async def test_multiple_rows_mapped_to_context_entries(self):
        now = _now()
        rows = [
            {
                "signal_type": "traveling",
                "value": "Berlin",
                "set_by_butler": "travel",
                "set_at": now,
                "expires_at": now + timedelta(hours=24),
                "confidence": 1.0,
                "metadata": None,
            },
            {
                "signal_type": "meeting",
                "value": "standup",
                "set_by_butler": "general",
                "set_at": now,
                "expires_at": now + timedelta(hours=1),
                "confidence": 0.8,
                "metadata": {"room": "zoom"},
            },
        ]
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=rows)
        result = await get_active_context(pool)
        assert len(result) == 2
        assert result[0].signal_type == "traveling"
        assert result[1].signal_type == "meeting"
        assert result[1].metadata == {"room": "zoom"}

    async def test_query_uses_correct_where_clause(self):
        """Verify the pool.fetch call contains the expected filter."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        await get_active_context(pool)
        sql = pool.fetch.call_args[0][0]
        assert "superseded_at IS NULL" in sql
        assert "expires_at > now()" in sql

    async def test_query_orders_by_confidence_desc_then_set_at_desc(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        await get_active_context(pool)
        sql = pool.fetch.call_args[0][0]
        assert "ORDER BY confidence DESC" in sql
        assert "set_at DESC" in sql


# ---------------------------------------------------------------------------
# is_user_in_context (unit: mock pool)
# ---------------------------------------------------------------------------


class TestIsUserInContext:
    async def test_returns_true_when_active_signal_exists(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"1": 1})
        result = await is_user_in_context(pool, "traveling")
        assert result is True

    async def test_returns_false_when_no_active_signal(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        result = await is_user_in_context(pool, "traveling")
        assert result is False

    async def test_passes_signal_type_as_parameter(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        await is_user_in_context(pool, "exercising")
        args = pool.fetchrow.call_args[0]
        assert "exercising" in args

    async def test_passes_min_confidence_as_parameter(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        await is_user_in_context(pool, "meeting", min_confidence=0.7)
        args = pool.fetchrow.call_args[0]
        assert 0.7 in args

    async def test_default_min_confidence_is_0_5(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        await is_user_in_context(pool, "meeting")
        args = pool.fetchrow.call_args[0]
        assert 0.5 in args

    async def test_custom_min_confidence_used(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        await is_user_in_context(pool, "meeting", min_confidence=0.2)
        args = pool.fetchrow.call_args[0]
        assert 0.2 in args

    async def test_query_includes_superseded_at_null_filter(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        await is_user_in_context(pool, "traveling")
        sql = pool.fetchrow.call_args[0][0]
        assert "superseded_at IS NULL" in sql

    async def test_query_includes_expires_at_filter(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        await is_user_in_context(pool, "traveling")
        sql = pool.fetchrow.call_args[0][0]
        assert "expires_at > now()" in sql


# ---------------------------------------------------------------------------
# set_context (unit: mock pool)
# ---------------------------------------------------------------------------


class TestSetContext:
    async def test_raises_value_error_for_invalid_signal_type(self):
        pool = AsyncMock()
        with pytest.raises(ValueError) as exc_info:
            await set_context(pool, "general", "partying")
        assert "partying" in str(exc_info.value)
        pool.execute.assert_not_called()

    async def test_value_error_lists_valid_signal_types(self):
        pool = AsyncMock()
        with pytest.raises(ValueError) as exc_info:
            await set_context(pool, "general", "partying")
        msg = str(exc_info.value)
        assert "traveling" in msg

    async def test_raises_permission_error_for_unauthorized_butler(self):
        pool = AsyncMock()
        with pytest.raises(PermissionError) as exc_info:
            await set_context(pool, "finance", "exercising")
        assert "finance" in str(exc_info.value)
        pool.execute.assert_not_called()

    async def test_calls_pool_execute_for_valid_authorized_write(self):
        pool = AsyncMock()
        pool.execute = AsyncMock()
        await set_context(pool, "health", "exercising")
        pool.execute.assert_called_once()

    async def test_applies_default_ttl_when_expires_at_omitted(self):
        """Without expires_at, default TTL for meeting (1h) is applied."""
        pool = AsyncMock()
        pool.execute = AsyncMock()
        before = _now()
        await set_context(pool, "general", "meeting")
        after = _now()

        args = pool.execute.call_args[0]
        expires_at_arg = args[5]  # position: signal_type, value, butler, set_at, expires_at, ...
        # find the datetime arg that is expires_at (not set_at)
        # The function sets expires_at = now() + 1h for meeting
        # Locate it in positional args
        datetime_args = [a for a in args if isinstance(a, datetime)]
        # set_at and expires_at
        assert len(datetime_args) == 2
        set_at_arg, expires_at_arg = datetime_args
        expected_min = before + timedelta(minutes=59)
        expected_max = after + timedelta(hours=1, seconds=1)
        assert expected_min <= expires_at_arg <= expected_max

    async def test_clamps_expires_at_to_max_ttl(self):
        """When expires_at exceeds the max, it's clamped to max."""
        pool = AsyncMock()
        pool.execute = AsyncMock()
        now = _now()
        far_future = now + timedelta(hours=100)  # way past 4h max for meeting
        await set_context(pool, "general", "meeting", expires_at=far_future)

        args = pool.execute.call_args[0]
        datetime_args = [a for a in args if isinstance(a, datetime)]
        expires_at_arg = max(datetime_args)  # expires_at is always > set_at
        # Should be clamped to ~4h from now
        max_allowed = now + timedelta(hours=4, seconds=5)
        assert expires_at_arg <= max_allowed

    async def test_uses_on_conflict_do_update(self):
        """SQL includes ON CONFLICT DO UPDATE for upsert semantics."""
        pool = AsyncMock()
        pool.execute = AsyncMock()
        await set_context(pool, "general", "dnd")
        sql = pool.execute.call_args[0][0]
        assert "ON CONFLICT" in sql
        assert "DO UPDATE" in sql

    async def test_clears_superseded_at_on_upsert(self):
        """Upsert SQL sets superseded_at = NULL."""
        pool = AsyncMock()
        pool.execute = AsyncMock()
        await set_context(pool, "general", "dnd")
        sql = pool.execute.call_args[0][0]
        assert "superseded_at = NULL" in sql or "superseded_at=NULL" in sql.replace(" ", "")

    async def test_passes_value_to_pool(self):
        pool = AsyncMock()
        pool.execute = AsyncMock()
        await set_context(pool, "general", "meeting", value="standup")
        args = pool.execute.call_args[0]
        assert "standup" in args

    async def test_passes_confidence_to_pool(self):
        pool = AsyncMock()
        pool.execute = AsyncMock()
        await set_context(pool, "general", "meeting", confidence=0.7)
        args = pool.execute.call_args[0]
        assert 0.7 in args

    async def test_passes_metadata_to_pool(self):
        import json as _json

        pool = AsyncMock()
        pool.execute = AsyncMock()
        meta = {"source": "calendar"}
        await set_context(pool, "general", "meeting", metadata=meta)
        args = pool.execute.call_args[0]
        # metadata is JSON-serialized before being passed to asyncpg (JSONB $7 arg)
        assert _json.dumps(meta) in args

    async def test_general_butler_can_write_most_signal_types(self):
        """general butler should succeed for all types except exercising (health-only per spec)."""
        pool = AsyncMock()
        pool.execute = AsyncMock()
        general_signals = {
            "traveling",
            "sleeping",
            "meeting",
            "focused",
            "sick",
            "socializing",
            "commuting",
            "at_home",
            "away",
            "dnd",
        }
        for signal_value in general_signals:
            await set_context(pool, "general", signal_value)

    async def test_general_butler_cannot_write_exercising(self):
        """exercising is health-only per the spec permission table."""
        pool = AsyncMock()
        with pytest.raises(PermissionError):
            await set_context(pool, "general", "exercising")

    async def test_does_not_write_on_invalid_signal_type_before_permission_check(self):
        """ValueError is raised before PermissionError (signal validated first)."""
        pool = AsyncMock()
        # Use a butler that would otherwise fail permission check too
        with pytest.raises(ValueError):
            await set_context(pool, "finance", "invalidtype")


# ---------------------------------------------------------------------------
# clear_context (unit: mock pool)
# ---------------------------------------------------------------------------


class TestClearContext:
    async def test_calls_pool_execute(self):
        pool = AsyncMock()
        pool.execute = AsyncMock()
        await clear_context(pool, "health", "exercising")
        pool.execute.assert_called_once()

    async def test_sql_filters_by_signal_type_and_set_by_butler(self):
        pool = AsyncMock()
        pool.execute = AsyncMock()
        await clear_context(pool, "health", "exercising")
        args = pool.execute.call_args[0]
        assert "exercising" in args
        assert "health" in args

    async def test_sql_sets_superseded_at(self):
        pool = AsyncMock()
        pool.execute = AsyncMock()
        await clear_context(pool, "health", "exercising")
        sql = pool.execute.call_args[0][0]
        assert "superseded_at" in sql

    async def test_sql_filters_superseded_at_null(self):
        """Clearing an already-cleared signal only touches NULL rows (idempotent)."""
        pool = AsyncMock()
        pool.execute = AsyncMock()
        await clear_context(pool, "health", "exercising")
        sql = pool.execute.call_args[0][0]
        assert "superseded_at IS NULL" in sql

    async def test_does_not_raise_on_no_matching_row(self):
        """No error raised when no rows match (non-existent or already-cleared)."""
        pool = AsyncMock()
        pool.execute = AsyncMock()
        # Should complete without exception
        await clear_context(pool, "health", "exercising")

    async def test_butler_name_is_used_as_set_by_butler_filter(self):
        """Clearing by general butler only affects rows set_by_butler='general'."""
        pool = AsyncMock()
        pool.execute = AsyncMock()
        await clear_context(pool, "general", "dnd")
        args = pool.execute.call_args[0]
        assert "general" in args


# ---------------------------------------------------------------------------
# format_context_preamble (unit: pure function)
# ---------------------------------------------------------------------------


class TestFormatContextPreamble:
    def test_empty_signals_returns_empty_string(self):
        assert format_context_preamble([]) == ""

    def test_single_signal_with_value(self):
        signals = [_entry("traveling", "Paris", confidence=1.0)]
        result = format_context_preamble(signals)
        assert result == "[User Context: traveling (Paris, explicit)]"

    def test_single_signal_without_value(self):
        signals = [_entry("dnd", None, "general", confidence=1.0)]
        result = format_context_preamble(signals)
        assert result == "[User Context: dnd (explicit)]"

    def test_multiple_signals_separated_by_comma(self):
        now = _now()
        signals = [
            _entry("traveling", "Paris", confidence=1.0, now=now),
            _entry("meeting", "standup", "general", confidence=0.8, now=now),
        ]
        result = format_context_preamble(signals)
        assert result == (
            "[User Context: traveling (Paris, explicit), meeting (standup, high confidence)]"
        )

    def test_confidence_label_explicit_for_1_0(self):
        signals = [_entry(confidence=1.0)]
        assert "explicit" in format_context_preamble(signals)

    def test_confidence_label_high_confidence_for_0_9(self):
        signals = [_entry(confidence=0.9)]
        assert "high confidence" in format_context_preamble(signals)

    def test_confidence_label_high_confidence_for_0_8(self):
        signals = [_entry(confidence=0.8)]
        assert "high confidence" in format_context_preamble(signals)

    def test_confidence_label_medium_confidence_for_0_7(self):
        signals = [_entry(confidence=0.7)]
        assert "medium confidence" in format_context_preamble(signals)

    def test_confidence_label_medium_confidence_for_0_5(self):
        signals = [_entry(confidence=0.5)]
        assert "medium confidence" in format_context_preamble(signals)

    def test_confidence_label_low_confidence_for_0_4(self):
        signals = [_entry(confidence=0.4)]
        assert "low confidence" in format_context_preamble(signals)

    def test_confidence_label_low_confidence_for_0_0(self):
        signals = [_entry(confidence=0.0)]
        assert "low confidence" in format_context_preamble(signals)

    def test_result_starts_with_bracket_prefix(self):
        signals = [_entry()]
        assert format_context_preamble(signals).startswith("[User Context: ")

    def test_result_ends_with_closing_bracket(self):
        signals = [_entry()]
        assert format_context_preamble(signals).endswith("]")

    def test_three_signals_formatted_correctly(self):
        now = _now()
        signals = [
            _entry("traveling", "Paris", "travel", 1.0, now=now),
            _entry("meeting", "standup", "general", 0.8, now=now),
            _entry("dnd", None, "general", 0.5, now=now),
        ]
        result = format_context_preamble(signals)
        assert "traveling (Paris, explicit)" in result
        assert "meeting (standup, high confidence)" in result
        assert "dnd (medium confidence)" in result
