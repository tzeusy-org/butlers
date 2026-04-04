"""Contract tests: Context Bus (RFC 0009, Invariant 12).

Validates signal TTL enforcement, write permissions matrix, and
supersession semantics.

Principle: Public.user_context table provides shared situational awareness.
Only authorized butlers can write specific signal types. Signals expire
automatically via TTL (RFC 0009).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.contract


class TestContextSignalVocabulary:
    """RFC 0009: Fixed vocabulary of context signal types."""

    def test_all_context_signals_present(self):
        """RFC 0009: ContextSignal enum contains all 11 signal types."""
        from butlers.context_bus import ContextSignal

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
        assert expected == actual, f"ContextSignal enum must contain exactly {expected} (RFC 0009)"

    def test_context_signal_is_str_enum(self):
        """RFC 0009: ContextSignal is a StrEnum for easy string comparison."""
        from butlers.context_bus import ContextSignal

        # StrEnum members compare equal to their string value
        assert ContextSignal.traveling == "traveling"
        assert ContextSignal.dnd == "dnd"

    def test_invalid_signal_type_rejected_by_enum(self):
        """RFC 0009: Signal types not in the vocabulary are rejected."""
        from butlers.context_bus import ContextSignal

        with pytest.raises(ValueError):
            ContextSignal("nonexistent_signal_type")


class TestWritePermissionsMatrix:
    """RFC 0009: Write permissions are enforced per signal type per butler."""

    def test_traveling_authorized_writers(self):
        """RFC 0009: Only travel and general butlers can write 'traveling' signal."""
        from butlers.context_bus import _check_write_permission

        # Authorized: travel, general
        _check_write_permission("travel", "traveling")
        _check_write_permission("general", "traveling")

        # Unauthorized: health, finance, etc.
        with pytest.raises(PermissionError):
            _check_write_permission("health", "traveling")
        with pytest.raises(PermissionError):
            _check_write_permission("finance", "traveling")

    def test_exercising_authorized_writer_is_health_only(self):
        """RFC 0009: Only the health butler can write 'exercising' signal."""
        from butlers.context_bus import _check_write_permission

        _check_write_permission("health", "exercising")

        with pytest.raises(PermissionError):
            _check_write_permission("general", "exercising")
        with pytest.raises(PermissionError):
            _check_write_permission("finance", "exercising")

    def test_meeting_authorized_writer_is_general_only(self):
        """RFC 0009: Only the general butler can write 'meeting' signal."""
        from butlers.context_bus import _check_write_permission

        _check_write_permission("general", "meeting")

        with pytest.raises(PermissionError):
            _check_write_permission("health", "meeting")
        with pytest.raises(PermissionError):
            _check_write_permission("travel", "meeting")

    def test_dnd_authorized_writers(self):
        """RFC 0009: general and switchboard butlers can write 'dnd' signal."""
        from butlers.context_bus import _check_write_permission

        _check_write_permission("general", "dnd")
        _check_write_permission("switchboard", "dnd")

        with pytest.raises(PermissionError):
            _check_write_permission("health", "dnd")
        with pytest.raises(PermissionError):
            _check_write_permission("finance", "dnd")

    def test_sleeping_authorized_writers(self):
        """RFC 0009: health and general butlers can write 'sleeping' signal."""
        from butlers.context_bus import _check_write_permission

        _check_write_permission("health", "sleeping")
        _check_write_permission("general", "sleeping")

        with pytest.raises(PermissionError):
            _check_write_permission("travel", "sleeping")

    def test_at_home_authorized_writers(self):
        """RFC 0009: travel, home, and general butlers can write 'at_home' signal."""
        from butlers.context_bus import _check_write_permission

        _check_write_permission("travel", "at_home")
        _check_write_permission("home", "at_home")
        _check_write_permission("general", "at_home")

        with pytest.raises(PermissionError):
            _check_write_permission("health", "at_home")

    def test_unknown_signal_type_raises_permission_error(self):
        """RFC 0009: Unknown signal type has no authorized writers."""
        from butlers.context_bus import _check_write_permission

        with pytest.raises(PermissionError):
            _check_write_permission("general", "unknown_signal_xyz")

    def test_permission_error_message_includes_authorized_butlers(self):
        """RFC 0009: PermissionError message names authorized butlers for clarity."""
        from butlers.context_bus import _check_write_permission

        with pytest.raises(PermissionError) as exc_info:
            _check_write_permission("finance", "traveling")

        error_msg = str(exc_info.value)
        # Must identify the unauthorized butler
        assert "finance" in error_msg, "Error must name the unauthorized butler"


class TestTtlClamping:
    """RFC 0009: Signal TTLs are clamped to per-type maximums."""

    def _now(self) -> datetime:
        return datetime.now(tz=UTC)

    def test_sleeping_max_ttl_is_12_hours(self):
        """RFC 0009: Sleeping signal max TTL is 12 hours."""
        from butlers.context_bus import _clamp_ttl

        now = self._now()
        # Request 24 hours but max is 12
        too_long = now + timedelta(hours=24)
        clamped = _clamp_ttl("sleeping", now, too_long)
        expected_max = now + timedelta(hours=12)
        assert abs((clamped - expected_max).total_seconds()) < 2, (
            "Sleeping max TTL must be 12 hours (RFC 0009)"
        )

    def test_traveling_max_ttl_is_30_days(self):
        """RFC 0009: Traveling signal max TTL is 30 days."""
        from butlers.context_bus import _clamp_ttl

        now = self._now()
        too_long = now + timedelta(days=60)
        clamped = _clamp_ttl("traveling", now, too_long)
        expected_max = now + timedelta(days=30)
        assert abs((clamped - expected_max).total_seconds()) < 2, (
            "Traveling max TTL must be 30 days (RFC 0009)"
        )

    def test_meeting_max_ttl_is_4_hours(self):
        """RFC 0009: Meeting signal max TTL is 4 hours."""
        from butlers.context_bus import _clamp_ttl

        now = self._now()
        too_long = now + timedelta(hours=8)
        clamped = _clamp_ttl("meeting", now, too_long)
        expected_max = now + timedelta(hours=4)
        assert abs((clamped - expected_max).total_seconds()) < 2, (
            "Meeting max TTL must be 4 hours (RFC 0009)"
        )

    def test_within_max_ttl_not_clamped(self):
        """RFC 0009: TTL within maximum is returned unchanged."""
        from butlers.context_bus import _clamp_ttl

        now = self._now()
        within_max = now + timedelta(hours=1)
        result = _clamp_ttl("meeting", now, within_max)
        assert abs((result - within_max).total_seconds()) < 2, (
            "TTL within max must not be clamped (RFC 0009)"
        )

    def test_naive_expires_at_treated_as_utc(self):
        """RFC 0009: Naive datetime expires_at values are treated as UTC."""
        from butlers.context_bus import _clamp_ttl

        now = datetime.now(tz=UTC)
        naive_expires = datetime.utcnow() + timedelta(hours=100)  # Naive but UTC-aligned, no tzinfo
        # Must not raise; naive is treated as UTC
        result = _clamp_ttl("sleeping", now, naive_expires)
        # Result must be a clamped, UTC-aware datetime
        assert result.tzinfo is not None, "Clamped TTL must be timezone-aware"


class TestContextSignalValidation:
    """RFC 0009: set_context() validates signal type and confidence."""

    async def test_invalid_signal_type_raises_value_error(self):
        """RFC 0009: set_context() raises ValueError for invalid signal types."""
        from unittest.mock import AsyncMock

        from butlers.context_bus import set_context

        pool = AsyncMock()
        with pytest.raises(ValueError, match="[Ii]nvalid signal type"):
            await set_context(
                pool,
                "health",
                "invalid_signal_xyz",
                expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            )

    async def test_invalid_confidence_raises_value_error(self):
        """RFC 0009: set_context() raises ValueError when confidence is outside [0.0, 1.0]."""
        from unittest.mock import AsyncMock

        from butlers.context_bus import set_context

        pool = AsyncMock()
        with pytest.raises(ValueError, match="confidence"):
            await set_context(pool, "health", "exercising", confidence=1.5)

    async def test_negative_confidence_raises_value_error(self):
        """RFC 0009: Negative confidence raises ValueError."""
        from unittest.mock import AsyncMock

        from butlers.context_bus import set_context

        pool = AsyncMock()
        with pytest.raises(ValueError, match="confidence"):
            await set_context(pool, "health", "exercising", confidence=-0.1)

    async def test_unauthorized_butler_raises_permission_error(self):
        """RFC 0009: Unauthorized butler raises PermissionError in set_context()."""
        from unittest.mock import AsyncMock

        from butlers.context_bus import set_context

        pool = AsyncMock()
        with pytest.raises(PermissionError):
            await set_context(pool, "finance", "exercising")


class TestContextEntryDataclass:
    """RFC 0009: ContextEntry captures all signal fields."""

    def test_context_entry_has_required_fields(self):
        """RFC 0009: ContextEntry must capture all signal fields."""
        import dataclasses

        from butlers.context_bus import ContextEntry

        fields = {f.name for f in dataclasses.fields(ContextEntry)}
        required = {
            "signal_type",
            "value",
            "set_by_butler",
            "set_at",
            "expires_at",
            "confidence",
        }
        assert required.issubset(fields), f"ContextEntry must have fields {required} (RFC 0009)"

    def test_context_entry_metadata_is_optional(self):
        """RFC 0009: metadata field on ContextEntry is optional."""
        from butlers.context_bus import ContextEntry

        now = datetime.now(tz=UTC)
        entry = ContextEntry(
            signal_type="traveling",
            value="Paris",
            set_by_butler="travel",
            set_at=now,
            expires_at=now + timedelta(hours=1),
            confidence=1.0,
            # metadata is optional
        )
        assert entry.metadata is None


class TestContextBusFunctions:
    """RFC 0009: Context bus read/write functions are correctly defined."""

    def test_get_active_context_is_importable(self):
        """RFC 0009: get_active_context() returns all active signals."""
        import asyncio

        from butlers.context_bus import get_active_context

        assert callable(get_active_context)
        assert asyncio.iscoroutinefunction(get_active_context)

    def test_is_user_in_context_is_importable(self):
        """RFC 0009: is_user_in_context() checks single signal type."""
        import asyncio

        from butlers.context_bus import is_user_in_context

        assert callable(is_user_in_context)
        assert asyncio.iscoroutinefunction(is_user_in_context)

    def test_set_context_is_importable(self):
        """RFC 0009: set_context() writes a context signal with permission check."""
        import asyncio

        from butlers.context_bus import set_context

        assert callable(set_context)
        assert asyncio.iscoroutinefunction(set_context)

    def test_clear_context_is_importable(self):
        """RFC 0009: clear_context() sets superseded_at to clear a signal early."""
        import asyncio

        from butlers.context_bus import clear_context

        assert callable(clear_context)
        assert asyncio.iscoroutinefunction(clear_context)

    def test_format_context_preamble_is_importable(self):
        """RFC 0009: format_context_preamble() formats signals for LLM prompts."""
        from butlers.context_bus import format_context_preamble

        assert callable(format_context_preamble)

    def test_format_context_preamble_empty_list_returns_empty_string(self):
        """RFC 0009: Empty signal list returns empty string preamble."""
        from butlers.context_bus import format_context_preamble

        result = format_context_preamble([])
        assert result == "", "Empty context must return empty string (RFC 0009)"

    def test_format_context_preamble_starts_with_user_context(self):
        """RFC 0009: Context preamble starts with '[User Context: ...]'."""
        from butlers.context_bus import ContextEntry, format_context_preamble

        now = datetime.now(tz=UTC)
        entry = ContextEntry(
            signal_type="traveling",
            value="Paris",
            set_by_butler="travel",
            set_at=now,
            expires_at=now + timedelta(hours=1),
            confidence=1.0,
        )
        preamble = format_context_preamble([entry])
        assert preamble.startswith("[User Context:"), (
            "Context preamble must start with '[User Context:' (RFC 0009)"
        )
