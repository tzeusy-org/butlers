"""Unit tests for the situational context bus module.

Covers:
  - ContextSignal enum completeness and string values
  - _check_write_permission() — authorised, unauthorised, general butler
  - _clamp_ttl() — within-range passthrough, clamping to max
  - format_context_preamble() — single signal, multiple signals, no value, empty
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from butlers.context_bus import (
    ContextEntry,
    ContextSignal,
    _check_write_permission,
    _clamp_ttl,
    format_context_preamble,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# ContextSignal enum
# ---------------------------------------------------------------------------


class TestContextSignalEnum:
    def test_all_expected_signals_present(self):
        """All 11 signal types defined in the spec are present."""
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

    def test_signal_count(self):
        """Exactly 11 signal types (no extras, no missing)."""
        assert len(ContextSignal) == 11

    def test_enum_values_match_names(self):
        """Each ContextSignal value equals its name."""
        for signal in ContextSignal:
            assert signal.value == signal.name

    def test_signals_are_strings(self):
        """Every ContextSignal value is a plain string (StrEnum)."""
        for signal in ContextSignal:
            assert isinstance(signal.value, str), f"{signal!r} value should be str"

    def test_lookup_by_value(self):
        """Can construct a ContextSignal from its string value."""
        assert ContextSignal("traveling") is ContextSignal.traveling
        assert ContextSignal("dnd") is ContextSignal.dnd

    def test_invalid_value_raises(self):
        """Looking up an unknown value raises ValueError."""
        with pytest.raises(ValueError):
            ContextSignal("partying")

    def test_signal_is_str_subclass(self):
        """ContextSignal (StrEnum) can be used as a plain string."""
        assert isinstance(ContextSignal.meeting, str)
        assert ContextSignal.meeting == "meeting"


# ---------------------------------------------------------------------------
# _check_write_permission  (signature: butler_name, signal_type_string)
# ---------------------------------------------------------------------------


class TestCheckWritePermission:
    def test_authorized_butler_succeeds(self):
        """health butler is authorized to write exercising."""
        _check_write_permission("health", "exercising")  # must not raise

    def test_unauthorized_butler_raises_permission_error(self):
        """finance butler is NOT authorized to write exercising."""
        with pytest.raises(PermissionError) as exc_info:
            _check_write_permission("finance", "exercising")
        assert "finance" in str(exc_info.value)
        assert "exercising" in str(exc_info.value)

    def test_permission_error_names_authorized_writers(self):
        """PermissionError message lists valid writers for the signal."""
        with pytest.raises(PermissionError) as exc_info:
            _check_write_permission("finance", "exercising")
        msg = str(exc_info.value)
        assert "health" in msg  # health is authorized for exercising

    def test_general_butler_can_write_traveling(self):
        """general butler has access to traveling."""
        _check_write_permission("general", "traveling")

    def test_general_butler_can_write_meeting(self):
        """general butler has access to meeting."""
        _check_write_permission("general", "meeting")

    def test_general_butler_can_write_sleeping(self):
        """general butler has access to sleeping."""
        _check_write_permission("general", "sleeping")

    def test_general_butler_can_write_dnd(self):
        """general butler has access to dnd."""
        _check_write_permission("general", "dnd")

    def test_general_butler_broad_access(self):
        """general butler is authorized for every signal type it covers."""
        signals_with_general = [
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
        ]
        for signal in signals_with_general:
            _check_write_permission("general", signal)  # must not raise

    def test_travel_butler_can_write_traveling(self):
        """travel butler is authorized for traveling."""
        _check_write_permission("travel", "traveling")

    def test_travel_butler_cannot_write_exercising(self):
        """travel butler is NOT authorized for exercising."""
        with pytest.raises(PermissionError):
            _check_write_permission("travel", "exercising")

    def test_switchboard_can_write_dnd(self):
        """switchboard butler is authorized for dnd."""
        _check_write_permission("switchboard", "dnd")

    def test_switchboard_cannot_write_meeting(self):
        """switchboard butler is NOT authorized for meeting."""
        with pytest.raises(PermissionError):
            _check_write_permission("switchboard", "meeting")

    def test_relationship_butler_can_write_socializing(self):
        """relationship butler is authorized for socializing."""
        _check_write_permission("relationship", "socializing")

    def test_home_butler_can_write_at_home(self):
        """home butler is authorized for at_home."""
        _check_write_permission("home", "at_home")

    def test_home_butler_cannot_write_away(self):
        """home butler is NOT authorized for away."""
        with pytest.raises(PermissionError):
            _check_write_permission("home", "away")


# ---------------------------------------------------------------------------
# _clamp_ttl  (signature: signal_type_str, set_at, expires_at)
# ---------------------------------------------------------------------------


class TestClampTtl:
    def test_within_range_passthrough(self):
        """expires_at within the max TTL is returned unchanged."""
        expires = _NOW + timedelta(minutes=30)  # meeting max is 4h
        result = _clamp_ttl("meeting", _NOW, expires)
        assert result == expires

    def test_clamped_to_max_ttl(self):
        """expires_at beyond max TTL is clamped to set_at + max_ttl."""
        # meeting max TTL = 4 hours
        expires = _NOW + timedelta(hours=10)
        result = _clamp_ttl("meeting", _NOW, expires)
        assert result == _NOW + timedelta(hours=4)

    def test_exactly_at_max_ttl_not_clamped(self):
        """expires_at exactly equal to set_at + max_ttl is accepted as-is."""
        max_expires = _NOW + timedelta(hours=4)  # meeting max
        result = _clamp_ttl("meeting", _NOW, max_expires)
        assert result == max_expires

    def test_clamped_to_max_ttl_traveling(self):
        """expires_at beyond 30-day max for traveling is clamped."""
        expires = _NOW + timedelta(days=60)
        result = _clamp_ttl("traveling", _NOW, expires)
        assert result == _NOW + timedelta(days=30)

    def test_clamped_to_max_ttl_sleeping(self):
        """expires_at beyond 12-hour max for sleeping is clamped."""
        expires = _NOW + timedelta(hours=20)
        result = _clamp_ttl("sleeping", _NOW, expires)
        assert result == _NOW + timedelta(hours=12)

    def test_clamped_to_max_ttl_commuting(self):
        """expires_at beyond 3-hour max for commuting is clamped."""
        expires = _NOW + timedelta(hours=5)
        result = _clamp_ttl("commuting", _NOW, expires)
        assert result == _NOW + timedelta(hours=3)

    def test_short_expiry_not_clamped(self):
        """expires_at within limit for exercising (max=3h) is not changed."""
        expires = _NOW + timedelta(minutes=30)
        result = _clamp_ttl("exercising", _NOW, expires)
        assert result == expires

    def test_result_is_always_datetime(self):
        """_clamp_ttl always returns a datetime."""
        result = _clamp_ttl("dnd", _NOW, _NOW + timedelta(hours=1))
        assert isinstance(result, datetime)


# ---------------------------------------------------------------------------
# format_context_preamble
# ---------------------------------------------------------------------------


def _entry(
    signal_type: str,
    value: str | None = None,
    confidence: float = 1.0,
) -> ContextEntry:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    return ContextEntry(
        signal_type=signal_type,
        value=value,
        set_by_butler="general",
        set_at=now,
        expires_at=now + timedelta(hours=1),
        confidence=confidence,
    )


class TestFormatContextPreamble:
    def test_empty_signals_returns_empty_string(self):
        """No active signals → empty string."""
        assert format_context_preamble([]) == ""

    def test_single_signal_with_value_and_explicit_confidence(self):
        """Single signal: [User Context: traveling (Paris, explicit)]"""
        result = format_context_preamble([_entry("traveling", value="Paris", confidence=1.0)])
        assert result == "[User Context: traveling (Paris, explicit)]"

    def test_single_signal_without_value(self):
        """Signal without value omits the value part."""
        result = format_context_preamble([_entry("dnd", value=None, confidence=1.0)])
        assert result == "[User Context: dnd (explicit)]"

    def test_high_confidence_label(self):
        """confidence >= 0.8 → 'high confidence' label."""
        result = format_context_preamble([_entry("meeting", value="standup", confidence=0.8)])
        assert result == "[User Context: meeting (standup, high confidence)]"

    def test_medium_confidence_label(self):
        """confidence >= 0.5 → 'medium confidence' label."""
        result = format_context_preamble([_entry("meeting", value=None, confidence=0.5)])
        assert result == "[User Context: meeting (medium confidence)]"

    def test_low_confidence_label(self):
        """confidence < 0.5 → 'low confidence' label."""
        result = format_context_preamble([_entry("meeting", value=None, confidence=0.3)])
        assert result == "[User Context: meeting (low confidence)]"

    def test_multiple_signals_formatted(self):
        """Multiple signals are comma-separated inside the brackets."""
        signals = [
            _entry("traveling", value="Paris", confidence=1.0),
            _entry("meeting", value="standup", confidence=0.8),
        ]
        result = format_context_preamble(signals)
        expected = "[User Context: traveling (Paris, explicit), meeting (standup, high confidence)]"
        assert result == expected

    def test_starts_with_bracket_tag(self):
        """Output always starts with '[User Context:'."""
        result = format_context_preamble([_entry("dnd")])
        assert result.startswith("[User Context:")

    def test_ends_with_closing_bracket(self):
        """Output always ends with ']'."""
        result = format_context_preamble([_entry("dnd")])
        assert result.endswith("]")

    def test_confidence_boundary_1_0_is_explicit(self):
        """Exactly 1.0 maps to 'explicit'."""
        result = format_context_preamble([_entry("focused", confidence=1.0)])
        assert "explicit" in result

    def test_confidence_boundary_0_8_is_high(self):
        """Exactly 0.8 maps to 'high confidence', not 'medium confidence'."""
        result = format_context_preamble([_entry("focused", confidence=0.8)])
        assert "high confidence" in result
        assert "medium confidence" not in result

    def test_confidence_boundary_0_5_is_medium(self):
        """Exactly 0.5 maps to 'medium confidence', not 'low confidence'."""
        result = format_context_preamble([_entry("focused", confidence=0.5)])
        assert "medium confidence" in result
        assert "low confidence" not in result

    def test_confidence_just_below_0_5_is_low(self):
        """0.49 maps to 'low confidence'."""
        result = format_context_preamble([_entry("focused", confidence=0.49)])
        assert "low confidence" in result

    def test_signal_order_preserved(self):
        """The order of signals in the output matches the input list order."""
        signals = [
            _entry("traveling", value="Paris", confidence=1.0),
            _entry("dnd", value=None, confidence=0.9),
        ]
        result = format_context_preamble(signals)
        traveling_pos = result.index("traveling")
        dnd_pos = result.index("dnd")
        assert traveling_pos < dnd_pos
