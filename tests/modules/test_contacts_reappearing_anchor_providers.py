"""A reappearing external id resumes its own inactive anchor for every provider."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from butlers.modules.contacts.backfill import ContactBackfillEngine
from butlers.modules.contacts.sync import CanonicalContact


@pytest.mark.parametrize("provider", ["telegram", "google", "whatsapp"])
async def test_reappearing_contact_resumes_own_anchor_not_a_new_candidate(provider):
    anchor = uuid.uuid4()
    pool = AsyncMock()
    pool.fetchrow.return_value = {"local_entity_id": anchor, "live": True}
    pool.fetchval.return_value = 1
    engine = ContactBackfillEngine(pool, provider=provider, account_id="acct")
    # Without resume, re-resolution is ambiguous and would create a duplicate candidate.
    engine._resolver.resolve = AsyncMock(return_value=(None, "ambiguous_name"))
    engine._writer = AsyncMock()
    engine._writer.update_contact.return_value = {}

    await engine(CanonicalContact(external_id="ext-1"))

    engine._resolver.resolve.assert_not_awaited()
    engine._writer.create_contact.assert_not_awaited()
    assert engine._writer.update_contact.await_args.args[0] == anchor
