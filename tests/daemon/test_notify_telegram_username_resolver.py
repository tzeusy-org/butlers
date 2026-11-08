"""ButlerDaemon._resolve_telegram_username_recipient (bu-dhloi1.177).

The Bot API cannot message a private user by @username; the resolver turns one into the
numeric chat id: the owner via the owner definer plus the owner's chat id, any other
contact via relationship.entity_facts.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from butlers.daemon import ButlerDaemon
from butlers.identity import ResolvedContact

_CORROBORATE = "butlers.identity.resolve_channel_contact_with_owner_corroboration"


def _daemon() -> ButlerDaemon:
    daemon = object.__new__(ButlerDaemon)
    daemon.db = SimpleNamespace(pool=object())
    return daemon


def _contact(*roles: str) -> ResolvedContact:
    return ResolvedContact(name=None, roles=list(roles), entity_id=uuid.uuid4())


async def test_owner_username_resolves_to_owner_chat_id() -> None:
    with (
        patch(_CORROBORATE, new=AsyncMock(return_value=_contact("owner"))),
        patch("butlers.daemon.resolve_owner_entity_info", new=AsyncMock(return_value=" 210 ")),
    ):
        assert await _daemon()._resolve_telegram_username_recipient("@Butlerly") == "210"


async def test_non_owner_username_resolves_via_entity_facts() -> None:
    contact = _contact("contact")
    daemon = _daemon()
    identifier = AsyncMock(return_value="555")
    with (
        patch(_CORROBORATE, new=AsyncMock(return_value=contact)),
        patch.object(daemon, "_resolve_entity_channel_identifier", new=identifier),
    ):
        assert await daemon._resolve_telegram_username_recipient("@Pal") == "555"
    identifier.assert_awaited_once_with(entity_id=contact.entity_id, channel="telegram")


async def test_unknown_username_is_none() -> None:
    with patch(_CORROBORATE, new=AsyncMock(return_value=None)):
        assert await _daemon()._resolve_telegram_username_recipient("@Nobody") is None


async def test_non_numeric_identifier_is_none() -> None:
    daemon = _daemon()
    with (
        patch(_CORROBORATE, new=AsyncMock(return_value=_contact("contact"))),
        patch.object(
            daemon, "_resolve_entity_channel_identifier", new=AsyncMock(return_value="@name")
        ),
    ):
        assert await daemon._resolve_telegram_username_recipient("@Pal") is None


async def test_owner_without_chat_id_is_none() -> None:
    with (
        patch(_CORROBORATE, new=AsyncMock(return_value=_contact("owner"))),
        patch("butlers.daemon.resolve_owner_entity_info", new=AsyncMock(return_value=None)),
    ):
        assert await _daemon()._resolve_telegram_username_recipient("@Butlerly") is None


async def test_no_pool_is_none() -> None:
    daemon = object.__new__(ButlerDaemon)
    daemon.db = None
    assert await daemon._resolve_telegram_username_recipient("@x") is None
