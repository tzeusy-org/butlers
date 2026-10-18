"""Contacts sync failure logs and tool results carry no contact content (bu-9a6jkg).

An exception raised while syncing can quote a contact's name or address. Each path below is
driven with an exception whose message holds a synthetic name and email; neither may reach a log
record (message or traceback) or the MCP tool result.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from fastmcp import Client, FastMCP

import butlers.modules.contacts as contacts_module
from butlers.modules.contacts import ContactsModule
from butlers.modules.contacts.backfill import ContactBackfillWriter
from butlers.modules.contacts.sync import (
    ContactsSyncEngine,
    ContactsSyncRuntime,
    ContactsSyncState,
)

pytestmark = pytest.mark.unit

_NAME = "Jordan Example"
_EMAIL = "jordan@example.invalid"
_SECRET = f"{_NAME} <{_EMAIL}>"


def _assert_no_content(records: list[logging.LogRecord]) -> None:
    assert records
    for record in records:
        text = record.getMessage() + repr(record.__dict__.get("args", ""))
        assert _NAME not in text and _EMAIL not in text
        assert record.exc_info is None


class _Store:
    def __init__(self, fail_load: bool = False) -> None:
        self.state = ContactsSyncState()
        self.fail_load = fail_load

    async def load(self, **kwargs):
        if self.fail_load:
            raise RuntimeError(f"cannot read {_SECRET}")
        return self.state.model_copy(deep=True)

    async def save(self, *, state, **kwargs):
        self.state = state.model_copy(deep=True)


class _Provider:
    name = "google"

    async def full_sync(self, *, account_id, page_token=None):
        raise RuntimeError(f"upstream rejected {_SECRET}")

    async def incremental_sync(self, *, account_id, cursor, page_token=None):
        raise RuntimeError(f"upstream rejected {_SECRET}")

    async def validate_credentials(self) -> None:
        return None


def _runtime(on_cycle_complete=None, store: _Store | None = None) -> ContactsSyncRuntime:
    store = store or _Store()
    engine = ContactsSyncEngine(provider=_Provider(), state_store=store, apply_contact=AsyncMock())
    return ContactsSyncRuntime(
        sync_engine=engine,
        state_store=store,
        provider_name="google",
        account_id="acct",
        on_cycle_complete=on_cycle_complete,
    )


async def test_entity_creation_failure_logs_no_canonical_name(caplog) -> None:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=asyncpg.PostgresError(f"bad row for {_NAME}"))
    engine = ContactBackfillWriter(pool, provider="google", account_id="acct")

    with caplog.at_level(logging.DEBUG):
        result = await engine._ensure_entity("Jordan", "Example", None, None)

    assert result is None
    _assert_no_content(caplog.records)
    assert any("PostgresError" in r.getMessage() for r in caplog.records)


async def test_post_sync_callback_failure_logs_no_exception_text(caplog) -> None:
    async def callback(_result):
        raise RuntimeError(f"callback saw {_SECRET}")

    runtime = _runtime(callback)
    runtime._sync_engine.sync = AsyncMock(
        return_value=SimpleNamespace(**dict.fromkeys(("fetched_contacts",), 0))
    )
    runtime._next_mode = AsyncMock(return_value="incremental")

    with caplog.at_level(logging.DEBUG):
        await runtime.run_sync_cycle()

    _assert_no_content(caplog.records)
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


async def test_poll_cycle_failure_logs_no_exception_text(caplog) -> None:
    runtime = _runtime()

    async def next_mode():
        runtime._stopping.set()  # run one iteration, then leave the loop
        return "full"

    runtime._next_mode = next_mode

    with caplog.at_level(logging.DEBUG):
        await runtime._run_loop()

    _assert_no_content(caplog.records)
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


async def _tool_result(monkeypatch, name: str, args: dict, store: _Store) -> dict:
    module, mcp = ContactsModule(), FastMCP("synthetic-contacts")
    monkeypatch.setattr(module, "_create_provider", AsyncMock(return_value=_Provider()))
    monkeypatch.setattr(contacts_module, "ContactsSyncStateStore", lambda pool: store)
    monkeypatch.setattr(ContactsSyncRuntime, "start", AsyncMock())
    config = {"provider": "google"}
    await module.register_tools(mcp, config, SimpleNamespace(pool=object()), "general")
    await module.on_startup(config, SimpleNamespace(pool=object()))
    async with Client(mcp) as client:
        result = await client.call_tool(name, args)
    return result.data if isinstance(result.data, dict) else result.structured_content


async def test_sync_now_result_and_log_carry_only_the_exception_class(monkeypatch, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        data = await _tool_result(
            monkeypatch, "contacts_sync_now", {"provider": "google", "mode": "full"}, _Store()
        )

    text = repr(data)
    assert "RuntimeError" in text
    assert _NAME not in text and _EMAIL not in text
    _assert_no_content([r for r in caplog.records if "contacts_sync_now" in r.getMessage()])


async def test_sync_status_result_and_log_carry_only_the_exception_class(
    monkeypatch, caplog
) -> None:
    with caplog.at_level(logging.DEBUG):
        data = await _tool_result(
            monkeypatch, "contacts_sync_status", {"provider": "google"}, _Store(fail_load=True)
        )

    text = repr(data)
    assert "RuntimeError" in text
    assert _NAME not in text and _EMAIL not in text
    _assert_no_content([r for r in caplog.records if "contacts_sync_status" in r.getMessage()])
