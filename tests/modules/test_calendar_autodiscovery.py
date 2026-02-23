"""Tests for calendar ID auto-discovery via credential store and Google Calendar API."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from butlers.modules.calendar import (
    _CALENDAR_DISCOVERY_NAME,
    _CREDENTIAL_KEY_CALENDAR_ID,
    CalendarModule,
)

pytestmark = pytest.mark.unit

DISCOVERED_CALENDAR_ID = "discovered123@group.calendar.google.com"
STORED_CALENDAR_ID = "stored456@group.calendar.google.com"


def _make_credential_store(
    *,
    calendar_id: str | None = None,
    include_oauth: bool = True,
) -> AsyncMock:
    """Build a mock credential store with optional calendar ID and OAuth keys."""
    store = AsyncMock()

    async def _resolve(key: str, env_fallback: bool = False) -> str | None:
        assert env_fallback is False
        values: dict[str, str] = {}
        if include_oauth:
            values.update(
                {
                    "GOOGLE_OAUTH_CLIENT_ID": "test-client-id",
                    "GOOGLE_OAUTH_CLIENT_SECRET": "test-client-secret",
                    "GOOGLE_REFRESH_TOKEN": "test-refresh-token",
                }
            )
        if calendar_id is not None:
            values[_CREDENTIAL_KEY_CALENDAR_ID] = calendar_id
        return values.get(key)

    store.resolve.side_effect = _resolve
    return store


def _make_google_provider_mock(
    *,
    created_calendar_id: str = DISCOVERED_CALENDAR_ID,
) -> AsyncMock:
    """Build a mock _GoogleProvider with discover_or_create_calendar support."""
    provider = AsyncMock()
    provider.name = "google"

    async def _discover_or_create(name: str) -> str:
        return created_calendar_id

    provider.discover_or_create_calendar.side_effect = _discover_or_create
    return provider


class TestResolveFromCredentialStore:
    async def test_returns_stored_id_without_api_call(self):
        """When credential store has GOOGLE_CALENDAR_ID, use it directly."""
        store = _make_credential_store(calendar_id=STORED_CALENDAR_ID)
        mod = CalendarModule()
        mod._provider = _make_google_provider_mock()

        result = await mod._resolve_startup_calendar_id(store)

        assert result == STORED_CALENDAR_ID
        mod._provider.discover_or_create_calendar.assert_not_called()

    async def test_does_not_persist_already_stored_id(self):
        """No store() call when ID came from credential store."""
        store = _make_credential_store(calendar_id=STORED_CALENDAR_ID)
        mod = CalendarModule()
        mod._provider = _make_google_provider_mock()

        await mod._resolve_startup_calendar_id(store)

        store.store.assert_not_called()


class TestDiscoverExistingCalendar:
    async def test_discovers_via_provider(self):
        """When credential store has no calendar ID, discover via API."""
        store = _make_credential_store(calendar_id=None)
        mod = CalendarModule()
        provider = _make_google_provider_mock(created_calendar_id=DISCOVERED_CALENDAR_ID)
        mod._provider = provider

        result = await mod._resolve_startup_calendar_id(store)

        assert result == DISCOVERED_CALENDAR_ID
        provider.discover_or_create_calendar.assert_awaited_once_with(_CALENDAR_DISCOVERY_NAME)


class TestPersistsToCredentialStore:
    async def test_persists_discovered_id(self):
        """After discovery, persist the ID to the credential store."""
        store = _make_credential_store(calendar_id=None)
        mod = CalendarModule()
        mod._provider = _make_google_provider_mock(created_calendar_id=DISCOVERED_CALENDAR_ID)

        await mod._resolve_startup_calendar_id(store)

        store.store.assert_awaited_once_with(
            _CREDENTIAL_KEY_CALENDAR_ID,
            DISCOVERED_CALENDAR_ID,
            category="google",
            description="Auto-discovered Google Calendar ID for the Butlers calendar",
            is_sensitive=False,
        )


class TestNoCredentialStore:
    async def test_discovery_works_without_credential_store(self):
        """When no credential store is provided, discovery still works."""
        mod = CalendarModule()
        mod._provider = _make_google_provider_mock(created_calendar_id=DISCOVERED_CALENDAR_ID)

        result = await mod._resolve_startup_calendar_id(None)

        assert result == DISCOVERED_CALENDAR_ID

    async def test_persistence_skipped_without_credential_store(self):
        """No persistence attempt when credential store is None."""
        mod = CalendarModule()
        provider = _make_google_provider_mock()
        mod._provider = provider

        # Should not raise even though there's no store to persist to.
        await mod._resolve_startup_calendar_id(None)


class TestDiscoverOrCreateCalendarOnProvider:
    """Integration-style tests for _GoogleProvider.discover_or_create_calendar."""

    async def test_finds_existing_calendar_by_name(self):
        """Discover a calendar whose summary matches the target name."""
        from butlers.modules.calendar import _GoogleProvider

        provider = _GoogleProvider.__new__(_GoogleProvider)

        call_log: list[tuple] = []

        async def fake_request(method, path, *, params=None, json_body=None, extra_headers=None):
            call_log.append((method, path))
            if method == "GET" and path == "/users/me/calendarList":
                return {
                    "items": [
                        {"summary": "Personal", "id": "personal@example.com"},
                        {"summary": "Butlers", "id": DISCOVERED_CALENDAR_ID},
                    ],
                }
            raise AssertionError(f"Unexpected request: {method} {path}")

        provider._request_google_json = fake_request

        result = await provider.discover_or_create_calendar("Butlers")
        assert result == DISCOVERED_CALENDAR_ID
        assert len(call_log) == 1
        assert call_log[0] == ("GET", "/users/me/calendarList")

    async def test_creates_calendar_when_not_found(self):
        """When no matching calendar exists, create a new one."""
        from butlers.modules.calendar import _GoogleProvider

        provider = _GoogleProvider.__new__(_GoogleProvider)

        call_log: list[tuple] = []
        created_id = "new-calendar-id@group.calendar.google.com"

        async def fake_request(method, path, *, params=None, json_body=None, extra_headers=None):
            call_log.append((method, path, json_body))
            if method == "GET" and path == "/users/me/calendarList":
                return {"items": [{"summary": "Personal", "id": "personal@example.com"}]}
            if method == "POST" and path == "/calendars":
                assert json_body == {"summary": "Butlers"}
                return {"id": created_id}
            raise AssertionError(f"Unexpected request: {method} {path}")

        provider._request_google_json = fake_request

        result = await provider.discover_or_create_calendar("Butlers")
        assert result == created_id
        assert len(call_log) == 2
        assert call_log[0][:2] == ("GET", "/users/me/calendarList")
        assert call_log[1][:2] == ("POST", "/calendars")

    async def test_pagination_finds_calendar_on_second_page(self):
        """Calendar discovery paginates through calendarList."""
        from butlers.modules.calendar import _GoogleProvider

        provider = _GoogleProvider.__new__(_GoogleProvider)
        page_count = 0

        async def fake_request(method, path, *, params=None, json_body=None, extra_headers=None):
            nonlocal page_count
            if method == "GET" and path == "/users/me/calendarList":
                page_count += 1
                if params and params.get("pageToken") == "page2":
                    return {
                        "items": [
                            {"summary": "Butlers", "id": DISCOVERED_CALENDAR_ID},
                        ],
                    }
                return {
                    "items": [{"summary": "Work", "id": "work@example.com"}],
                    "nextPageToken": "page2",
                }
            raise AssertionError(f"Unexpected request: {method} {path}")

        provider._request_google_json = fake_request

        result = await provider.discover_or_create_calendar("Butlers")
        assert result == DISCOVERED_CALENDAR_ID
        assert page_count == 2

    async def test_empty_calendar_list_creates_new(self):
        """When the calendar list is completely empty, create a new calendar."""
        from butlers.modules.calendar import _GoogleProvider

        provider = _GoogleProvider.__new__(_GoogleProvider)
        created_id = "brand-new@group.calendar.google.com"

        async def fake_request(method, path, *, params=None, json_body=None, extra_headers=None):
            if method == "GET" and path == "/users/me/calendarList":
                return {"items": []}
            if method == "POST" and path == "/calendars":
                return {"id": created_id}
            raise AssertionError(f"Unexpected request: {method} {path}")

        provider._request_google_json = fake_request

        result = await provider.discover_or_create_calendar("Butlers")
        assert result == created_id


class TestOnStartupResolvesCalendarId:
    """Verify on_startup wires calendar ID resolution end-to-end."""

    async def test_startup_resolves_calendar_id_from_store(self):
        store = _make_credential_store(calendar_id=STORED_CALENDAR_ID)
        mod = CalendarModule()

        await mod.on_startup({"provider": "google"}, db=None, credential_store=store)

        assert mod._resolved_calendar_id == STORED_CALENDAR_ID
