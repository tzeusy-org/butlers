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
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


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
             message_count, total_input_tokens, total_output_tokens, total_duration_ms)
        VALUES ($1, $2, $3, 'active', $4, $4, 0, 0, 0, 0)
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
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_duration_ms": 0,
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
               message_count, total_input_tokens, total_output_tokens, total_duration_ms
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
    """
    if status == "all":
        where = "butler_name = $1"
        args: list[Any] = [butler_name]
    else:
        where = "butler_name = $1 AND status = $2"
        args = [butler_name, status]

    total: int = (
        await pool.fetchval(
            f"SELECT COUNT(*) FROM public.dashboard_conversations WHERE {where}",
            *args,
        )
        or 0
    )

    rows = await pool.fetch(
        f"""
        SELECT id, butler_name, title, status, created_at, updated_at,
               message_count, total_input_tokens, total_output_tokens, total_duration_ms
        FROM public.dashboard_conversations
        WHERE {where}
        ORDER BY updated_at DESC
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
                  message_count, total_input_tokens, total_output_tokens, total_duration_ms
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


async def conversation_update_aggregates(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    butler_name: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_ms: int = 0,
) -> None:
    """Increment conversation denormalized aggregate counters.

    Scoped by both ``id`` and ``butler_name`` to prevent accidental
    cross-butler updates if the helper is reused outside the current
    router's "conversation_get first" pattern.
    """
    await pool.execute(
        """
        UPDATE public.dashboard_conversations
        SET message_count = message_count + 1,
            total_input_tokens = total_input_tokens + $3,
            total_output_tokens = total_output_tokens + $4,
            total_duration_ms = total_duration_ms + $5,
            updated_at = now()
        WHERE id = $1 AND butler_name = $2
        """,
        conversation_id,
        butler_name,
        input_tokens,
        output_tokens,
        duration_ms,
    )


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
            sub.message_count, sub.total_input_tokens, sub.total_output_tokens,
            sub.total_duration_ms, sub.snippet, sub.msg_created_at
        FROM (
            SELECT DISTINCT ON (c.id)
                c.id, c.butler_name, c.title, c.status, c.created_at, c.updated_at,
                c.message_count, c.total_input_tokens, c.total_output_tokens, c.total_duration_ms,
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
            COALESCE(SUM(message_count), 0) AS total_messages,
            COALESCE(SUM(total_input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(total_output_tokens), 0) AS total_output_tokens,
            COALESCE(SUM(total_duration_ms), 0) AS total_duration_ms
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
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_duration_ms": 0,
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
    tool_calls_json = json.dumps(tool_calls) if tool_calls is not None else None

    await pool.execute(
        """
        INSERT INTO public.dashboard_messages
            (id, conversation_id, role, content, created_at,
             session_id, model_name, input_tokens, output_tokens,
             duration_ms, tool_calls, error, request_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $13)
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
        tool_calls_json,
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
