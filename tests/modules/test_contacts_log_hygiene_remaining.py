"""Remaining contacts log and error-text hygiene (bu-0lxktk).

Sibling of ``test_contacts_failure_content_free.py``. Each path is driven with contact field
values or an exception message holding a synthetic name, email and street; none may reach a log
message, a traceback or a raised error message.
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastmcp import Client, FastMCP

import butlers.modules.contacts as contacts_module
from butlers.modules.contacts import ContactsModule
from butlers.modules.contacts.backfill import ContactBackfillWriter
from butlers.modules.contacts.sync import (
    CanonicalContact,
    ContactAddress,
    ContactDate,
    ContactsSyncRuntime,
    ContactsSyncState,
    ContactsTokenRefreshError,
    _GoogleOAuthClient,
    _GoogleOAuthCredentials,
)

pytestmark = pytest.mark.unit

_NAME = "Jordan Example"
_EMAIL = "jordan@example.invalid"
_STREET = "12 Synthetic Street"
_SECRET = f"{_NAME} <{_EMAIL}> {_STREET}"
_NEEDLES = (_NAME, _EMAIL, _STREET, "Synthetic Label")


def _assert_clean(records: list[logging.LogRecord]) -> None:
    assert records
    for record in records:
        text = record.getMessage() + repr(record.args)
        assert not any(n in text for n in _NEEDLES)
        assert record.exc_info is None


def _writer() -> ContactBackfillWriter:
    pool = MagicMock()
    pool.execute = AsyncMock(side_effect=RuntimeError(f"insert rejected {_SECRET}"))
    pool.fetchval = AsyncMock(side_effect=RuntimeError(f"select rejected {_SECRET}"))
    writer = ContactBackfillWriter(pool, provider="google", account_id="acct")
    flags = {t: True for t in ("addresses", "important_dates", "labels", "contact_labels")}
    flags.update({f"{t}_entity": True for t in ("addresses", "important_dates", "contact_labels")})
    writer._table_flags = flags
    return writer


def _contact(**kwargs) -> CanonicalContact:
    return CanonicalContact(external_id="people/1", display_name=_NAME, **kwargs)


async def test_upsert_addresses_failure_logs_no_street_or_label(caplog) -> None:
    contact = _contact(addresses=[ContactAddress(street=_STREET, label="Synthetic Label")])
    with caplog.at_level(logging.DEBUG):
        await _writer().upsert_addresses(uuid.uuid4(), contact)

    _assert_clean(caplog.records)
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


async def test_upsert_important_dates_failure_logs_no_label(caplog) -> None:
    contact = _contact(birthdays=[ContactDate(month=3, day=4, label="Synthetic Label")])
    with caplog.at_level(logging.DEBUG):
        await _writer().upsert_important_dates(uuid.uuid4(), contact)

    _assert_clean(caplog.records)
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


async def test_upsert_labels_failure_logs_no_label_name(caplog) -> None:
    contact = _contact(group_memberships=["contactGroups/Synthetic Label"])
    with caplog.at_level(logging.DEBUG):
        await _writer().upsert_labels(uuid.uuid4(), contact)

    _assert_clean(caplog.records)
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


async def test_source_list_state_failure_logs_only_the_exception_class(monkeypatch, caplog) -> None:
    class _FailingStore:
        async def load(self, **kwargs):
            raise RuntimeError(f"cannot read {_SECRET}")

    module, mcp = ContactsModule(), FastMCP("synthetic-contacts")
    runtime = MagicMock(spec=ContactsSyncRuntime)
    runtime._state_store = _FailingStore()
    runtime._provider_name = "google"
    runtime._account_id = "acct"
    monkeypatch.setattr(contacts_module, "ContactsSyncState", ContactsSyncState)
    config = {"provider": "google"}
    await module.register_tools(mcp, config, SimpleNamespace(pool=object()), "general")
    module._runtimes = {"google": runtime}

    with caplog.at_level(logging.DEBUG):
        async with Client(mcp) as client:
            result = await client.call_tool("contacts_source_list", {"provider": "google"})

    assert not any(n in repr(result) for n in _NEEDLES)
    _assert_clean([r for r in caplog.records if "contacts_source_list" in r.getMessage()])
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


async def test_invalid_telegram_api_id_error_omits_the_stored_value(monkeypatch) -> None:
    values = {
        "telegram_api_id": _SECRET,
        "telegram_api_hash": "hash",
        "telegram_user_session": "session",
    }

    async def resolve(_pool, ci_type):
        return values[ci_type]

    monkeypatch.setattr("butlers.credential_store.resolve_owner_entity_info", resolve)

    with pytest.raises(RuntimeError) as raised:
        await ContactsModule()._create_telegram_provider(object())

    assert not any(n in str(raised.value) for n in _NEEDLES)
    assert "telegram_api_id" in str(raised.value)


async def test_google_token_refresh_error_names_only_the_transport_class() -> None:
    credentials = _GoogleOAuthCredentials(
        client_id="id", client_secret="secret", refresh_token="refresh"
    )
    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError(f"refused {_SECRET}"))

    with pytest.raises(ContactsTokenRefreshError) as raised:
        await _GoogleOAuthClient(credentials, client)._refresh_access_token()

    assert not any(n in str(raised.value) for n in _NEEDLES)
    assert "ConnectError" in str(raised.value)
