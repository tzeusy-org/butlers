"""Unit tests for calendar module sync polling system (spec section 10).

Covers:
- CalendarSyncConfig defaults and validation
- CalendarSyncState model
- Sync state KV store persistence (_load_sync_state, _save_sync_state)
- _GoogleProvider.sync_incremental: incremental flow, full-sync fallback,
  pagination, 410 token-expired handling
- CalendarModule._sync_calendar: happy path, token expiry re-sync, error
  handling and state persistence
- CalendarModule._run_sync_poller: interval scheduling, force-sync signal
- calendar_sync_status MCP tool
- calendar_force_sync MCP tool
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.modules.calendar import (
    DEFAULT_SYNC_INTERVAL_MINUTES,
    DEFAULT_SYNC_WINDOW_DAYS,
    SYNC_STATE_KEY_PREFIX,
    CalendarAuthError,
    CalendarConfig,
    CalendarEvent,
    CalendarModule,
    CalendarProvider,
    CalendarRequestError,
    CalendarSyncConfig,
    CalendarSyncState,
    CalendarSyncTokenExpiredError,
    _GoogleProvider,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers and fixtures
# ---------------------------------------------------------------------------

GOOGLE_CREDS_JSON = json.dumps(
    {
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "refresh_token": "test-refresh-token",
    }
)


def _make_sample_event(event_id: str = "evt-001") -> CalendarEvent:
    return CalendarEvent(
        event_id=event_id,
        title="BUTLER: Sample Event",
        start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
        timezone="UTC",
        butler_generated=True,
        butler_name="general",
    )


def _make_google_event_payload(event_id: str, status: str = "confirmed") -> dict[str, Any]:
    return {
        "id": event_id,
        "status": status,
        "summary": "Test Event",
        "start": {"dateTime": "2026-03-01T10:00:00Z"},
        "end": {"dateTime": "2026-03-01T11:00:00Z"},
    }


def _make_mock_db(state_store: dict | None = None) -> MagicMock:
    """Return a mock Database object with a pool that stores state in-memory."""
    store: dict[str, Any] = state_store if state_store is not None else {}

    async def mock_fetchval(sql: str, key: str, *args: Any) -> Any:
        if "SELECT value FROM state" in sql:
            val = store.get(key)
            if isinstance(val, dict):
                return json.dumps(val)
            return val
        return None

    async def mock_execute(sql: str, key: str, *args: Any) -> None:
        if "INSERT INTO state" in sql or "ON CONFLICT" in sql:
            value_str = args[0] if args else None
            if value_str is not None:
                store[key] = json.loads(value_str)

    pool = AsyncMock()
    pool.fetchval = AsyncMock(side_effect=mock_fetchval)
    pool.execute = AsyncMock(side_effect=mock_execute)

    db = MagicMock()
    db.pool = pool
    db.db_name = "butler_test"
    return db


class _StubMCP:
    """Minimal MCP stub that records registered tool functions."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class _SyncCapableProviderDouble(CalendarProvider):
    """Provider test double with controllable sync_incremental behavior.

    ``call_side_effects`` is a list of outcomes per call (in order):
    each entry is either an Exception to raise or a result tuple to return.
    When exhausted, the last entry is repeated.  Takes precedence over
    the other convenience parameters.
    """

    def __init__(
        self,
        *,
        sync_result: tuple[list[CalendarEvent], list[str], str] | None = None,
        sync_error: Exception | None = None,
        call_side_effects: list[Exception | tuple] | None = None,
    ) -> None:
        # Default: return empty sync with a stub token.
        self._sync_result = sync_result or ([], [], "next-token-123")
        self._sync_error = sync_error
        self._call_side_effects = call_side_effects
        self.sync_calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "stub"

    async def list_events(self, *, calendar_id: str, **kwargs: Any) -> list[CalendarEvent]:
        return []

    async def get_event(self, *, calendar_id: str, event_id: str) -> CalendarEvent | None:
        return None

    async def create_event(self, *, calendar_id: str, payload: Any) -> CalendarEvent:
        raise NotImplementedError

    async def update_event(self, *, calendar_id: str, event_id: str, patch: Any) -> CalendarEvent:
        raise NotImplementedError

    async def delete_event(self, *, calendar_id: str, event_id: str, **kwargs: Any) -> None:
        raise NotImplementedError

    async def find_conflicts(self, *, calendar_id: str, candidate: Any) -> list[CalendarEvent]:
        return []

    async def add_attendees(
        self,
        *,
        calendar_id: str,
        event_id: str,
        attendees: list[str],
        optional: bool = False,
        send_updates: str = "none",
    ) -> CalendarEvent:
        raise NotImplementedError

    async def remove_attendees(
        self,
        *,
        calendar_id: str,
        event_id: str,
        attendees: list[str],
        send_updates: str = "none",
    ) -> CalendarEvent:
        raise NotImplementedError

    async def sync_incremental(
        self,
        *,
        calendar_id: str,
        sync_token: str | None,
        full_sync_window_days: int = DEFAULT_SYNC_WINDOW_DAYS,
    ) -> tuple[list[CalendarEvent], list[str], str]:
        self.sync_calls.append(
            {
                "calendar_id": calendar_id,
                "sync_token": sync_token,
                "full_sync_window_days": full_sync_window_days,
            }
        )
        call_index = len(self.sync_calls) - 1

        if self._call_side_effects is not None:
            # Use the indexed entry (clamp to last if exhausted).
            idx = min(call_index, len(self._call_side_effects) - 1)
            effect = self._call_side_effects[idx]
            if isinstance(effect, Exception):
                raise effect
            return effect  # type: ignore[return-value]

        if self._sync_error is not None:
            raise self._sync_error
        return self._sync_result

    async def shutdown(self) -> None:
        return None


# ---------------------------------------------------------------------------
# CalendarSyncConfig tests
# ---------------------------------------------------------------------------


class TestCalendarSyncConfig:
    def test_defaults(self):
        cfg = CalendarSyncConfig()
        assert cfg.enabled is False
        assert cfg.interval_minutes == DEFAULT_SYNC_INTERVAL_MINUTES
        assert cfg.full_sync_window_days == DEFAULT_SYNC_WINDOW_DAYS

    def test_enabled_true(self):
        cfg = CalendarSyncConfig(enabled=True)
        assert cfg.enabled is True

    def test_custom_interval(self):
        cfg = CalendarSyncConfig(interval_minutes=10, full_sync_window_days=60)
        assert cfg.interval_minutes == 10
        assert cfg.full_sync_window_days == 60

    def test_interval_must_be_positive(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CalendarSyncConfig(interval_minutes=0)

    def test_window_days_must_be_positive(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CalendarSyncConfig(full_sync_window_days=0)

    def test_extra_fields_forbidden(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CalendarSyncConfig(unknown_field="oops")


class TestCalendarConfigSyncField:
    def test_sync_defaults_to_disabled(self):
        cfg = CalendarConfig(provider="google", calendar_id="primary")
        assert cfg.sync.enabled is False

    def test_sync_can_be_enabled_in_config(self):
        cfg = CalendarConfig(
            provider="google",
            calendar_id="primary",
            sync=CalendarSyncConfig(enabled=True, interval_minutes=3),
        )
        assert cfg.sync.enabled is True
        assert cfg.sync.interval_minutes == 3


# ---------------------------------------------------------------------------
# CalendarSyncState tests
# ---------------------------------------------------------------------------


class TestCalendarSyncState:
    def test_defaults(self):
        state = CalendarSyncState()
        assert state.sync_token is None
        assert state.last_sync_at is None
        assert state.last_sync_error is None
        assert state.last_batch_change_count == 0

    def test_with_values(self):
        state = CalendarSyncState(
            sync_token="tok-abc",
            last_sync_at="2026-03-01T10:00:00+00:00",
            last_sync_error=None,
            last_batch_change_count=5,
        )
        assert state.sync_token == "tok-abc"
        assert state.last_batch_change_count == 5

    def test_model_dump_roundtrip(self):
        state = CalendarSyncState(
            sync_token="tok-123",
            last_sync_at="2026-03-01T10:00:00+00:00",
            last_batch_change_count=3,
        )
        dumped = state.model_dump()
        restored = CalendarSyncState(**dumped)
        assert restored.sync_token == state.sync_token
        assert restored.last_sync_at == state.last_sync_at
        assert restored.last_batch_change_count == state.last_batch_change_count

    def test_extra_fields_ignored(self):
        # extra="ignore" should silently discard unknown fields.
        state = CalendarSyncState(sync_token="t", unknown="ignored")
        assert state.sync_token == "t"
        assert not hasattr(state, "unknown")


# ---------------------------------------------------------------------------
# Sync state KV persistence tests
# ---------------------------------------------------------------------------


class TestCalendarModuleSyncStatePersistence:
    async def test_load_sync_state_returns_default_when_no_entry(self):
        mod = CalendarModule()
        mod._db = _make_mock_db({})
        state = await mod._load_sync_state("cal@example.com")
        assert state.sync_token is None
        assert state.last_sync_at is None

    async def test_load_sync_state_returns_persisted_values(self):
        calendar_id = "cal@example.com"
        key = f"{SYNC_STATE_KEY_PREFIX}{calendar_id}"
        existing = CalendarSyncState(
            sync_token="existing-token",
            last_sync_at="2026-02-01T08:00:00+00:00",
            last_batch_change_count=2,
        )
        store = {key: existing.model_dump()}
        mod = CalendarModule()
        mod._db = _make_mock_db(store)

        state = await mod._load_sync_state(calendar_id)
        assert state.sync_token == "existing-token"
        assert state.last_sync_at == "2026-02-01T08:00:00+00:00"
        assert state.last_batch_change_count == 2

    async def test_save_sync_state_persists_to_kv(self):
        store: dict[str, Any] = {}
        mod = CalendarModule()
        mod._db = _make_mock_db(store)
        calendar_id = "cal@example.com"
        state = CalendarSyncState(sync_token="new-token", last_sync_at="2026-03-01T12:00:00+00:00")
        await mod._save_sync_state(calendar_id, state)

        key = f"{SYNC_STATE_KEY_PREFIX}{calendar_id}"
        assert key in store
        assert store[key]["sync_token"] == "new-token"

    async def test_load_returns_default_when_db_pool_is_none(self):
        mod = CalendarModule()
        db = MagicMock()
        db.pool = None
        mod._db = db
        state = await mod._load_sync_state("cal@example.com")
        assert state.sync_token is None

    async def test_save_is_no_op_when_db_pool_is_none(self):
        """_save_sync_state should not raise when pool is None."""
        mod = CalendarModule()
        db = MagicMock()
        db.pool = None
        mod._db = db
        # Should not raise.
        await mod._save_sync_state("cal@example.com", CalendarSyncState())

    async def test_load_returns_default_when_db_is_none(self):
        mod = CalendarModule()
        mod._db = None
        state = await mod._load_sync_state("cal@example.com")
        assert state.sync_token is None

    async def test_sync_state_key_format(self):
        mod = CalendarModule()
        key = mod._sync_state_key("user@gmail.com")
        assert key == f"{SYNC_STATE_KEY_PREFIX}user@gmail.com"


# ---------------------------------------------------------------------------
# _GoogleProvider.sync_incremental tests
# ---------------------------------------------------------------------------


class TestGoogleProviderSyncIncremental:
    """Unit tests for the _GoogleProvider sync_incremental implementation."""

    def _make_provider(self, http_client: httpx.AsyncClient) -> _GoogleProvider:
        import os

        os.environ["BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON"] = GOOGLE_CREDS_JSON
        cfg = CalendarConfig(provider="google", calendar_id="primary")
        provider = _GoogleProvider(cfg, http_client=http_client)
        return provider

    def _make_http_response(
        self,
        payload: dict[str, Any],
        status_code: int = 200,
    ) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            json=payload,
            request=httpx.Request("GET", "https://example.com"),
        )

    async def _mock_access_token(self, provider: _GoogleProvider) -> None:
        """Inject a valid access token so we skip the OAuth refresh flow."""
        provider._oauth._access_token = "test-access-token"
        provider._oauth._access_token_expires_at = datetime.now(UTC) + timedelta(hours=1)

    async def test_incremental_sync_uses_sync_token(self, monkeypatch):
        """When a sync_token is provided, it is included in the request params."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        provider = self._make_provider(mock_client)
        await self._mock_access_token(provider)

        response_payload = {
            "items": [_make_google_event_payload("evt-001")],
            "nextSyncToken": "next-token-abc",
        }
        mock_client.request = AsyncMock(return_value=self._make_http_response(response_payload))

        updated, cancelled, next_token = await provider.sync_incremental(
            calendar_id="primary",
            sync_token="existing-token",
        )

        assert next_token == "next-token-abc"
        assert len(updated) == 1
        assert len(cancelled) == 0

        # Verify syncToken was sent in the request params.
        assert "existing-token" in str(mock_client.request.call_args)

    async def test_full_sync_when_no_token(self, monkeypatch):
        """When sync_token is None, a full sync over the window is performed."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        provider = self._make_provider(mock_client)
        await self._mock_access_token(provider)

        response_payload = {
            "items": [],
            "nextSyncToken": "full-sync-token",
        }
        mock_client.request = AsyncMock(return_value=self._make_http_response(response_payload))

        updated, cancelled, next_token = await provider.sync_incremental(
            calendar_id="primary",
            sync_token=None,
            full_sync_window_days=30,
        )

        assert next_token == "full-sync-token"
        assert len(updated) == 0
        # Verify no syncToken was sent; timeMin was set instead.
        call_str = str(mock_client.request.call_args)
        assert "syncToken" not in call_str
        assert "timeMin" in call_str

    async def test_cancelled_events_are_separated(self, monkeypatch):
        """Events with status=cancelled are returned as cancelled_event_ids."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        provider = self._make_provider(mock_client)
        await self._mock_access_token(provider)

        response_payload = {
            "items": [
                _make_google_event_payload("evt-keep", "confirmed"),
                _make_google_event_payload("evt-gone", "cancelled"),
            ],
            "nextSyncToken": "tok-after",
        }
        mock_client.request = AsyncMock(return_value=self._make_http_response(response_payload))

        updated, cancelled, next_token = await provider.sync_incremental(
            calendar_id="primary",
            sync_token="old-tok",
        )

        assert [e.event_id for e in updated] == ["evt-keep"]
        assert cancelled == ["evt-gone"]
        assert next_token == "tok-after"

    async def test_pagination_follows_next_page_token(self, monkeypatch):
        """Pagination: fetches all pages before returning results."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        provider = self._make_provider(mock_client)
        await self._mock_access_token(provider)

        page1 = {
            "items": [_make_google_event_payload("evt-p1")],
            "nextPageToken": "page-2-token",
        }
        page2 = {
            "items": [_make_google_event_payload("evt-p2")],
            "nextSyncToken": "final-sync-token",
        }
        mock_client.request = AsyncMock(
            side_effect=[
                self._make_http_response(page1),
                self._make_http_response(page2),
            ]
        )

        updated, cancelled, next_token = await provider.sync_incremental(
            calendar_id="primary",
            sync_token="tok",
        )

        assert len(updated) == 2
        assert {e.event_id for e in updated} == {"evt-p1", "evt-p2"}
        assert next_token == "final-sync-token"
        assert mock_client.request.call_count == 2

    async def test_410_gone_raises_sync_token_expired(self, monkeypatch):
        """410 Gone response raises CalendarSyncTokenExpiredError."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        provider = self._make_provider(mock_client)
        await self._mock_access_token(provider)

        mock_client.request = AsyncMock(return_value=self._make_http_response({}, status_code=410))

        with pytest.raises(CalendarSyncTokenExpiredError, match="Sync token expired"):
            await provider.sync_incremental(
                calendar_id="primary",
                sync_token="expired-tok",
            )

    async def test_non_410_error_raises_request_error(self, monkeypatch):
        """Non-200 non-410 responses raise CalendarRequestError."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        provider = self._make_provider(mock_client)
        await self._mock_access_token(provider)

        mock_client.request = AsyncMock(
            return_value=self._make_http_response({"error": {"message": "quota exceeded"}}, 429)
        )

        with pytest.raises(CalendarRequestError) as exc_info:
            await provider.sync_incremental(
                calendar_id="primary",
                sync_token="some-tok",
            )
        assert exc_info.value.status_code == 429

    async def test_missing_next_sync_token_raises_auth_error(self, monkeypatch):
        """Response without nextSyncToken raises CalendarAuthError."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        provider = self._make_provider(mock_client)
        await self._mock_access_token(provider)

        # Response with no nextSyncToken and no nextPageToken.
        mock_client.request = AsyncMock(return_value=self._make_http_response({"items": []}))

        with pytest.raises(CalendarAuthError, match="nextSyncToken"):
            await provider.sync_incremental(
                calendar_id="primary",
                sync_token="tok",
            )


# ---------------------------------------------------------------------------
# CalendarModule._sync_calendar tests
# ---------------------------------------------------------------------------


class TestCalendarModuleSyncCalendar:
    def _make_module_with_provider(
        self,
        provider: CalendarProvider,
        sync_config: CalendarSyncConfig | None = None,
        state_store: dict | None = None,
    ) -> CalendarModule:
        mod = CalendarModule()
        mod._provider = provider
        mod._db = _make_mock_db(state_store)
        cfg = CalendarConfig(
            provider="google",
            calendar_id="primary",
            sync=sync_config or CalendarSyncConfig(enabled=True),
        )
        mod._config = cfg
        return mod

    async def test_happy_path_persists_new_sync_token(self):
        """A successful sync stores the next token in the KV store."""
        store: dict[str, Any] = {}
        event = _make_sample_event()
        provider = _SyncCapableProviderDouble(sync_result=([event], [], "new-token"))
        mod = self._make_module_with_provider(provider, state_store=store)

        await mod._sync_calendar("primary")

        key = f"{SYNC_STATE_KEY_PREFIX}primary"
        assert key in store
        assert store[key]["sync_token"] == "new-token"

    async def test_updates_in_memory_sync_state(self):
        """After sync, the in-memory cache is updated."""
        event = _make_sample_event()
        cancelled = ["old-evt-id"]
        provider = _SyncCapableProviderDouble(sync_result=([event], cancelled, "tok-new"))
        mod = self._make_module_with_provider(provider)

        await mod._sync_calendar("primary")

        state = mod._sync_states.get("primary")
        assert state is not None
        assert state.sync_token == "tok-new"
        # last_batch_change_count = updated + cancelled.
        assert state.last_batch_change_count == 2
        assert state.last_sync_error is None

    async def test_token_expiry_triggers_full_resync(self):
        """On CalendarSyncTokenExpiredError, falls back to full sync (token=None)."""
        event = _make_sample_event()
        # First call raises expired; second call (full sync) succeeds.
        provider = _SyncCapableProviderDouble(
            call_side_effects=[
                CalendarSyncTokenExpiredError("token expired"),
                ([event], [], "fresh-token"),
            ]
        )
        # Pre-populate state store with an existing (now expired) token.
        key = f"{SYNC_STATE_KEY_PREFIX}primary"
        store: dict[str, Any] = {key: CalendarSyncState(sync_token="expired-tok").model_dump()}
        mod = self._make_module_with_provider(provider, state_store=store)

        await mod._sync_calendar("primary")

        # Second call should have been with sync_token=None.
        assert len(provider.sync_calls) == 2
        assert provider.sync_calls[-1]["sync_token"] is None
        # State should reflect the full-sync result.
        assert store[key]["sync_token"] == "fresh-token"

    async def test_token_expiry_full_resync_also_errors(self):
        """When both incremental and full-sync fail, error is logged and state updated."""
        # First call raises token expired; second call (full sync) also errors.
        provider = _SyncCapableProviderDouble(
            call_side_effects=[
                CalendarSyncTokenExpiredError("expired"),
                CalendarAuthError("full sync failed too"),
            ]
        )
        mod = self._make_module_with_provider(provider)

        # Should not raise — errors are swallowed.
        await mod._sync_calendar("primary")

        state = mod._sync_states.get("primary")
        assert state is not None
        assert "full sync failed too" in (state.last_sync_error or "")

    async def test_provider_auth_error_is_swallowed(self):
        """CalendarAuthError during sync is swallowed and logged (poller stays alive)."""
        provider = _SyncCapableProviderDouble(sync_error=CalendarAuthError("quota exceeded"))
        mod = self._make_module_with_provider(provider)

        # Must not raise.
        await mod._sync_calendar("primary")

        state = mod._sync_states.get("primary")
        assert state is not None
        assert "quota exceeded" in (state.last_sync_error or "")

    async def test_error_state_persisted_to_kv(self):
        """Error state is written to the KV store so it survives restarts."""
        store: dict[str, Any] = {}
        provider = _SyncCapableProviderDouble(sync_error=CalendarAuthError("network failure"))
        mod = self._make_module_with_provider(provider, state_store=store)

        await mod._sync_calendar("primary")

        key = f"{SYNC_STATE_KEY_PREFIX}primary"
        assert key in store
        assert "network failure" in (store[key].get("last_sync_error") or "")

    async def test_uses_existing_sync_token_from_kv(self):
        """_sync_calendar loads the saved token from the KV store on first call."""
        key = f"{SYNC_STATE_KEY_PREFIX}primary"
        store: dict[str, Any] = {key: CalendarSyncState(sync_token="saved-token").model_dump()}
        provider = _SyncCapableProviderDouble()
        mod = self._make_module_with_provider(provider, state_store=store)

        await mod._sync_calendar("primary")

        assert provider.sync_calls[0]["sync_token"] == "saved-token"

    async def test_uses_none_token_on_first_sync(self):
        """With no saved state, sync_token is None (full sync)."""
        provider = _SyncCapableProviderDouble()
        mod = self._make_module_with_provider(provider)

        await mod._sync_calendar("primary")

        assert provider.sync_calls[0]["sync_token"] is None

    async def test_noop_when_provider_is_none(self):
        """_sync_calendar is a no-op when provider hasn't been initialized."""
        mod = CalendarModule()
        mod._provider = None
        mod._config = CalendarConfig(provider="google", calendar_id="primary")
        # Should not raise.
        await mod._sync_calendar("primary")


# ---------------------------------------------------------------------------
# CalendarModule on_startup sync poller tests
# ---------------------------------------------------------------------------


class TestCalendarModuleStartupPoller:
    async def test_poller_started_when_sync_enabled(self, monkeypatch):
        """When sync.enabled=True, a background task is started on_startup."""
        monkeypatch.setenv("BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON", GOOGLE_CREDS_JSON)
        mod = CalendarModule()
        config = {"provider": "google", "calendar_id": "primary", "sync": {"enabled": True}}

        # Patch _run_sync_poller to prevent it from actually running.
        poller_started = []

        async def fake_poller():
            poller_started.append(True)
            # Sleep until cancelled.
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass

        with patch.object(mod, "_run_sync_poller", side_effect=fake_poller):
            await mod.on_startup(config, db=None)

        try:
            assert mod._sync_task is not None
        finally:
            if mod._sync_task and not mod._sync_task.done():
                mod._sync_task.cancel()
            await mod.on_shutdown()

    async def test_poller_not_started_when_sync_disabled(self, monkeypatch):
        """When sync.enabled=False (default), no background task is created."""
        monkeypatch.setenv("BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON", GOOGLE_CREDS_JSON)
        mod = CalendarModule()
        config = {"provider": "google", "calendar_id": "primary"}
        await mod.on_startup(config, db=None)
        try:
            assert mod._sync_task is None
        finally:
            await mod.on_shutdown()

    async def test_on_shutdown_cancels_poller(self, monkeypatch):
        """on_shutdown cancels the sync poller task."""
        monkeypatch.setenv("BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON", GOOGLE_CREDS_JSON)
        mod = CalendarModule()
        config = {"provider": "google", "calendar_id": "primary", "sync": {"enabled": True}}

        async def fake_poller():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass

        with patch.object(mod, "_run_sync_poller", side_effect=fake_poller):
            await mod.on_startup(config, db=None)

        task = mod._sync_task
        assert task is not None
        await mod.on_shutdown()
        assert task.done()
        assert mod._sync_task is None

    async def test_on_shutdown_without_poller_is_safe(self, monkeypatch):
        """on_shutdown when no poller was started should not raise."""
        monkeypatch.setenv("BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON", GOOGLE_CREDS_JSON)
        mod = CalendarModule()
        config = {"provider": "google", "calendar_id": "primary"}
        await mod.on_startup(config, db=None)
        # Should not raise.
        await mod.on_shutdown()


# ---------------------------------------------------------------------------
# calendar_sync_status MCP tool tests
# ---------------------------------------------------------------------------


class TestCalendarSyncStatusTool:
    def _make_module(
        self,
        *,
        sync_enabled: bool = True,
        sync_state: CalendarSyncState | None = None,
        state_store: dict | None = None,
    ) -> tuple[CalendarModule, _StubMCP]:
        mod = CalendarModule()
        provider = _SyncCapableProviderDouble()
        mod._provider = provider
        cfg = CalendarConfig(
            provider="google",
            calendar_id="primary",
            sync=CalendarSyncConfig(enabled=sync_enabled),
        )
        mod._config = cfg
        mod._db = _make_mock_db(state_store)
        if sync_state is not None:
            mod._sync_states["primary"] = sync_state
        mcp = _StubMCP()
        return mod, mcp

    async def test_returns_sync_disabled_when_not_configured(self):
        mod, mcp = self._make_module(sync_enabled=False)
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=_make_mock_db(),
        )
        result = await mcp.tools["calendar_sync_status"]()
        assert result["sync_enabled"] is False
        assert result["status"] == "ok"
        assert result["calendar_id"] == "primary"

    async def test_returns_sync_state_from_memory_cache(self):
        state = CalendarSyncState(
            sync_token="cached-token",
            last_sync_at="2026-03-01T10:00:00+00:00",
            last_batch_change_count=7,
        )
        mod, mcp = self._make_module(sync_enabled=True, sync_state=state)
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary", "sync": {"enabled": True}},
            db=mod._db,
        )

        result = await mcp.tools["calendar_sync_status"]()
        assert result["sync_enabled"] is True
        assert result["sync_token_valid"] is True
        assert result["last_sync_at"] == "2026-03-01T10:00:00+00:00"
        assert result["last_batch_change_count"] == 7
        assert result["status"] == "ok"

    async def test_returns_state_from_kv_when_no_cache(self):
        key = "calendar::sync::primary"
        stored = CalendarSyncState(
            sync_token="kv-token",
            last_sync_at="2026-02-28T08:00:00+00:00",
            last_batch_change_count=3,
        )
        store = {key: stored.model_dump()}
        mod, mcp = self._make_module(sync_enabled=True, state_store=store)
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary", "sync": {"enabled": True}},
            db=mod._db,
        )

        result = await mcp.tools["calendar_sync_status"]()
        assert result["sync_token_valid"] is True
        assert result["last_batch_change_count"] == 3

    async def test_token_valid_false_when_no_token(self):
        mod, mcp = self._make_module(sync_enabled=True)
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary", "sync": {"enabled": True}},
            db=mod._db,
        )
        result = await mcp.tools["calendar_sync_status"]()
        assert result["sync_token_valid"] is False

    async def test_last_sync_error_is_surfaced(self):
        state = CalendarSyncState(last_sync_error="Auth failed")
        mod, mcp = self._make_module(sync_enabled=True, sync_state=state)
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary", "sync": {"enabled": True}},
            db=mod._db,
        )
        result = await mcp.tools["calendar_sync_status"]()
        assert result["last_sync_error"] == "Auth failed"


# ---------------------------------------------------------------------------
# calendar_force_sync MCP tool tests
# ---------------------------------------------------------------------------


class TestCalendarForceSyncTool:
    def _make_module(
        self,
        provider: CalendarProvider | None = None,
        *,
        sync_enabled: bool = False,
        state_store: dict | None = None,
    ) -> tuple[CalendarModule, _StubMCP]:
        mod = CalendarModule()
        mod._provider = provider or _SyncCapableProviderDouble()
        cfg = CalendarConfig(
            provider="google",
            calendar_id="primary",
            sync=CalendarSyncConfig(enabled=sync_enabled),
        )
        mod._config = cfg
        db = _make_mock_db(state_store)
        mod._db = db
        mcp = _StubMCP()
        return mod, mcp

    async def test_force_sync_with_no_background_poller_runs_inline(self):
        """When no poller is active, force_sync executes a sync immediately."""
        event = _make_sample_event()
        provider = _SyncCapableProviderDouble(sync_result=([event], [], "tok-force"))
        mod, mcp = self._make_module(provider=provider, sync_enabled=False)
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=mod._db,
        )

        result = await mcp.tools["calendar_force_sync"]()

        assert result["status"] == "sync_completed"
        assert result["calendar_id"] == "primary"
        assert provider.sync_calls, "sync_incremental should have been called"

    async def test_force_sync_with_active_poller_signals_event(self):
        """When the poller is running, force_sync sets the force-sync event."""
        mod, mcp = self._make_module(sync_enabled=True)
        # Simulate a running task.
        mod._sync_task = asyncio.create_task(asyncio.sleep(3600))
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary", "sync": {"enabled": True}},
            db=mod._db,
        )

        result = await mcp.tools["calendar_force_sync"]()

        assert result["status"] == "sync_triggered"
        assert mod._force_sync_event.is_set()

        mod._sync_task.cancel()
        try:
            await mod._sync_task
        except asyncio.CancelledError:
            pass

    async def test_force_sync_error_is_recorded_in_last_sync_error(self):
        """Provider errors during inline force_sync are recorded in last_sync_error.

        Per spec section 4.4, sync failures are logged but do not block butler
        operation.  The result status is still 'sync_completed' and the error
        is surfaced via last_sync_error so the caller can inspect it.
        """
        provider = _SyncCapableProviderDouble(
            sync_error=CalendarRequestError(status_code=503, message="Service unavailable")
        )
        mod, mcp = self._make_module(provider=provider, sync_enabled=False)
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=mod._db,
        )

        result = await mcp.tools["calendar_force_sync"]()

        # Sync errors are swallowed (fail-open) — result is still sync_completed.
        assert result["status"] == "sync_completed"
        # The error is surfaced in last_sync_error for observability.
        assert result["last_sync_error"] is not None
        err = result["last_sync_error"]
        assert "503" in err or "Service unavailable" in err

    async def test_force_sync_returns_last_sync_at(self):
        """After a successful inline sync, last_sync_at is returned."""
        provider = _SyncCapableProviderDouble(sync_result=([], [], "tok-out"))
        mod, mcp = self._make_module(provider=provider, sync_enabled=False)
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=mod._db,
        )

        result = await mcp.tools["calendar_force_sync"]()

        assert result["status"] == "sync_completed"
        assert result["last_sync_at"] is not None  # Set by _sync_calendar.

    async def test_force_sync_respects_calendar_id_override(self):
        """When calendar_id is passed explicitly, it overrides the config default."""
        provider = _SyncCapableProviderDouble()
        mod, mcp = self._make_module(provider=provider, sync_enabled=False)
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=mod._db,
        )

        result = await mcp.tools["calendar_force_sync"](calendar_id="other@example.com")

        assert result["calendar_id"] == "other@example.com"
