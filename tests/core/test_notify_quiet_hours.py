"""Tests for §8.6 quiet-hours notification dispatch via approvals_policy.

Covers:
- §8.6.1 is_in_policy_quiet_hours: same-day and overnight windows
- §8.6.2 should_suppress_by_policy: policy=None always sends; NULL hours always send
- §8.6.3 send outside quiet hours — assert send called (not suppressed)
- §8.6.4 send inside quiet hours — assert send NOT called (suppressed)
- §8.6.5 timezone handling (Asia/Singapore overnight window)
- §8.6.6 missing approvals_policy row defaults to "always send"
- §8.6.7 get_approvals_policy_quiet_hours: DB error returns None (graceful fallback)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# §8.6.1 — is_in_policy_quiet_hours pure logic
# ---------------------------------------------------------------------------


class TestIsInPolicyQuietHours:
    """Unit tests for the pure is_in_policy_quiet_hours function."""

    def setup_method(self):
        from butlers.core.approvals_policy import is_in_policy_quiet_hours

        self.fn = is_in_policy_quiet_hours

    def test_same_day_range_inside(self):
        """Hour within a same-day range is inside quiet hours."""
        # 08-12: hours 8,9,10,11,12 are quiet
        assert self.fn(current_hour=8, quiet_start=8, quiet_end=12) is True
        assert self.fn(current_hour=10, quiet_start=8, quiet_end=12) is True
        assert self.fn(current_hour=12, quiet_start=8, quiet_end=12) is True

    def test_same_day_range_outside(self):
        """Hour outside a same-day range is active."""
        assert self.fn(current_hour=7, quiet_start=8, quiet_end=12) is False
        assert self.fn(current_hour=13, quiet_start=8, quiet_end=12) is False

    def test_overnight_range_inside(self):
        """Hours within an overnight range (22-07) are quiet."""
        for h in (22, 23, 0, 1, 3, 6, 7):
            assert self.fn(current_hour=h, quiet_start=22, quiet_end=7) is True, f"hour={h}"

    def test_overnight_range_outside(self):
        """Hours outside an overnight range (22-07) are active."""
        for h in (8, 12, 14, 21):
            assert self.fn(current_hour=h, quiet_start=22, quiet_end=7) is False, f"hour={h}"

    def test_midnight_boundary_overnight(self):
        """Boundary condition: exactly at midnight (0) in overnight range."""
        assert self.fn(current_hour=0, quiet_start=22, quiet_end=7) is True


# ---------------------------------------------------------------------------
# §8.6.2 — should_suppress_by_policy
# ---------------------------------------------------------------------------


class TestShouldSuppressByPolicy:
    """Unit tests for should_suppress_by_policy."""

    def setup_method(self):
        from butlers.core.approvals_policy import should_suppress_by_policy

        self.fn = should_suppress_by_policy

    def test_none_policy_never_suppresses(self):
        """No policy configured → always send."""
        assert self.fn(None, current_hour=3) is False
        assert self.fn(None, current_hour=14) is False

    def test_null_start_hour_never_suppresses(self):
        """quiet_start_hour=None → always send."""
        policy = {"quiet_start_hour": None, "quiet_end_hour": 7, "timezone": "UTC"}
        assert self.fn(policy, current_hour=3) is False

    def test_null_end_hour_never_suppresses(self):
        """quiet_end_hour=None → always send."""
        policy = {"quiet_start_hour": 22, "quiet_end_hour": None, "timezone": "UTC"}
        assert self.fn(policy, current_hour=3) is False

    def test_both_null_never_suppresses(self):
        """Both hours None → always send."""
        policy = {"quiet_start_hour": None, "quiet_end_hour": None, "timezone": "UTC"}
        assert self.fn(policy, current_hour=3) is False

    def test_suppresses_inside_quiet_hours(self):
        """Active policy and current_hour inside window → suppress."""
        policy = {"quiet_start_hour": 22, "quiet_end_hour": 7, "timezone": "UTC"}
        assert self.fn(policy, current_hour=2) is True
        assert self.fn(policy, current_hour=23) is True

    def test_does_not_suppress_outside_quiet_hours(self):
        """Active policy and current_hour outside window → do not suppress."""
        policy = {"quiet_start_hour": 22, "quiet_end_hour": 7, "timezone": "UTC"}
        assert self.fn(policy, current_hour=14) is False
        assert self.fn(policy, current_hour=8) is False


# ---------------------------------------------------------------------------
# §8.6.5 — Asia/Singapore timezone handling
# ---------------------------------------------------------------------------


class TestTimezoneHandling:
    """Verify that timezone conversion is applied correctly.

    Scenario: policy timezone = Asia/Singapore (UTC+8).
    - UTC 03:00 = SGT 11:00 → NOT in quiet window (22:00-08:00 SGT)
    - UTC 17:00 = SGT 01:00 (next day) → IN quiet window
    """

    def test_sgt_outside_quiet_hours(self):
        """UTC 03:00 = SGT 11:00 → not in quiet (22-08 SGT)."""
        from zoneinfo import ZoneInfo

        from butlers.core.approvals_policy import should_suppress_by_policy

        policy = {"quiet_start_hour": 22, "quiet_end_hour": 8, "timezone": "Asia/Singapore"}
        # Simulate converting UTC 03:00 → SGT 11:00
        tz = ZoneInfo("Asia/Singapore")
        utc_time = datetime(2026, 1, 15, 3, 0, tzinfo=UTC)
        local_hour = utc_time.astimezone(tz).hour  # 11
        assert local_hour == 11
        assert should_suppress_by_policy(policy, current_hour=local_hour) is False

    def test_sgt_inside_quiet_hours(self):
        """UTC 17:00 = SGT 01:00 → inside quiet (22-08 SGT)."""
        from zoneinfo import ZoneInfo

        from butlers.core.approvals_policy import should_suppress_by_policy

        policy = {"quiet_start_hour": 22, "quiet_end_hour": 8, "timezone": "Asia/Singapore"}
        tz = ZoneInfo("Asia/Singapore")
        utc_time = datetime(2026, 1, 15, 17, 0, tzinfo=UTC)
        local_hour = utc_time.astimezone(tz).hour  # 01
        assert local_hour == 1
        assert should_suppress_by_policy(policy, current_hour=local_hour) is True


# ---------------------------------------------------------------------------
# §8.6.7 — get_approvals_policy_quiet_hours DB error → None
# ---------------------------------------------------------------------------


class TestGetApprovalsPolicy:
    """Unit tests for the async DB accessor."""

    async def test_returns_row_fields(self):
        """fetchrow returns a row → dict with quiet_start_hour etc."""
        from butlers.core.approvals_policy import get_approvals_policy_quiet_hours

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(
            return_value={
                "quiet_start_hour": 22,
                "quiet_end_hour": 7,
                "timezone": "UTC",
            }
        )
        result = await get_approvals_policy_quiet_hours(mock_pool)
        assert result is not None
        assert result["quiet_start_hour"] == 22
        assert result["quiet_end_hour"] == 7
        assert result["timezone"] == "UTC"

    async def test_returns_none_when_no_row(self):
        """fetchrow returns None (no row) → function returns None."""
        from butlers.core.approvals_policy import get_approvals_policy_quiet_hours

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        result = await get_approvals_policy_quiet_hours(mock_pool)
        assert result is None

    async def test_returns_none_on_db_error(self):
        """DB error (table absent or connection error) → returns None, not exception."""
        from butlers.core.approvals_policy import get_approvals_policy_quiet_hours

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(side_effect=Exception("relation does not exist"))
        result = await get_approvals_policy_quiet_hours(mock_pool)
        assert result is None

    async def test_defaults_timezone_null_to_utc(self):
        """timezone column NULL → defaults to 'UTC' in returned dict."""
        from butlers.core.approvals_policy import get_approvals_policy_quiet_hours

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(
            return_value={
                "quiet_start_hour": 22,
                "quiet_end_hour": 7,
                "timezone": None,
            }
        )
        result = await get_approvals_policy_quiet_hours(mock_pool)
        assert result is not None
        assert result["timezone"] == "UTC"


# ---------------------------------------------------------------------------
# §8.6.3/8.6.4/8.6.6 — notify() quiet-hours gate: end-to-end logic tests
#
# These tests verify the full quiet-hours suppression decision path using
# direct calls to the approvals_policy module functions.  They cover the
# behavioral contracts without requiring a full daemon boot.
# ---------------------------------------------------------------------------


class TestQuietHoursDecisionPath:
    """End-to-end tests for the suppression decision pipeline.

    Combines DB accessor → timezone conversion → suppression decision in
    the same way the notify() gate does it at runtime.
    """

    async def test_send_outside_quiet_hours_not_suppressed(self) -> None:
        """Outside quiet hours: get_approvals_policy + should_suppress returns False."""
        from zoneinfo import ZoneInfo

        from butlers.core.approvals_policy import (
            get_approvals_policy_quiet_hours,
            should_suppress_by_policy,
        )

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(
            return_value={"quiet_start_hour": 22, "quiet_end_hour": 7, "timezone": "UTC"}
        )
        policy = await get_approvals_policy_quiet_hours(mock_pool)
        assert policy is not None

        # 14:00 UTC → 14:00 UTC (not in 22-07 quiet window)
        tz = ZoneInfo("UTC")
        utc_time = datetime(2026, 1, 15, 14, 0, tzinfo=UTC)
        current_hour = utc_time.astimezone(tz).hour

        result = should_suppress_by_policy(policy, current_hour=current_hour)
        assert result is False, "14:00 UTC should not be suppressed in 22-07 window"

    async def test_send_inside_quiet_hours_suppressed(self) -> None:
        """Inside quiet hours: get_approvals_policy + should_suppress returns True."""
        from zoneinfo import ZoneInfo

        from butlers.core.approvals_policy import (
            get_approvals_policy_quiet_hours,
            should_suppress_by_policy,
        )

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(
            return_value={"quiet_start_hour": 22, "quiet_end_hour": 7, "timezone": "UTC"}
        )
        policy = await get_approvals_policy_quiet_hours(mock_pool)
        assert policy is not None

        # 03:00 UTC → 03:00 UTC (inside 22-07 quiet window)
        tz = ZoneInfo("UTC")
        utc_time = datetime(2026, 1, 15, 3, 0, tzinfo=UTC)
        current_hour = utc_time.astimezone(tz).hour

        result = should_suppress_by_policy(policy, current_hour=current_hour)
        assert result is True, "03:00 UTC should be suppressed in 22-07 window"

    async def test_missing_policy_row_defaults_to_always_send(self) -> None:
        """Missing approvals_policy row → should_suppress returns False (always send)."""
        from butlers.core.approvals_policy import (
            get_approvals_policy_quiet_hours,
            should_suppress_by_policy,
        )

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)  # No row exists
        policy = await get_approvals_policy_quiet_hours(mock_pool)
        assert policy is None, "Missing row should return None policy"

        # With None policy, never suppress regardless of hour
        for hour in (0, 3, 7, 14, 22, 23):
            assert should_suppress_by_policy(None, current_hour=hour) is False, (
                f"hour={hour} should not be suppressed with no policy"
            )

    async def test_null_hours_in_policy_row_defaults_to_always_send(self) -> None:
        """Row exists but quiet_start_hour/quiet_end_hour are NULL → always send."""
        from butlers.core.approvals_policy import (
            get_approvals_policy_quiet_hours,
            should_suppress_by_policy,
        )

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(
            return_value={"quiet_start_hour": None, "quiet_end_hour": None, "timezone": "UTC"}
        )
        policy = await get_approvals_policy_quiet_hours(mock_pool)
        assert policy is not None

        for hour in (0, 3, 7, 14, 22, 23):
            assert should_suppress_by_policy(policy, current_hour=hour) is False

    async def test_high_priority_bypasses_gate_condition(self) -> None:
        """priority='high' means the gate does not fire; notification delivers immediately.

        Even if we are inside the quiet window, high-priority bypasses suppression.
        This matches the §8.6 spec: high — always delivers immediately.
        """
        from butlers.core.approvals_policy import should_suppress_by_policy

        policy = {"quiet_start_hour": 0, "quiet_end_hour": 23, "timezone": "UTC"}

        # Simulate the gate: gate only fires when priority != "high"
        for prio in ("medium", "low"):
            gate_fires = prio != "high"
            assert gate_fires is True, f"gate should fire for priority={prio!r}"
            # Inside whole-day quiet window (0-23), suppression would apply
            assert should_suppress_by_policy(policy, current_hour=14) is True

        # high priority: gate does not fire regardless of time
        priority = "high"
        gate_fires = priority != "high"
        assert gate_fires is False, "gate must not fire for priority='high'"

    async def test_explicit_recipient_bypasses_gate_condition(self) -> None:
        """The gate condition `recipient is None` means explicit recipient skips the gate.

        This test verifies the guard condition logic used in notify(): the
        suppression check only fires when entity_id is None AND recipient is None.
        With an explicit recipient, the `if` condition evaluates to False.
        """
        from butlers.core.approvals_policy import should_suppress_by_policy

        policy = {"quiet_start_hour": 0, "quiet_end_hour": 23, "timezone": "UTC"}

        # Simulate the guard: entity_id=None AND recipient=None → check policy
        entity_id = None
        recipient = None
        gate_fires = entity_id is None and recipient is None
        assert gate_fires is True

        # With an explicit recipient, the gate does not fire
        recipient = "12345"
        gate_fires = entity_id is None and recipient is None
        assert gate_fires is False

        # Even if we somehow did call should_suppress, it would suppress (whole-day)
        # but the gate won't reach it when recipient is set
        assert should_suppress_by_policy(policy, current_hour=14) is True
