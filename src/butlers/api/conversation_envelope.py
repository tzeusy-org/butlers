"""Dashboard ingestion envelope construction for conversation messages.

Builds valid ``ingest.v1`` envelopes that flow through the standard
Switchboard ingestion pipeline from dashboard conversation messages.

Usage::

    envelope = build_dashboard_envelope(
        conversation_id=uuid.UUID("..."),
        message_id=uuid.UUID("..."),
        message_text="Hello butler",
        conversation_context="",
    )
    # Submit to Switchboard ingestion pipeline
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID


def build_dashboard_envelope(
    conversation_id: UUID,
    message_id: UUID,
    message_text: str,
    conversation_context: str = "",
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a valid ``ingest.v1`` envelope for a dashboard conversation message.

    Constructs the envelope according to the dashboard-conversations spec.
    The ``payload.normalized_text`` is the user's message text; for follow-up
    messages it should be pre-processed with ``format_context_preamble`` before
    passing here.

    Parameters
    ----------
    conversation_id:
        The UUID of the conversation this message belongs to.
    message_id:
        The UUID of the user message row (used as ``event.external_event_id``).
    message_text:
        The user's message content. For follow-up messages, this should
        already include the conversation context preamble (via
        ``format_context_preamble`` from ``butlers.api.conversations``).
    conversation_context:
        Raw prior-context string to embed in ``payload.raw`` for traceability.
        Does NOT affect ``normalized_text`` — callers should use
        ``format_context_preamble`` to combine context with the message before
        passing ``message_text``.
    observed_at:
        Timestamp for ``event.observed_at``. Defaults to current UTC time.

    Returns
    -------
    dict
        A valid ``ingest.v1`` envelope dict suitable for submission to the
        Switchboard ingestion pipeline.

    Examples
    --------
    >>> import uuid
    >>> conv_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    >>> msg_id  = uuid.UUID("00000000-0000-0000-0000-000000000002")
    >>> env = build_dashboard_envelope(conv_id, msg_id, "Hello butler")
    >>> env["schema_version"]
    'ingest.v1'
    >>> env["source"]["channel"]
    'dashboard'
    >>> env["control"]["policy_tier"]
    'interactive'
    """
    ts = (observed_at or datetime.now(UTC)).isoformat()

    conversation_id_str = str(conversation_id)
    message_id_str = str(message_id)

    envelope: dict[str, Any] = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "dashboard",
            "provider": "internal",
            "endpoint_identity": f"dashboard:web:{conversation_id_str}",
        },
        "event": {
            "external_event_id": message_id_str,
            "external_thread_id": conversation_id_str,
            "observed_at": ts,
        },
        "sender": {
            "identity": "dashboard:operator",
        },
        "payload": {
            "normalized_text": message_text,
            "raw": {
                "source": "dashboard",
                "conversation_id": conversation_id_str,
                "message_id": message_id_str,
                "message": message_text,
                **({"conversation_context": conversation_context} if conversation_context else {}),
            },
        },
        "control": {
            "policy_tier": "interactive",
            "ingestion_tier": "full",
        },
    }

    return envelope
