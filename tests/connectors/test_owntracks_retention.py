"""Tests for OwnTracks data retention logic (tasks 5.1–5.5)."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.owntracks import (
    DEFAULT_RETENTION_DAYS,
    MIN_RETENTION_DAYS,
    RETENTION_PURGE_INTERVAL_S,
    OwnTracksRetention,
    OwnTracksRetentionConfig,
    _parse_delete_count,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# OwnTracksRetentionConfig tests
# ---------------------------------------------------------------------------


class TestOwnTracksRetentionConfig:
    """Tests for retention configuration loading and validation."""

    def test_default_retention_days(self) -> None:
        """Default retention period is DEFAULT_RETENTION_DAYS when env var is absent."""
        config = OwnTracksRetentionConfig()
        assert config.retention_days == DEFAULT_RETENTION_DAYS

    def test_default_is_30_days(self) -> None:
        """Default retention period is exactly 30 days per spec."""
        assert DEFAULT_RETENTION_DAYS == 30

    def test_min_is_1_day(self) -> None:
        """Minimum retention period is 1 day per spec."""
        assert MIN_RETENTION_DAYS == 1

    def test_from_env_uses_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env() returns default when OWNTRACKS_RETENTION_DAYS is absent."""
        monkeypatch.delenv("OWNTRACKS_RETENTION_DAYS", raising=False)
        config = OwnTracksRetentionConfig.from_env()
        assert config.retention_days == DEFAULT_RETENTION_DAYS

    def test_from_env_reads_positive_integer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env() reads OWNTRACKS_RETENTION_DAYS as a positive integer."""
        monkeypatch.setenv("OWNTRACKS_RETENTION_DAYS", "90")
        config = OwnTracksRetentionConfig.from_env()
        assert config.retention_days == 90

    def test_from_env_accepts_minimum_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env() accepts the minimum value of 1."""
        monkeypatch.setenv("OWNTRACKS_RETENTION_DAYS", "1")
        config = OwnTracksRetentionConfig.from_env()
        assert config.retention_days == 1

    def test_from_env_rejects_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env() raises ValueError when OWNTRACKS_RETENTION_DAYS=0."""
        monkeypatch.setenv("OWNTRACKS_RETENTION_DAYS", "0")
        with pytest.raises(ValueError, match="OWNTRACKS_RETENTION_DAYS"):
            OwnTracksRetentionConfig.from_env()

    def test_from_env_rejects_negative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env() raises ValueError when OWNTRACKS_RETENTION_DAYS is negative."""
        monkeypatch.setenv("OWNTRACKS_RETENTION_DAYS", "-10")
        with pytest.raises(ValueError, match="OWNTRACKS_RETENTION_DAYS"):
            OwnTracksRetentionConfig.from_env()

    def test_from_env_rejects_non_integer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env() raises ValueError when OWNTRACKS_RETENTION_DAYS is not an integer."""
        monkeypatch.setenv("OWNTRACKS_RETENTION_DAYS", "thirty")
        with pytest.raises(ValueError, match="OWNTRACKS_RETENTION_DAYS"):
            OwnTracksRetentionConfig.from_env()

    def test_from_env_rejects_float_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env() raises ValueError when OWNTRACKS_RETENTION_DAYS is a float."""
        monkeypatch.setenv("OWNTRACKS_RETENTION_DAYS", "30.5")
        with pytest.raises(ValueError, match="OWNTRACKS_RETENTION_DAYS"):
            OwnTracksRetentionConfig.from_env()


# ---------------------------------------------------------------------------
# _parse_delete_count tests
# ---------------------------------------------------------------------------


class TestParseDeleteCount:
    """Tests for the asyncpg DELETE status string parser."""

    def test_parses_normal_delete(self) -> None:
        """Parses 'DELETE 42' correctly."""
        assert _parse_delete_count("DELETE 42") == 42

    def test_parses_zero_deletes(self) -> None:
        """Parses 'DELETE 0' correctly."""
        assert _parse_delete_count("DELETE 0") == 0

    def test_parses_large_count(self) -> None:
        """Parses a large delete count correctly."""
        assert _parse_delete_count("DELETE 999999") == 999999

    def test_returns_zero_for_unexpected_format(self) -> None:
        """Returns 0 for unexpected status strings."""
        assert _parse_delete_count("SELECT 5") == 0
        assert _parse_delete_count("") == 0
        assert _parse_delete_count("DELETE") == 0

    def test_returns_zero_for_none_like_input(self) -> None:
        """Returns 0 when parsing fails due to attribute errors."""
        assert _parse_delete_count("bad input xyz") == 0


# ---------------------------------------------------------------------------
# OwnTracksRetention purge_once tests
# ---------------------------------------------------------------------------


def _make_pool(execute_result: str = "DELETE 0") -> MagicMock:
    """Build a fake asyncpg pool whose connections return a preset execute result."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=execute_result)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncContextManager(conn))
    return pool, conn


class _AsyncContextManager:
    """Minimal async context manager wrapper for a connection mock."""

    def __init__(self, value: object) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(self, *args: object) -> None:
        pass


class TestOwnTracksRetentionPurgeOnce:
    """Unit tests for OwnTracksRetention.purge_once()."""

    async def test_purge_once_returns_deleted_count(self) -> None:
        """purge_once() returns the number of rows deleted."""
        pool, conn = _make_pool("DELETE 17")
        config = OwnTracksRetentionConfig(retention_days=30)
        retention = OwnTracksRetention(config, pool)

        deleted = await retention.purge_once()

        assert deleted == 17

    async def test_purge_once_returns_zero_when_nothing_deleted(self) -> None:
        """purge_once() returns 0 when no rows are deleted."""
        pool, conn = _make_pool("DELETE 0")
        config = OwnTracksRetentionConfig(retention_days=30)
        retention = OwnTracksRetention(config, pool)

        deleted = await retention.purge_once()

        assert deleted == 0

    async def test_purge_once_uses_correct_retention_days(self) -> None:
        """purge_once() passes the configured retention_days into the SQL."""
        pool, conn = _make_pool("DELETE 5")
        config = OwnTracksRetentionConfig(retention_days=90)
        retention = OwnTracksRetention(config, pool)

        await retention.purge_once()

        # Verify the SQL contains the correct interval
        executed_sql: str = conn.execute.call_args[0][0]
        assert "90 days" in executed_sql

    async def test_purge_once_filters_owntracks_channel(self) -> None:
        """purge_once() SQL restricts to source_channel = 'owntracks'."""
        pool, conn = _make_pool("DELETE 1")
        config = OwnTracksRetentionConfig(retention_days=30)
        retention = OwnTracksRetention(config, pool)

        await retention.purge_once()

        executed_sql: str = conn.execute.call_args[0][0]
        assert "source_channel = 'owntracks'" in executed_sql

    async def test_purge_once_targets_received_at(self) -> None:
        """purge_once() SQL uses received_at column for the age comparison."""
        pool, conn = _make_pool("DELETE 0")
        config = OwnTracksRetentionConfig(retention_days=30)
        retention = OwnTracksRetention(config, pool)

        await retention.purge_once()

        executed_sql: str = conn.execute.call_args[0][0]
        assert "received_at" in executed_sql

    async def test_purge_once_targets_shared_ingestion_events(self) -> None:
        """purge_once() SQL targets shared.ingestion_events."""
        pool, conn = _make_pool("DELETE 0")
        config = OwnTracksRetentionConfig(retention_days=30)
        retention = OwnTracksRetention(config, pool)

        await retention.purge_once()

        executed_sql: str = conn.execute.call_args[0][0]
        assert "shared.ingestion_events" in executed_sql

    async def test_purge_once_propagates_db_errors(self) -> None:
        """purge_once() re-raises database exceptions."""
        conn = AsyncMock()
        conn.execute = AsyncMock(side_effect=RuntimeError("DB unavailable"))
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncContextManager(conn))

        config = OwnTracksRetentionConfig(retention_days=30)
        retention = OwnTracksRetention(config, pool)

        with pytest.raises(RuntimeError, match="DB unavailable"):
            await retention.purge_once()


# ---------------------------------------------------------------------------
# OwnTracksRetention logging tests
# ---------------------------------------------------------------------------


class TestOwnTracksRetentionLogging:
    """Tests that the retention task logs at the correct levels."""

    async def test_successful_purge_logged_at_info(self, caplog: pytest.LogCaptureFixture) -> None:
        """A successful purge cycle logs deleted count at INFO."""
        pool, _conn = _make_pool("DELETE 42")
        config = OwnTracksRetentionConfig(retention_days=7)
        retention = OwnTracksRetention(config, pool)

        with caplog.at_level(logging.INFO, logger="butlers.connectors.owntracks"):
            await retention._run_purge()

        assert any("42" in r.message for r in caplog.records)
        assert any(r.levelno == logging.INFO for r in caplog.records)

    async def test_failed_purge_logged_at_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A purge failure is logged at WARNING without re-raising."""
        conn = AsyncMock()
        conn.execute = AsyncMock(side_effect=OSError("connection lost"))
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncContextManager(conn))

        config = OwnTracksRetentionConfig(retention_days=30)
        retention = OwnTracksRetention(config, pool)

        with caplog.at_level(logging.WARNING, logger="butlers.connectors.owntracks"):
            # Must NOT raise
            await retention._run_purge()

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) >= 1
        assert any(
            "purge" in r.message.lower() or "failed" in r.message.lower() for r in warning_records
        )

    async def test_failed_purge_does_not_raise(self) -> None:
        """_run_purge() swallows exceptions so the connector does not crash."""
        conn = AsyncMock()
        conn.execute = AsyncMock(side_effect=RuntimeError("boom"))
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncContextManager(conn))

        config = OwnTracksRetentionConfig(retention_days=30)
        retention = OwnTracksRetention(config, pool)

        # Should not raise
        await retention._run_purge()


# ---------------------------------------------------------------------------
# OwnTracksRetention lifecycle tests (start/stop)
# ---------------------------------------------------------------------------


class TestOwnTracksRetentionLifecycle:
    """Tests for the background task start/stop lifecycle."""

    async def test_start_creates_background_task(self) -> None:
        """start() schedules a background asyncio task."""
        pool, _conn = _make_pool("DELETE 0")
        config = OwnTracksRetentionConfig(retention_days=30)
        retention = OwnTracksRetention(config, pool, purge_interval_s=9999)

        retention.start()
        try:
            assert retention._task is not None
            assert not retention._task.done()
        finally:
            await retention.stop()

    async def test_start_idempotent(self) -> None:
        """Calling start() twice does not create a second task."""
        pool, _conn = _make_pool("DELETE 0")
        config = OwnTracksRetentionConfig(retention_days=30)
        retention = OwnTracksRetention(config, pool, purge_interval_s=9999)

        retention.start()
        first_task = retention._task
        retention.start()  # second call — should be ignored
        try:
            assert retention._task is first_task
        finally:
            await retention.stop()

    async def test_stop_cancels_task(self) -> None:
        """stop() cancels the background task and clears the reference."""
        pool, _conn = _make_pool("DELETE 0")
        config = OwnTracksRetentionConfig(retention_days=30)
        retention = OwnTracksRetention(config, pool, purge_interval_s=9999)

        retention.start()
        task = retention._task
        assert task is not None

        await retention.stop()

        assert retention._task is None
        assert task.cancelled()

    async def test_stop_when_not_started_is_safe(self) -> None:
        """stop() is a no-op when the task was never started."""
        pool, _conn = _make_pool("DELETE 0")
        config = OwnTracksRetentionConfig(retention_days=30)
        retention = OwnTracksRetention(config, pool)

        # Should not raise
        await retention.stop()

    async def test_purge_runs_after_interval(self) -> None:
        """The purge loop calls purge_once() after the configured interval elapses."""
        pool, conn = _make_pool("DELETE 3")
        config = OwnTracksRetentionConfig(retention_days=30)
        # Use a very short interval for the test
        retention = OwnTracksRetention(config, pool, purge_interval_s=0)

        retention.start()
        # Give the event loop a brief moment to execute the sleep(0) and the purge
        await asyncio.sleep(0.05)
        await retention.stop()

        # The purge SQL should have been executed at least once
        assert conn.execute.call_count >= 1

    async def test_purge_interval_is_6_hours(self) -> None:
        """Default purge interval constant is 6 hours (21600 seconds)."""
        assert RETENTION_PURGE_INTERVAL_S == 6 * 60 * 60

    def test_retention_days_property(self) -> None:
        """retention_days property reflects the configured value."""
        pool = MagicMock()
        config = OwnTracksRetentionConfig(retention_days=45)
        retention = OwnTracksRetention(config, pool)
        assert retention.retention_days == 45
