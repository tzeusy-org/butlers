"""The Telegram bot's endpoint identity: one definition for both ends of a reply.

The inbound connector stamps every message with ``telegram:bot:@<username>`` and Messenger
reports the same string for a message it sends, so an owner's reply can be matched to the prompt
it answers (RFC 0038 S2.2). Both import this function; neither carries a copy, and neither
imports the other (connectors and modules may not).
"""

from __future__ import annotations

import logging

from butlers.egress.http_transport import external_async_client

logger = logging.getLogger(__name__)


async def resolve_telegram_endpoint_identity(token: str) -> str:
    """Resolve the bot identity from the Telegram Bot API via ``getMe``.

    Returns ``telegram:bot:@<username>`` (or ``telegram:bot:@<first_name>``
    if the bot has no username set).

    Raises:
        RuntimeError: If the API call fails or returns no usable identity.
    """
    try:
        async with external_async_client(timeout=10.0) as client:
            response = await client.get(
                f"https://api.telegram.org/bot{token}/getMe",
            )
            response.raise_for_status()
            data = response.json()
            if data.get("ok"):
                result = data.get("result", {})
                username = result.get("username") or result.get("first_name", "")
                if username:
                    identity = f"telegram:bot:@{username}"
                    logger.info(
                        "Telegram bot connector: resolved endpoint_identity=%s",
                        identity,
                    )
                    return identity
            raise RuntimeError("Telegram bot connector: getMe response missing username/first_name")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Telegram bot connector: failed to resolve endpoint identity: {exc}"
        ) from exc
