"""Boundaries and privacy of per-contact isolation in the sync engine (bu-z0gofs)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from butlers.modules.contacts.backfill import ContactBackfillEngine
from butlers.modules.contacts.sync import (
    CONTACT_APPLY_FAILED_ERROR,
    CanonicalContact,
    ContactBatch,
    ContactsSyncEngine,
    ContactsSyncState,
)

pytestmark = pytest.mark.unit

_SECRET = "Jordan Example <jordan@example.invalid>"


class _Store:
    def __init__(self, state: ContactsSyncState) -> None:
        self.state = state

    async def load(self, **kwargs):
        return self.state.model_copy(deep=True)

    async def save(self, *, state, **kwargs):
        self.state = state.model_copy(deep=True)


class _Provider:
    name = "google"

    def __init__(self, contacts) -> None:
        self.contacts = contacts

    async def full_sync(self, *, account_id, page_token=None):
        return ContactBatch(contacts=self.contacts, next_sync_cursor="cursor-2")

    async def incremental_sync(self, *, account_id, cursor, page_token=None):
        return ContactBatch(contacts=self.contacts, next_sync_cursor="cursor-2")

    async def validate_credentials(self) -> None:
        return None


def _contact(external_id: str, *, deleted: bool = False) -> CanonicalContact:
    return CanonicalContact(external_id=external_id, deleted=deleted, raw={"id": external_id})


def _engine(contacts, failing: set[str], state: ContactsSyncState | None = None, exc=None):
    store = _Store(state or ContactsSyncState(sync_cursor="cursor-1"))

    async def apply(contact):
        if contact.external_id in failing:
            raise exc or RuntimeError("storage down")

    return ContactsSyncEngine(
        provider=_Provider(contacts), state_store=store, apply_contact=apply
    ), store


async def test_one_attempted_contact_that_fails_still_advances_the_cursor() -> None:
    engine, store = _engine([_contact("people/1")], {"people/1"})

    result = await engine.sync(account_id="acct", mode="incremental")

    assert result.failed_contacts == 1
    assert store.state.sync_cursor == "cursor-2"
    assert store.state.last_error == CONTACT_APPLY_FAILED_ERROR


async def test_two_failures_with_none_applied_fail_the_run_and_keep_the_cursor() -> None:
    engine, store = _engine([_contact("people/1"), _contact("people/2")], {"people/1", "people/2"})

    with pytest.raises(RuntimeError):
        await engine.sync(account_id="acct", mode="incremental")

    assert store.state.sync_cursor == "cursor-1"


async def test_two_failures_beside_one_applied_contact_advance_the_cursor() -> None:
    contacts = [_contact("people/1"), _contact("people/2"), _contact("people/3")]
    engine, store = _engine(contacts, {"people/1", "people/2"})

    result = await engine.sync(account_id="acct", mode="incremental")

    assert (result.applied_contacts, result.failed_contacts) == (1, 2)
    assert store.state.sync_cursor == "cursor-2"


async def test_a_failed_tombstone_fails_the_run_so_the_next_incremental_run_redelivers_it() -> None:
    state = ContactsSyncState(sync_cursor="cursor-1", contact_versions={"people/1": "v1"})
    contacts = [_contact("people/1", deleted=True), _contact("people/3")]
    engine, store = _engine(contacts, {"people/1"}, state)

    with pytest.raises(RuntimeError):
        await engine.sync(account_id="acct", mode="incremental")

    assert store.state.sync_cursor == "cursor-1"
    assert store.state.contact_versions == {"people/1": "v1"}
    assert store.state.last_error is None or "partial" not in store.state.last_error


async def test_cancellation_is_not_swallowed_by_the_isolation() -> None:
    engine, store = _engine([_contact("people/1")], {"people/1"}, exc=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await engine.sync(account_id="acct", mode="incremental")

    assert store.state.sync_cursor == "cursor-1"


async def test_the_production_apply_wrapper_logs_no_contact_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    backfill = object.__new__(ContactBackfillEngine)

    async def failing_apply(contact):
        raise RuntimeError(f"cannot write {_SECRET}")

    backfill._apply = failing_apply
    store = _Store(ContactsSyncState())
    engine = ContactsSyncEngine(
        provider=_Provider([_contact("people/1"), _contact("people/2")]),
        state_store=store,
        apply_contact=backfill,
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError):
        await engine.sync(account_id="acct", mode="full")

    assert caplog.records
    for record in caplog.records:
        assert _SECRET not in record.getMessage() and "example.invalid" not in repr(record.__dict__)
        assert record.exc_info is None
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)
