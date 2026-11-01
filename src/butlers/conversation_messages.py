"""Shared dashboard message persistence for API and daemon callers.

The API facade retains its existing exports; the pipeline uses this lower
boundary directly. Persistence, count updates and best-effort reply notification
remain one implementation, independent of HTTP pagination and SSE listeners.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from butlers.chat_stream_events import publish_chat_stream_event


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
    sources: list[str] | None = None,
    citations: list[dict[str, Any]] | None = None,
    routed_butler: str | None = None,
    page_context: dict[str, Any] | None = None,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    """Insert a new message row.  Returns the full message dict.

    ``page_context``/``captured_at`` carry the dashboard ContextChip snapshot
    for a user-role message (bu-0ynlk.4); ``captured_at`` defaults to now()
    whenever ``page_context`` is supplied and the caller omits it.
    """
    msg_id = _generate_uuid7()
    now = datetime.now(UTC)
    if page_context is not None and captured_at is None:
        captured_at = now
    await pool.execute(
        """
        INSERT INTO public.dashboard_messages
            (id, conversation_id, role, content, created_at,
             session_id, model_name, input_tokens, output_tokens,
             duration_ms, tool_calls, error, request_id, sources, citations,
             routed_butler, page_context, captured_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
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
        sources,
        citations,
        routed_butler,
        page_context,
        captured_at,
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
        "sources": sources,
        "citations": citations,
        "routed_butler": routed_butler,
        "page_context": page_context,
        "captured_at": captured_at,
    }


async def conversation_reply_create(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    message: str,
    request_id: UUID | None = None,
    sources: list[str] | None = None,
    citations: list[dict[str, Any]] | None = None,
    routed_butler: str | None = None,
    session_id: UUID | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Persist the ``conversation_reply`` confirm-loop message for a conversation.

    Writes an assistant-role row and bumps the conversation's message count.
    Returns the created message dict, or ``None`` if ``conversation_id`` does
    not reference an existing conversation (the caller — the ``conversation_reply``
    MCP tool — surfaces this as an actionable error to the calling model rather
    than raising, since a stale/hallucinated id is a model-correctable mistake).

    ``sources`` carries an answer-lane reply's citations (see the
    ``conversation_reply`` MCP tool); ``None`` for every other lane.

    ``session_id``/``tool_calls`` are the ambient runtime session id and the
    executed tool calls captured for this turn (best-effort — ``None`` when
    the runtime context is unavailable, e.g. a session that never bound one).

    Publishes a best-effort ``reply_ready`` NOTIFY on the request's
    chat-stream channel (see ``butlers.api.chat_stream``) so an SSE generator
    watching this turn wakes immediately instead of waiting for its next
    safety-net poll (bu-0ynlk.7). A dropped/failed NOTIFY never affects this
    write — the poll remains the source of truth either way.
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
        sources=sources,
        citations=citations,
        routed_butler=routed_butler,
        session_id=session_id,
        tool_calls=tool_calls,
    )
    await pool.execute(
        """
        UPDATE public.dashboard_conversations
        SET message_count = message_count + 1, updated_at = now()
        WHERE id = $1
        """,
        conversation_id,
    )
    if request_id is not None:
        await publish_chat_stream_event(pool, request_id, "reply_ready")
    return msg
