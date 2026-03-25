"""Data access functions for dashboard conversations and messages.

Provides CRUD, search, and aggregate query helpers for the
``shared.dashboard_conversations`` and ``shared.dashboard_messages`` tables.

All functions accept an asyncpg connection or pool object (``asyncpg.Pool``
or ``asyncpg.Connection``) via the ``conn`` parameter.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

_Conn = asyncpg.Pool | asyncpg.Connection

# ---------------------------------------------------------------------------
# Conversation helpers
# ---------------------------------------------------------------------------

_CONVERSATION_COLUMNS = (
    "id, butler_name, title, status, created_at, updated_at, "
    "message_count, total_input_tokens, total_output_tokens, total_duration_ms"
)


def _row_to_conversation(row: asyncpg.Record) -> dict[str, Any]:
    """Convert an asyncpg Record to a conversation dict."""
    return {
        "id": row["id"],
        "butler_name": row["butler_name"],
        "title": row["title"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "message_count": row["message_count"],
        "total_input_tokens": row["total_input_tokens"],
        "total_output_tokens": row["total_output_tokens"],
        "total_duration_ms": row["total_duration_ms"],
    }


# ---------------------------------------------------------------------------
# Conversation CRUD
# ---------------------------------------------------------------------------


async def conversation_create(
    conn: _Conn,
    *,
    conversation_id: UUID,
    butler_name: str,
    title: str,
) -> dict[str, Any]:
    """Insert a new conversation row and return the created record.

    Parameters
    ----------
    conn:
        asyncpg pool or connection.
    conversation_id:
        Pre-generated UUID7 for the new conversation.
    butler_name:
        Name of the butler this conversation belongs to.
    title:
        Initial title (derived from the first user message).

    Returns
    -------
    dict
        The inserted conversation row as a dict.
    """
    row = await conn.fetchrow(
        f"""
        INSERT INTO shared.dashboard_conversations
            (id, butler_name, title, status)
        VALUES ($1, $2, $3, 'active')
        RETURNING {_CONVERSATION_COLUMNS}
        """,
        conversation_id,
        butler_name,
        title,
    )
    if row is None:
        raise RuntimeError(f"Failed to insert conversation {conversation_id}")
    return _row_to_conversation(row)


async def conversation_get(
    conn: _Conn,
    *,
    conversation_id: UUID,
    butler_name: str,
) -> dict[str, Any] | None:
    """Fetch a single conversation by ID, scoped to a butler.

    Returns None if not found or belongs to a different butler.
    """
    row = await conn.fetchrow(
        f"""
        SELECT {_CONVERSATION_COLUMNS}
        FROM shared.dashboard_conversations
        WHERE id = $1 AND butler_name = $2
        """,
        conversation_id,
        butler_name,
    )
    return _row_to_conversation(row) if row is not None else None


async def conversation_list(
    conn: _Conn,
    *,
    butler_name: str,
    status: str | None = "active",
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Return paginated conversations for a butler.

    Parameters
    ----------
    status:
        Filter by status. Pass ``"all"`` or ``None`` for no filter.
        Defaults to ``"active"``.

    Returns
    -------
    (rows, total)
        A tuple of (page of conversation dicts, total count).
    """
    conditions: list[str] = ["butler_name = $1"]
    args: list[Any] = [butler_name]
    idx = 2

    if status and status != "all":
        conditions.append(f"status = ${idx}")
        args.append(status)
        idx += 1

    where = " WHERE " + " AND ".join(conditions)

    total: int = (
        await conn.fetchval(
            f"SELECT count(*) FROM shared.dashboard_conversations{where}",
            *args,
        )
        or 0
    )

    rows = await conn.fetch(
        f"""
        SELECT {_CONVERSATION_COLUMNS}
        FROM shared.dashboard_conversations{where}
        ORDER BY updated_at DESC
        OFFSET ${idx} LIMIT ${idx + 1}
        """,
        *args,
        offset,
        limit,
    )

    return [_row_to_conversation(r) for r in rows], total


async def conversation_update(
    conn: _Conn,
    *,
    conversation_id: UUID,
    butler_name: str,
    title: str | None = None,
    status: str | None = None,
) -> dict[str, Any] | None:
    """Update title and/or status of a conversation.

    Returns the updated row, or None if the conversation was not found or
    belongs to a different butler.
    """
    if title is None and status is None:
        # Nothing to update — fetch and return current state
        return await conversation_get(
            conn, conversation_id=conversation_id, butler_name=butler_name
        )

    sets: list[str] = []
    args: list[Any] = []
    idx = 1

    if title is not None:
        sets.append(f"title = ${idx}")
        args.append(title)
        idx += 1

    if status is not None:
        sets.append(f"status = ${idx}")
        args.append(status)
        idx += 1

    # Always bump updated_at
    sets.append("updated_at = now()")

    args.append(conversation_id)
    args.append(butler_name)

    row = await conn.fetchrow(
        f"""
        UPDATE shared.dashboard_conversations
        SET {", ".join(sets)}
        WHERE id = ${idx} AND butler_name = ${idx + 1}
        RETURNING {_CONVERSATION_COLUMNS}
        """,
        *args,
    )
    return _row_to_conversation(row) if row is not None else None


async def conversation_search(
    conn: _Conn,
    *,
    butler_name: str,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Full-text search across conversation messages for a butler.

    Finds conversations whose messages contain ``query`` (case-insensitive
    substring match). Returns conversation metadata with a ``snippet`` field
    containing the first matching message content, truncated to 200 chars.

    Results are ordered by the most recent matching message first.
    """
    rows = await conn.fetch(
        f"""
        SELECT DISTINCT ON (c.id, c.updated_at)
               {", ".join(f"c.{col}" for col in _CONVERSATION_COLUMNS.split(", "))},
               LEFT(m.content, 200) AS snippet
        FROM shared.dashboard_conversations c
        JOIN shared.dashboard_messages m ON m.conversation_id = c.id
        WHERE c.butler_name = $1
          AND m.content ILIKE $2
        ORDER BY c.updated_at DESC, c.id
        LIMIT $3
        """,
        butler_name,
        f"%{query}%",
        limit,
    )
    results = []
    for row in rows:
        data = _row_to_conversation(row)
        data["snippet"] = row["snippet"]
        results.append(data)
    return results


async def conversation_summary(
    conn: _Conn,
    *,
    butler_name: str,
) -> dict[str, Any]:
    """Return aggregate statistics for all conversations of a butler.

    Returns
    -------
    dict with keys:
        total_conversations, active_conversations, total_messages,
        total_input_tokens, total_output_tokens, total_duration_ms
    """
    row = await conn.fetchrow(
        """
        SELECT
            count(*)                                    AS total_conversations,
            count(*) FILTER (WHERE status = 'active')  AS active_conversations,
            coalesce(sum(message_count), 0)             AS total_messages,
            coalesce(sum(total_input_tokens), 0)        AS total_input_tokens,
            coalesce(sum(total_output_tokens), 0)       AS total_output_tokens,
            coalesce(sum(total_duration_ms), 0)         AS total_duration_ms
        FROM shared.dashboard_conversations
        WHERE butler_name = $1
        """,
        butler_name,
    )
    if row is None:
        return {
            "total_conversations": 0,
            "active_conversations": 0,
            "total_messages": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_duration_ms": 0,
        }
    return {
        "total_conversations": row["total_conversations"],
        "active_conversations": row["active_conversations"],
        "total_messages": row["total_messages"],
        "total_input_tokens": row["total_input_tokens"],
        "total_output_tokens": row["total_output_tokens"],
        "total_duration_ms": row["total_duration_ms"],
    }


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------

_MESSAGE_COLUMNS = (
    "id, conversation_id, role, content, created_at, "
    "session_id, model_name, input_tokens, output_tokens, duration_ms, "
    "tool_calls, error, request_id"
)


def _row_to_message(row: asyncpg.Record) -> dict[str, Any]:
    """Convert an asyncpg Record to a message dict."""
    tool_calls = row["tool_calls"]
    # asyncpg returns JSONB as a Python object; ensure it's the right type
    if isinstance(tool_calls, str):
        import json

        tool_calls = json.loads(tool_calls)

    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "role": row["role"],
        "content": row["content"],
        "created_at": row["created_at"],
        "session_id": row["session_id"],
        "model_name": row["model_name"],
        "input_tokens": row["input_tokens"],
        "output_tokens": row["output_tokens"],
        "duration_ms": row["duration_ms"],
        "tool_calls": tool_calls,
        "error": row["error"],
        "request_id": row["request_id"],
    }


# ---------------------------------------------------------------------------
# Message CRUD
# ---------------------------------------------------------------------------


async def message_create(
    conn: _Conn,
    *,
    message_id: UUID,
    conversation_id: UUID,
    role: str,
    content: str,
    session_id: UUID | None = None,
    model_name: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    duration_ms: int | None = None,
    tool_calls: list[Any] | None = None,
    error: str | None = None,
    request_id: UUID | None = None,
) -> dict[str, Any]:
    """Insert a new message row and update conversation aggregates.

    For assistant messages, increments conversation ``message_count`` and
    adds to the token/duration aggregates. For user messages, only increments
    ``message_count``.

    Returns the inserted message dict.
    """
    import json as _json

    tool_calls_json = _json.dumps(tool_calls) if tool_calls is not None else None

    row = await conn.fetchrow(
        f"""
        INSERT INTO shared.dashboard_messages
            (id, conversation_id, role, content,
             session_id, model_name, input_tokens, output_tokens,
             duration_ms, tool_calls, error, request_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12)
        RETURNING {_MESSAGE_COLUMNS}
        """,
        message_id,
        conversation_id,
        role,
        content,
        session_id,
        model_name,
        input_tokens,
        output_tokens,
        duration_ms,
        tool_calls_json,
        error,
        request_id,
    )
    if row is None:
        raise RuntimeError(f"Failed to insert message {message_id}")

    # Update conversation aggregates
    await _update_conversation_aggregates(
        conn,
        conversation_id=conversation_id,
        role=role,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=duration_ms,
    )

    return _row_to_message(row)


async def _update_conversation_aggregates(
    conn: _Conn,
    *,
    conversation_id: UUID,
    role: str,
    input_tokens: int | None,
    output_tokens: int | None,
    duration_ms: int | None,
) -> None:
    """Increment conversation aggregate counters after a message is added.

    Always increments ``message_count`` and ``updated_at``.
    For assistant messages, also increments token/duration aggregates.
    """
    if role == "assistant":
        await conn.execute(
            """
            UPDATE shared.dashboard_conversations
            SET message_count = message_count + 1,
                total_input_tokens  = total_input_tokens  + coalesce($2, 0),
                total_output_tokens = total_output_tokens + coalesce($3, 0),
                total_duration_ms   = total_duration_ms   + coalesce($4, 0),
                updated_at = now()
            WHERE id = $1
            """,
            conversation_id,
            input_tokens,
            output_tokens,
            duration_ms,
        )
    else:
        await conn.execute(
            """
            UPDATE shared.dashboard_conversations
            SET message_count = message_count + 1,
                updated_at = now()
            WHERE id = $1
            """,
            conversation_id,
        )


async def message_list(
    conn: _Conn,
    *,
    conversation_id: UUID,
    butler_name: str,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int] | None:
    """Return paginated messages for a conversation, ordered by created_at ASC.

    Returns None if the conversation does not exist or belongs to a different
    butler. Returns (rows, total) on success.
    """
    # Verify the conversation belongs to this butler
    exists = await conn.fetchval(
        "SELECT 1 FROM shared.dashboard_conversations WHERE id = $1 AND butler_name = $2",
        conversation_id,
        butler_name,
    )
    if exists is None:
        return None

    total: int = (
        await conn.fetchval(
            "SELECT count(*) FROM shared.dashboard_messages WHERE conversation_id = $1",
            conversation_id,
        )
        or 0
    )

    rows = await conn.fetch(
        f"""
        SELECT {_MESSAGE_COLUMNS}
        FROM shared.dashboard_messages
        WHERE conversation_id = $1
        ORDER BY created_at ASC
        OFFSET $2 LIMIT $3
        """,
        conversation_id,
        offset,
        limit,
    )

    return [_row_to_message(r) for r in rows], total


async def message_get(
    conn: _Conn,
    *,
    message_id: UUID,
) -> dict[str, Any] | None:
    """Fetch a single message by ID.

    Returns None if not found.
    """
    row = await conn.fetchrow(
        f"SELECT {_MESSAGE_COLUMNS} FROM shared.dashboard_messages WHERE id = $1",
        message_id,
    )
    return _row_to_message(row) if row is not None else None


# ---------------------------------------------------------------------------
# Title generation
# ---------------------------------------------------------------------------


def generate_conversation_title(text: str, max_length: int = 80) -> str:
    """Derive a conversation title from the first user message.

    Takes up to ``max_length`` characters of ``text``, truncating at the last
    word boundary (with an ellipsis) if truncation occurs.
    """
    text = text.strip()
    if len(text) <= max_length:
        return text

    truncated = text[:max_length]
    # Truncate at last word boundary
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated + "…"


# ---------------------------------------------------------------------------
# Conversation context for follow-up messages
# ---------------------------------------------------------------------------


async def build_conversation_context(
    conn: _Conn,
    *,
    conversation_id: UUID,
    max_pairs: int = 5,
) -> str:
    """Serialize the last N user/assistant exchange pairs as a text preamble.

    Fetches the most recent ``max_pairs * 2`` messages from the conversation
    and formats them as a readable prior-context block. Returns an empty
    string if there are no prior messages.

    Parameters
    ----------
    max_pairs:
        Maximum number of user/assistant exchange pairs to include.
        Defaults to 5 (last 10 messages).
    """
    rows = await conn.fetch(
        """
        SELECT role, content
        FROM shared.dashboard_messages
        WHERE conversation_id = $1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        conversation_id,
        max_pairs * 2,
    )

    if not rows:
        return ""

    # Reverse to chronological order
    messages = list(reversed(rows))

    lines: list[str] = ["--- Prior conversation context ---"]
    for msg in messages:
        role_label = "User" if msg["role"] == "user" else "Assistant"
        content = msg["content"].strip()
        lines.append(f"{role_label}: {content}")
    lines.append("--- End of prior context ---")

    return "\n".join(lines)


def format_context_preamble(context: str, new_message: str) -> str:
    """Prepend context block to a new message for follow-up ingestion.

    If context is empty, returns ``new_message`` unchanged.
    """
    if not context:
        return new_message
    return f"{context}\n\nNew message: {new_message}"
