"""Telegram numeric/bot identifiers resolve against the canonical prefixed handle.

Telegram has-handle triples are stored canonically as ``telegram:<bare>``
(migration rel_019). A numeric chat id from ``telegram_send_message`` or an
inbound ``telegram_bot`` sender arrives bare, so resolution must try the
``telegram:``-prefixed form for every Telegram channel type. Otherwise the owner
is unresolvable and their notifications park forever (the bug this guards).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

from butlers.identity import (
    _CHANNEL_TYPE_TO_PREDICATE,
    _TELEGRAM_PREFIX_CHANNEL_TYPES,
    canonical_identity_channel_type,
    resolve_contact_by_channel,
)


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


class TestChannelMap:
    def test_telegram_bot_mapped_and_in_prefix_set(self) -> None:
        assert _CHANNEL_TYPE_TO_PREDICATE.get("telegram_bot") == "has-handle"
        assert "telegram_bot" in _TELEGRAM_PREFIX_CHANNEL_TYPES
        assert "telegram" in _TELEGRAM_PREFIX_CHANNEL_TYPES
        assert canonical_identity_channel_type("whatsapp") == "whatsapp_jid"


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
