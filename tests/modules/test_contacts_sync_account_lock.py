"""One sync run per account at a time for every provider, not only Telegram (bu-p0lfus)."""

from __future__ import annotations

import asyncio

import pytest

from butlers.modules.contacts.sync import (
    CanonicalContact,
    ContactBatch,
    ContactsSyncEngine,
    ContactsSyncState,
)

pytestmark = pytest.mark.unit


class _Store:
    def __init__(self) -> None:
        self.state = ContactsSyncState()

    async def load(self, **kwargs):
        return self.state.model_copy(deep=True)

    async def save(self, *, state, **kwargs):
        self.state = state.model_copy(deep=True)


class _Provider:
    """A non-Telegram provider whose full fetch parks until released."""

    name = "google"

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.running = 0
        self.max_running = 0
        self.fetches = 0

    async def full_sync(self, *, account_id, page_token=None):
        self.fetches += 1
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            await self.release.wait()
        finally:
            self.running -= 1
        return ContactBatch(
            contacts=[CanonicalContact(external_id="people/1", raw={})],
            next_sync_cursor="cursor-1",
        )

    async def incremental_sync(self, *, account_id, cursor, page_token=None):
        return ContactBatch(next_sync_cursor=cursor)

    async def validate_credentials(self) -> None:
        return None


def _engine(provider: _Provider, writes: list[str]) -> ContactsSyncEngine:
    async def apply(contact):
        writes.append(contact.external_id)

    return ContactsSyncEngine(provider=provider, state_store=_Store(), apply_contact=apply)


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_an_overlapping_sync_now_waits_for_the_poller_on_a_non_telegram_account() -> None:
    provider, writes = _Provider(), []
    engine = _engine(provider, writes)

    poll = asyncio.create_task(engine.sync(account_id="acct", mode="full"))
    await _settle()
    sync_now = asyncio.create_task(engine.sync(account_id="acct", mode="full"))
    await _settle()

    # Only the poller is fetching; contacts_sync_now has not started a second run.
    assert provider.fetches == 1 and provider.max_running == 1
    provider.release.set()
    await asyncio.gather(poll, sync_now)

    assert provider.max_running == 1  # never two runs at once
    assert provider.fetches == 2  # the later run ran after the first finished


async def test_different_accounts_are_not_serialized_against_each_other() -> None:
    provider, writes = _Provider(), []
    engine = _engine(provider, writes)

    first = asyncio.create_task(engine.sync(account_id="acct-a", mode="full"))
    second = asyncio.create_task(engine.sync(account_id="acct-b", mode="full"))
    await _settle()

    assert provider.max_running == 2
    provider.release.set()
    await asyncio.gather(first, second)
