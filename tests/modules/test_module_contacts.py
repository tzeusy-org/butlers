"""Condensed contacts module tests — behavioral contract only.

Replaces test_module_contacts.py (77), test_contacts_backfill.py (50),
test_contacts_sync.py (34), test_contacts_telegram_provider.py (32),
test_canonical_contact_fields.py (27), test_google_contact_payload_mapping.py (58)
= ~278 tests replaced with ~25.

Covers:
- Module ABC compliance
- ContactsConfig validation (provider required, multi-provider, normalization)
- Registry discovery
- Google contact payload parsing (key field extraction)
- Sync state roundtrip
- Backfill resolver helpers (display_name, group_label)

[bu-7sd7a]
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from butlers.modules.base import Module
from butlers.modules.contacts import (
    ContactsConfig,
    ContactsModule,
    ProviderEntry,
)
from butlers.modules.contacts.backfill import (
    _build_display_name,
    _normalize_group_label,
)
from butlers.modules.contacts.sync import (
    CanonicalContact,
    ContactsSyncState,
    _parse_google_contact,
)
from butlers.modules.registry import default_registry

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABCCompliance:
    def test_module_contract(self) -> None:
        """ContactsModule satisfies Module ABC: name, config_schema, dependencies, revisions."""
        mod = ContactsModule()
        assert issubclass(ContactsModule, Module)
        assert mod.name == "contacts"
        assert mod.config_schema is ContactsConfig
        assert mod.dependencies == []
        assert mod.migration_revisions() == "contacts"
        assert "contacts" in default_registry().available_modules


# ---------------------------------------------------------------------------
# ContactsConfig validation
# ---------------------------------------------------------------------------


class TestContactsConfig:
    def test_provider_required(self) -> None:
        with pytest.raises(ValidationError):
            ContactsConfig()

    def test_defaults(self) -> None:
        config = ContactsConfig(provider="google")
        assert config.provider == "google"
        assert config.include_other_contacts is False
        # Only the owning butler (Relationship) opts in; a new butler never silently syncs.
        assert config.sync.enabled is False
        assert config.sync.interval_minutes == 15

    def test_legacy_provider_creates_providers_list(self) -> None:
        config = ContactsConfig(provider="google")
        assert config.providers == [ProviderEntry(type="google")]

    def test_providers_list_accepted(self) -> None:
        config = ContactsConfig(providers=[{"type": "google"}, {"type": "telegram"}])
        assert len(config.providers) == 2

    def test_both_provider_and_providers_raises(self) -> None:
        with pytest.raises(ValidationError, match="Cannot specify both"):
            ContactsConfig(provider="google", providers=[{"type": "google"}])

    def test_duplicate_provider_types_raises(self) -> None:
        with pytest.raises(ValidationError, match="distinct"):
            ContactsConfig(providers=[{"type": "google"}, {"type": "google"}])

    def test_provider_normalization(self) -> None:
        config = ContactsConfig(provider="  GOOGLE  ")
        assert config.provider == "google"

    def test_multi_account_with_distinct_accounts_succeeds(self) -> None:
        config = ContactsConfig(
            providers=[
                {"type": "google", "account": "personal@gmail.com"},
                {"type": "google", "account": "work@gmail.com"},
            ]
        )
        assert len(config.providers) == 2

    def test_whitespace_only_provider_raises(self) -> None:
        with pytest.raises(ValidationError):
            ContactsConfig(provider="   ")

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ContactsConfig(provider="google", unsupported=True)


# ---------------------------------------------------------------------------
# Google contact payload mapping
# ---------------------------------------------------------------------------


class TestGoogleContactParsing:
    def _make_raw(self, **kwargs) -> dict:
        base = {
            "resourceName": "people/c123",
            "etag": "abc",
            "names": [
                {
                    "givenName": "Alice",
                    "familyName": "Smith",
                    "displayName": "Alice Smith",
                    "metadata": {"primary": True},
                }
            ],
            "emailAddresses": [{"value": "alice@example.com", "metadata": {"primary": True}}],
        }
        base.update(kwargs)
        return base

    def test_parse_basic_contact(self) -> None:
        raw = self._make_raw()
        contact = _parse_google_contact(raw)
        assert isinstance(contact, CanonicalContact)
        assert contact.first_name == "Alice"
        assert contact.last_name == "Smith"
        assert len(contact.emails) >= 1

    def test_parse_phone_numbers(self) -> None:
        raw = self._make_raw(
            phoneNumbers=[{"value": "+1-555-555-1234", "metadata": {"primary": True}}]
        )
        contact = _parse_google_contact(raw)
        assert len(contact.phones) >= 1

    def test_parse_organization(self) -> None:
        raw = self._make_raw(
            organizations=[
                {"name": "Acme Corp", "title": "Engineer", "metadata": {"primary": True}}
            ]
        )
        contact = _parse_google_contact(raw)
        assert len(contact.organizations) >= 1
        # ContactOrganization maps 'name' → 'company'
        assert contact.organizations[0].company == "Acme Corp"


# ---------------------------------------------------------------------------
# Backfill helpers
# ---------------------------------------------------------------------------


class TestBackfillHelpers:
    def test_build_display_name_from_parts(self):
        contact = CanonicalContact(external_id="people/c1", first_name="Alice", last_name="Smith")
        assert _build_display_name(contact) == "Alice Smith"

    def test_build_display_name_from_display(self):
        contact = CanonicalContact(external_id="people/c2", display_name="Bob Jones")
        assert _build_display_name(contact) == "Bob Jones"

    def test_normalize_group_label_from_resource(self):
        result = _normalize_group_label("contactGroups/friends")
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Sync state roundtrip
# ---------------------------------------------------------------------------


class TestContactsSyncState:
    def test_default_state(self) -> None:
        state = ContactsSyncState()
        assert state.sync_cursor is None
        assert state.last_full_sync_at is None

    def test_model_dump_roundtrip(self) -> None:
        state = ContactsSyncState(sync_cursor="tok-123", last_error=None)
        restored = ContactsSyncState(**state.model_dump())
        assert restored.sync_cursor == state.sync_cursor


# bu-dridrs: full-cycle completion, never a per-contact/post-save approximation.
class _CycleStore:
    def __init__(self, state):
        self.state = state
        self.saves = []

    async def load(self, **kwargs):
        return self.state.model_copy(deep=True)

    async def save(self, *, state, **kwargs):
        self.saves.append(state.model_copy(deep=True))
        self.state = state.model_copy(deep=True)


class _FullFinalizer:
    def __init__(self, store, events):
        self.store, self.events = store, events
        self.calls = []

    async def begin_sync(self, **kwargs):
        self.events.append("baseline")
        return {}

    async def finalize_sync(self, **kwargs):
        self.events.append("finalize")
        self.calls.append(kwargs)
        await self.store.save(state=kwargs["next_state"])
        return 0

    async def record_failure(self, *, state_store, expected_state, expired_cursor):
        state = expected_state.model_copy(deep=True)
        state.last_error = "contacts sync failed"
        if expired_cursor:
            state.sync_cursor = None
        await state_store.save(state=state)


@pytest.mark.parametrize(
    "case",
    [
        "complete",
        "late_failure",
        "missing_cursor",
        "cancel",
        "expired",
        "incremental_list",
        "incremental_noop",
    ],
)
async def test_telegram_full_cycle_completion_disposition(case):
    import asyncio

    from butlers.modules.contacts.sync import (
        ContactBatch,
        ContactsSyncEngine,
        ContactsSyncError,
        ContactsSyncTokenExpiredError,
    )

    events = []
    store = _CycleStore(ContactsSyncState(sync_cursor="previous"))
    finalizer = _FullFinalizer(store, events)
    contact = CanonicalContact(external_id="telegram:10", raw={"id": 10})

    class Provider:
        name = "telegram"

        async def full_sync(self, *, account_id, page_token=None):
            events.append("page:" + str(page_token))
            if page_token is not None:
                if case == "late_failure":
                    raise ContactsSyncError("synthetic page failure")
                if case == "cancel":
                    raise asyncio.CancelledError()
                return ContactBatch(
                    next_sync_cursor=None if case == "missing_cursor" else "next",
                    checkpoint={"complete_full_fetch": True},
                )
            return ContactBatch(
                contacts=[contact],
                next_page_token="second",
                checkpoint={"complete_full_fetch": True},
            )

        async def incremental_sync(self, **kwargs):
            if case == "expired":
                raise ContactsSyncTokenExpiredError("expired")
            return ContactBatch(
                contacts=[] if case == "incremental_noop" else [contact],
                next_sync_cursor="previous",
            )

    async def apply(value):
        assert value.external_id == contact.external_id
        events.append("apply")

    engine = ContactsSyncEngine(
        provider=Provider(), state_store=store, apply_contact=apply, batch_finalizer=finalizer
    )
    mode = "incremental" if case in {"expired", "incremental_list", "incremental_noop"} else "full"
    if case in {"late_failure", "missing_cursor", "cancel"}:
        error = asyncio.CancelledError if case == "cancel" else ContactsSyncError
        with pytest.raises(error):
            await engine.sync(account_id="account-a", mode=mode)
        assert finalizer.calls == []
        assert "apply" not in events
        assert store.state.sync_cursor == "previous"
        assert store.state.contact_versions == {}
        if case == "missing_cursor":
            # An earlier page cursor cannot certify guarded terminal completion.
            observations = {}
            for terminal_cursor in [None, "", "terminal-valid"]:
                cycle_store = _CycleStore(ContactsSyncState(sync_cursor="previous"))
                cycle_finalizer = _FullFinalizer(cycle_store, [])
                applied = []

                class EarlierCursorProvider:
                    name = "telegram"

                    async def full_sync(
                        self, *, account_id, page_token=None, terminal_cursor=terminal_cursor
                    ):
                        return ContactBatch(
                            contacts=[contact] if page_token is None else [],
                            next_page_token="terminal" if page_token is None else None,
                            next_sync_cursor="earlier" if page_token is None else terminal_cursor,
                            checkpoint={"complete_full_fetch": True},
                        )

                async def observed_apply(value, *, applied=applied):
                    applied.append(value)

                guarded = ContactsSyncEngine(
                    provider=EarlierCursorProvider(),
                    state_store=cycle_store,
                    apply_contact=observed_apply,
                    batch_finalizer=cycle_finalizer,
                )
                try:
                    outcome = await guarded.sync(account_id="account-a", mode="full")
                except ContactsSyncError:
                    outcome = None
                observations[terminal_cursor] = (
                    outcome is None,
                    len(applied),
                    len(cycle_finalizer.calls),
                    cycle_store.state.sync_cursor,
                    bool(cycle_store.state.contact_versions),
                    cycle_store.state.last_success_at is None,
                )
                if terminal_cursor == "terminal-valid":
                    assert outcome is not None and outcome.applied_contacts == 1
                    assert applied == [contact] and len(cycle_finalizer.calls) == 1
                    assert cycle_finalizer.calls[0]["next_state"].sync_cursor == terminal_cursor
                    assert cycle_store.state.sync_cursor == terminal_cursor
                    assert set(cycle_store.state.contact_versions) == {contact.external_id}
                    assert cycle_store.state.last_success_at is not None

                # Non-guarded Google keeps its existing optional terminal cursor behavior.
                class GoogleEarlierCursorProvider(EarlierCursorProvider):
                    name = "google"

                    async def full_sync(self, **kwargs):
                        batch = await super().full_sync(**kwargs)
                        return batch.model_copy(update={"checkpoint": None})

                google_store = _CycleStore(ContactsSyncState())
                google = ContactsSyncEngine(
                    provider=GoogleEarlierCursorProvider(),
                    state_store=google_store,
                    apply_contact=observed_apply,
                )
                assert (
                    await google.sync(account_id="google-account", mode="full")
                ).applied_contacts == 1
                assert google_store.state.sync_cursor == (terminal_cursor or "earlier")
                assert len(google_store.saves) == 1
            assert {key: observations[key] for key in [None, ""]} == {
                None: (True, 0, 0, "previous", False, True),
                "": (True, 0, 0, "previous", False, True),
            }
    else:
        result = await engine.sync(account_id="account-a", mode=mode)
        assert len(finalizer.calls) == 1
        assert finalizer.calls[0]["mode"] == ("full" if case == "expired" else mode)
        assert finalizer.calls[0]["contacts"] == ([] if case == "incremental_noop" else [contact])
        assert events[-1] == "finalize"
        if mode == "full" or case == "expired":
            assert events.index("apply") > events.index("page:second")
            assert store.state.sync_cursor == "next"
        assert result.deleted_contacts == 0
        assert len(store.saves) == 1
        if case == "complete":
            # An inactive source anchor overrides an identical cached live version.
            async def inactive_baseline(**kwargs):
                return {contact.external_id: {"deleted_at": "synthetic-inactive"}}

            finalizer.begin_sync = inactive_baseline
            reappeared = await engine.sync(account_id="account-a", mode="full")
            assert reappeared.applied_contacts == 1 and reappeared.skipped_contacts == 0
            # All malformed FULL species fail before any contact or finalizer work.
            for bad in [
                ContactBatch(contacts=[contact], next_sync_cursor="bad"),
                ContactBatch(
                    contacts=[contact, contact],
                    next_sync_cursor="bad",
                    checkpoint={"complete_full_fetch": True},
                ),
                ContactBatch(
                    contacts=[contact.model_copy(update={"deleted": True})],
                    next_sync_cursor="bad",
                    checkpoint={"complete_full_fetch": True},
                ),
                ContactBatch(
                    contacts=[contact],
                    next_page_token="repeated",
                    checkpoint={"complete_full_fetch": True},
                ),
            ]:

                class BadProvider:
                    name = "telegram"

                    async def full_sync(self, *, bad=bad, **kwargs):
                        return bad

                unchanged = store.state.model_copy(deep=True)
                calls_before = len(finalizer.calls)
                broken = ContactsSyncEngine(
                    provider=BadProvider(),
                    state_store=store,
                    apply_contact=apply,
                    batch_finalizer=finalizer,
                )
                with pytest.raises(ContactsSyncError):
                    await broken.sync(account_id="account-a", mode="full")
                assert len(finalizer.calls) == calls_before
                assert store.state.sync_cursor == unchanged.sync_cursor
                assert store.state.contact_versions == unchanged.contact_versions

            # Existing non-guarded provider contracts do not require a Telegram receipt.
            class GoogleProvider(Provider):
                name = "google"

                async def full_sync(self, **kwargs):
                    batch = await super().full_sync(**kwargs)
                    return batch.model_copy(update={"checkpoint": None})

            google_store = _CycleStore(ContactsSyncState())
            google = ContactsSyncEngine(
                provider=GoogleProvider(), state_store=google_store, apply_contact=apply
            )
            assert (
                await google.sync(account_id="google-account", mode="full")
            ).applied_contacts == 1
            assert google_store.state.sync_cursor == "next" and len(google_store.saves) == 1
        if case == "expired":
            # Failed full fallback must invalidate only the known-expired cursor.
            class FailedFallback(Provider):
                async def full_sync(self, **kwargs):
                    raise ContactsSyncError("synthetic failed fallback")

            expected_versions = dict(store.state.contact_versions)
            calls_before = len(finalizer.calls)
            broken = ContactsSyncEngine(
                provider=FailedFallback(),
                state_store=store,
                apply_contact=apply,
                batch_finalizer=finalizer,
            )
            with pytest.raises(ContactsSyncError):
                await broken.sync(account_id="account-a", mode="incremental")
            assert len(finalizer.calls) == calls_before
            assert store.state.sync_cursor is None
            assert store.state.contact_versions == expected_versions


async def test_telegram_provider_equal_hash_keeps_full_list_and_full_receipt(monkeypatch):
    from unittest.mock import AsyncMock

    from telethon.tl.types import Contact, User
    from telethon.tl.types.contacts import Contacts, ContactsNotModified

    from butlers.modules.contacts.backfill import ContactBackfillEngine
    from butlers.modules.contacts.sync import ContactsSyncError
    from butlers.modules.contacts.telegram_provider import TelegramContactsProvider

    guard = ContactBackfillEngine(object(), provider="telegram", account_id="synthetic")
    assert guard._max_shrink_fraction == 0.5
    for invalid in [float("nan"), float("inf"), -0.1, 1.0, True]:
        with pytest.raises(ValueError):
            ContactBackfillEngine(
                object(),
                provider="telegram",
                account_id="synthetic",
                max_shrink_fraction=invalid,
            )

    provider = TelegramContactsProvider(
        api_id=123, api_hash="synthetic", session_string="synthetic"
    )
    response = Contacts(contacts=[], saved_count=1, users=[User(id=10, first_name="Synthetic")])
    client = AsyncMock(return_value=response)
    monkeypatch.setattr(provider, "_ensure_client", AsyncMock(return_value=client))
    full = await provider.full_sync(account_id="account-a")
    assert full.checkpoint == {"complete_full_fetch": True}
    same = await provider.incremental_sync(account_id="account-a", cursor=full.next_sync_cursor)
    assert same.contacts == full.contacts and same.next_sync_cursor == full.next_sync_cursor
    assert len(same.contacts) == 1
    assert all(call.args[0].hash == 0 for call in client.call_args_list)
    for incomplete in [
        ContactsNotModified(),
        Contacts(contacts=[Contact(user_id=10, mutual=False)], saved_count=1, users=[]),
    ]:
        client.return_value = incomplete
        with pytest.raises(ContactsSyncError):
            await provider.full_sync(account_id="account-a")


async def test_contacts_startup_poll_and_sync_now_share_full_finalizer(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastmcp import Client, FastMCP

    import butlers.modules.contacts as contacts_module
    from butlers.modules.contacts.sync import ContactBatch, ContactsSyncRuntime

    events = []
    store = _CycleStore(ContactsSyncState())
    finalizer = _FullFinalizer(store, events)
    entered, release, manual_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Provider:
        name = "telegram"
        calls = 0

        async def full_sync(self, **kwargs):
            self.calls += 1
            entered.set()
            await release.wait()
            return ContactBatch(next_sync_cursor="next", checkpoint={"complete_full_fetch": True})

        async def incremental_sync(self, **kwargs):
            return await self.full_sync(**kwargs)

    provider = Provider()
    module, mcp = ContactsModule(), FastMCP("synthetic-contacts-cycle")
    monkeypatch.setattr(module, "_create_provider", AsyncMock(return_value=provider))
    monkeypatch.setattr(contacts_module, "ContactsSyncStateStore", lambda pool: store)
    monkeypatch.setattr(contacts_module, "ContactBackfillEngine", lambda *args, **kwargs: finalizer)
    monkeypatch.setattr(ContactsSyncRuntime, "start", AsyncMock())
    await module.register_tools(
        mcp, {"provider": "telegram"}, SimpleNamespace(pool=object()), "general"
    )
    await module.on_startup(
        {"provider": "telegram", "sync": {"enabled": True}}, SimpleNamespace(pool=object())
    )
    runtime = module._runtimes["telegram"]
    engine = runtime._sync_engine
    original_sync = engine.sync
    calls = 0

    async def observed_sync(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            manual_entered.set()
        return await original_sync(**kwargs)

    monkeypatch.setattr(engine, "sync", observed_sync)
    async with Client(mcp) as client:
        poll = asyncio.create_task(runtime.run_sync_cycle())
        manual = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            manual = asyncio.create_task(
                client.call_tool("contacts_sync_now", {"provider": "telegram", "mode": "full"})
            )
            await asyncio.wait_for(manual_entered.wait(), 5)
            assert provider.calls == 1 and not finalizer.calls
            release.set()
            await poll
            result = await manual
            assert not result.is_error
            assert len(finalizer.calls) == 2 and len(store.saves) == 2
            assert all(c["mode"] == "full" for c in finalizer.calls)
        finally:
            release.set()
            tasks = [task for task in [poll, manual] if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
    # Cancellation releases the same account lock, so a later full cycle can run.
    entered.clear()
    release.clear()
    cancelled = asyncio.create_task(runtime.run_sync_cycle())
    await asyncio.wait_for(entered.wait(), 5)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    release.set()
    await asyncio.wait_for(original_sync(account_id="default", mode="full"), 5)
    assert len(finalizer.calls) == 3
