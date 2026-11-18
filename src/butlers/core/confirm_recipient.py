"""The owner's signed-confirm recipient, shared by every deterministic confirm source."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from butlers.core.confirm_models import Recipient

#: The owner's current confirm recipient, or None when it cannot be resolved.
OwnerRecipient = Callable[[], Awaitable[Recipient | None]]


def owner_recipient_reader(pool: Any) -> OwnerRecipient:
    """The owner entity and the numeric Telegram chat id it answers from.

    A ``@username`` cannot be a confirm recipient: the callback names a numeric
    sender and chat, so only a stored ``telegram_chat_id`` qualifies.
    """
    from butlers.core.owner import resolve_owner_entity_id_two_step
    from butlers.credential_store import resolve_owner_entity_info

    async def read() -> Recipient | None:
        entity = await resolve_owner_entity_id_two_step(pool)
        chat = await resolve_owner_entity_info(pool, "telegram_chat_id")
        if entity is None or not chat or not str(chat).lstrip("-").isdigit():
            return None
        return Recipient(entity, str(chat), str(chat), True)

    return read
