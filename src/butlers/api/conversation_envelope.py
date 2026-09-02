"""Dashboard ingest.v1 envelope construction.

Builds ``ingest.v1`` envelopes for dashboard conversation messages that flow
through the Switchboard ingestion pipeline.  The dashboard channel is treated
as a trusted internal operator channel (``source.channel = "dashboard"``,
``source.provider = "internal"``).

Conversation context is serialized as a text preamble in
``payload.normalized_text`` for follow-up messages.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

_DEFAULT_CONTEXT_PAIRS: int = 5
_MAX_CONTEXT_CHARS: int = 4000


def _build_context_preamble(
    history: list[dict[str, Any]],
    *,
    max_pairs: int = _DEFAULT_CONTEXT_PAIRS,
) -> str:
    """Serialize the last ``max_pairs`` exchange pairs as a text preamble.

    Only includes messages with role ``user`` or ``assistant`` and non-empty
    content.  The preamble is prepended to ``payload.normalized_text`` so the
    butler has conversation history available as plain text.
    """
    # Filter to user/assistant messages with content
    relevant = [m for m in history if m.get("role") in ("user", "assistant") and m.get("content")]

    # Last N pairs = last 2*N messages (1 user + 1 assistant per pair)
    max_msgs = max_pairs * 2
    recent = relevant[-max_msgs:]

    if not recent:
        return ""

    lines = ["## Conversation history"]
    for msg in recent:
        role = msg["role"].capitalize()
        content = str(msg["content"]).strip()
        # Truncate very long individual messages
        if len(content) > 500:
            content = content[:497] + "..."
        lines.append(f"{role}: {content}")
    lines.append("")  # blank line before new message

    preamble = "\n".join(lines)

    # Truncate the whole preamble if it exceeds the limit
    if len(preamble) > _MAX_CONTEXT_CHARS:
        preamble = preamble[-_MAX_CONTEXT_CHARS:]

    return preamble


def build_dashboard_envelope(
    *,
    conversation_id: UUID,
    message_id: UUID,
    message_text: str,
    conversation_context: list[dict[str, Any]] | None = None,
    max_context_pairs: int = _DEFAULT_CONTEXT_PAIRS,
    page_context: dict[str, Any] | None = None,
    pinned_target: str | None = None,
) -> dict[str, Any]:
    """Construct a valid ``ingest.v1`` envelope for a dashboard message.

    Parameters
    ----------
    conversation_id:
        The UUID of the dashboard conversation.
    message_id:
        The stable UUID of the persisted user message row. A retry reuses it
        so ``event.external_event_id`` remains unchanged.
    message_text:
        The user's raw message text.
    conversation_context:
        Optional list of prior conversation messages (dicts with ``role``
        and ``content`` keys) used to build a context preamble for
        follow-up messages.  Pass ``None`` or ``[]`` for new conversations.
    max_context_pairs:
        Number of prior exchange pairs to include in the context preamble.
    page_context:
        Optional dashboard route/query/entity context the message was sent
        from (e.g. ``{"route": "/entities/concentration", "query_params":
        {...}, "entity_ref": "..."}``).  Carried in ``payload.raw`` so a
        routed butler session can ground the statement.
    pinned_target:
        Optional butler name to deterministically route this envelope to
        (``control.pinned_target``), bypassing Switchboard classification.
        Used for per-butler dashboard conversations; omit for the
        classification-routed Switchboard widget conversation.

    Returns
    -------
    dict
        A validated ``ingest.v1`` envelope dict ready for submission to
        the Switchboard ingest API.
    """
    observed_at = datetime.now(UTC).isoformat()

    # Build normalized_text: context preamble + current message
    normalized_text = message_text
    if conversation_context:
        preamble = _build_context_preamble(conversation_context, max_pairs=max_context_pairs)
        if preamble:
            normalized_text = f"{preamble}\nUser: {message_text}"

    conv_id_str = str(conversation_id)
    msg_id_str = str(message_id)

    raw: dict[str, Any] = {
        "source": "dashboard",
        "conversation_id": conv_id_str,
        "message_id": msg_id_str,
        "message": message_text,
    }
    if page_context:
        raw["page_context"] = page_context

    control: dict[str, Any] = {
        "policy_tier": "interactive",
        "ingestion_tier": "full",
    }
    if pinned_target:
        control["pinned_target"] = pinned_target

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "dashboard",
            "provider": "internal",
            "endpoint_identity": f"dashboard:web:{conv_id_str}",
        },
        "event": {
            "external_event_id": msg_id_str,
            "external_conversation_id": f"dashboard:{conv_id_str}",
            "reply_target_ref": conv_id_str,
            "observed_at": observed_at,
        },
        "sender": {
            "identity": "dashboard:operator",
        },
        "payload": {
            "normalized_text": normalized_text,
            "raw": raw,
        },
        "control": control,
    }
