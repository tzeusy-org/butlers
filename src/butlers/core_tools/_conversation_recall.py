"""conversation_recall core tool — cross-butler dashboard chat recall.

Exposes two always-on MCP tools backed by the ``search_vector``/trigram
indexes added in ``core_221_dashboard_messages_search_index``:

- ``conversation_recall`` — full-text search over every butler's dashboard
  messages, answering "what did I ask you last week about X" regardless of
  which butler originally handled that turn.
- ``conversation_thread_read`` — reads a window of messages around a recalled
  hit for context, ahead of the full ``/chat`` page (bu-0ynlk.11).

Always registered on every butler — recall is owner-scoped (it reads across
every ``butler_name``, not just the caller's own), so it cannot be gated to a
subset of butlers the way module-specific tools are.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import Field

from butlers.core_tools._base import ToolContext

logger = logging.getLogger(__name__)

_MAX_RECALL_LIMIT = 100


def _parse_iso_datetime(value: str | None, *, field_name: str) -> datetime | None:
    """Parse an optional ISO-8601 timestamp, raising a clear error on bad input.

    Returns ``None`` unchanged so an omitted bound imposes no filter. Never
    silently drops a malformed bound as if it were absent — that would make
    the tool answer a broader query than the caller asked for.
    """
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp, got {value!r}") from exc


def _parse_uuid(value: str, *, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{field_name} must be a UUID, got {value!r}") from exc


def register_conversation_recall_tool(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register the ``conversation_recall``/``conversation_thread_read`` MCP tools.

    Group: infra, always-on.
    """
    pool = ctx.pool

    @_core_tool("infra")
    async def conversation_recall(
        query: Annotated[
            str,
            Field(
                description=(
                    "Free-text search term (max 512 chars), e.g. 'landlord' or "
                    "'renewal deadline'. Matched with PostgreSQL full-text search "
                    "against dashboard chat message content."
                )
            ),
        ],
        since: Annotated[
            str | None,
            Field(description="ISO-8601 timestamp — only messages at or after this time."),
        ] = None,
        until: Annotated[
            str | None,
            Field(description="ISO-8601 timestamp — only messages strictly before this time."),
        ] = None,
        limit: Annotated[
            int, Field(ge=1, le=_MAX_RECALL_LIMIT, description="Max excerpts to return.")
        ] = 20,
        channel: Annotated[
            str | None,
            Field(
                description=(
                    "Restrict to one source channel (e.g. 'dashboard', 'telegram'). "
                    "Omit to search every channel."
                )
            ),
        ] = None,
        butler: Annotated[
            str | None,
            Field(
                description=(
                    "Restrict to one butler's conversations. Omit to search across "
                    "every butler the owner has ever talked to — recall is scoped to "
                    "the owner, not to whichever butler is calling this tool."
                )
            ),
        ] = None,
    ) -> list[dict[str, Any]]:
        """Recall what the owner said or was told, across every dashboard chat.

        Answers "what did I ask you last week about X" even when X was asked
        to a different butler than the one calling this tool. Ranked by text
        relevance then recency. Returns ``[]`` when ``query`` is blank or
        nothing matches — this tool never infers or fabricates a recollection
        when there is no actual match; an empty result means exactly that.

        Each returned excerpt: ``conversation_id``, ``message_id``, ``role``,
        ``created_at`` (ISO-8601), ``butler_name``, ``snippet``, ``session_id``
        (nullable), ``deep_link`` (a dashboard path to open for more context).
        """
        if pool is None:
            raise RuntimeError("Database pool is not available")
        if not query or not query.strip():
            return []

        since_dt = _parse_iso_datetime(since, field_name="since")
        until_dt = _parse_iso_datetime(until, field_name="until")

        from butlers.api.conversations import message_search

        result = await message_search(
            pool,
            query=query,
            since=since_dt,
            until=until_dt,
            channel=channel,
            butler=butler,
            limit=limit,
        )

        return [
            {
                "conversation_id": str(item["conversation_id"]),
                "message_id": str(item["message_id"]),
                "role": item["role"],
                "created_at": item["created_at"].isoformat(),
                "butler_name": item["butler_name"],
                "snippet": item["snippet"],
                "session_id": str(item["session_id"]) if item["session_id"] else None,
                "deep_link": item["deep_link"],
            }
            for item in result["items"]
        ]

    @_core_tool("infra")
    async def conversation_thread_read(
        conversation_id: Annotated[
            str,
            Field(description="Dashboard conversation UUID to read (from conversation_recall)."),
        ],
        around_message_id: Annotated[
            str | None,
            Field(
                description=(
                    "Message UUID to center the window on (from conversation_recall). "
                    "Omit to read the most recent messages in the conversation instead."
                )
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Read a window of messages around a conversation_recall hit for context.

        Returns up to 11 messages (5 before, the anchor, 5 after) ordered
        oldest-first. Returns an empty ``messages`` list when the conversation
        has no messages, or ``around_message_id`` does not belong to it.
        """
        if pool is None:
            raise RuntimeError("Database pool is not available")

        conv_uuid = _parse_uuid(conversation_id, field_name="conversation_id")
        anchor_uuid = (
            _parse_uuid(around_message_id, field_name="around_message_id")
            if around_message_id is not None
            else None
        )

        from butlers.api.conversations import message_thread_window

        messages = await message_thread_window(pool, conv_uuid, around_message_id=anchor_uuid)

        return {
            "conversation_id": str(conv_uuid),
            "messages": [
                {
                    "message_id": str(m["id"]),
                    "role": m["role"],
                    "content": m["content"],
                    "created_at": m["created_at"].isoformat(),
                    "session_id": str(m["session_id"]) if m["session_id"] else None,
                }
                for m in messages
            ],
        }
