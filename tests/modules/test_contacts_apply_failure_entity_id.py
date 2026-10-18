"""contact_sync_apply_failed carries the entity a contact is already linked to (bu-2yn08e)."""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP

import butlers.modules.contacts as contacts_module
from butlers.modules.contacts import ContactsModule
from butlers.modules.contacts.backfill import ContactBackfillEngine
from butlers.modules.contacts.sync import (
    CanonicalContact,
    ContactBatch,
    ContactsSyncEngine,
    ContactsSyncRuntime,
    ContactsSyncState,
)

pytestmark = pytest.mark.unit

_ENTITY = uuid.UUID("11111111-2222-3333-4444-555555555555")
_SECRET = "Jordan Example <jordan@example.invalid>"


class _Store:
    def __init__(self) -> None:
        self.state = ContactsSyncState()

    async def load(self, **kwargs):
        return self.state.model_copy(deep=True)

    async def save(self, *, state, **kwargs):
        self.state = state.model_copy(deep=True)


class _Provider:
    name = "google"

    def __init__(self, contacts) -> None:
        self._contacts = contacts

    async def full_sync(self, *, account_id, page_token=None):
        return ContactBatch(contacts=self._contacts, next_sync_cursor="cursor-2")

    async def incremental_sync(self, *, account_id, cursor, page_token=None):
        return ContactBatch(contacts=self._contacts, next_sync_cursor="cursor-3")

    async def validate_credentials(self) -> None:
        return None


def _contact(external_id: str) -> CanonicalContact:
    return CanonicalContact(external_id=external_id, raw={"id": external_id})


async def _failure_records(contacts, known_entity_id, caplog) -> list[logging.LogRecord]:
    async def apply(contact):
        if contact.external_id == "people/ok":
            return
        raise RuntimeError(f"cannot write {_SECRET}")

    engine = ContactsSyncEngine(
        provider=_Provider(contacts),
        state_store=_Store(),
        apply_contact=apply,
        known_entity_id=known_entity_id,
    )
    with caplog.at_level(logging.DEBUG):
        await engine.sync(account_id="acct", mode="full")
    return [r for r in caplog.records if r.getMessage() == "contact_sync_apply_failed"]


async def test_a_linked_contact_failure_carries_its_entity_id(caplog) -> None:
    async def lookup(contact):
        return _ENTITY if contact.external_id == "people/1" else None

    (record,) = await _failure_records([_contact("people/1")], lookup, caplog)

    assert record.entity_id == str(_ENTITY)
    assert record.external_id == "people/1"
    assert _SECRET not in record.getMessage() + repr(record.__dict__)


async def test_an_unlinked_contact_failure_logs_no_entity_id(caplog) -> None:
    async def lookup(contact):
        return None

    (record,) = await _failure_records([_contact("people/9")], lookup, caplog)

    assert record.entity_id is None


async def test_only_the_linked_contact_among_several_carries_an_id(caplog) -> None:
    async def lookup(contact):
        return _ENTITY if contact.external_id == "people/1" else None

    # One contact applies, so the run is not systemic and the failures are only logged.
    records = await _failure_records(
        [_contact("people/1"), _contact("people/2"), _contact("people/ok")], lookup, caplog
    )

    assert {r.external_id: r.entity_id for r in records} == {
        "people/1": str(_ENTITY),
        "people/2": None,
    }


async def test_a_failing_lookup_never_masks_the_apply_failure(caplog) -> None:
    async def lookup(contact):
        raise RuntimeError(f"lookup failed for {_SECRET}")

    records = await _failure_records([_contact("people/1")], lookup, caplog)

    assert [r.entity_id for r in records] == [None]
    assert _SECRET not in repr([r.__dict__ for r in records])


async def test_no_lookup_configured_logs_no_entity_id(caplog) -> None:
    (record,) = await _failure_records([_contact("people/1")], None, caplog)

    assert record.entity_id is None


async def test_the_backfill_engine_reads_the_contacts_source_link() -> None:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"local_entity_id": _ENTITY})
    engine = ContactBackfillEngine(pool, provider="google", account_id="acct")

    assert await engine.known_entity_id(_contact("people/1")) == _ENTITY
    args = pool.fetchrow.call_args[0]
    assert "contacts_source_links" in args[0] and args[1:] == ("google", "acct", "people/1")

    pool.fetchrow = AsyncMock(return_value=None)
    assert await engine.known_entity_id(_contact("people/2")) is None


async def test_module_startup_wires_the_backfill_lookup_into_the_sync_engine(monkeypatch) -> None:
    captured: dict = {}
    real_engine = ContactsSyncEngine

    def capture(**kwargs):
        captured.update(kwargs)
        return real_engine(**kwargs)

    module = ContactsModule()
    monkeypatch.setattr(module, "_create_provider", AsyncMock(return_value=_Provider([])))
    monkeypatch.setattr(contacts_module, "ContactsSyncEngine", capture)
    monkeypatch.setattr(ContactsSyncRuntime, "start", AsyncMock())
    config = {"provider": "google"}
    db = SimpleNamespace(pool=object())
    await module.register_tools(FastMCP("synthetic-contacts"), config, db, "general")
    await module.on_startup(config, db)

    lookup = captured["known_entity_id"]
    assert lookup.__self__ is captured["apply_contact"]
    assert lookup.__func__ is ContactBackfillEngine.known_entity_id
