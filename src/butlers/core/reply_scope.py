"""The one definition of an outbound message's reply scope (RFC 0038 S2.2).

An owner's reply joins a waiting goal only when it names the delivery the goal's prompt went to.
Two writers must agree on that name byte for byte: the capability broker, which records it on the
immutable effect when the prompt commits, and Switchboard admission, which derives it from the
inbound reply's own scope and looks the effect up by it. Both import this function; neither
carries a copy.
"""

from __future__ import annotations

from butlers.core.goal_admission import EvidenceRefused, digest

# How long a prompt may wait for its answer: ``notify(awaits_reply_seconds=...)``. The same bounds
# as a confirm prompt's timeout; the broker clamps a recorded expiry to the value the call named.
MIN_AWAITS_REPLY_SECONDS = 30
MAX_AWAITS_REPLY_SECONDS = 86400


def scoped_reply_ref(
    *, transport, endpoint, conversation, recipient_entity, recipient_channel, locator
):
    """Server-side outbound writers bind this value on the immutable effect.

    The incoming provider locator is never an asserted effect/goal identifier.
    Every scope component must agree before admission can even find a goal.
    """
    if (
        not all(
            isinstance(x, str) and x
            for x in (transport, endpoint, conversation, recipient_channel, locator)
        )
        or recipient_entity is None
    ):
        raise EvidenceRefused("reply_scope_unavailable")
    return "goal-reply-v1:" + digest(
        [transport, endpoint, conversation, str(recipient_entity), recipient_channel, locator]
    )
