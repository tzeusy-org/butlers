"""Factory functions for constructing realistic ingest.v1 payloads.

These factories produce valid ingest.v1 envelopes for use in E2E scenario
definitions and injection tests. They abstract away the verbose envelope
structure so scenario authors can focus on message content.

Key design decisions:
- Deterministic idempotency keys derived from message content
- Realistic payload.raw that mirrors real connector output
- Correct source/provider pairs per channel type
- Thread-ID support for email reply affinity routing
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any


def _make_message_id(prefix: str, *parts: str) -> str:
    """Generate a deterministic pseudo-message-id from content parts.

    Uses SHA-256 of concatenated parts, truncated to 16 hex chars.
    Stable across calls — same inputs always produce the same ID.
    """
    content = ":".join(parts)
    return prefix + hashlib.sha256(content.encode()).hexdigest()[:16]


def email_envelope(
    sender: str,
    subject: str,
    body: str,
    *,
    thread_id: str | None = None,
    endpoint_identity: str = "gmail:test-account",
    policy_tier: str = "default",
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a valid ingest.v1 email envelope.

    Mirrors the structure produced by the Gmail connector's
    ``_build_envelope()`` method for a Tier 1 (full) ingestion.

    Parameters
    ----------
    sender:
        Sender email address (e.g. "alice@example.com").
    subject:
        Email subject line.
    body:
        Plaintext email body.
    thread_id:
        Optional Gmail thread ID. When set, populates
        ``event.external_thread_id`` to enable thread-affinity routing.
    endpoint_identity:
        Source endpoint identity string (defaults to test-account sentinel).
    policy_tier:
        Ingestion policy tier — one of "default", "interactive",
        "high_priority". Defaults to "default".
    observed_at:
        Timestamp for ``event.observed_at``. Defaults to current UTC time.

    Returns
    -------
    dict
        A valid ``ingest.v1`` payload suitable for injection via
        ``ingest_v1(pool, envelope)``.

    Examples
    --------
    >>> env = email_envelope(
    ...     sender="alice@example.com",
    ...     subject="Team lunch Thursday",
    ...     body="Let's do noon at the usual place",
    ... )
    >>> env["source"]["channel"]
    'email'
    >>> env["source"]["provider"]
    'gmail'
    """
    ts = (observed_at or datetime.now(UTC)).isoformat()

    # Generate a deterministic RFC 2822-style message ID
    message_id = _make_message_id("<", sender, subject, body, ">")
    # Idempotency key: email:<message_id> (stable across retries)
    idempotency_key = f"email:{message_id}"

    raw: dict[str, Any] = {
        "id": message_id,
        "threadId": thread_id or message_id,
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": "owner@example.com"},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": ts},
                {"name": "Message-ID", "value": message_id},
            ],
            "mimeType": "text/plain",
            "body": {
                "data": body,
            },
        },
    }

    envelope: dict[str, Any] = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "email",
            "provider": "gmail",
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": message_id,
            "observed_at": ts,
        },
        "sender": {
            "identity": sender,
        },
        "payload": {
            "raw": raw,
            "normalized_text": f"Subject: {subject}\n\n{body}",
        },
        "control": {
            "idempotency_key": idempotency_key,
            "policy_tier": policy_tier,
        },
    }

    # Thread affinity: set external_thread_id when thread_id is provided
    if thread_id:
        envelope["event"]["external_thread_id"] = thread_id

    return envelope


def telegram_envelope(
    chat_id: int,
    text: str,
    *,
    from_user: str = "test-user",
    message_id: int | None = None,
    endpoint_identity: str = "test-bot",
    policy_tier: str = "default",
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a valid ingest.v1 Telegram envelope.

    Mirrors the structure produced by the Telegram Bot connector's
    ``_normalize_to_ingest_v1()`` method.

    Parameters
    ----------
    chat_id:
        Telegram chat ID (integer, e.g. 12345 for personal chats,
        negative values like -100123 for groups/channels).
    text:
        Message text content.
    from_user:
        Telegram username or display name for the sender
        (used to construct a test sender identity).
    message_id:
        Optional explicit message ID. When None, a deterministic ID is
        generated from (chat_id, text). Providing an explicit value enables
        precise idempotency key control in tests.
    endpoint_identity:
        Bot identity string (defaults to "test-bot" sentinel).
    policy_tier:
        Ingestion policy tier. Defaults to "default".
    observed_at:
        Timestamp for ``event.observed_at``. Defaults to current UTC time.

    Returns
    -------
    dict
        A valid ``ingest.v1`` payload suitable for injection via
        ``ingest_v1(pool, envelope)``.

    Examples
    --------
    >>> env = telegram_envelope(chat_id=12345, text="I ran 5km this morning")
    >>> env["source"]["channel"]
    'telegram_bot'
    >>> env["source"]["provider"]
    'telegram'
    >>> "tg:12345:" in env["control"]["idempotency_key"]
    True
    """
    ts = (observed_at or datetime.now(UTC)).isoformat()

    # Always compute id_hash for deterministic update_id generation in raw payload.
    # When message_id is provided, id_hash is derived from (chat_id, text) for
    # update_id only — the caller's message_id takes precedence.
    id_hash = _make_message_id("", str(chat_id), text)

    # Generate a deterministic message_id if not provided
    if message_id is None:
        # Use a numeric-range-friendly hash: take first 8 hex chars → int
        message_id = int(id_hash[:8], 16) % 1_000_000 + 1

    # Canonical idempotency key: tg:<chat_id>:<message_id>
    # Matches the real connector's key strategy for cross-connector dedup.
    idempotency_key = f"tg:{chat_id}:{message_id}"

    # Thread identity: chat_id:message_id (mirrors real connector)
    thread_identity = f"{chat_id}:{message_id}"

    # Sender identity: numeric user_id derived from from_user
    # Use SHA-256 for a stable, deterministic mapping across all Python runs
    # (built-in hash() is not stable across interpreter restarts due to hash randomization).
    user_id = int(hashlib.sha256(from_user.encode()).hexdigest()[:8], 16) % 1_000_000 + 100_000

    # Build realistic Telegram update payload (mirrors real API shape)
    raw_update: dict[str, Any] = {
        "update_id": int(id_hash[:6], 16) % 1_000_000,  # type: ignore[arg-type]
        "message": {
            "message_id": message_id,
            "from": {
                "id": user_id,
                "is_bot": False,
                "first_name": from_user,
                "username": from_user,
            },
            "chat": {
                "id": chat_id,
                "type": "private" if chat_id > 0 else "group",
                "title": None if chat_id > 0 else f"Test Group {abs(chat_id)}",
                "username": from_user if chat_id > 0 else None,
            },
            # Derive date from observed_at so the envelope is internally consistent.
            "date": int((observed_at or datetime.now(UTC)).timestamp()),
            "text": text,
        },
    }

    # For consistency, remove None values from chat dict
    raw_update["message"]["chat"] = {
        k: v for k, v in raw_update["message"]["chat"].items() if v is not None
    }

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram_bot",
            "provider": "telegram",
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": str(raw_update["update_id"]),
            "external_thread_id": thread_identity,
            "observed_at": ts,
        },
        "sender": {
            "identity": str(user_id),
        },
        "payload": {
            "raw": raw_update,
            "normalized_text": text,
        },
        "control": {
            "idempotency_key": idempotency_key,
            "policy_tier": policy_tier,
        },
    }
