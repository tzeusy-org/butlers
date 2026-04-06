"""Contract tests: Context Bus (RFC 0009, Invariant 12).

Validates signal vocabulary, write permissions, TTL clamping, validation,
data model, and bus functions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.contract


class TestContextSignalVocabulary:
    """RFC 0009: Fixed vocabulary of context signal types."""

    def test_all_context_signals_present_and_is_str_enum(self):
        from butlers.context_bus import ContextSignal

        expected = {
            "traveling", "sleeping", "meeting", "focused", "exercising",
            "sick", "socializing", "commuting", "at_home", "away", "dnd",
        }
        assert {s.value for s in ContextSignal} == expected
        assert ContextSignal.traveling == "traveling"

        with pytest.raises(ValueError):
            ContextSignal("nonexistent_signal_type")


class TestWritePermissionsMatrix:
    """RFC 0009: Write permissions are enforced per signal type per butler."""

    def test_authorized_writers(self):
        from butlers.context_bus import _check_write_permission

        allowed = [
            ("travel", "traveling"), ("general", "traveling"),
            ("health", "exercising"), ("general", "meeting"),
            ("general", "dnd"), ("switchboard", "dnd"),
            ("health", "sleeping"), ("general", "sleeping"),
            ("travel", "at_home"), ("home", "at_home"), ("general", "at_home"),
        ]
        for butler, signal in allowed:
            _check_write_permission(butler, signal)

    def test_unauthorized_writers_raise(self):
        from butlers.context_bus import _check_write_permission

        denied = [
            ("health", "traveling"), ("finance", "traveling"),
            ("general", "exercising"), ("health", "meeting"),
            ("health", "dnd"), ("travel", "sleeping"), ("health", "at_home"),
        ]
        for butler, signal in denied:
            with pytest.raises(PermissionError):
                _check_write_permission(butler, signal)

    def test_unknown_signal_and_error_message(self):
        from butlers.context_bus import _check_write_permission

        with pytest.raises(PermissionError):
            _check_write_permission("general", "unknown_signal_xyz")
        with pytest.raises(PermissionError) as exc_info:
            _check_write_permission("finance", "traveling")
        assert "finance" in str(exc_info.value)


class TestTtlClamping:
    """RFC 0009: Signal TTLs are clamped to per-type maximums."""

    def test_max_ttl_clamped_and_within_max_passes(self):
        from butlers.context_bus import _clamp_ttl

        now = datetime.now(tz=UTC)
        cases = [
            ("sleeping", timedelta(hours=24), timedelta(hours=12)),
            ("traveling", timedelta(days=60), timedelta(days=30)),
            ("meeting", timedelta(hours=8), timedelta(hours=4)),
        ]
        for signal, request_delta, max_delta in cases:
            clamped = _clamp_ttl(signal, now, now + request_delta)
            assert abs((clamped - (now + max_delta)).total_seconds()) < 2

        # Within max is not clamped
        within = now + timedelta(hours=1)
        assert abs((_clamp_ttl("meeting", now, within) - within).total_seconds()) < 2

    def test_naive_expires_at_treated_as_utc(self):
        from butlers.context_bus import _clamp_ttl

        now = datetime.now(tz=UTC)
        naive = datetime.utcnow() + timedelta(hours=100)
        result = _clamp_ttl("sleeping", now, naive)
        assert result.tzinfo is not None


class TestContextSignalValidation:
    """RFC 0009: set_context() validates signal type, confidence, and permissions."""

    async def test_validation_errors(self):
        from unittest.mock import AsyncMock

        from butlers.context_bus import set_context

        pool = AsyncMock()
        # Invalid signal type
        with pytest.raises(ValueError, match="[Ii]nvalid signal type"):
            await set_context(pool, "health", "invalid_xyz",
                              expires_at=datetime.now(tz=UTC) + timedelta(hours=1))
        # Confidence too high
        with pytest.raises(ValueError, match="confidence"):
            await set_context(pool, "health", "exercising", confidence=1.5)
        # Confidence negative
        with pytest.raises(ValueError, match="confidence"):
            await set_context(pool, "health", "exercising", confidence=-0.1)
        # Unauthorized butler
        with pytest.raises(PermissionError):
            await set_context(pool, "finance", "exercising")


class TestContextEntryDataclass:
    """RFC 0009: ContextEntry captures all signal fields."""

    def test_required_fields_and_optional_metadata(self):
        import dataclasses

        from butlers.context_bus import ContextEntry

        fields = {f.name for f in dataclasses.fields(ContextEntry)}
        required = {"signal_type", "value", "set_by_butler", "set_at", "expires_at", "confidence"}
        assert required.issubset(fields)

        now = datetime.now(tz=UTC)
        entry = ContextEntry(
            signal_type="traveling", value="Paris", set_by_butler="travel",
            set_at=now, expires_at=now + timedelta(hours=1), confidence=1.0,
        )
        assert entry.metadata is None


class TestContextBusFunctions:
    """RFC 0009: Context bus read/write functions are correctly defined."""

    def test_all_bus_functions_importable_and_callable(self):
        import asyncio

        from butlers.context_bus import (
            clear_context,
            format_context_preamble,
            get_active_context,
            is_user_in_context,
            set_context,
        )

        for fn in [get_active_context, is_user_in_context, set_context, clear_context]:
            assert callable(fn) and asyncio.iscoroutinefunction(fn)
        assert callable(format_context_preamble)

    def test_format_context_preamble(self):
        from butlers.context_bus import ContextEntry, format_context_preamble

        assert format_context_preamble([]) == ""

        now = datetime.now(tz=UTC)
        entry = ContextEntry(
            signal_type="traveling", value="Paris", set_by_butler="travel",
            set_at=now, expires_at=now + timedelta(hours=1), confidence=1.0,
        )
        assert format_context_preamble([entry]).startswith("[User Context:")
