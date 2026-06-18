"""Telegram numeric/bot identifiers resolve against the canonical prefixed handle.

Telegram has-handle triples are stored canonically as ``telegram:<bare>``
(migration rel_019).  A numeric chat id from ``telegram_send_message`` or an
inbound ``telegram_bot`` sender arrives bare, so resolution (and the approval
gate's owner-primary check) must try the ``telegram:``-prefixed form for ALL
telegram channel types — not just ``telegram_user_client``.  Otherwise the owner
is unresolvable and their notifications park forever (the bug this guards).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

from butlers.identity import (
    _CHANNEL_TYPE_TO_PREDICATE,
    _TELEGRAM_PREFIX_CHANNEL_TYPES,
    resolve_contact_by_channel,
)
from butlers.modules.approvals._shared import is_primary_contact


def _resolve_pool(stored_object: str, *, roles: list[str]) -> Any:
    """Fake pool whose entity_facts resolve query matches one stored object."""
    entity_id = uuid.uuid4()

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        if args and args[-1] == stored_object:
            return {"entity_id": entity_id, "name": "Owner", "roles": roles}
        return None

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    return pool


def _primary_pool(stored_object: str) -> Any:
    async def _fetchrow(query: str, *args: Any) -> dict | None:
        # is_primary query: (entity_id, predicate, object)
        if len(args) == 3 and args[2] == stored_object:
            return {"primary": True}
        return None

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    return pool


class TestChannelMap:
    def test_telegram_bot_mapped_and_in_prefix_set(self) -> None:
        assert _CHANNEL_TYPE_TO_PREDICATE.get("telegram_bot") == "has-handle"
        assert "telegram_bot" in _TELEGRAM_PREFIX_CHANNEL_TYPES
        assert "telegram" in _TELEGRAM_PREFIX_CHANNEL_TYPES


class TestResolveTelegramPrefix:
    async def test_numeric_chat_id_resolves_prefixed_handle(self) -> None:
        pool = _resolve_pool("telegram:206570151", roles=["owner"])
        rc = await resolve_contact_by_channel(pool, "telegram", "206570151")
        assert rc is not None and "owner" in rc.roles

    async def test_telegram_bot_channel_resolves(self) -> None:
        pool = _resolve_pool("telegram:206570151", roles=["owner"])
        rc = await resolve_contact_by_channel(pool, "telegram_bot", "206570151")
        assert rc is not None and "owner" in rc.roles

    async def test_already_prefixed_value_resolves(self) -> None:
        pool = _resolve_pool("telegram:206570151", roles=["owner"])
        rc = await resolve_contact_by_channel(pool, "telegram", "telegram:206570151")
        assert rc is not None and "owner" in rc.roles

    async def test_unknown_value_returns_none(self) -> None:
        pool = _resolve_pool("telegram:206570151", roles=["owner"])
        assert await resolve_contact_by_channel(pool, "telegram", "999") is None


class TestIsPrimaryTelegramPrefix:
    async def test_numeric_chat_id_is_primary_via_prefixed_handle(self) -> None:
        entity_id = uuid.uuid4()
        pool = _primary_pool("telegram:206570151")
        assert await is_primary_contact(pool, entity_id, "telegram", "206570151") is True

    async def test_telegram_bot_is_primary_via_prefixed_handle(self) -> None:
        entity_id = uuid.uuid4()
        pool = _primary_pool("telegram:206570151")
        assert await is_primary_contact(pool, entity_id, "telegram_bot", "206570151") is True
