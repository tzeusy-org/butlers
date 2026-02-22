"""Unit tests for contacts sync core (Google provider + sync engine/state)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from butlers.modules.contacts.sync import (
    GOOGLE_CONTACT_GROUPS_URL,
    GOOGLE_OAUTH_TOKEN_URL,
    GOOGLE_PEOPLE_API_CONNECTIONS_URL,
    CanonicalContact,
    CanonicalGroup,
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
    GroupBatch,
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

    async def validate_credentials(self) -> None:
        return None

    async def list_groups(
        self,
        *,
        account_id: str,
        page_token: str | None = None,
    ) -> GroupBatch:
        return GroupBatch()

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


class TestGoogleContactsProviderValidateCredentials:
    async def _make_provider(
        self,
        handler,
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

    async def test_validate_credentials_refreshes_token_and_makes_lightweight_call(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if str(request.url) == GOOGLE_OAUTH_TOKEN_URL:
                return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
            if str(request.url).startswith(GOOGLE_PEOPLE_API_CONNECTIONS_URL):
                # Lightweight call with pageSize=1 and personFields=names
                return httpx.Response(200, json={"connections": [], "nextSyncToken": "tok"})
            return httpx.Response(500, json={"error": {"message": "unexpected request"}})

        provider = await self._make_provider(handler)
        try:
            await provider.validate_credentials()
        finally:
            await provider.shutdown()

        # First request is OAuth token refresh, second is lightweight People API call
        assert len(requests) == 2
        token_req = requests[0]
        assert str(token_req.url) == GOOGLE_OAUTH_TOKEN_URL
        people_req = requests[1]
        assert people_req.url.params.get("pageSize") == "1"
        assert people_req.url.params.get("personFields") == "names"

    async def test_validate_credentials_raises_on_bad_refresh_token(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == GOOGLE_OAUTH_TOKEN_URL:
                return httpx.Response(
                    401,
                    json={"error": "invalid_grant", "error_description": "Token has been expired"},
                )
            return httpx.Response(500)

        from butlers.modules.contacts.sync import ContactsTokenRefreshError

        provider = await self._make_provider(handler)
        try:
            with pytest.raises(ContactsTokenRefreshError):
                await provider.validate_credentials()
        finally:
            await provider.shutdown()


class TestGoogleContactsProviderListGroups:
    async def _make_provider(
        self,
        handler,
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

    async def test_list_groups_fetches_and_parses_contact_groups(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if str(request.url) == GOOGLE_OAUTH_TOKEN_URL:
                return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
            if str(request.url).startswith(GOOGLE_CONTACT_GROUPS_URL):
                return httpx.Response(
                    200,
                    json={
                        "contactGroups": [
                            {
                                "resourceName": "contactGroups/friends",
                                "name": "Friends",
                                "groupType": "USER_CONTACT_GROUP",
                                "memberCount": 42,
                            },
                            {
                                "resourceName": "contactGroups/myContacts",
                                "name": "My Contacts",
                                "groupType": "SYSTEM_CONTACT_GROUP",
                                "memberCount": 100,
                            },
                        ],
                        "nextPageToken": None,
                    },
                )
            return httpx.Response(500, json={"error": {"message": "unexpected request"}})

        provider = await self._make_provider(handler)
        try:
            batch = await provider.list_groups(account_id="acct-1")
        finally:
            await provider.shutdown()

        assert len(batch.groups) == 2
        assert batch.next_page_token is None

        friends = batch.groups[0]
        assert friends.external_id == "contactGroups/friends"
        assert friends.name == "Friends"
        assert friends.group_type == "USER_CONTACT_GROUP"
        assert friends.member_count == 42

        my_contacts = batch.groups[1]
        assert my_contacts.external_id == "contactGroups/myContacts"
        assert my_contacts.name == "My Contacts"
        assert my_contacts.group_type == "SYSTEM_CONTACT_GROUP"
        assert my_contacts.member_count == 100

        groups_req = requests[-1]
        assert groups_req.url.path == "/v1/contactGroups"
        assert groups_req.url.params.get("pageSize") == "1000"

    async def test_list_groups_respects_pagination(self):
        page_tokens_received: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == GOOGLE_OAUTH_TOKEN_URL:
                return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
            if str(request.url).startswith(GOOGLE_CONTACT_GROUPS_URL):
                page_token = request.url.params.get("pageToken")
                page_tokens_received.append(page_token)
                if page_token is None:
                    return httpx.Response(
                        200,
                        json={
                            "contactGroups": [
                                {"resourceName": "contactGroups/1", "name": "Group 1"},
                            ],
                            "nextPageToken": "page-2-token",
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "contactGroups": [
                            {"resourceName": "contactGroups/2", "name": "Group 2"},
                        ],
                    },
                )
            return httpx.Response(500)

        provider = await self._make_provider(handler)
        try:
            # First page
            batch1 = await provider.list_groups(account_id="acct-1")
            assert len(batch1.groups) == 1
            assert batch1.next_page_token == "page-2-token"
            assert batch1.groups[0].external_id == "contactGroups/1"

            # Second page
            batch2 = await provider.list_groups(account_id="acct-1", page_token="page-2-token")
            assert len(batch2.groups) == 1
            assert batch2.next_page_token is None
            assert batch2.groups[0].external_id == "contactGroups/2"
        finally:
            await provider.shutdown()

        # Verify the page token was forwarded correctly
        assert "page-2-token" in page_tokens_received

    async def test_list_groups_skips_items_without_resource_name(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == GOOGLE_OAUTH_TOKEN_URL:
                return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
            if str(request.url).startswith(GOOGLE_CONTACT_GROUPS_URL):
                return httpx.Response(
                    200,
                    json={
                        "contactGroups": [
                            {"name": "No Resource Name Here"},  # should be skipped
                            {"resourceName": "contactGroups/valid", "name": "Valid Group"},
                        ],
                    },
                )
            return httpx.Response(500)

        provider = await self._make_provider(handler)
        try:
            batch = await provider.list_groups(account_id="acct-1")
        finally:
            await provider.shutdown()

        assert len(batch.groups) == 1
        assert batch.groups[0].external_id == "contactGroups/valid"


class TestGroupBatchModel:
    def test_canonical_group_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            CanonicalGroup(external_id="contactGroups/1", name="Test", unknown_field="x")

    def test_group_batch_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            GroupBatch(groups=[], unknown_field="x")

    def test_group_batch_page_token_normalization(self):
        batch = GroupBatch(groups=[], next_page_token="  token-1  ")
        assert batch.next_page_token == "token-1"

    def test_group_batch_empty_page_token_becomes_none(self):
        batch = GroupBatch(groups=[], next_page_token="  ")
        assert batch.next_page_token is None

    def test_canonical_group_member_count_optional(self):
        group = CanonicalGroup(external_id="contactGroups/1", name="Friends")
        assert group.member_count is None
        assert group.group_type is None


class _FakePool:
    """In-memory asyncpg pool double for ContactsSyncStateStore tests."""

    def __init__(self) -> None:
        # Maps (provider, account_id) -> row dict or None
        self._rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        provider, account_id = args[0], args[1]
        return self._rows.get((provider, account_id))

    async def execute(self, query: str, *args: Any) -> str:
        import json

        self.execute_calls.append((query, args))
        # Simulate upsert: extract positional params
        # Args order: provider, account_id, sync_cursor, cursor_issued_at,
        #             last_full_sync_at, last_incremental_sync_at,
        #             last_success_at, last_error, contact_versions
        provider, account_id = args[0], args[1]
        self._rows[(provider, account_id)] = {
            "sync_cursor": args[2],
            "cursor_issued_at": args[3],
            "last_full_sync_at": args[4],
            "last_incremental_sync_at": args[5],
            "last_success_at": args[6],
            "last_error": args[7],
            "contact_versions": (
                json.loads(args[8]) if args[8] is not None and args[8] != "" else {}
            ),
        }
        return "INSERT 0 1"


class TestContactsSyncStateStore:
    async def test_load_returns_empty_state_when_no_row_exists(self) -> None:
        """load() returns a default ContactsSyncState when no DB row is found."""
        pool = _FakePool()
        store = ContactsSyncStateStore(pool)
        state = await store.load(provider="google", account_id="acct-1")
        assert isinstance(state, ContactsSyncState)
        assert state.sync_cursor is None
        assert state.last_error is None
        assert state.contact_versions == {}

    async def test_save_and_load_round_trip(self) -> None:
        """save() persists state and load() retrieves it correctly."""
        pool = _FakePool()
        store = ContactsSyncStateStore(pool)

        original = ContactsSyncState(
            sync_cursor="tok-abc",
            last_full_sync_at="2026-02-20T08:00:00+00:00",
            last_success_at="2026-02-20T10:00:00+00:00",
            last_error=None,
            contact_versions={"people/1": "etag:v1:deleted=0"},
        )
        await store.save(provider="google", account_id="acct-1", state=original)
        loaded = await store.load(provider="google", account_id="acct-1")

        assert loaded.sync_cursor == "tok-abc"
        assert loaded.last_full_sync_at == "2026-02-20T08:00:00+00:00"
        assert loaded.last_success_at == "2026-02-20T10:00:00+00:00"
        assert loaded.last_error is None
        assert loaded.contact_versions == {"people/1": "etag:v1:deleted=0"}

    async def test_save_normalizes_provider_and_account(self) -> None:
        """save() normalizes provider to lowercase and strips account_id."""
        pool = _FakePool()
        store = ContactsSyncStateStore(pool)

        state = ContactsSyncState(sync_cursor="tok-1")
        await store.save(provider="  GOOGLE  ", account_id="  acct-1  ", state=state)

        # Should be stored under normalized keys
        assert ("google", "acct-1") in pool._rows

    async def test_load_normalizes_provider_and_account(self) -> None:
        """load() normalizes provider to lowercase and strips account_id."""
        pool = _FakePool()
        store = ContactsSyncStateStore(pool)

        state = ContactsSyncState(sync_cursor="tok-norm")
        await store.save(provider="google", account_id="acct-1", state=state)

        # Loading with non-normalized keys should still find the row
        loaded = await store.load(provider="  GOOGLE  ", account_id="  acct-1  ")
        assert loaded.sync_cursor == "tok-norm"

    async def test_key_helper_returns_legacy_format(self) -> None:
        """key() returns the legacy KV key string for backward-compatibility."""
        store = ContactsSyncStateStore(None)  # type: ignore[arg-type]
        key = store.key(provider="Google", account_id="  my-acct  ")
        assert key == "contacts::sync::google::my-acct"

    async def test_save_issues_upsert_query(self) -> None:
        """save() calls pool.execute with an INSERT ... ON CONFLICT query."""
        pool = _FakePool()
        store = ContactsSyncStateStore(pool)

        state = ContactsSyncState(sync_cursor="tok-upsert")
        await store.save(provider="google", account_id="acct-1", state=state)

        assert len(pool.execute_calls) == 1
        query, _ = pool.execute_calls[0]
        assert "ON CONFLICT" in query
        assert "contacts_sync_state" in query

    async def test_save_multiple_accounts_independently(self) -> None:
        """save() stores state independently per (provider, account_id) pair."""
        pool = _FakePool()
        store = ContactsSyncStateStore(pool)

        state_a = ContactsSyncState(sync_cursor="cursor-a")
        state_b = ContactsSyncState(sync_cursor="cursor-b")
        await store.save(provider="google", account_id="acct-a", state=state_a)
        await store.save(provider="google", account_id="acct-b", state=state_b)

        loaded_a = await store.load(provider="google", account_id="acct-a")
        loaded_b = await store.load(provider="google", account_id="acct-b")
        assert loaded_a.sync_cursor == "cursor-a"
        assert loaded_b.sync_cursor == "cursor-b"

    async def test_save_overwrites_previous_state(self) -> None:
        """Saving updated state replaces the previous row for the same key."""
        pool = _FakePool()
        store = ContactsSyncStateStore(pool)

        state1 = ContactsSyncState(sync_cursor="tok-first")
        await store.save(provider="google", account_id="acct-1", state=state1)

        state2 = ContactsSyncState(sync_cursor="tok-second", last_error="oops")
        await store.save(provider="google", account_id="acct-1", state=state2)

        loaded = await store.load(provider="google", account_id="acct-1")
        assert loaded.sync_cursor == "tok-second"
        assert loaded.last_error == "oops"


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
