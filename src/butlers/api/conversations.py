"""Data access layer for dashboard conversation and message persistence.

Provides CRUD functions over ``public.dashboard_conversations`` and
``public.dashboard_messages``.  All functions accept an asyncpg Pool and
return plain dicts so callers can construct Pydantic models as needed.

UUID7 generation follows the pattern in the Switchboard ingest module.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)

# Provider resume handles are considered fresh for this long after their last
# refresh. Chosen as a generous same-day window: long enough that a user
# picking a conversation back up later the same day still gets warm
# prompt-cache continuity, short enough that a handle from days ago (likely
# evicted provider-side already) is never worth an attempted resume. Pure
# staleness check -- there is no separate eviction job (bu-ep4ks.8 [decision]).
_PROVIDER_SESSION_TTL_SECONDS: int = 24 * 60 * 60


# ---------------------------------------------------------------------------
# UUID7 helper (time-ordered)
# ---------------------------------------------------------------------------


def _generate_uuid7() -> UUID:
    """Generate a UUIDv7-compatible UUID (time-ordered)."""
    timestamp_ms = int(datetime.now(UTC).timestamp() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)

    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)


# ---------------------------------------------------------------------------
# Title generation
# ---------------------------------------------------------------------------


def _auto_title(message: str, max_len: int = 80) -> str:
    """Generate a conversation title from the first user message.

    Truncates at word boundary with ellipsis if needed.
    """
    message = message.strip()
    if len(message) <= max_len:
        return message
    # Truncate at word boundary
    truncated = message[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated + "…"


# ---------------------------------------------------------------------------
# Conversation CRUD
# ---------------------------------------------------------------------------


async def conversation_create(
    pool: asyncpg.Pool,
    *,
    butler_name: str,
    first_message: str,
) -> dict[str, Any]:
    """Insert a new conversation row.

    Returns a dict with all conversation columns.
    """
    conv_id = _generate_uuid7()
    title = _auto_title(first_message)
    now = datetime.now(UTC)

    await pool.execute(
        """
        INSERT INTO public.dashboard_conversations
            (id, butler_name, title, status, created_at, updated_at,
             message_count)
        VALUES ($1, $2, $3, 'active', $4, $4, 0)
        """,
        conv_id,
        butler_name,
        title,
        now,
    )

    return {
        "id": conv_id,
        "butler_name": butler_name,
        "title": title,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "message_count": 0,
        "routed_butler": None,
    }


async def conversation_get(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    butler_name: str,
) -> dict[str, Any] | None:
    """Fetch a conversation by id + butler_name.  Returns None if not found."""
    row = await pool.fetchrow(
        """
        SELECT id, butler_name, title, status, created_at, updated_at,
               message_count, routed_butler
        FROM public.dashboard_conversations
        WHERE id = $1 AND butler_name = $2
        """,
        conversation_id,
        butler_name,
    )
    return dict(row) if row else None


async def conversation_list(
    pool: asyncpg.Pool,
    *,
    butler_name: str,
    status: str = "active",
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """List conversations for a butler with pagination.

    Returns (rows, total_count).  status='all' returns both active and archived.

    Each row also carries ``latest_assistant_reply_at`` (the max ``created_at``
    of that conversation's assistant-role messages, or ``None`` if it has no
    replies yet). The unread-badge watermark (``use-chat-unread.ts``) keys off
    that persisted-message timestamp.
    """
    if status == "all":
        where = "c.butler_name = $1"
        args: list[Any] = [butler_name]
    else:
        where = "c.butler_name = $1 AND c.status = $2"
        args = [butler_name, status]

    total: int = (
        await pool.fetchval(
            f"SELECT COUNT(*) FROM public.dashboard_conversations c WHERE {where}",
            *args,
        )
        or 0
    )

    rows = await pool.fetch(
        f"""
        SELECT c.id, c.butler_name, c.title, c.status, c.created_at, c.updated_at,
               c.message_count, c.routed_butler,
               (
                   SELECT MAX(m.created_at)
                   FROM public.dashboard_messages m
                   WHERE m.conversation_id = c.id AND m.role = 'assistant'
               ) AS latest_assistant_reply_at
        FROM public.dashboard_conversations c
        WHERE {where}
        ORDER BY c.updated_at DESC
        OFFSET ${len(args) + 1} LIMIT ${len(args) + 2}
        """,
        *args,
        offset,
        limit,
    )

    return [dict(r) for r in rows], total


async def conversation_update(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    butler_name: str,
    title: str | None = None,
    status: str | None = None,
) -> dict[str, Any] | None:
    """Update conversation title and/or status.

    Returns updated conversation dict, or None if not found / wrong butler.
    """
    set_clauses: list[str] = ["updated_at = now()"]
    args: list[Any] = []
    idx = 1

    if title is not None:
        set_clauses.append(f"title = ${idx}")
        args.append(title)
        idx += 1

    if status is not None:
        set_clauses.append(f"status = ${idx}")
        args.append(status)
        idx += 1

    args.extend([conversation_id, butler_name])

    row = await pool.fetchrow(
        f"""
        UPDATE public.dashboard_conversations
        SET {", ".join(set_clauses)}
        WHERE id = ${idx} AND butler_name = ${idx + 1}
        RETURNING id, butler_name, title, status, created_at, updated_at,
                  message_count, routed_butler
        """,
        *args,
    )
    return dict(row) if row else None


async def conversation_unarchive_if_needed(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    butler_name: str,
) -> None:
    """Reactivate an archived conversation before processing a new message."""
    await pool.execute(
        """
        UPDATE public.dashboard_conversations
        SET status = 'active', updated_at = now()
        WHERE id = $1 AND butler_name = $2 AND status = 'archived'
        """,
        conversation_id,
        butler_name,
    )


async def conversation_set_routed_butler(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    routed_butler: str,
) -> None:
    """Stamp the sticky ``routed_butler`` on a conversation's first successful route.

    A no-op once ``routed_butler`` is already set, so repeat calls (e.g. from a
    retried submission) never clobber the original routing decision. Scoped by
    id only — classification-routed (Switchboard widget) conversations are the
    only callers, and ``id`` is already a globally unique key.
    """
    await pool.execute(
        """
        UPDATE public.dashboard_conversations
        SET routed_butler = $2, updated_at = now()
        WHERE id = $1 AND routed_butler IS NULL
        """,
        conversation_id,
        routed_butler,
    )


async def conversation_get_or_create_by_thread(
    pool: asyncpg.Pool,
    *,
    butler_name: str,
    source_channel: str,
    external_conversation_id: str,
    first_message: str,
) -> tuple[dict[str, Any], bool]:
    """Upsert the channel-agnostic conversation anchor for an inbound thread.

    Generalizes conversation creation beyond the dashboard-only
    ``conversation_create``: any channel that already normalizes a
    ``external_conversation_id`` at ingest (Telegram, email, ...) can call this
    once per inbound message to get a stable ``dashboard_conversations`` row
    to attach session lineage and a provider resume handle to, without
    needing to track its own anchor concept.

    Concurrency-safe: relies on the partial unique index on
    ``(butler_name, source_channel, external_conversation_id)`` (core_209) so
    two concurrent callers for the same thread converge on one row via
    ``ON CONFLICT ... DO NOTHING`` rather than racing to create duplicates.

    Returns ``(conversation, is_new)``.
    """
    conv_id = _generate_uuid7()
    title = _auto_title(first_message)
    now = datetime.now(UTC)

    inserted = await pool.fetchrow(
        """
        INSERT INTO public.dashboard_conversations
            (id, butler_name, title, status, created_at, updated_at,
             message_count, source_channel, source_thread_identity,
             external_conversation_id)
        VALUES ($1, $2, $3, 'active', $4, $4, 0, $5, $6, $6)
        ON CONFLICT (butler_name, source_channel, external_conversation_id)
            WHERE external_conversation_id IS NOT NULL
        DO NOTHING
        RETURNING id, butler_name, title, status, created_at, updated_at,
                  message_count, routed_butler, source_channel, source_thread_identity,
                  external_conversation_id
        """,
        conv_id,
        butler_name,
        title,
        now,
        source_channel,
        external_conversation_id,
    )
    if inserted is not None:
        return dict(inserted), True

    existing = await pool.fetchrow(
        """
        SELECT id, butler_name, title, status, created_at, updated_at,
               message_count, routed_butler, source_channel, source_thread_identity,
               external_conversation_id
        FROM public.dashboard_conversations
        WHERE butler_name = $1 AND source_channel = $2 AND external_conversation_id = $3
        """,
        butler_name,
        source_channel,
        external_conversation_id,
    )
    if existing is None:
        raise RuntimeError(
            f"Conversation anchor for {butler_name}/{source_channel}/"
            f"{external_conversation_id} disappeared after an upsert conflict"
        )
    return dict(existing), False


async def conversation_get_provider_session(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    butler_name: str,
) -> dict[str, Any] | None:
    """Fetch the current provider resume handle for a conversation, if any.

    Returns ``None`` when the conversation has never had a provider session
    recorded (including when the conversation itself does not exist) --
    callers treat that identically to "cold start", never as an error.
    """
    row = await pool.fetchrow(
        """
        SELECT provider_session_id, provider_runtime_type, provider_session_updated_at
        FROM public.dashboard_conversations
        WHERE id = $1 AND butler_name = $2
        """,
        conversation_id,
        butler_name,
    )
    if row is None or row["provider_session_id"] is None:
        return None
    return dict(row)


async def conversation_set_provider_session(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    butler_name: str,
    provider_session_id: str,
    provider_runtime_type: str,
) -> None:
    """Record the provider-native session id minted for a conversation's turn.

    Overwrites any prior handle -- "one memory per thread" means only the
    most recent provider session is ever worth resuming from.
    """
    await pool.execute(
        """
        UPDATE public.dashboard_conversations
        SET provider_session_id = $3,
            provider_runtime_type = $4,
            provider_session_updated_at = now()
        WHERE id = $1 AND butler_name = $2
        """,
        conversation_id,
        butler_name,
        provider_session_id,
        provider_runtime_type,
    )


async def conversation_clear_provider_session(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    butler_name: str,
) -> None:
    """Evict a conversation's provider resume handle (fall-back-to-cold).

    Callers invoke this when a resume attempt fails (the provider rejects
    the handle as expired or unknown) so the *next* turn cold-starts cleanly
    instead of repeatedly retrying a dead handle.
    """
    await pool.execute(
        """
        UPDATE public.dashboard_conversations
        SET provider_session_id = NULL,
            provider_runtime_type = NULL,
            provider_session_updated_at = NULL
        WHERE id = $1 AND butler_name = $2
        """,
        conversation_id,
        butler_name,
    )


def resolve_resume_handle(
    provider_session: dict[str, Any] | None,
    *,
    runtime_type: str,
    ttl_seconds: int = _PROVIDER_SESSION_TTL_SECONDS,
    now: datetime | None = None,
) -> str | None:
    """Return a usable provider resume handle, or ``None`` if not applicable.

    Pure function (no DB access) so eviction/TTL logic is unit-testable
    without a pool. A handle is usable only when:

    - one is present at all,
    - it was minted by the *same* ``runtime_type`` the caller is about to
      invoke (resume tokens are provider-specific and never portable across
      adapters), and
    - its ``provider_session_updated_at`` is within ``ttl_seconds`` of `now`.

    Any other case (missing, runtime mismatch, expired) returns ``None`` --
    the caller's contract is to treat that as a transparent cold start, never
    an error.
    """
    if provider_session is None:
        return None
    handle = provider_session.get("provider_session_id")
    if not handle:
        return None
    if provider_session.get("provider_runtime_type") != runtime_type:
        return None
    updated_at = provider_session.get("provider_session_updated_at")
    if updated_at is None:
        return None
    effective_now = now if now is not None else datetime.now(UTC)
    if effective_now - updated_at > timedelta(seconds=ttl_seconds):
        return None
    return handle


async def conversation_search(
    pool: asyncpg.Pool,
    *,
    butler_name: str,
    query: str,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Substring search across conversation messages for a butler.

    Returns (results, total_count).  Each result includes the conversation
    metadata plus a ``snippet`` field from the matching message.  Results are
    ordered by most recent matching message first (``msg_created_at DESC``).
    """
    rows = await pool.fetch(
        """
        SELECT
            sub.id, sub.butler_name, sub.title, sub.status,
            sub.created_at, sub.updated_at,
            sub.message_count, sub.routed_butler,
            (
                SELECT MAX(reply.created_at)
                FROM public.dashboard_messages reply
                WHERE reply.conversation_id = sub.id AND reply.role = 'assistant'
            ) AS latest_assistant_reply_at,
            sub.snippet, sub.msg_created_at
        FROM (
            SELECT DISTINCT ON (c.id)
                c.id, c.butler_name, c.title, c.status, c.created_at, c.updated_at,
                c.message_count, c.routed_butler,
                substring(m.content, 1, 200) AS snippet,
                m.created_at AS msg_created_at
            FROM public.dashboard_conversations c
            JOIN public.dashboard_messages m ON m.conversation_id = c.id
            WHERE c.butler_name = $1
              AND m.content ILIKE $2
            ORDER BY c.id, m.created_at DESC
        ) AS sub
        ORDER BY sub.msg_created_at DESC
        LIMIT $3 OFFSET $4
        """,
        butler_name,
        f"%{query}%",
        limit,
        offset,
    )

    count: int = (
        await pool.fetchval(
            """
        SELECT COUNT(DISTINCT c.id)
        FROM public.dashboard_conversations c
        JOIN public.dashboard_messages m ON m.conversation_id = c.id
        WHERE c.butler_name = $1
          AND m.content ILIKE $2
        """,
            butler_name,
            f"%{query}%",
        )
        or 0
    )

    results = []
    for r in rows:
        d = dict(r)
        d.pop("msg_created_at", None)
        results.append(d)

    return results, count


async def conversation_summary(
    pool: asyncpg.Pool,
    *,
    butler_name: str,
) -> dict[str, Any]:
    """Return aggregate statistics for all conversations of a butler."""
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) AS total_conversations,
            COUNT(*) FILTER (WHERE status = 'active') AS active_conversations,
            COALESCE(SUM(message_count), 0) AS total_messages
        FROM public.dashboard_conversations
        WHERE butler_name = $1
        """,
        butler_name,
    )
    return (
        dict(row)
        if row
        else {
            "total_conversations": 0,
            "active_conversations": 0,
            "total_messages": 0,
        }
    )


# ---------------------------------------------------------------------------
# Message CRUD
# ---------------------------------------------------------------------------


async def message_create(
    pool: asyncpg.Pool,
    *,
    conversation_id: UUID,
    role: str,
    content: str,
    session_id: UUID | None = None,
    model_name: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    duration_ms: int | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    error: str | None = None,
    request_id: UUID | None = None,
) -> dict[str, Any]:
    """Insert a new message row.  Returns the full message dict."""
    msg_id = _generate_uuid7()
    now = datetime.now(UTC)
    await pool.execute(
        """
        INSERT INTO public.dashboard_messages
            (id, conversation_id, role, content, created_at,
             session_id, model_name, input_tokens, output_tokens,
             duration_ms, tool_calls, error, request_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        """,
        msg_id,
        conversation_id,
        role,
        content,
        now,
        session_id,
        model_name,
        input_tokens,
        output_tokens,
        duration_ms,
        tool_calls,
        error,
        request_id,
    )

    return {
        "id": msg_id,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "created_at": now,
        "session_id": session_id,
        "model_name": model_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "duration_ms": duration_ms,
        "tool_calls": tool_calls,
        "error": error,
        "request_id": request_id,
    }


async def message_create_idempotent(
    pool: asyncpg.Pool,
    *,
    message_id: UUID,
    conversation_id: UUID,
    role: str,
    content: str,
) -> tuple[dict[str, Any], bool]:
    """Create a client-identified message once, returning ``(message, is_new)``.

    Dashboard retries reuse a client-generated message UUID.  The first writer
    persists that message and later identical submissions recover the original
    row, so the Switchboard sees the same external event identity rather than
    relying on its content-hash fallback.
    """
    now = datetime.now(UTC)
    inserted = await pool.fetchrow(
        """
        INSERT INTO public.dashboard_messages
            (id, conversation_id, role, content, created_at,
             session_id, model_name, input_tokens, output_tokens,
             duration_ms, tool_calls, error, request_id)
        VALUES ($1, $2, $3, $4, $5, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)
        ON CONFLICT (id) DO NOTHING
        RETURNING id, conversation_id, role, content, created_at,
                  session_id, model_name, input_tokens, output_tokens,
                  duration_ms, tool_calls, error, request_id
        """,
        message_id,
        conversation_id,
        role,
        content,
        now,
    )
    if inserted is not None:
        return dict(inserted), True

    existing = await pool.fetchrow(
        """
        SELECT id, conversation_id, role, content, created_at,
               session_id, model_name, input_tokens, output_tokens,
               duration_ms, tool_calls, error, request_id
        FROM public.dashboard_messages
        WHERE id = $1
        """,
        message_id,
    )
    if existing is None:
        raise RuntimeError(f"Message {message_id} disappeared after an idempotency conflict")

    message = dict(existing)
    if (
        message["conversation_id"] != conversation_id
        or message["role"] != role
        or message["content"] != content
    ):
        raise ValueError("message_id is already associated with a different dashboard message")
    return message, False


async def message_get_by_id(
    pool: asyncpg.Pool,
    message_id: UUID,
) -> dict[str, Any] | None:
    """Fetch a dashboard message by its client-provided identity."""
    row = await pool.fetchrow(
        """
        SELECT id, conversation_id, role, content, created_at,
               session_id, model_name, input_tokens, output_tokens,
               duration_ms, tool_calls, error, request_id
        FROM public.dashboard_messages
        WHERE id = $1
        """,
        message_id,
    )
    return dict(row) if row else None


async def conversation_reply_create(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    message: str,
    request_id: UUID | None = None,
) -> dict[str, Any] | None:
    """Persist the ``conversation_reply`` confirm-loop message for a conversation.

    Writes an assistant-role row and bumps the conversation's message count.
    Returns the created message dict, or ``None`` if ``conversation_id`` does
    not reference an existing conversation (the caller — the ``conversation_reply``
    MCP tool — surfaces this as an actionable error to the calling model rather
    than raising, since a stale/hallucinated id is a model-correctable mistake).
    """
    exists = await pool.fetchval(
        "SELECT 1 FROM public.dashboard_conversations WHERE id = $1", conversation_id
    )
    if not exists:
        return None

    msg = await message_create(
        pool,
        conversation_id=conversation_id,
        role="assistant",
        content=message,
        request_id=request_id,
    )
    await pool.execute(
        """
        UPDATE public.dashboard_conversations
        SET message_count = message_count + 1, updated_at = now()
        WHERE id = $1
        """,
        conversation_id,
    )
    return msg


async def message_list(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """List messages in a conversation ordered by created_at ASC.

    Returns (messages, total_count).
    """
    total: int = (
        await pool.fetchval(
            "SELECT COUNT(*) FROM public.dashboard_messages WHERE conversation_id = $1",
            conversation_id,
        )
        or 0
    )

    rows = await pool.fetch(
        """
        SELECT id, conversation_id, role, content, created_at,
               session_id, model_name, input_tokens, output_tokens,
               duration_ms, tool_calls, error, request_id
        FROM public.dashboard_messages
        WHERE conversation_id = $1
        ORDER BY created_at ASC
        OFFSET $2 LIMIT $3
        """,
        conversation_id,
        offset,
        limit,
    )

    messages = []
    for row in rows:
        d = dict(row)
        # Deserialize tool_calls JSONB
        if isinstance(d.get("tool_calls"), str):
            try:
                d["tool_calls"] = json.loads(d["tool_calls"])
            except (json.JSONDecodeError, TypeError):
                d["tool_calls"] = None
        messages.append(d)

    return messages, total


async def message_find_reply_since(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    since: datetime,
) -> dict[str, Any] | None:
    """Find the earliest ``conversation_reply`` assistant message after ``since``.

    Used by the dashboard SSE poller to detect a fresh reply without
    depending on the routed butler's ``sessions`` row — the whole point of
    the confirm-loop reply is that it can land well before the spawned
    session finishes, so polling session completion would miss it.
    """
    row = await pool.fetchrow(
        """
        SELECT id, content, created_at, session_id, model_name,
               input_tokens, output_tokens, duration_ms, tool_calls, error, request_id
        FROM public.dashboard_messages
        WHERE conversation_id = $1 AND role = 'assistant' AND created_at > $2
        ORDER BY created_at ASC
        LIMIT 1
        """,
        conversation_id,
        since,
    )
    if row is None:
        return None

    d = dict(row)
    if isinstance(d.get("tool_calls"), str):
        try:
            d["tool_calls"] = json.loads(d["tool_calls"])
        except (json.JSONDecodeError, TypeError):
            d["tool_calls"] = None
    return d


async def conversation_message_count_increment(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    butler_name: str,
) -> None:
    """Increment the user message count on a conversation (no token data).

    Scoped by both ``id`` and ``butler_name`` to prevent accidental
    cross-butler updates.
    """
    await pool.execute(
        """
        UPDATE public.dashboard_conversations
        SET message_count = message_count + 1, updated_at = now()
        WHERE id = $1 AND butler_name = $2
        """,
        conversation_id,
        butler_name,
    )
