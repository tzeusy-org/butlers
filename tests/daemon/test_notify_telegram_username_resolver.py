"""ButlerDaemon._resolve_telegram_username_recipient (bu-dhloi1.177).

The Bot API cannot message a private user by @username; the resolver turns one into the
numeric chat id: the owner via the owner chat id, any other contact via their numeric
telegram handle. Handle matching itself is covered DB-backed in test_notify_entity_id.py.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from butlers.daemon import ButlerDaemon
from butlers.identity import ResolvedContact

_CONTACT = "butlers.identity.resolve_telegram_username_contact"
_CHAT_ID = "butlers.identity.resolve_entity_telegram_chat_id"


def _daemon() -> ButlerDaemon:
    daemon = object.__new__(ButlerDaemon)
    daemon.db = SimpleNamespace(pool=object())
    return daemon


def _contact(*roles: str) -> ResolvedContact:
    return ResolvedContact(name="Pal", roles=list(roles), entity_id=uuid.uuid4())


async def test_owner_username_resolves_to_owner_chat_id() -> None:
    with (
        patch(_CONTACT, new=AsyncMock(return_value=_contact("owner"))),
        patch("butlers.daemon.resolve_owner_entity_info", new=AsyncMock(return_value=" 210 ")),
    ):
        assert await _daemon()._resolve_telegram_username_recipient("@Butlerly") == ("210", "Pal")


async def test_non_owner_username_resolves_to_their_numeric_handle() -> None:
    contact = _contact("contact")
    chat_id = AsyncMock(return_value="555")
    with (
        patch(_CONTACT, new=AsyncMock(return_value=contact)),
        patch(_CHAT_ID, new=chat_id),
    ):
        assert await _daemon()._resolve_telegram_username_recipient("@Pal") == ("555", "Pal")
    assert chat_id.await_args.args[1] == contact.entity_id


async def test_unknown_username_is_none() -> None:
    with patch(_CONTACT, new=AsyncMock(return_value=None)):
        assert await _daemon()._resolve_telegram_username_recipient("@Nobody") is None


async def test_non_numeric_identifier_is_none() -> None:
    with (
        patch(_CONTACT, new=AsyncMock(return_value=_contact("contact"))),
        patch(_CHAT_ID, new=AsyncMock(return_value="@name")),
    ):
        assert await _daemon()._resolve_telegram_username_recipient("@Pal") is None


async def test_owner_without_chat_id_is_none() -> None:
    with (
        patch(_CONTACT, new=AsyncMock(return_value=_contact("owner"))),
        patch("butlers.daemon.resolve_owner_entity_info", new=AsyncMock(return_value=None)),
    ):
        assert await _daemon()._resolve_telegram_username_recipient("@Butlerly") is None


async def test_no_pool_is_none() -> None:
    daemon = object.__new__(ButlerDaemon)
    daemon.db = None
    assert await daemon._resolve_telegram_username_recipient("@x") is None
