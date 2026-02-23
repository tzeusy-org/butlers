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
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.modules.calendar import (
    DEFAULT_INTERNAL_PROJECTION_INTERVAL_MINUTES,
    DEFAULT_SCHEDULED_TASK_DURATION_MINUTES,
    DEFAULT_SYNC_INTERVAL_MINUTES,
    DEFAULT_SYNC_WINDOW_DAYS,
    RECURRENCE_PROJECTION_WINDOW_DAYS,
    SOURCE_KIND_INTERNAL_REMINDERS,
    SOURCE_KIND_INTERNAL_SCHEDULER,
    SOURCE_KIND_PROVIDER,
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
    _cron_next_occurrence,
    _cron_occurrences_in_window,
    _GoogleOAuthCredentials,
    _GoogleProvider,
    _rrule_occurrences_in_window,
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
    versions: dict[str, int] = {}

    async def mock_fetchval(sql: str, key: str, *args: Any) -> Any:
        if "SELECT value FROM state" in sql:
            val = store.get(key)
            if isinstance(val, dict):
                return json.dumps(val)
            return val
        if "INSERT INTO state" in sql and "RETURNING version" in sql:
            value_str = args[0] if args else None
            if value_str is not None:
                store[key] = json.loads(value_str)
            versions[key] = versions.get(key, 0) + 1
            return versions[key]
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


def _make_credential_store() -> AsyncMock:
    store = AsyncMock()

    async def _resolve(key: str, env_fallback: bool = False) -> str | None:
        assert env_fallback is False
        values = {
            "GOOGLE_OAUTH_CLIENT_ID": "test-client-id",
            "GOOGLE_OAUTH_CLIENT_SECRET": "test-client-secret",
            "GOOGLE_REFRESH_TOKEN": "test-refresh-token",
        }
        return values.get(key)

    store.resolve.side_effect = _resolve
    return store


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
        cfg = CalendarConfig(provider="google", calendar_id="primary")
        credentials = _GoogleOAuthCredentials(
            client_id="test-client-id",
            client_secret="test-client-secret",
            refresh_token="test-refresh-token",
        )
        return _GoogleProvider(cfg, credentials=credentials, http_client=http_client)

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
    async def test_poller_started_when_sync_enabled(self):
        """When sync.enabled=True, a background task is started on_startup."""
        mod = CalendarModule()
        credential_store = _make_credential_store()
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
            await mod.on_startup(config, db=None, credential_store=credential_store)

        try:
            assert mod._sync_task is not None
        finally:
            if mod._sync_task and not mod._sync_task.done():
                mod._sync_task.cancel()
            await mod.on_shutdown()

    async def test_poller_not_started_when_sync_disabled(self):
        """When sync.enabled=False (default), no background task is created."""
        mod = CalendarModule()
        credential_store = _make_credential_store()
        config = {"provider": "google", "calendar_id": "primary"}
        await mod.on_startup(config, db=None, credential_store=credential_store)
        try:
            assert mod._sync_task is None
        finally:
            await mod.on_shutdown()

    async def test_on_shutdown_cancels_poller(self):
        """on_shutdown cancels the sync poller task."""
        mod = CalendarModule()
        credential_store = _make_credential_store()
        config = {"provider": "google", "calendar_id": "primary", "sync": {"enabled": True}}

        async def fake_poller():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass

        with patch.object(mod, "_run_sync_poller", side_effect=fake_poller):
            await mod.on_startup(config, db=None, credential_store=credential_store)

        task = mod._sync_task
        assert task is not None
        await mod.on_shutdown()
        assert task.done()
        assert mod._sync_task is None

    async def test_on_shutdown_without_poller_is_safe(self):
        """on_shutdown when no poller was started should not raise."""
        mod = CalendarModule()
        credential_store = _make_credential_store()
        config = {"provider": "google", "calendar_id": "primary"}
        await mod.on_startup(config, db=None, credential_store=credential_store)
        # Should not raise.
        await mod.on_shutdown()


# ---------------------------------------------------------------------------
# CalendarModule internal projection poller tests
# ---------------------------------------------------------------------------


class TestCalendarModuleInternalProjectionPoller:
    """Tests for _run_internal_projection_poller and its lifecycle."""

    async def test_internal_projection_task_started_on_startup(self):
        """on_startup always creates a background internal projection task."""
        mod = CalendarModule()
        credential_store = _make_credential_store()
        config = {"provider": "google", "calendar_id": "primary"}

        async def fake_poller():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass

        with patch.object(mod, "_run_internal_projection_poller", side_effect=fake_poller):
            await mod.on_startup(config, db=None, credential_store=credential_store)

        try:
            assert mod._internal_projection_task is not None
            assert not mod._internal_projection_task.done()
        finally:
            await mod.on_shutdown()

    async def test_internal_projection_task_started_even_when_sync_disabled(self):
        """Internal projection poller starts regardless of sync.enabled setting."""
        mod = CalendarModule()
        credential_store = _make_credential_store()
        config = {"provider": "google", "calendar_id": "primary"}  # sync disabled by default

        async def fake_poller():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass

        with patch.object(mod, "_run_internal_projection_poller", side_effect=fake_poller):
            await mod.on_startup(config, db=None, credential_store=credential_store)

        try:
            assert mod._sync_task is None, "Sync poller should not start when sync disabled"
            assert mod._internal_projection_task is not None
        finally:
            await mod.on_shutdown()

    async def test_on_shutdown_cancels_internal_projection_task(self):
        """on_shutdown cancels the internal projection task."""
        mod = CalendarModule()
        credential_store = _make_credential_store()
        config = {"provider": "google", "calendar_id": "primary"}

        async def fake_poller():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass

        with patch.object(mod, "_run_internal_projection_poller", side_effect=fake_poller):
            await mod.on_startup(config, db=None, credential_store=credential_store)

        task = mod._internal_projection_task
        assert task is not None
        await mod.on_shutdown()
        assert task.done()
        assert mod._internal_projection_task is None

    async def test_run_internal_projection_poller_calls_project_on_first_iteration(self):
        """_run_internal_projection_poller calls _project_internal_sources immediately."""
        mod = CalendarModule()
        projection_calls: list[None] = []

        async def fake_project_internal():
            projection_calls.append(None)

        mod._db = None

        # Use a very short sleep to let the first iteration run before cancelling.
        async def fake_sleep(seconds: float) -> None:
            # Simulate the sleep resolving immediately so the loop would repeat,
            # then raise CancelledError on the second call to stop the loop.
            if len(projection_calls) >= 1:
                raise asyncio.CancelledError

        with (
            patch.object(mod, "_project_internal_sources", side_effect=fake_project_internal),
            patch("asyncio.sleep", side_effect=fake_sleep),
        ):
            try:
                await mod._run_internal_projection_poller()
            except asyncio.CancelledError:
                pass

        assert len(projection_calls) >= 1

    async def test_run_internal_projection_poller_uses_configured_interval(self):
        """_run_internal_projection_poller sleeps for the configured interval in seconds."""
        mod = CalendarModule()
        sleep_calls: list[float] = []

        async def fake_project_internal():
            pass

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            raise asyncio.CancelledError  # Stop after first sleep

        with (
            patch.object(mod, "_project_internal_sources", side_effect=fake_project_internal),
            patch("asyncio.sleep", side_effect=fake_sleep),
        ):
            try:
                await mod._run_internal_projection_poller()
            except asyncio.CancelledError:
                pass

        assert len(sleep_calls) == 1
        assert sleep_calls[0] == DEFAULT_INTERNAL_PROJECTION_INTERVAL_MINUTES * 60

    async def test_run_internal_projection_poller_error_does_not_stop_loop(self):
        """Errors from _project_internal_sources are caught; poller continues running."""
        mod = CalendarModule()
        call_count = 0

        async def failing_project():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("projection failure")

        sleep_call_count = 0

        async def fake_sleep(seconds: float) -> None:
            nonlocal sleep_call_count
            sleep_call_count += 1
            if sleep_call_count >= 2:
                raise asyncio.CancelledError

        with (
            patch.object(mod, "_project_internal_sources", side_effect=failing_project),
            patch("asyncio.sleep", side_effect=fake_sleep),
        ):
            try:
                await mod._run_internal_projection_poller()
            except asyncio.CancelledError:
                pass

        # Poller should have looped at least twice despite errors.
        assert call_count >= 2

    async def test_run_internal_projection_poller_stops_on_cancel(self):
        """_run_internal_projection_poller exits cleanly on CancelledError from sleep."""
        mod = CalendarModule()
        projection_calls: list[None] = []

        async def fake_project():
            projection_calls.append(None)

        async def cancelling_sleep(seconds: float) -> None:
            raise asyncio.CancelledError

        with (
            patch.object(mod, "_project_internal_sources", side_effect=fake_project),
            patch("asyncio.sleep", side_effect=cancelling_sleep),
        ):
            # Should return without raising CancelledError (loop exits via break).
            await mod._run_internal_projection_poller()

        # _project_internal_sources ran once before the cancelled sleep.
        assert len(projection_calls) == 1


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


# ---------------------------------------------------------------------------
# Unified projection tests (issue butlers-x6wi.3)
# ---------------------------------------------------------------------------


class TestCalendarProjectionSync:
    def _make_module_with_projection_db(
        self,
        provider: CalendarProvider | None = None,
        *,
        sync_enabled: bool = True,
    ) -> CalendarModule:
        mod = CalendarModule()
        mod._provider = provider or _SyncCapableProviderDouble()
        mod._config = CalendarConfig(
            provider="google",
            calendar_id="primary",
            sync=CalendarSyncConfig(enabled=sync_enabled),
        )
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        pool.fetchrow = AsyncMock(return_value=None)
        pool.fetchval = AsyncMock(return_value=None)
        pool.execute = AsyncMock(return_value="OK")
        db = MagicMock()
        db.pool = pool
        db.db_name = "butler_general"
        mod._db = db
        mod._butler_name = "general"
        return mod

    async def test_sync_uses_projection_cursor_token_for_incremental_pull(self):
        event = _make_sample_event("evt-proj-001")
        provider = _SyncCapableProviderDouble(
            sync_result=([event], ["evt-cancelled"], "token-next")
        )
        mod = self._make_module_with_projection_db(provider)
        mod._sync_states["primary"] = CalendarSyncState(sync_token="kv-fallback")

        source_id = uuid.uuid4()
        with (
            patch.object(mod, "_projection_tables_available", AsyncMock(return_value=True)),
            patch.object(mod, "_ensure_calendar_source", AsyncMock(return_value=source_id)),
            patch.object(
                mod,
                "_load_projection_cursor",
                AsyncMock(
                    return_value={
                        "sync_token": "cursor-token",
                        "checkpoint": {"provider": "google"},
                        "full_sync_required": False,
                        "last_synced_at": None,
                        "last_success_at": None,
                        "last_error_at": None,
                        "last_error": None,
                    }
                ),
            ),
            patch.object(mod, "_project_provider_changes", AsyncMock()) as project_provider_mock,
            patch.object(mod, "_upsert_projection_cursor", AsyncMock()) as cursor_upsert_mock,
            patch.object(mod, "_record_projection_action", AsyncMock()) as action_log_mock,
            patch.object(mod, "_project_internal_sources", AsyncMock()) as internal_project_mock,
        ):
            await mod._sync_calendar("primary")

        assert provider.sync_calls[0]["sync_token"] == "cursor-token"
        assert project_provider_mock.await_count == 1
        assert cursor_upsert_mock.await_count >= 1
        assert action_log_mock.await_count >= 1
        assert internal_project_mock.await_count == 1

        state = mod._sync_states["primary"]
        assert state.sync_token == "token-next"
        assert state.last_batch_change_count == 2

    async def test_sync_projects_internal_sources_even_when_provider_fails(self):
        provider = _SyncCapableProviderDouble(sync_error=CalendarAuthError("provider down"))
        mod = self._make_module_with_projection_db(provider)

        with (
            patch.object(mod, "_projection_tables_available", AsyncMock(return_value=True)),
            patch.object(mod, "_ensure_calendar_source", AsyncMock(return_value=uuid.uuid4())),
            patch.object(mod, "_load_projection_cursor", AsyncMock(return_value=None)),
            patch.object(mod, "_upsert_projection_cursor", AsyncMock()),
            patch.object(mod, "_record_projection_action", AsyncMock()),
            patch.object(mod, "_project_internal_sources", AsyncMock()) as internal_project_mock,
        ):
            await mod._sync_calendar("primary")

        assert internal_project_mock.await_count == 1
        assert "provider down" in (mod._sync_states["primary"].last_sync_error or "")

    async def test_project_scheduler_source_upserts_origin_ref_linked_rows(self):
        mod = self._make_module_with_projection_db()
        source_id = uuid.uuid4()
        row_id = uuid.uuid4()
        starts_at = datetime(2026, 3, 5, 14, 0, tzinfo=UTC)
        ends_at = datetime(2026, 3, 5, 15, 0, tzinfo=UTC)
        mod._db.pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": row_id,
                    "name": "focus-time",
                    "cron": "0 14 * * 1-5",
                    "dispatch_mode": "prompt",
                    "prompt": "do work",
                    "job_name": None,
                    "job_args": {"n": 1},
                    "timezone": "UTC",
                    "start_at": starts_at,
                    "end_at": ends_at,
                    "until_at": None,
                    "display_title": "Deep Focus",
                    "calendar_event_id": None,
                    "enabled": True,
                    "updated_at": starts_at,
                }
            ]
        )

        with (
            patch.object(mod, "_projection_tables_available", AsyncMock(return_value=True)),
            patch.object(mod, "_table_exists", AsyncMock(return_value=True)),
            patch.object(mod, "_ensure_calendar_source", AsyncMock(return_value=source_id)),
            patch.object(
                mod, "_upsert_projection_event", AsyncMock(return_value=uuid.uuid4())
            ) as upsert_event_mock,
            patch.object(mod, "_upsert_projection_instance", AsyncMock()) as upsert_instance_mock,
            patch.object(
                mod, "_mark_projection_source_stale_events_cancelled", AsyncMock()
            ) as stale_cancel_mock,
            patch.object(mod, "_upsert_projection_cursor", AsyncMock()) as cursor_upsert_mock,
        ):
            await mod._project_scheduler_source()

        assert upsert_event_mock.await_count == 1
        event_kwargs = upsert_event_mock.await_args.kwargs
        assert event_kwargs["source_id"] == source_id
        assert event_kwargs["origin_ref"] == str(row_id)
        assert event_kwargs["title"] == "Deep Focus"
        assert event_kwargs["recurrence_rule"] == "0 14 * * 1-5"
        assert upsert_instance_mock.await_count == 1
        assert stale_cancel_mock.await_count == 1
        assert cursor_upsert_mock.await_count == 1

    async def test_project_reminders_source_handles_active_and_dismissed_rows(self):
        mod = self._make_module_with_projection_db()
        source_id = uuid.uuid4()
        reminder_active_id = uuid.uuid4()
        reminder_dismissed_id = uuid.uuid4()
        trigger_at = datetime(2026, 3, 6, 9, 30, tzinfo=UTC)

        mod._db.pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": reminder_active_id,
                    "label": "Call mom",
                    "message": "Call mom",
                    "type": "one_time",
                    "reminder_type": "one_time",
                    "contact_id": None,
                    "timezone": "UTC",
                    "next_trigger_at": trigger_at,
                    "due_at": trigger_at,
                    "until_at": None,
                    "calendar_event_id": None,
                    "dismissed": False,
                    "updated_at": trigger_at,
                },
                {
                    "id": reminder_dismissed_id,
                    "label": "Dismissed",
                    "message": "Dismissed",
                    "type": "one_time",
                    "reminder_type": "one_time",
                    "contact_id": None,
                    "timezone": "UTC",
                    "next_trigger_at": None,
                    "due_at": None,
                    "until_at": None,
                    "calendar_event_id": None,
                    "dismissed": True,
                    "updated_at": trigger_at,
                },
            ]
        )

        with (
            patch.object(mod, "_projection_tables_available", AsyncMock(return_value=True)),
            patch.object(mod, "_table_exists", AsyncMock(return_value=True)),
            patch.object(mod, "_ensure_calendar_source", AsyncMock(return_value=source_id)),
            patch.object(
                mod, "_upsert_projection_event", AsyncMock(return_value=uuid.uuid4())
            ) as upsert_event_mock,
            patch.object(mod, "_upsert_projection_instance", AsyncMock()) as upsert_instance_mock,
            patch.object(
                mod, "_mark_projection_event_cancelled", AsyncMock()
            ) as mark_cancelled_mock,
            patch.object(
                mod, "_mark_projection_source_stale_events_cancelled", AsyncMock()
            ) as stale_cancel_mock,
            patch.object(mod, "_upsert_projection_cursor", AsyncMock()) as cursor_upsert_mock,
        ):
            await mod._project_reminders_source()

        assert upsert_event_mock.await_count == 1
        event_kwargs = upsert_event_mock.await_args.kwargs
        assert event_kwargs["origin_ref"] == str(reminder_active_id)
        assert event_kwargs["title"] == "Call mom"
        assert upsert_instance_mock.await_count == 1
        assert mark_cancelled_mock.await_count == 1
        cancel_kwargs = mark_cancelled_mock.await_args.kwargs
        assert cancel_kwargs["origin_ref"] == str(reminder_dismissed_id)
        assert stale_cancel_mock.await_count == 1
        assert cursor_upsert_mock.await_count == 1


class TestCronNextOccurrence:
    """Unit tests for _cron_next_occurrence helper."""

    def test_returns_future_datetime(self):
        """Next occurrence must be after the anchor."""
        now = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        result = _cron_next_occurrence("0 14 * * *", now=now)
        assert result > now

    def test_result_is_timezone_aware(self):
        result = _cron_next_occurrence("*/5 * * * *")
        assert result.tzinfo is not None

    def test_hourly_cron_returns_next_hour(self):
        """For @hourly on the dot, next hit is one hour ahead."""
        # Anchor exactly at midnight; "0 * * * *" should fire at 01:00.
        now = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        result = _cron_next_occurrence("0 * * * *", now=now)
        expected = datetime(2026, 3, 1, 1, 0, 0, tzinfo=UTC)
        assert result == expected

    def test_default_now_is_used_when_not_provided(self):
        before = datetime.now(UTC)
        result = _cron_next_occurrence("* * * * *")
        after = datetime.now(UTC)
        # Result must fall strictly between before and after + 1 min.
        assert result >= before
        assert result <= after + timedelta(minutes=1)


class TestProjectSchedulerSourceSyntheticWindow:
    """Tests for synthetic start_at/end_at computation in _project_scheduler_source."""

    def _make_module(self) -> CalendarModule:
        mod = CalendarModule()
        mod._config = CalendarConfig(
            provider="google",
            calendar_id="primary",
            sync=CalendarSyncConfig(enabled=True),
        )
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        pool.fetchrow = AsyncMock(return_value=None)
        pool.fetchval = AsyncMock(return_value=None)
        pool.execute = AsyncMock(return_value="OK")
        db = MagicMock()
        db.pool = pool
        db.db_name = "butler_general"
        mod._db = db
        mod._butler_name = "general"
        return mod

    def _make_task_row(
        self,
        *,
        task_id: uuid.UUID | None = None,
        name: str = "daily-standup",
        cron: str = "0 9 * * 1-5",
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        display_title: str | None = None,
        enabled: bool = True,
    ) -> dict:
        tid = task_id or uuid.uuid4()
        now = datetime(2026, 3, 1, 8, 0, 0, tzinfo=UTC)
        return {
            "id": tid,
            "name": name,
            "cron": cron,
            "dispatch_mode": "prompt",
            "prompt": "run standup",
            "job_name": None,
            "job_args": None,
            "timezone": "UTC",
            "start_at": start_at,
            "end_at": end_at,
            "until_at": None,
            "display_title": display_title,
            "calendar_event_id": None,
            "enabled": enabled,
            "updated_at": now,
        }

    async def test_null_start_at_end_at_row_is_projected_using_cron(self):
        """A row with start_at=NULL and end_at=NULL should be projected using
        the synthetic window derived from the cron expression."""
        mod = self._make_module()
        source_id = uuid.uuid4()
        row = self._make_task_row(cron="0 9 * * 1-5", start_at=None, end_at=None)
        mod._db.pool.fetch = AsyncMock(return_value=[row])

        captured_kwargs: list[dict] = []

        async def capture_event(**kwargs):
            captured_kwargs.append(kwargs)
            return uuid.uuid4()

        with (
            patch.object(mod, "_projection_tables_available", AsyncMock(return_value=True)),
            patch.object(mod, "_table_exists", AsyncMock(return_value=True)),
            patch.object(mod, "_ensure_calendar_source", AsyncMock(return_value=source_id)),
            patch.object(mod, "_upsert_projection_event", capture_event),
            patch.object(mod, "_upsert_projection_instance", AsyncMock()),
            patch.object(mod, "_mark_projection_source_stale_events_cancelled", AsyncMock()),
            patch.object(mod, "_upsert_projection_cursor", AsyncMock()),
        ):
            await mod._project_scheduler_source()

        assert len(captured_kwargs) == 1, "Expected exactly one event upserted"
        kw = captured_kwargs[0]

        # start_at must be the next cron occurrence and be timezone-aware.
        assert kw["starts_at"].tzinfo is not None
        # end_at must be exactly DEFAULT_SCHEDULED_TASK_DURATION_MINUTES after start_at.
        assert kw["ends_at"] == kw["starts_at"] + timedelta(
            minutes=DEFAULT_SCHEDULED_TASK_DURATION_MINUTES
        )

    async def test_explicit_start_at_end_at_are_preserved(self):
        """If start_at/end_at are already set, they must not be overwritten."""
        mod = self._make_module()
        source_id = uuid.uuid4()
        explicit_start = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
        explicit_end = datetime(2026, 3, 2, 16, 0, tzinfo=UTC)
        row = self._make_task_row(start_at=explicit_start, end_at=explicit_end)
        mod._db.pool.fetch = AsyncMock(return_value=[row])

        captured_kwargs: list[dict] = []

        async def capture_event(**kwargs):
            captured_kwargs.append(kwargs)
            return uuid.uuid4()

        with (
            patch.object(mod, "_projection_tables_available", AsyncMock(return_value=True)),
            patch.object(mod, "_table_exists", AsyncMock(return_value=True)),
            patch.object(mod, "_ensure_calendar_source", AsyncMock(return_value=source_id)),
            patch.object(mod, "_upsert_projection_event", capture_event),
            patch.object(mod, "_upsert_projection_instance", AsyncMock()),
            patch.object(mod, "_mark_projection_source_stale_events_cancelled", AsyncMock()),
            patch.object(mod, "_upsert_projection_cursor", AsyncMock()),
        ):
            await mod._project_scheduler_source()

        assert len(captured_kwargs) == 1
        kw = captured_kwargs[0]
        assert kw["starts_at"] == explicit_start
        assert kw["ends_at"] == explicit_end

    async def test_null_end_at_with_explicit_start_at_uses_default_duration(self):
        """If start_at is set but end_at is NULL, end_at = start_at + default duration."""
        mod = self._make_module()
        source_id = uuid.uuid4()
        explicit_start = datetime(2026, 3, 3, 10, 0, tzinfo=UTC)
        row = self._make_task_row(start_at=explicit_start, end_at=None)
        mod._db.pool.fetch = AsyncMock(return_value=[row])

        captured_kwargs: list[dict] = []

        async def capture_event(**kwargs):
            captured_kwargs.append(kwargs)
            return uuid.uuid4()

        with (
            patch.object(mod, "_projection_tables_available", AsyncMock(return_value=True)),
            patch.object(mod, "_table_exists", AsyncMock(return_value=True)),
            patch.object(mod, "_ensure_calendar_source", AsyncMock(return_value=source_id)),
            patch.object(mod, "_upsert_projection_event", capture_event),
            patch.object(mod, "_upsert_projection_instance", AsyncMock()),
            patch.object(mod, "_mark_projection_source_stale_events_cancelled", AsyncMock()),
            patch.object(mod, "_upsert_projection_cursor", AsyncMock()),
        ):
            await mod._project_scheduler_source()

        assert len(captured_kwargs) == 1
        kw = captured_kwargs[0]
        assert kw["starts_at"] == explicit_start
        assert kw["ends_at"] == explicit_start + timedelta(
            minutes=DEFAULT_SCHEDULED_TASK_DURATION_MINUTES
        )

    async def test_row_without_cron_and_null_start_at_is_skipped(self):
        """A row with no cron expression and NULL start_at cannot produce a
        synthetic window — it should be silently skipped."""
        mod = self._make_module()
        source_id = uuid.uuid4()
        row = self._make_task_row(cron=None, start_at=None, end_at=None)
        mod._db.pool.fetch = AsyncMock(return_value=[row])

        upsert_event_mock = AsyncMock(return_value=uuid.uuid4())
        with (
            patch.object(mod, "_projection_tables_available", AsyncMock(return_value=True)),
            patch.object(mod, "_table_exists", AsyncMock(return_value=True)),
            patch.object(mod, "_ensure_calendar_source", AsyncMock(return_value=source_id)),
            patch.object(mod, "_upsert_projection_event", upsert_event_mock),
            patch.object(mod, "_upsert_projection_instance", AsyncMock()),
            patch.object(mod, "_mark_projection_source_stale_events_cancelled", AsyncMock()),
            patch.object(mod, "_upsert_projection_cursor", AsyncMock()),
        ):
            await mod._project_scheduler_source()

        # Nothing projected for rows without cron and without start_at.
        assert upsert_event_mock.await_count == 0

    async def test_multiple_toml_tasks_all_projected(self):
        """All rows — whether they have explicit or synthetic windows — appear
        in the projected output."""
        mod = self._make_module()
        source_id = uuid.uuid4()
        explicit_start = datetime(2026, 3, 4, 9, 0, tzinfo=UTC)
        explicit_end = datetime(2026, 3, 4, 10, 0, tzinfo=UTC)
        rows = [
            # Task with explicit window.
            self._make_task_row(
                name="morning-brief",
                start_at=explicit_start,
                end_at=explicit_end,
            ),
            # Task with no window — synthetic fallback from cron.
            self._make_task_row(
                name="evening-review",
                cron="0 18 * * *",
                start_at=None,
                end_at=None,
            ),
        ]
        mod._db.pool.fetch = AsyncMock(return_value=rows)

        upsert_event_mock = AsyncMock(return_value=uuid.uuid4())
        with (
            patch.object(mod, "_projection_tables_available", AsyncMock(return_value=True)),
            patch.object(mod, "_table_exists", AsyncMock(return_value=True)),
            patch.object(mod, "_ensure_calendar_source", AsyncMock(return_value=source_id)),
            patch.object(mod, "_upsert_projection_event", upsert_event_mock),
            patch.object(mod, "_upsert_projection_instance", AsyncMock()),
            patch.object(mod, "_mark_projection_source_stale_events_cancelled", AsyncMock()),
            patch.object(mod, "_upsert_projection_cursor", AsyncMock()),
        ):
            await mod._project_scheduler_source()

        # Both tasks must be projected.
        assert upsert_event_mock.await_count == 2


class TestCalendarProjectionFreshness:
    async def test_freshness_metadata_classifies_source_states(self):
        mod = CalendarModule()
        mod._config = CalendarConfig(
            provider="google",
            calendar_id="primary",
            sync=CalendarSyncConfig(enabled=True, interval_minutes=5),
        )
        pool = MagicMock()
        now = datetime.now(UTC)
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": uuid.uuid4(),
                    "source_key": "provider:google:primary",
                    "source_kind": SOURCE_KIND_PROVIDER,
                    "lane": "user",
                    "provider": "google",
                    "calendar_id": "primary",
                    "butler_name": None,
                    "cursor_name": "provider_sync",
                    "last_synced_at": now - timedelta(minutes=1),
                    "last_success_at": now - timedelta(minutes=1),
                    "last_error_at": None,
                    "last_error": None,
                    "full_sync_required": False,
                },
                {
                    "id": uuid.uuid4(),
                    "source_key": "internal_scheduler:general",
                    "source_kind": SOURCE_KIND_INTERNAL_SCHEDULER,
                    "lane": "butler",
                    "provider": "internal",
                    "calendar_id": None,
                    "butler_name": "general",
                    "cursor_name": "projection",
                    "last_synced_at": now - timedelta(hours=3),
                    "last_success_at": now - timedelta(hours=3),
                    "last_error_at": None,
                    "last_error": None,
                    "full_sync_required": False,
                },
                {
                    "id": uuid.uuid4(),
                    "source_key": "internal_reminders:general",
                    "source_kind": SOURCE_KIND_INTERNAL_REMINDERS,
                    "lane": "butler",
                    "provider": "internal",
                    "calendar_id": None,
                    "butler_name": "general",
                    "cursor_name": "projection",
                    "last_synced_at": now - timedelta(minutes=2),
                    "last_success_at": now - timedelta(hours=1),
                    "last_error_at": now - timedelta(minutes=2),
                    "last_error": "boom",
                    "full_sync_required": False,
                },
            ]
        )
        db = MagicMock()
        db.pool = pool
        mod._db = db

        with patch.object(mod, "_projection_tables_available", AsyncMock(return_value=True)):
            freshness = await mod._projection_freshness_metadata()

        assert freshness["available"] is True
        assert len(freshness["sources"]) == 3
        states = {source["source_key"]: source["sync_state"] for source in freshness["sources"]}
        assert states["provider:google:primary"] == "fresh"
        assert states["internal_scheduler:general"] == "stale"
        assert states["internal_reminders:general"] == "failed"


class TestCronOccurrencesInWindow:
    """Unit tests for _cron_occurrences_in_window helper."""

    def test_daily_cron_returns_occurrences_in_window(self):
        """A daily cron returns ~90 occurrences across a 90-day window."""
        now = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        window_end = now + timedelta(days=90)
        result = _cron_occurrences_in_window("0 9 * * *", window_start=now, window_end=window_end)
        # Expect roughly 90 occurrences (one per day).
        assert 88 <= len(result) <= 91

    def test_each_result_is_starts_at_ends_at_tuple(self):
        now = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        window_end = now + timedelta(days=2)
        result = _cron_occurrences_in_window("0 9 * * *", window_start=now, window_end=window_end)
        for starts_at, ends_at in result:
            assert isinstance(starts_at, datetime)
            assert isinstance(ends_at, datetime)
            assert ends_at > starts_at

    def test_occurrences_respect_window_end(self):
        """No occurrence is returned that falls after window_end."""
        now = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        window_end = now + timedelta(hours=48)
        result = _cron_occurrences_in_window("0 9 * * *", window_start=now, window_end=window_end)
        for starts_at, _ in result:
            assert starts_at <= window_end

    def test_duration_parameter_controls_ends_at(self):
        now = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        window_end = now + timedelta(days=1)
        result = _cron_occurrences_in_window(
            "0 9 * * *", window_start=now, window_end=window_end, duration_minutes=30
        )
        assert len(result) >= 1
        starts_at, ends_at = result[0]
        assert (ends_at - starts_at).total_seconds() == 30 * 60

    def test_empty_window_returns_no_occurrences(self):
        """When window_start equals window_end, no occurrences are returned."""
        ts = datetime(2026, 3, 1, 9, 1, tzinfo=UTC)  # Just after the cron fires.
        result = _cron_occurrences_in_window("0 9 * * *", window_start=ts, window_end=ts)
        assert result == []

    def test_results_are_utc_aware(self):
        now = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        window_end = now + timedelta(days=3)
        result = _cron_occurrences_in_window("0 9 * * *", window_start=now, window_end=window_end)
        for starts_at, ends_at in result:
            assert starts_at.tzinfo is not None
            assert ends_at.tzinfo is not None

    def test_weekday_only_cron_skips_weekends(self):
        """A Mon-Fri cron should yield 5 occurrences per week."""
        # 2026-03-02 is a Monday.
        monday = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)
        window_end = monday + timedelta(days=7)
        result = _cron_occurrences_in_window(
            "0 9 * * 1-5", window_start=monday, window_end=window_end
        )
        assert len(result) == 5


class TestRruleOccurrencesInWindow:
    """Unit tests for _rrule_occurrences_in_window helper."""

    def test_daily_rrule_returns_occurrences_in_window(self):
        dtstart = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
        now = dtstart
        window_end = now + timedelta(days=90)
        result = _rrule_occurrences_in_window(
            "FREQ=DAILY", dtstart=dtstart, window_start=now, window_end=window_end
        )
        assert 88 <= len(result) <= 91

    def test_rrule_prefix_is_optional(self):
        """Both 'FREQ=DAILY' and 'RRULE:FREQ=DAILY' should work."""
        dtstart = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
        now = dtstart
        window_end = now + timedelta(days=3)
        r1 = _rrule_occurrences_in_window(
            "FREQ=DAILY", dtstart=dtstart, window_start=now, window_end=window_end
        )
        r2 = _rrule_occurrences_in_window(
            "RRULE:FREQ=DAILY", dtstart=dtstart, window_start=now, window_end=window_end
        )
        assert r1 == r2

    def test_weekly_rrule_returns_correct_count(self):
        dtstart = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)  # Monday
        now = dtstart
        window_end = now + timedelta(weeks=4)
        result = _rrule_occurrences_in_window(
            "FREQ=WEEKLY;BYDAY=MO",
            dtstart=dtstart,
            window_start=now,
            window_end=window_end,
        )
        assert len(result) == 5  # 4 complete weeks + start date occurrence

    def test_occurrences_respect_window_start(self):
        """Occurrences before window_start must be excluded."""
        dtstart = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
        window_start = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        window_end = window_start + timedelta(days=10)
        result = _rrule_occurrences_in_window(
            "FREQ=DAILY",
            dtstart=dtstart,
            window_start=window_start,
            window_end=window_end,
        )
        for starts_at, _ in result:
            assert starts_at >= window_start

    def test_rrule_with_count_stops_at_count(self):
        dtstart = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
        now = dtstart
        window_end = now + timedelta(days=90)
        result = _rrule_occurrences_in_window(
            "FREQ=DAILY;COUNT=5",
            dtstart=dtstart,
            window_start=now,
            window_end=window_end,
        )
        assert len(result) == 5

    def test_invalid_rrule_returns_empty_list(self):
        dtstart = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
        result = _rrule_occurrences_in_window(
            "NOT_A_VALID_RULE",
            dtstart=dtstart,
            window_start=dtstart,
            window_end=dtstart + timedelta(days=10),
        )
        assert result == []

    def test_results_are_utc_aware(self):
        dtstart = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
        now = dtstart
        window_end = now + timedelta(days=5)
        result = _rrule_occurrences_in_window(
            "FREQ=DAILY", dtstart=dtstart, window_start=now, window_end=window_end
        )
        for starts_at, ends_at in result:
            assert starts_at.tzinfo is not None
            assert ends_at.tzinfo is not None

    def test_duration_controls_ends_at(self):
        dtstart = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
        now = dtstart
        window_end = now + timedelta(days=2)
        result = _rrule_occurrences_in_window(
            "FREQ=DAILY",
            dtstart=dtstart,
            window_start=now,
            window_end=window_end,
            duration_minutes=45,
        )
        for starts_at, ends_at in result:
            assert (ends_at - starts_at).total_seconds() == 45 * 60


class TestWindowedRecurrenceExpansion:
    """Integration-style tests for the windowed projection of recurring sources."""

    def _make_module_with_projection_db(self) -> CalendarModule:
        mod = CalendarModule()
        mod._config = CalendarConfig(
            provider="google",
            calendar_id="primary",
            sync=CalendarSyncConfig(enabled=True),
        )
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        pool.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
        pool.fetchval = AsyncMock(return_value=None)
        pool.execute = AsyncMock(return_value="OK")
        db = MagicMock()
        db.pool = pool
        db.db_name = "butler_general"
        mod._db = db
        mod._butler_name = "general"
        return mod

    async def test_cron_recurring_task_expands_to_multiple_instances(self):
        """A task with cron and no start_at should produce multiple instances (90-day window)."""
        mod = self._make_module_with_projection_db()
        source_id = uuid.uuid4()
        task_id = uuid.uuid4()
        now = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)

        mod._db.pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": task_id,
                    "name": "medication",
                    "cron": "0 9 * * *",
                    "dispatch_mode": "prompt",
                    "prompt": "take meds",
                    "job_name": None,
                    "job_args": None,
                    "timezone": "UTC",
                    "start_at": None,  # Recurring — no explicit start_at
                    "end_at": None,
                    "until_at": None,
                    "display_title": "Morning Medication",
                    "calendar_event_id": None,
                    "enabled": True,
                    "updated_at": now,
                }
            ]
        )

        upsert_instance_mock = AsyncMock(return_value=uuid.uuid4())
        with (
            patch.object(mod, "_projection_tables_available", AsyncMock(return_value=True)),
            patch.object(mod, "_table_exists", AsyncMock(return_value=True)),
            patch.object(mod, "_ensure_calendar_source", AsyncMock(return_value=source_id)),
            patch.object(mod, "_upsert_projection_event", AsyncMock(return_value=uuid.uuid4())),
            patch.object(mod, "_upsert_projection_instance", upsert_instance_mock),
            patch.object(mod, "_prune_recurring_instances_outside_window", AsyncMock()),
            patch.object(mod, "_mark_projection_source_stale_events_cancelled", AsyncMock()),
            patch.object(mod, "_upsert_projection_cursor", AsyncMock()),
        ):
            await mod._project_scheduler_source()

        # Should have created ~90 instances for a daily cron over 90 days.
        instance_count = upsert_instance_mock.await_count
        assert instance_count >= 88, f"Expected >=88 instances for daily cron, got {instance_count}"

    async def test_cron_recurring_task_uses_isoformat_origin_instance_ref(self):
        """origin_instance_ref for cron occurrences uses ISO datetime format."""
        mod = self._make_module_with_projection_db()
        source_id = uuid.uuid4()
        task_id = uuid.uuid4()
        now = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)

        mod._db.pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": task_id,
                    "name": "daily-task",
                    "cron": "0 9 * * *",
                    "dispatch_mode": "prompt",
                    "prompt": "do stuff",
                    "job_name": None,
                    "job_args": None,
                    "timezone": "UTC",
                    "start_at": None,
                    "end_at": None,
                    "until_at": None,
                    "display_title": "Daily",
                    "calendar_event_id": None,
                    "enabled": True,
                    "updated_at": now,
                }
            ]
        )

        captured_refs: list[str] = []

        async def capture_instance(**kwargs):
            captured_refs.append(kwargs["origin_instance_ref"])
            return uuid.uuid4()

        with (
            patch.object(mod, "_projection_tables_available", AsyncMock(return_value=True)),
            patch.object(mod, "_table_exists", AsyncMock(return_value=True)),
            patch.object(mod, "_ensure_calendar_source", AsyncMock(return_value=source_id)),
            patch.object(mod, "_upsert_projection_event", AsyncMock(return_value=uuid.uuid4())),
            patch.object(mod, "_upsert_projection_instance", capture_instance),
            patch.object(mod, "_prune_recurring_instances_outside_window", AsyncMock()),
            patch.object(mod, "_mark_projection_source_stale_events_cancelled", AsyncMock()),
            patch.object(mod, "_upsert_projection_cursor", AsyncMock()),
        ):
            await mod._project_scheduler_source()

        assert len(captured_refs) >= 1
        for ref in captured_refs:
            # origin_instance_ref must be "{task_id}:{iso_datetime}"
            assert ref.startswith(str(task_id) + ":")
            # The part after the first colon must be a parseable ISO datetime.
            ts_part = ref[len(str(task_id)) + 1 :]
            parsed = datetime.fromisoformat(ts_part)
            assert parsed.tzinfo is not None

    async def test_recurring_reminder_rrule_expands_to_multiple_instances(self):
        """A reminder with RRULE=FREQ=DAILY should yield multiple instances."""
        mod = self._make_module_with_projection_db()
        source_id = uuid.uuid4()
        reminder_id = uuid.uuid4()
        # Use a past dtstart so occurrences are generated from now through the full window.
        trigger_at = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)

        mod._db.pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": reminder_id,
                    "label": "Take medication",
                    "message": "Take medication",
                    "type": "recurring",
                    "reminder_type": "recurring",
                    "contact_id": None,
                    "timezone": "UTC",
                    "next_trigger_at": trigger_at,
                    "due_at": trigger_at,
                    "until_at": None,
                    "calendar_event_id": None,
                    "dismissed": False,
                    "updated_at": trigger_at,
                    "recurrence_rule": "FREQ=DAILY",
                }
            ]
        )

        upsert_instance_mock = AsyncMock(return_value=uuid.uuid4())
        with (
            patch.object(mod, "_projection_tables_available", AsyncMock(return_value=True)),
            patch.object(mod, "_table_exists", AsyncMock(return_value=True)),
            patch.object(mod, "_ensure_calendar_source", AsyncMock(return_value=source_id)),
            patch.object(mod, "_upsert_projection_event", AsyncMock(return_value=uuid.uuid4())),
            patch.object(mod, "_upsert_projection_instance", upsert_instance_mock),
            patch.object(mod, "_prune_recurring_instances_outside_window", AsyncMock()),
            patch.object(mod, "_mark_projection_event_cancelled", AsyncMock()),
            patch.object(mod, "_mark_projection_source_stale_events_cancelled", AsyncMock()),
            patch.object(mod, "_upsert_projection_cursor", AsyncMock()),
        ):
            await mod._project_reminders_source()

        instance_count = upsert_instance_mock.await_count
        assert instance_count >= 85, (
            f"Expected >=85 instances for daily RRULE over 90-day window, got {instance_count}"
        )

    async def test_one_time_reminder_produces_single_instance(self):
        """A reminder with no recurrence_rule always yields exactly one instance."""
        mod = self._make_module_with_projection_db()
        source_id = uuid.uuid4()
        reminder_id = uuid.uuid4()
        trigger_at = datetime(2026, 3, 5, 10, 0, tzinfo=UTC)

        mod._db.pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": reminder_id,
                    "label": "Doctor appointment",
                    "message": "Go to doctor",
                    "type": "one_time",
                    "reminder_type": "one_time",
                    "contact_id": None,
                    "timezone": "UTC",
                    "next_trigger_at": trigger_at,
                    "due_at": trigger_at,
                    "until_at": None,
                    "calendar_event_id": None,
                    "dismissed": False,
                    "updated_at": trigger_at,
                    "recurrence_rule": None,
                }
            ]
        )

        upsert_instance_mock = AsyncMock(return_value=uuid.uuid4())
        with (
            patch.object(mod, "_projection_tables_available", AsyncMock(return_value=True)),
            patch.object(mod, "_table_exists", AsyncMock(return_value=True)),
            patch.object(mod, "_ensure_calendar_source", AsyncMock(return_value=source_id)),
            patch.object(mod, "_upsert_projection_event", AsyncMock(return_value=uuid.uuid4())),
            patch.object(mod, "_upsert_projection_instance", upsert_instance_mock),
            patch.object(mod, "_prune_recurring_instances_outside_window", AsyncMock()),
            patch.object(mod, "_mark_projection_event_cancelled", AsyncMock()),
            patch.object(mod, "_mark_projection_source_stale_events_cancelled", AsyncMock()),
            patch.object(mod, "_upsert_projection_cursor", AsyncMock()),
        ):
            await mod._project_reminders_source()

        assert upsert_instance_mock.await_count == 1

    async def test_dismissed_recurring_reminder_produces_single_instance(self):
        """A dismissed recurring reminder should not be expanded — just one instance."""
        mod = self._make_module_with_projection_db()
        source_id = uuid.uuid4()
        reminder_id = uuid.uuid4()
        trigger_at = datetime(2026, 3, 5, 10, 0, tzinfo=UTC)

        mod._db.pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": reminder_id,
                    "label": "Snoozed",
                    "message": "Snoozed reminder",
                    "type": "recurring",
                    "reminder_type": "recurring",
                    "contact_id": None,
                    "timezone": "UTC",
                    "next_trigger_at": trigger_at,
                    "due_at": trigger_at,
                    "until_at": None,
                    "calendar_event_id": None,
                    "dismissed": True,
                    "updated_at": trigger_at,
                    "recurrence_rule": "FREQ=DAILY",
                }
            ]
        )

        upsert_instance_mock = AsyncMock(return_value=uuid.uuid4())
        with (
            patch.object(mod, "_projection_tables_available", AsyncMock(return_value=True)),
            patch.object(mod, "_table_exists", AsyncMock(return_value=True)),
            patch.object(mod, "_ensure_calendar_source", AsyncMock(return_value=source_id)),
            patch.object(mod, "_upsert_projection_event", AsyncMock(return_value=uuid.uuid4())),
            patch.object(mod, "_upsert_projection_instance", upsert_instance_mock),
            patch.object(mod, "_prune_recurring_instances_outside_window", AsyncMock()),
            patch.object(mod, "_mark_projection_event_cancelled", AsyncMock()),
            patch.object(mod, "_mark_projection_source_stale_events_cancelled", AsyncMock()),
            patch.object(mod, "_upsert_projection_cursor", AsyncMock()),
        ):
            await mod._project_reminders_source()

        assert upsert_instance_mock.await_count == 1

    async def test_prune_called_for_recurring_cron_task(self):
        """_prune_recurring_instances_outside_window must be called for cron tasks."""
        mod = self._make_module_with_projection_db()
        source_id = uuid.uuid4()
        task_id = uuid.uuid4()
        now = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)

        mod._db.pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": task_id,
                    "name": "daily",
                    "cron": "0 8 * * *",
                    "dispatch_mode": "prompt",
                    "prompt": "daily run",
                    "job_name": None,
                    "job_args": None,
                    "timezone": "UTC",
                    "start_at": None,
                    "end_at": None,
                    "until_at": None,
                    "display_title": "Daily",
                    "calendar_event_id": None,
                    "enabled": True,
                    "updated_at": now,
                }
            ]
        )

        prune_mock = AsyncMock()
        with (
            patch.object(mod, "_projection_tables_available", AsyncMock(return_value=True)),
            patch.object(mod, "_table_exists", AsyncMock(return_value=True)),
            patch.object(mod, "_ensure_calendar_source", AsyncMock(return_value=source_id)),
            patch.object(mod, "_upsert_projection_event", AsyncMock(return_value=uuid.uuid4())),
            patch.object(mod, "_upsert_projection_instance", AsyncMock(return_value=uuid.uuid4())),
            patch.object(mod, "_prune_recurring_instances_outside_window", prune_mock),
            patch.object(mod, "_mark_projection_source_stale_events_cancelled", AsyncMock()),
            patch.object(mod, "_upsert_projection_cursor", AsyncMock()),
        ):
            await mod._project_scheduler_source()

        assert prune_mock.await_count == 1
        prune_kwargs = prune_mock.await_args.kwargs
        assert "event_id" in prune_kwargs
        assert "window_start" in prune_kwargs
        assert "window_end" in prune_kwargs
        # window_end should be ~90 days after window_start.
        window_delta = prune_kwargs["window_end"] - prune_kwargs["window_start"]
        assert abs(window_delta.days - RECURRENCE_PROJECTION_WINDOW_DAYS) <= 1

    async def test_explicit_start_at_task_not_expanded(self):
        """A task with explicit start_at should produce exactly one instance."""
        mod = self._make_module_with_projection_db()
        source_id = uuid.uuid4()
        task_id = uuid.uuid4()
        explicit_start = datetime(2026, 3, 10, 14, 0, tzinfo=UTC)
        explicit_end = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)

        mod._db.pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": task_id,
                    "name": "one-time-meeting",
                    "cron": None,
                    "dispatch_mode": "prompt",
                    "prompt": "attend meeting",
                    "job_name": None,
                    "job_args": None,
                    "timezone": "UTC",
                    "start_at": explicit_start,
                    "end_at": explicit_end,
                    "until_at": None,
                    "display_title": "Board Meeting",
                    "calendar_event_id": None,
                    "enabled": True,
                    "updated_at": explicit_start,
                }
            ]
        )

        upsert_instance_mock = AsyncMock(return_value=uuid.uuid4())
        with (
            patch.object(mod, "_projection_tables_available", AsyncMock(return_value=True)),
            patch.object(mod, "_table_exists", AsyncMock(return_value=True)),
            patch.object(mod, "_ensure_calendar_source", AsyncMock(return_value=source_id)),
            patch.object(mod, "_upsert_projection_event", AsyncMock(return_value=uuid.uuid4())),
            patch.object(mod, "_upsert_projection_instance", upsert_instance_mock),
            patch.object(mod, "_prune_recurring_instances_outside_window", AsyncMock()),
            patch.object(mod, "_mark_projection_source_stale_events_cancelled", AsyncMock()),
            patch.object(mod, "_upsert_projection_cursor", AsyncMock()),
        ):
            await mod._project_scheduler_source()

        assert upsert_instance_mock.await_count == 1
        instance_kwargs = upsert_instance_mock.await_args.kwargs
        assert instance_kwargs["starts_at"] == explicit_start
        assert instance_kwargs["ends_at"] == explicit_end
