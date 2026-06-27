"""Butler-specific exceptions."""

from __future__ import annotations

import re


class RuntimeBinaryNotFoundError(RuntimeError):
    """Raised when the runtime adapter's binary is not found on PATH."""


# Channel-egress ownership ---------------------------------------------------
#
# Outbound channel egress (sending/replying on Telegram, email, WhatsApp, ...)
# is reserved for the designated messenger butler. Egress tools follow the
# naming convention ``<channel>_<verb>`` where the verb is one of the outbound
# send/reply verbs below. Any other butler that tries to register such a tool
# is attempting to grab a channel it does not own, so registration fails closed.
_CHANNEL_EGRESS_VERBS = (
    "send_message",
    "reply_to_message",
    "send_email",
    "reply_to_thread",
)

# Matches ``<channel>_<verb>`` (e.g. ``telegram_send_message``,
# ``email_reply_to_thread``). The channel segment must be non-empty.
_CHANNEL_EGRESS_TOOL_PATTERN = re.compile(r"^.+_(?:" + "|".join(_CHANNEL_EGRESS_VERBS) + r")$")


def is_channel_egress_tool(tool_name: str) -> bool:
    """Return ``True`` if ``tool_name`` is a channel-egress (outbound) tool.

    Channel-egress tools match ``<channel>_(send_message|reply_to_message|
    send_email|reply_to_thread)`` — the outbound send/reply surface that only
    the messenger butler is permitted to own.
    """
    return bool(_CHANNEL_EGRESS_TOOL_PATTERN.match(tool_name))


class ChannelEgressOwnershipError(RuntimeError):
    """Raised when a non-messenger butler tries to register a channel-egress tool.

    Outbound channel egress (sending/replying on a messaging channel) is owned
    exclusively by the designated messenger butler. All other butlers route
    outbound delivery through ``notify()`` instead of registering their own
    egress tools, so an attempt to register one fails closed at startup.
    """

    def __init__(self, *, butler_name: str, tool_name: str, module_name: str | None = None) -> None:
        self.butler_name = butler_name
        self.tool_name = tool_name
        self.module_name = module_name
        where = f" (module '{module_name}')" if module_name else ""
        super().__init__(
            f"Butler '{butler_name}'{where} may not register channel-egress tool "
            f"'{tool_name}': outbound channel egress is reserved for the messenger "
            f"butler. Use notify() for outbound delivery."
        )
