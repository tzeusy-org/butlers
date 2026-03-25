"""Tests for Google Calendar connector.

Covers:
- CalendarProcessConfig.from_env() loading and per-account config overrides (task 5.4)
- Per-account asyncio poll loop spawning and error isolation (task 5.1)
- Dynamic account discovery and rescan (task 5.2)
- Graceful loop shutdown on account removal (task 5.3)
- IngestionPolicyEvaluator integration (task 6.1)
- Filtered event batch flush (task 6.2)
- StartingSoonSeenSet dedup, pruning, and restart recovery (task 6.4 / starting-soon logic)
- Aggregated health status (task 6.7)
- ingest.v1 envelope normalization helpers
- syncToken cursor lifecycle (load, save, 410 expiry)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.google_calendar import (
    AccountHealthStatus,
    CalendarAccountConfig,
    CalendarAccountLoop,
    CalendarConnectorManager,
    CalendarConnectorRuntime,
    CalendarProcessConfig,
    StartingSoonSeenSet,
    _build_ingest_envelope,
    _build_normalized_text,
    _parse_dt,
    _parse_event_end,
    _parse_event_start,
    _redact_email,
    _SyncTokenExpiredError,
)
from butlers.ingestion_policy import PolicyDecision

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def account_config() -> CalendarAccountConfig:
    """Create a minimal CalendarAccountConfig for testing."""
    return CalendarAccountConfig(
        email="test@example.com",
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-token",
        switchboard_mcp_url="http://localhost:41100/sse",
        poll_interval_s=60,
        starting_soon_lead_minutes=15,
        starting_soon_window_hours=2,
    )


@pytest.fixture
def process_config() -> CalendarProcessConfig:
    """Create a minimal CalendarProcessConfig for testing."""
    return CalendarProcessConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        poll_interval_s=60,
        account_rescan_interval_s=300,
    )


@pytest.fixture
def mock_db_pool() -> MagicMock:
    """Create a mock asyncpg pool."""
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()))
    return pool


# ---------------------------------------------------------------------------
# CalendarProcessConfig
# ---------------------------------------------------------------------------


class TestCalendarProcessConfig:
    """Tests for CalendarProcessConfig.from_env()."""

    def test_from_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that defaults are applied when env vars are not set."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.delenv("GCAL_POLL_INTERVAL_S", raising=False)
        monkeypatch.delenv("GCAL_ACCOUNT_RESCAN_INTERVAL_S", raising=False)

        config = CalendarProcessConfig.from_env()

        assert config.switchboard_mcp_url == "http://localhost:41100/sse"
        assert config.poll_interval_s == 60
        assert config.account_rescan_interval_s == 300
        assert config.starting_soon_lead_minutes == 15
        assert config.starting_soon_window_hours == 2

    def test_from_env_custom_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that custom env vars override defaults."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("GCAL_POLL_INTERVAL_S", "120")
        monkeypatch.setenv("GCAL_ACCOUNT_RESCAN_INTERVAL_S", "600")
        monkeypatch.setenv("GCAL_STARTING_SOON_LEAD_MINUTES", "30")
        monkeypatch.setenv("GCAL_STARTING_SOON_WINDOW_HOURS", "4")

        config = CalendarProcessConfig.from_env()

        assert config.poll_interval_s == 120
        assert config.account_rescan_interval_s == 600
        assert config.starting_soon_lead_minutes == 30
        assert config.starting_soon_window_hours == 4

    def test_from_env_missing_switchboard_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that missing SWITCHBOARD_MCP_URL raises ValueError."""
        monkeypatch.delenv("SWITCHBOARD_MCP_URL", raising=False)

        with pytest.raises(ValueError, match="SWITCHBOARD_MCP_URL is required"):
            CalendarProcessConfig.from_env()

    def test_from_env_extra_lead_minutes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test parsing of comma-separated extra lead times."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("GCAL_STARTING_SOON_EXTRA_LEAD_MINUTES", "5,30,60")

        config = CalendarProcessConfig.from_env()

        assert config.starting_soon_extra_lead_minutes == (5, 30, 60)

    def test_from_env_invalid_poll_interval_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that invalid int env vars fall back to defaults."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("GCAL_POLL_INTERVAL_S", "not-an-int")

        config = CalendarProcessConfig.from_env()

        assert config.poll_interval_s == 60  # default

    def test_make_account_config_applies_metadata_overrides(
        self, process_config: CalendarProcessConfig
    ) -> None:
        """Test that metadata.calendar overrides are applied to per-account config (task 5.4)."""
        metadata = {
            "poll_interval_s": 120,
            "starting_soon_lead_minutes": 30,
            "starting_soon_window_hours": 4,
        }
        account_cfg = process_config.make_account_config(
            email="user@example.com",
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            metadata_calendar=metadata,
        )
        assert account_cfg.poll_interval_s == 120
        assert account_cfg.starting_soon_lead_minutes == 30
        assert account_cfg.starting_soon_window_hours == 4

    def test_make_account_config_no_metadata_uses_process_defaults(
        self, process_config: CalendarProcessConfig
    ) -> None:
        """Test that absent metadata uses process-level defaults."""
        account_cfg = process_config.make_account_config(
            email="user@example.com",
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            metadata_calendar=None,
        )
        assert account_cfg.poll_interval_s == process_config.poll_interval_s


# ---------------------------------------------------------------------------
# CalendarAccountConfig
# ---------------------------------------------------------------------------


class TestCalendarAccountConfig:
    """Tests for CalendarAccountConfig properties."""

    def test_endpoint_identity(self, account_config: CalendarAccountConfig) -> None:
        assert account_config.endpoint_identity == "google_calendar:user:test@example.com"

    def test_cursor_key_same_as_endpoint_identity(
        self, account_config: CalendarAccountConfig
    ) -> None:
        assert account_config.cursor_key == account_config.endpoint_identity

    def test_all_lead_minutes_dedup_and_sort(self) -> None:
        """Test that all_lead_minutes deduplicates and sorts lead times."""
        config = CalendarAccountConfig(
            email="a@b.com",
            client_id="x",
            client_secret="y",
            refresh_token="z",
            switchboard_mcp_url="http://localhost/sse",
            starting_soon_lead_minutes=15,
            starting_soon_extra_lead_minutes=(5, 15, 30),
        )
        assert config.all_lead_minutes == [5, 15, 30]


# ---------------------------------------------------------------------------
# StartingSoonSeenSet
# ---------------------------------------------------------------------------


class TestStartingSoonSeenSet:
    """Tests for StartingSoonSeenSet dedup and pruning logic."""

    def test_has_seen_returns_false_initially(self) -> None:
        seen = StartingSoonSeenSet()
        assert not seen.has_seen("event-1", 15)

    def test_mark_seen_and_has_seen(self) -> None:
        seen = StartingSoonSeenSet()
        start_dt = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
        seen.mark_seen("event-1", 15, start_dt)
        assert seen.has_seen("event-1", 15)

    def test_different_lead_minutes_are_independent(self) -> None:
        seen = StartingSoonSeenSet()
        start_dt = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
        seen.mark_seen("event-1", 15, start_dt)
        # 30-minute lead not yet seen
        assert not seen.has_seen("event-1", 30)

    def test_prune_removes_past_events(self) -> None:
        seen = StartingSoonSeenSet()
        past_dt = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
        future_dt = datetime(2099, 1, 1, 9, 0, tzinfo=UTC)
        seen.mark_seen("past-event", 15, past_dt)
        seen.mark_seen("future-event", 15, future_dt)

        now = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
        pruned = seen.prune(now)

        assert pruned == 1
        assert not seen.has_seen("past-event", 15)
        assert seen.has_seen("future-event", 15)

    def test_prune_empty_set_returns_zero(self) -> None:
        seen = StartingSoonSeenSet()
        pruned = seen.prune(datetime.now(UTC))
        assert pruned == 0

    def test_len_matches_entry_count(self) -> None:
        seen = StartingSoonSeenSet()
        assert len(seen) == 0
        start_dt = datetime(2026, 6, 1, tzinfo=UTC)
        seen.mark_seen("e1", 15, start_dt)
        seen.mark_seen("e1", 30, start_dt)
        assert len(seen) == 2


# ---------------------------------------------------------------------------
# ingest.v1 envelope normalization helpers
# ---------------------------------------------------------------------------


class TestNormalizedTextBuilder:
    """Tests for _build_normalized_text."""

    def test_created_event(self) -> None:
        text = _build_normalized_text(
            change_type="created",
            summary="Team meeting",
            start_dt=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            end_dt=datetime(2026, 6, 1, 11, 0, tzinfo=UTC),
            organizer_email="organizer@example.com",
            attendees=["alice@example.com", "bob@example.com"],
        )
        assert "[CREATED]" in text
        assert "Team meeting" in text
        assert "2026-06-01" in text
        assert "organizer@example.com" in text
        assert "alice@example.com" in text

    def test_deleted_event_no_attendees(self) -> None:
        text = _build_normalized_text(
            change_type="deleted",
            summary="Cancelled call",
            start_dt=None,
            end_dt=None,
            organizer_email="unknown",
            attendees=[],
        )
        assert "[DELETED]" in text
        assert "Cancelled call" in text
        # unknown organizer should be omitted
        assert "Organizer" not in text

    def test_attendee_list_capped_at_10(self) -> None:
        attendees = [f"user{i}@example.com" for i in range(15)]
        text = _build_normalized_text(
            change_type="updated",
            summary="Big meeting",
            start_dt=None,
            end_dt=None,
            organizer_email="org@example.com",
            attendees=attendees,
        )
        assert "(+5 more)" in text


class TestBuildIngestEnvelope:
    """Tests for _build_ingest_envelope."""

    def test_schema_version(self) -> None:
        envelope = _build_ingest_envelope(
            event_id="evt-123",
            change_type="created",
            summary="Sync",
            event={"id": "evt-123"},
            endpoint_identity="google_calendar:user:a@b.com",
            observed_at="2026-06-01T10:00:00+00:00",
            organizer_email="a@b.com",
            normalized_text="[CREATED] Sync",
        )
        assert envelope["schema_version"] == "ingest.v1"

    def test_source_fields(self) -> None:
        envelope = _build_ingest_envelope(
            event_id="evt-1",
            change_type="updated",
            summary="Test",
            event={},
            endpoint_identity="google_calendar:user:test@test.com",
            observed_at="2026-01-01T00:00:00Z",
            organizer_email="org@test.com",
            normalized_text="[UPDATED] Test",
        )
        assert envelope["source"]["channel"] == "google_calendar"
        assert envelope["source"]["provider"] == "google_calendar"
        assert envelope["source"]["endpoint_identity"] == "google_calendar:user:test@test.com"

    def test_event_type_field(self) -> None:
        for change_type in ("created", "updated", "deleted"):
            envelope = _build_ingest_envelope(
                event_id="e",
                change_type=change_type,
                summary="X",
                event={},
                endpoint_identity="google_calendar:user:x@y.com",
                observed_at="2026-01-01T00:00:00Z",
                organizer_email="org@y.com",
                normalized_text=f"[{change_type.upper()}] X",
            )
            assert envelope["event"]["event_type"] == f"calendar.event.{change_type}"

    def test_policy_tier_default(self) -> None:
        envelope = _build_ingest_envelope(
            event_id="e",
            change_type="created",
            summary="X",
            event={},
            endpoint_identity="google_calendar:user:x@y.com",
            observed_at="2026-01-01T00:00:00Z",
            organizer_email="org@y.com",
            normalized_text="[CREATED] X",
        )
        assert envelope["control"]["policy_tier"] == "default"


# ---------------------------------------------------------------------------
# DateTime parsing helpers
# ---------------------------------------------------------------------------


class TestDateTimeParsing:
    """Tests for _parse_dt and event start/end extraction."""

    def test_parse_iso_with_timezone(self) -> None:
        dt = _parse_dt("2026-06-01T10:00:00+00:00")
        assert dt is not None
        assert dt.year == 2026
        assert dt.tzinfo is not None

    def test_parse_z_suffix(self) -> None:
        dt = _parse_dt("2026-06-01T10:00:00Z")
        assert dt is not None
        assert dt.year == 2026

    def test_parse_all_day_event(self) -> None:
        dt = _parse_dt("2026-06-01")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 1
        assert dt.tzinfo == UTC

    def test_parse_invalid_returns_none(self) -> None:
        assert _parse_dt("not-a-date") is None
        assert _parse_dt("") is None

    def test_parse_event_start_datetime(self) -> None:
        event = {"start": {"dateTime": "2026-06-01T10:00:00Z"}}
        dt = _parse_event_start(event)
        assert dt is not None
        assert dt.year == 2026

    def test_parse_event_start_date(self) -> None:
        event = {"start": {"date": "2026-06-01"}}
        dt = _parse_event_start(event)
        assert dt is not None

    def test_parse_event_start_missing(self) -> None:
        event: dict[str, Any] = {}
        assert _parse_event_start(event) is None

    def test_parse_event_end(self) -> None:
        event = {"end": {"dateTime": "2026-06-01T11:00:00Z"}}
        dt = _parse_event_end(event)
        assert dt is not None
        assert dt.hour == 11


# ---------------------------------------------------------------------------
# _redact_email
# ---------------------------------------------------------------------------


class TestRedactEmail:
    """Tests for _redact_email helper."""

    def test_redacts_local_part(self) -> None:
        result = _redact_email("alice@example.com")
        assert result == "al***@example.com"

    def test_short_local_part(self) -> None:
        result = _redact_email("a@example.com")
        assert result == "a***@example.com"

    def test_none_returns_none(self) -> None:
        assert _redact_email(None) is None

    def test_no_at_sign_returns_masked(self) -> None:
        result = _redact_email("noemail")
        assert result == "***"


# ---------------------------------------------------------------------------
# CalendarConnectorRuntime — policy evaluation and event processing
# ---------------------------------------------------------------------------


class TestCalendarConnectorRuntimePolicyEvaluation:
    """Tests for IngestionPolicyEvaluator integration in CalendarConnectorRuntime."""

    async def test_blocked_event_recorded_in_filtered_buffer(
        self, account_config: CalendarAccountConfig
    ) -> None:
        """Events blocked by policy are buffered, not ingested (task 6.1 + 6.2)."""
        runtime = CalendarConnectorRuntime(account_config)

        # Patch the policy evaluator to return 'block'
        block_decision = PolicyDecision(
            action="block",
            matched_rule_id="rule-1",
            matched_rule_type="sender_domain",
            reason="sender_domain match -> block",
        )
        runtime._ingestion_policy._rules = []  # ensure evaluate() runs
        with patch.object(
            runtime._ingestion_policy,
            "evaluate",
            return_value=block_decision,
        ):
            event = {
                "id": "evt-blocked",
                "status": "confirmed",
                "summary": "Blocked event",
                "start": {"dateTime": "2026-06-01T10:00:00Z"},
                "end": {"dateTime": "2026-06-01T11:00:00Z"},
                "created": "2026-01-01T00:00:00Z",
                "updated": "2026-01-02T00:00:00Z",
                "organizer": {"email": "blocked@example.com"},
            }
            ingested = await runtime._process_event(event)

        assert not ingested
        assert len(runtime._filtered_event_buffer) == 1

    async def test_allowed_event_submitted_to_switchboard(
        self, account_config: CalendarAccountConfig
    ) -> None:
        """Events allowed by policy are submitted to the Switchboard (task 6.1)."""
        runtime = CalendarConnectorRuntime(account_config)

        # Patch policy to allow
        allow_decision = PolicyDecision(
            action="pass_through",
            reason="no rule matched",
        )
        with patch.object(
            runtime._ingestion_policy,
            "evaluate",
            return_value=allow_decision,
        ):
            # Patch submission to succeed
            runtime._mcp_client = MagicMock()
            runtime._mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})

            event = {
                "id": "evt-allowed",
                "status": "confirmed",
                "summary": "Allowed event",
                "start": {"dateTime": "2026-06-01T10:00:00Z"},
                "end": {"dateTime": "2026-06-01T11:00:00Z"},
                "created": "2026-01-01T00:00:00Z",
                "updated": "2026-01-01T00:00:00Z",  # same as created → "created" type
                "organizer": {"email": "org@example.com"},
            }
            ingested = await runtime._process_event(event)

        assert ingested
        runtime._mcp_client.call_tool.assert_called_once()


# ---------------------------------------------------------------------------
# syncToken cursor lifecycle
# ---------------------------------------------------------------------------


class TestSyncTokenCursorLifecycle:
    """Tests for syncToken load, save, and 410 expiry handling."""

    async def test_ensure_sync_token_loads_from_cursor_store(
        self, account_config: CalendarAccountConfig
    ) -> None:
        """_ensure_sync_token() loads stored token and skips full sync."""
        runtime = CalendarConnectorRuntime(account_config)

        mock_pool = MagicMock()
        runtime._cursor_pool = mock_pool

        with patch(
            "butlers.connectors.google_calendar.load_cursor",
            new=AsyncMock(return_value="stored-sync-token"),
        ):
            await runtime._ensure_sync_token()

        assert runtime._sync_token == "stored-sync-token"

    async def test_ensure_sync_token_performs_full_sync_when_no_cursor(
        self, account_config: CalendarAccountConfig
    ) -> None:
        """When no cursor exists, _ensure_sync_token() calls _perform_full_sync."""
        runtime = CalendarConnectorRuntime(account_config)
        runtime._cursor_pool = MagicMock()

        with patch(
            "butlers.connectors.google_calendar.load_cursor",
            new=AsyncMock(return_value=None),
        ):
            with patch.object(runtime, "_perform_full_sync", new=AsyncMock()) as mock_full_sync:
                await runtime._ensure_sync_token()

        mock_full_sync.assert_called_once_with(ingest_events=False)

    async def test_save_sync_token_calls_cursor_store(
        self, account_config: CalendarAccountConfig
    ) -> None:
        """_save_sync_token() persists token via save_cursor."""
        runtime = CalendarConnectorRuntime(account_config)
        runtime._cursor_pool = MagicMock()

        with patch(
            "butlers.connectors.google_calendar.save_cursor",
            new=AsyncMock(),
        ) as mock_save:
            await runtime._save_sync_token("new-token")

        mock_save.assert_called_once()
        assert runtime._last_checkpoint_save is not None

    async def test_incremental_sync_raises_on_410(
        self, account_config: CalendarAccountConfig
    ) -> None:
        """_incremental_sync() raises _SyncTokenExpiredError on 410 Gone."""
        runtime = CalendarConnectorRuntime(account_config)
        runtime._http_client = MagicMock()

        with patch.object(
            runtime,
            "_gcal_api_get",
            side_effect=_SyncTokenExpiredError("syncToken expired (410 Gone)"),
        ):
            with pytest.raises(_SyncTokenExpiredError):
                await runtime._incremental_sync("expired-token")

    async def test_poll_cycle_falls_back_to_full_sync_on_410(
        self, account_config: CalendarAccountConfig
    ) -> None:
        """When _incremental_sync raises _SyncTokenExpiredError, a full sync is performed."""
        runtime = CalendarConnectorRuntime(account_config)
        runtime._sync_token = "old-token"

        with patch.object(
            runtime,
            "_incremental_sync",
            side_effect=_SyncTokenExpiredError("expired"),
        ):
            with patch.object(runtime, "_perform_full_sync", new=AsyncMock()) as mock_full:
                await runtime._run_one_poll_cycle()

        mock_full.assert_called_once_with(ingest_events=True)


# ---------------------------------------------------------------------------
# CalendarAccountLoop — error isolation
# ---------------------------------------------------------------------------


class TestCalendarAccountLoop:
    """Tests for per-account loop spawning and error isolation (task 5.1)."""

    async def test_loop_starts_and_task_is_created(
        self, account_config: CalendarAccountConfig
    ) -> None:
        """start() creates an asyncio task for the account."""
        loop = CalendarAccountLoop(
            email=account_config.email,
            config=account_config,
            db_pool=None,
            cursor_pool=None,
        )

        with patch.object(loop._runtime, "start", new=AsyncMock(return_value=None)):
            loop.start()
            assert loop._task is not None
            # Give the event loop a chance to start the task
            await asyncio.sleep(0)
            await loop.stop()

    async def test_loop_error_is_isolated_in_error_attribute(
        self, account_config: CalendarAccountConfig
    ) -> None:
        """Runtime exceptions are captured in _error, not propagated to caller."""
        loop = CalendarAccountLoop(
            email=account_config.email,
            config=account_config,
            db_pool=None,
            cursor_pool=None,
        )

        with patch.object(
            loop._runtime,
            "start",
            new=AsyncMock(side_effect=RuntimeError("api-down")),
        ):
            loop.start()
            await asyncio.sleep(0.01)  # let the task fail

        # Error should be captured in _error, task should be done
        assert loop._task is not None and loop._task.done()
        assert loop._error == "api-down"
        assert not loop.is_running

    def test_get_health_returns_error_when_loop_stopped_with_error(
        self, account_config: CalendarAccountConfig
    ) -> None:
        """get_health() returns error status when loop has failed."""
        loop = CalendarAccountLoop(
            email=account_config.email,
            config=account_config,
            db_pool=None,
            cursor_pool=None,
        )
        loop._error = "something went wrong"

        health = loop.get_health()

        assert health.status == "error"
        assert health.error == "something went wrong"

    def test_get_health_redacts_email(self, account_config: CalendarAccountConfig) -> None:
        """get_health() redacts the email address in health status."""
        loop = CalendarAccountLoop(
            email="alice@example.com",
            config=account_config,
            db_pool=None,
            cursor_pool=None,
        )
        health = loop.get_health()
        assert health.email == "al***@example.com"


# ---------------------------------------------------------------------------
# CalendarConnectorManager — account lifecycle and health aggregation
# ---------------------------------------------------------------------------


class TestCalendarConnectorManager:
    """Tests for dynamic account discovery, rescan, and aggregated health (tasks 5.2, 5.3, 6.7)."""

    def _make_manager(
        self,
        process_config: CalendarProcessConfig,
        db_rows: list[dict[str, Any]] | None = None,
    ) -> CalendarConnectorManager:
        """Create a manager with mocked DB pool."""
        mock_pool = AsyncMock()

        async def _fetch(*args: Any, **kwargs: Any) -> list[Any]:
            if db_rows is None:
                return []
            return [MagicMock(**row) for row in db_rows]

        mock_pool.acquire.return_value.__aenter__.return_value.fetch = _fetch
        # Also support pool.fetch directly (used in _discover_qualifying_accounts)
        mock_pool.fetch = AsyncMock(return_value=[])

        return CalendarConnectorManager(
            process_config=process_config,
            db_pool=mock_pool,
            cursor_pool=None,
        )

    async def test_discover_qualifying_accounts_empty(
        self, process_config: CalendarProcessConfig
    ) -> None:
        """When no accounts exist, qualifying list is empty."""
        manager = self._make_manager(process_config)
        qualifying = await manager._discover_qualifying_accounts()
        assert qualifying == []

    async def test_sync_accounts_starts_new_loop(
        self, process_config: CalendarProcessConfig
    ) -> None:
        """_sync_accounts() starts loops for new qualifying accounts (task 5.2)."""
        manager = self._make_manager(process_config)

        # Mock qualifying accounts to return one account
        with patch.object(
            manager,
            "_discover_qualifying_accounts",
            new=AsyncMock(return_value=[("user@example.com", None)]),
        ):
            with patch.object(
                manager,
                "_resolve_credentials_for_account",
                new=AsyncMock(
                    return_value={
                        "client_id": "cid",
                        "client_secret": "cs",
                        "refresh_token": "rt",
                    }
                ),
            ):
                with patch.object(CalendarAccountLoop, "start", return_value=None):
                    added, removed, unchanged = await manager._sync_accounts()

        assert "user@example.com" in added
        assert "user@example.com" in manager._loops

    async def test_sync_accounts_removes_stale_loop(
        self, process_config: CalendarProcessConfig
    ) -> None:
        """_sync_accounts() stops loops for accounts no longer in qualifying set (task 5.3)."""
        manager = self._make_manager(process_config)

        # Pre-populate with a loop
        mock_loop = AsyncMock(spec=CalendarAccountLoop)
        mock_loop.is_running = True
        manager._loops["stale@example.com"] = mock_loop

        with patch.object(
            manager,
            "_discover_qualifying_accounts",
            new=AsyncMock(return_value=[]),  # no accounts → all removed
        ):
            added, removed, unchanged = await manager._sync_accounts()

        assert "stale@example.com" in removed
        assert "stale@example.com" not in manager._loops
        mock_loop.stop.assert_called_once()

    def test_get_multi_account_health_no_accounts_is_degraded(
        self, process_config: CalendarProcessConfig
    ) -> None:
        """Aggregated health is 'degraded' when no accounts are running (task 6.7)."""
        manager = self._make_manager(process_config)
        health = manager._get_multi_account_health()
        assert health.status == "degraded"
        assert health.active_accounts == 0

    def test_get_multi_account_health_worst_case_aggregation(
        self, process_config: CalendarProcessConfig
    ) -> None:
        """Aggregated health takes the worst status across all accounts (task 6.7)."""
        manager = self._make_manager(process_config)

        # Create mock loops with known health
        def _make_mock_loop(status: str) -> MagicMock:
            m = MagicMock(spec=CalendarAccountLoop)
            m.get_health.return_value = AccountHealthStatus(
                email="te***@example.com",
                endpoint_identity="google_calendar:user:test@example.com",
                status=status,
                last_checkpoint_save_at=None,
                last_ingest_submit_at=None,
                source_api_connectivity="unknown",
            )
            return m

        # One healthy, one error → overall should be "error"
        manager._loops = {
            "a@example.com": _make_mock_loop("healthy"),
            "b@example.com": _make_mock_loop("error"),
        }

        health = manager._get_multi_account_health()

        assert health.status == "error"
        assert health.active_accounts == 2

    def test_get_multi_account_health_all_healthy(
        self, process_config: CalendarProcessConfig
    ) -> None:
        """Aggregated health is 'healthy' when all accounts are healthy."""
        manager = self._make_manager(process_config)

        def _make_mock_loop(status: str) -> MagicMock:
            m = MagicMock(spec=CalendarAccountLoop)
            m.get_health.return_value = AccountHealthStatus(
                email="te***@example.com",
                endpoint_identity="google_calendar:user:test@example.com",
                status=status,
                last_checkpoint_save_at=None,
                last_ingest_submit_at=None,
                source_api_connectivity="connected",
            )
            return m

        manager._loops = {
            "a@example.com": _make_mock_loop("healthy"),
            "b@example.com": _make_mock_loop("healthy"),
        }

        health = manager._get_multi_account_health()
        assert health.status == "healthy"


# ---------------------------------------------------------------------------
# "Starting soon" notification tests
# ---------------------------------------------------------------------------


class TestStartingSoonNotifications:
    """Tests for starting-soon synthetic notification logic."""

    async def test_starting_soon_emitted_within_window(
        self, account_config: CalendarAccountConfig
    ) -> None:
        """A starting-soon notification is emitted when event is within lead window."""
        runtime = CalendarConnectorRuntime(account_config)

        # Place an event that starts in 10 minutes (within 15-minute lead)
        now = datetime.now(UTC)
        start_dt = now + timedelta(minutes=10)
        runtime._upcoming_events["evt-soon"] = ("Team sync", start_dt)

        runtime._mcp_client = MagicMock()
        runtime._mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})

        await runtime._check_starting_soon()

        runtime._mcp_client.call_tool.assert_called_once()
        assert runtime._seen_set.has_seen("evt-soon", 15)

    async def test_starting_soon_not_emitted_twice(
        self, account_config: CalendarAccountConfig
    ) -> None:
        """Dedup: a starting-soon notification is NOT emitted twice for the same event."""
        runtime = CalendarConnectorRuntime(account_config)

        now = datetime.now(UTC)
        start_dt = now + timedelta(minutes=10)
        runtime._upcoming_events["evt-soon"] = ("Team sync", start_dt)
        runtime._seen_set.mark_seen("evt-soon", 15, start_dt)  # already seen

        runtime._mcp_client = MagicMock()
        runtime._mcp_client.call_tool = AsyncMock()

        await runtime._check_starting_soon()

        runtime._mcp_client.call_tool.assert_not_called()

    async def test_starting_soon_not_emitted_for_future_event_outside_window(
        self, account_config: CalendarAccountConfig
    ) -> None:
        """No notification emitted for events starting far in the future."""
        runtime = CalendarConnectorRuntime(account_config)

        now = datetime.now(UTC)
        start_dt = now + timedelta(hours=3)  # 3 hours away, lead=15m
        runtime._upcoming_events["evt-far"] = ("Far event", start_dt)

        runtime._mcp_client = MagicMock()
        runtime._mcp_client.call_tool = AsyncMock()

        await runtime._check_starting_soon()

        runtime._mcp_client.call_tool.assert_not_called()

    async def test_starting_soon_emitted_for_overdue_not_yet_started(
        self, account_config: CalendarAccountConfig
    ) -> None:
        """On restart, overdue notifications for events that haven't started yet are emitted."""
        runtime = CalendarConnectorRuntime(account_config)

        now = datetime.now(UTC)
        # Event starts in 5 minutes (past the 15-minute lead, but not yet started)
        start_dt = now + timedelta(minutes=5)
        runtime._upcoming_events["evt-overdue"] = ("Overdue event", start_dt)

        runtime._mcp_client = MagicMock()
        runtime._mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})

        await runtime._check_starting_soon()

        # Should emit notification because now <= start_dt and we're within the poll interval
        runtime._mcp_client.call_tool.assert_called_once()
        assert runtime._seen_set.has_seen("evt-overdue", 15)
