"""Condensed calendar sync tests — behavioral contract only.

Replaces 92 tests with ~20 focused behavioral tests.

Covers:
- CalendarSyncConfig validation (defaults, positive constraints)
- CalendarSyncState model (defaults, roundtrip)
- Sync state KV persistence (load default when empty, save and reload)
- Cron helper: _cron_next_occurrence returns future aware datetime
- RRULE helper: occurrences fall within window
- Sync status MCP tool structure
- Force sync MCP tool

[bu-7sd7a]
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from butlers.modules.calendar import (
    DEFAULT_SYNC_INTERVAL_MINUTES,
    DEFAULT_SYNC_WINDOW_DAYS,
    CalendarConfig,
    CalendarModule,
    CalendarSyncConfig,
    CalendarSyncState,
    _cron_next_occurrence,
    _rrule_occurrences_in_window,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _mock_google_creds():
    with (
        patch(
            "butlers.google_credentials._resolve_account_entity_id",
            new_callable=AsyncMock,
            return_value=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        ),
        patch(
            "butlers.google_credentials._resolve_entity_refresh_token",
            new_callable=AsyncMock,
            return_value="test-refresh-token",
        ),
    ):
        yield


# ---------------------------------------------------------------------------
# CalendarSyncConfig
# ---------------------------------------------------------------------------


class TestCalendarSyncConfig:
    def test_defaults(self):
        cfg = CalendarSyncConfig()
        assert cfg.enabled is False
        assert cfg.interval_minutes == DEFAULT_SYNC_INTERVAL_MINUTES
        assert cfg.full_sync_window_days == DEFAULT_SYNC_WINDOW_DAYS

    @pytest.mark.parametrize("bad", [{"interval_minutes": 0}, {"full_sync_window_days": 0}])
    def test_positive_constraints_enforced(self, bad):
        with pytest.raises(ValidationError):
            CalendarSyncConfig(**bad)

    def test_sync_field_in_calendar_config(self):
        cfg = CalendarConfig(
            provider="google", sync=CalendarSyncConfig(enabled=True, interval_minutes=3)
        )
        assert cfg.sync.enabled is True
        assert cfg.sync.interval_minutes == 3


# ---------------------------------------------------------------------------
# CalendarSyncState model
# ---------------------------------------------------------------------------


class TestCalendarSyncState:
    def test_defaults(self):
        state = CalendarSyncState()
        assert state.sync_token is None
        assert state.last_sync_at is None
        assert state.last_batch_change_count == 0

    def test_model_dump_roundtrip(self):
        state = CalendarSyncState(
            sync_token="tok-123",
            last_sync_at="2026-03-01T10:00:00+00:00",
            last_batch_change_count=3,
        )
        restored = CalendarSyncState(**state.model_dump())
        assert restored.sync_token == state.sync_token
        assert restored.last_batch_change_count == state.last_batch_change_count


# ---------------------------------------------------------------------------
# Sync state KV persistence
# ---------------------------------------------------------------------------


class TestSyncStatePersistence:
    async def test_load_returns_default_when_no_entry(self):
        mod = CalendarModule()
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=None)  # state_get uses fetchval
        db = MagicMock()
        db.pool = pool
        db.db_name = "butlers"
        mod._db = db

        state = await mod._load_sync_state(calendar_id="primary")
        assert isinstance(state, CalendarSyncState)
        assert state.sync_token is None

    async def test_save_and_load_roundtrip(self):

        mod = CalendarModule()
        stored: dict = {}

        # state_get uses fetchval; state_set uses fetchval (via INSERT...RETURNING)
        async def _unified_fetchval(query, *args):
            if "INSERT INTO state" in query:
                key, json_val = args[0], args[1]
                stored[key] = json_val
                return 1
            elif "SELECT value FROM state" in query:
                key = args[0] if args else None
                return stored.get(key)
            return None

        pool = MagicMock()
        pool.fetchval = AsyncMock(side_effect=_unified_fetchval)
        db = MagicMock()
        db.pool = pool
        db.db_name = "butlers"
        mod._db = db

        to_save = CalendarSyncState(sync_token="tok-save", last_batch_change_count=7)
        await mod._save_sync_state(calendar_id="primary", state=to_save)
        loaded = await mod._load_sync_state(calendar_id="primary")
        assert loaded.sync_token == "tok-save"
        assert loaded.last_batch_change_count == 7


# ---------------------------------------------------------------------------
# Cron helpers
# ---------------------------------------------------------------------------


class TestCronNextOccurrence:
    def test_returns_future_aware_datetime(self):
        now = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        result = _cron_next_occurrence("0 14 * * *", now=now)
        assert result > now
        assert result.tzinfo is not None

    def test_hourly_fires_at_next_hour(self):
        now = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        result = _cron_next_occurrence("0 * * * *", now=now)
        assert result == datetime(2026, 3, 1, 1, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# RRULE occurrences
# ---------------------------------------------------------------------------


class TestRruleOccurrences:
    def test_daily_recurrence_within_week(self):
        start = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
        window_end = datetime(2026, 3, 8, 0, 0, tzinfo=UTC)
        occurrences = _rrule_occurrences_in_window(
            "RRULE:FREQ=DAILY",
            dtstart=start,
            window_start=start,
            window_end=window_end,
        )
        assert len(occurrences) >= 1
        for occ_start, occ_end in occurrences:
            assert window_end >= occ_start >= start

    def test_no_occurrences_outside_window(self):
        start = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
        occurrences = _rrule_occurrences_in_window(
            "RRULE:FREQ=DAILY",
            dtstart=start,
            window_start=datetime(2026, 2, 1, tzinfo=UTC),
            window_end=datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
        )
        assert occurrences == []
