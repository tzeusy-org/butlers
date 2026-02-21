"""Unit tests for contacts sync core (Google provider + sync engine/state)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from butlers.modules.contacts.sync import (
    GOOGLE_OAUTH_TOKEN_URL,
    GOOGLE_PEOPLE_API_CONNECTIONS_URL,
    CanonicalContact,
    ContactBatch,
    ContactsProvider,
    ContactsSyncEngine,
    ContactsSyncError,
    ContactsSyncResult,
    ContactsSyncRuntime,
    ContactsSyncState,
    ContactsSyncStateStore,
    ContactsSyncTokenExpiredError,
    GoogleContactsProvider,
)

pytestmark = pytest.mark.unit


class _InMemorySyncStore:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str], ContactsSyncState] = {}

    async def load(self, *, provider: str, account_id: str) -> ContactsSyncState:
        return self.states.get((provider, account_id), ContactsSyncState())

    async def save(self, *, provider: str, account_id: str, state: ContactsSyncState) -> None:
        self.states[(provider, account_id)] = state.model_copy(deep=True)


class _ProviderDouble(ContactsProvider):
    def __init__(self) -> None:
        self.full_pages: list[ContactBatch] = []
        self.incremental_pages: list[ContactBatch] = []
        self.raise_incremental: Exception | None = None
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "double"

    async def full_sync(self, *, account_id: str, page_token: str | None = None) -> ContactBatch:
        self.calls.append({"mode": "full", "account_id": account_id, "page_token": page_token})
        if not self.full_pages:
            raise AssertionError("No full pages configured")
        index = len([c for c in self.calls if c["mode"] == "full"]) - 1
        index = min(index, len(self.full_pages) - 1)
        return self.full_pages[index]

    async def incremental_sync(
        self,
        *,
        account_id: str,
        cursor: str,
        page_token: str | None = None,
    ) -> ContactBatch:
        self.calls.append(
            {
                "mode": "incremental",
                "account_id": account_id,
                "cursor": cursor,
                "page_token": page_token,
            }
        )
        if self.raise_incremental is not None:
            raise self.raise_incremental
        if not self.incremental_pages:
            raise AssertionError("No incremental pages configured")
        index = len([c for c in self.calls if c["mode"] == "incremental"]) - 1
        index = min(index, len(self.incremental_pages) - 1)
        return self.incremental_pages[index]

    async def shutdown(self) -> None:
        return None


class _SyncEngineDouble:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def sync(self, *, account_id: str, mode: str = "incremental") -> ContactsSyncResult:
        self.calls.append({"account_id": account_id, "mode": mode})
        return ContactsSyncResult(
            mode=mode,
            fetched_contacts=0,
            applied_contacts=0,
            skipped_contacts=0,
            deleted_contacts=0,
            next_sync_cursor="cursor-next",
        )


def _contact(external_id: str, etag: str, *, deleted: bool = False) -> CanonicalContact:
    return CanonicalContact(
        external_id=external_id,
        etag=etag,
        display_name=f"Name {external_id}",
        deleted=deleted,
        raw={"resourceName": external_id, "etag": etag, "metadata": {"deleted": deleted}},
    )


class TestGoogleContactsProvider:
    async def _make_provider(
        self,
        handler: Callable[[httpx.Request], httpx.Response],
    ) -> GoogleContactsProvider:
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        provider = GoogleContactsProvider(
            client_id="cid",
            client_secret="secret",
            refresh_token="rtok",
            http_client=client,
        )
        return provider

    async def test_full_sync_requests_sync_token_and_parses_people(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if str(request.url) == GOOGLE_OAUTH_TOKEN_URL:
                return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
            if str(request.url).startswith(GOOGLE_PEOPLE_API_CONNECTIONS_URL):
                return httpx.Response(
                    200,
                    json={
                        "connections": [
                            {
                                "resourceName": "people/1",
                                "etag": "etag-1",
                                "names": [
                                    {
                                        "displayName": "Alice Example",
                                        "givenName": "Alice",
                                        "familyName": "Example",
                                        "metadata": {"primary": True},
                                    }
                                ],
                                "emailAddresses": [
                                    {
                                        "value": "Alice@Example.com",
                                        "type": "home",
                                        "metadata": {"primary": True},
                                    }
                                ],
                                "phoneNumbers": [
                                    {"value": "+1 555-1000", "canonicalForm": "+15551000"}
                                ],
                                "memberships": [
                                    {
                                        "contactGroupMembership": {
                                            "contactGroupResourceName": "contactGroups/friends"
                                        }
                                    }
                                ],
                            }
                        ],
                        "nextSyncToken": "sync-1",
                    },
                )
            return httpx.Response(500, json={"error": {"message": "unexpected request"}})

        provider = await self._make_provider(handler)
        try:
            batch = await provider.full_sync(account_id="acct-1")
        finally:
            await provider.shutdown()

        people_call = requests[-1]
        assert people_call.url.path == "/v1/people/me/connections"
        assert people_call.url.params.get("requestSyncToken") == "true"
        assert people_call.url.params.get("personFields") is not None

        assert batch.next_sync_cursor == "sync-1"
        assert len(batch.contacts) == 1
        assert batch.contacts[0].external_id == "people/1"
        assert batch.contacts[0].emails[0].normalized_value == "alice@example.com"
        assert batch.contacts[0].group_memberships == ["contactGroups/friends"]

    async def test_incremental_sync_410_raises_token_expired(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == GOOGLE_OAUTH_TOKEN_URL:
                return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
            if str(request.url).startswith(GOOGLE_PEOPLE_API_CONNECTIONS_URL):
                return httpx.Response(410, json={"error": {"message": "Sync token is expired"}})
            return httpx.Response(500)

        provider = await self._make_provider(handler)
        try:
            with pytest.raises(ContactsSyncTokenExpiredError):
                await provider.incremental_sync(account_id="acct-1", cursor="expired-cursor")
        finally:
            await provider.shutdown()


class TestContactsSyncStateStore:
    async def test_state_round_trip_uses_provider_account_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        store_data: dict[str, Any] = {}

        async def fake_state_get(pool: Any, key: str) -> Any:
            assert pool == "pool"
            return store_data.get(key)

        async def fake_state_set(pool: Any, key: str, value: Any) -> int:
            assert pool == "pool"
            store_data[key] = value
            return 1

        monkeypatch.setattr("butlers.modules.contacts.sync._state_get", fake_state_get)
        monkeypatch.setattr("butlers.modules.contacts.sync._state_set", fake_state_set)

        store = ContactsSyncStateStore("pool")
        state = ContactsSyncState(sync_cursor="tok-1", last_success_at="2026-02-20T10:00:00+00:00")
        await store.save(provider="google", account_id="acct-1", state=state)

        loaded = await store.load(provider="google", account_id="acct-1")
        assert loaded.sync_cursor == "tok-1"
        assert loaded.last_success_at == "2026-02-20T10:00:00+00:00"
        assert "contacts::sync::google::acct-1" in store_data


class TestContactsSyncEngine:
    async def test_no_cursor_runs_full_sync_and_persists_state(self):
        provider = _ProviderDouble()
        provider.full_pages = [
            ContactBatch(contacts=[_contact("people/1", "v1")], next_page_token="page-2"),
            ContactBatch(contacts=[_contact("people/2", "v2")], next_sync_cursor="cursor-new"),
        ]
        store = _InMemorySyncStore()

        applied: list[str] = []

        async def apply_contact(contact: CanonicalContact) -> None:
            applied.append(contact.external_id)

        engine = ContactsSyncEngine(
            provider=provider,
            state_store=store,
            apply_contact=apply_contact,
        )
        result = await engine.sync(account_id="acct-1", mode="incremental")

        assert result.mode == "full"
        assert result.fetched_contacts == 2
        assert result.applied_contacts == 2
        assert result.skipped_contacts == 0
        assert applied == ["people/1", "people/2"]

        saved = await store.load(provider="double", account_id="acct-1")
        assert saved.sync_cursor == "cursor-new"
        assert saved.last_full_sync_at is not None
        assert saved.last_error is None

    async def test_incremental_sync_skips_unchanged_contacts_idempotently(self):
        provider = _ProviderDouble()
        provider.incremental_pages = [
            ContactBatch(
                contacts=[_contact("people/1", "v1"), _contact("people/2", "v2")],
                next_sync_cursor="cursor-next",
            )
        ]
        store = _InMemorySyncStore()
        await store.save(
            provider="double",
            account_id="acct-1",
            state=ContactsSyncState(
                sync_cursor="cursor-old",
                contact_versions={"people/1": "etag:v1:deleted=0"},
            ),
        )

        applied: list[str] = []

        async def apply_contact(contact: CanonicalContact) -> None:
            applied.append(contact.external_id)

        engine = ContactsSyncEngine(
            provider=provider,
            state_store=store,
            apply_contact=apply_contact,
        )
        result = await engine.sync(account_id="acct-1", mode="incremental")

        assert result.mode == "incremental"
        assert result.fetched_contacts == 2
        assert result.applied_contacts == 1
        assert result.skipped_contacts == 1
        assert applied == ["people/2"]

        saved = await store.load(provider="double", account_id="acct-1")
        assert saved.sync_cursor == "cursor-next"
        assert saved.contact_versions["people/1"] == "etag:v1:deleted=0"
        assert saved.contact_versions["people/2"] == "etag:v2:deleted=0"

    async def test_incremental_token_expired_falls_back_to_full(self):
        provider = _ProviderDouble()
        provider.raise_incremental = ContactsSyncTokenExpiredError("expired")
        provider.full_pages = [
            ContactBatch(
                contacts=[_contact("people/1", "v2")],
                next_sync_cursor="cursor-after-full",
            )
        ]
        store = _InMemorySyncStore()
        await store.save(
            provider="double",
            account_id="acct-1",
            state=ContactsSyncState(sync_cursor="expired-cursor"),
        )

        applied: list[str] = []

        async def apply_contact(contact: CanonicalContact) -> None:
            applied.append(contact.external_id)

        engine = ContactsSyncEngine(
            provider=provider,
            state_store=store,
            apply_contact=apply_contact,
        )
        result = await engine.sync(account_id="acct-1", mode="incremental")

        assert result.mode == "full"
        assert applied == ["people/1"]

        assert provider.calls[0]["mode"] == "incremental"
        assert provider.calls[1]["mode"] == "full"
        saved = await store.load(provider="double", account_id="acct-1")
        assert saved.sync_cursor == "cursor-after-full"
        assert saved.last_error is None

    async def test_failure_updates_last_error_state(self):
        provider = _ProviderDouble()
        provider.full_pages = [
            ContactBatch(contacts=[_contact("people/1", "v1")], next_sync_cursor="cursor-final")
        ]
        store = _InMemorySyncStore()

        async def apply_contact(contact: CanonicalContact) -> None:
            raise ContactsSyncError(f"failed apply for {contact.external_id}")

        engine = ContactsSyncEngine(
            provider=provider,
            state_store=store,
            apply_contact=apply_contact,
        )

        with pytest.raises(ContactsSyncError):
            await engine.sync(account_id="acct-1", mode="full")

        saved = await store.load(provider="double", account_id="acct-1")
        assert saved.last_error is not None
        assert "failed apply for people/1" in saved.last_error


class TestContactsSyncRuntime:
    async def test_no_cursor_forces_full_sync_cycle(self):
        engine = _SyncEngineDouble()
        store = _InMemorySyncStore()
        runtime = ContactsSyncRuntime(
            sync_engine=engine,
            state_store=store,
            provider_name="double",
            account_id="acct-1",
        )

        await runtime.run_sync_cycle()
        assert engine.calls == [{"account_id": "acct-1", "mode": "full"}]

    async def test_recent_full_sync_uses_incremental_mode(self):
        now = datetime(2026, 2, 21, 12, 0, tzinfo=UTC)
        engine = _SyncEngineDouble()
        store = _InMemorySyncStore()
        await store.save(
            provider="double",
            account_id="acct-1",
            state=ContactsSyncState(
                sync_cursor="cursor-existing",
                last_full_sync_at=(now - timedelta(days=1)).isoformat(),
            ),
        )
        runtime = ContactsSyncRuntime(
            sync_engine=engine,
            state_store=store,
            provider_name="double",
            account_id="acct-1",
            now_fn=lambda: now,
        )

        await runtime.run_sync_cycle()
        assert engine.calls == [{"account_id": "acct-1", "mode": "incremental"}]

    async def test_stale_cursor_age_forces_full_sync(self):
        now = datetime(2026, 2, 21, 12, 0, tzinfo=UTC)
        engine = _SyncEngineDouble()
        store = _InMemorySyncStore()
        await store.save(
            provider="double",
            account_id="acct-1",
            state=ContactsSyncState(
                sync_cursor="cursor-existing",
                cursor_issued_at=(now - timedelta(days=7)).isoformat(),
            ),
        )
        runtime = ContactsSyncRuntime(
            sync_engine=engine,
            state_store=store,
            provider_name="double",
            account_id="acct-1",
            now_fn=lambda: now,
        )

        await runtime.run_sync_cycle()
        assert engine.calls == [{"account_id": "acct-1", "mode": "full"}]

    async def test_poller_runs_immediately_and_supports_force_trigger(self):
        engine = _SyncEngineDouble()
        store = _InMemorySyncStore()
        await store.save(
            provider="double",
            account_id="acct-1",
            state=ContactsSyncState(
                sync_cursor="cursor-existing",
                last_full_sync_at=datetime.now(UTC).isoformat(),
            ),
        )
        runtime = ContactsSyncRuntime(
            sync_engine=engine,
            state_store=store,
            provider_name="double",
            account_id="acct-1",
            incremental_interval=timedelta(hours=1),
        )

        try:
            await runtime.start()
            for _ in range(40):
                if len(engine.calls) >= 1:
                    break
                await asyncio.sleep(0.01)
            assert len(engine.calls) >= 1

            runtime.trigger_immediate_sync()
            for _ in range(40):
                if len(engine.calls) >= 2:
                    break
                await asyncio.sleep(0.01)
            assert len(engine.calls) >= 2
        finally:
            await runtime.stop()
