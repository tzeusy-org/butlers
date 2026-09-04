"""conversation_reply core tool — dashboard chat confirm-loop replies.

Exposes a single MCP tool, ``conversation_reply``, that a routed butler
session calls to persist its interpretation/confirmation text into the
dashboard conversation it was routed from (``public.dashboard_messages``).
Always registered on every butler — any butler can be the classification or
pinned-target destination of a dashboard conversation, so this cannot be
gated to a subset of butlers the way module-specific tools are.

The dashboard SSE poller (``_stream_conversation_response`` /
``message_find_reply_since`` in ``butlers.api.routers.conversations`` /
``butlers.api.conversations``) watches for this message rather than raw
session completion, so the owner sees the butler's deliberate reply instead
of its transcript.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from pydantic import Field

from butlers.core.telemetry import tool_span
from butlers.core.tool_call_capture import (
    get_current_runtime_session_id,
    get_current_runtime_session_routing_context,
    peek_runtime_session_tool_calls,
)
from butlers.core_tools._base import ToolContext

logger = logging.getLogger(__name__)


def _best_effort_request_id() -> UUID | None:
    """Recover request_id from ambient routing context, for lineage only.

    Reply correlation does not depend on this — the dashboard poller matches
    on conversation_id and message timestamp — so a missing or malformed
    value here degrades to ``None`` rather than failing the tool call.
    """
    routing_context = get_current_runtime_session_routing_context()
    if not routing_context:
        return None
    raw = routing_context.get("request_id")
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


def _best_effort_session_id() -> UUID | None:
    """Recover the ambient runtime session id, for the message's own lineage.

    Absent when this butler's runtime never bound one (e.g. a legacy/mocked
    call path) -- degrades to ``None`` rather than failing the tool call, so
    the dashboard SSE poller's request_id-based backfill can fill the gap.
    """
    raw = get_current_runtime_session_id()
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


def _best_effort_tool_calls(session_id: UUID | None) -> list[dict[str, Any]] | None:
    """Snapshot this turn's executed tool calls so far, without draining them.

    Non-destructive: the Spawner still consumes the full session buffer at
    session finish to persist the session-level record. ``None`` (not an
    empty list) when there is no session context, mirroring the nullable
    ``dashboard_messages.tool_calls`` column semantics.

    ``tool_call_capture``'s records use the capture-side shape (``name``,
    ``input``, ...); the dashboard chat widget's ``MessageToolCall`` type
    (frontend/src/api/types.ts) expects ``{id, name, arguments, result}``
    (the ``ToolCallDetails`` component reads ``.arguments`` directly) — this
    reshapes rather than passing the raw capture records through.
    """
    if session_id is None:
        return None
    calls = peek_runtime_session_tool_calls(str(session_id))
    if not calls:
        return None
    return [
        {
            "id": None,
            "name": call.get("name", ""),
            "arguments": call.get("input"),
            "result": call.get("result"),
        }
        for call in calls
    ]


def register_conversation_reply_tool(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register the ``conversation_reply`` MCP tool (group: infra, always-on)."""
    pool = ctx.pool
    butler_name = ctx.butler_name

    @_core_tool("infra")
    @tool_span("conversation_reply", butler_name=butler_name)
    async def conversation_reply(
        conversation_id: Annotated[
            str,
            Field(
                description=(
                    "The dashboard conversation UUID to reply into (from the "
                    "REQUEST CONTEXT you were routed with)."
                )
            ),
        ],
        message: Annotated[
            str,
            Field(
                description=(
                    "The confirmation/interpretation text to show the owner, e.g. "
                    "'Recorded: Alice child-of Bob — correct?'."
                )
            ),
        ],
        sources: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Answer-lane only: what you consulted to produce this reply "
                    "(tool names, record identifiers, etc.), e.g. "
                    "['finance.get_budget', 'transaction#a1b2c3']. Omit entirely for "
                    "a confirm-loop/action-proposal/bug-report reply. For an "
                    "answer-lane reply, pass a NON-EMPTY list of NON-BLANK names — "
                    "an empty/blank citation is rejected, since an unsourced "
                    "'answer' is indistinguishable from a fabricated one; give an "
                    "honest decline instead."
                )
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Reply into a dashboard conversation for owner confirmation.

        Call this once you have interpreted and applied the owner's dashboard
        statement (or filed a bug report) to tell them what you did and ask
        them to confirm. The dashboard chat widget's SSE stream delivers this
        text to the owner — it does NOT see your raw session transcript, so a
        session that never calls this tool leaves the owner's chat waiting
        until it times out.
        """
        try:
            conv_uuid = UUID(str(conversation_id))
        except (ValueError, AttributeError, TypeError):
            return {
                "status": "error",
                "error": f"conversation_id {conversation_id!r} is not a valid UUID",
            }

        if sources is not None and (
            not sources
            or any(not isinstance(source, str) or not source.strip() for source in sources)
        ):
            return {
                "status": "error",
                "error": (
                    "sources must contain only non-empty names for an answer-lane "
                    "reply — name what you consulted, or omit `sources` entirely "
                    "and give an honest decline instead of fabricating a citation."
                ),
            }

        if pool is None:
            return {"status": "error", "error": "Database pool is not available"}

        request_id = _best_effort_request_id()
        session_id = _best_effort_session_id()
        tool_calls = _best_effort_tool_calls(session_id)

        from butlers.api.conversations import conversation_reply_create

        try:
            msg = await conversation_reply_create(
                pool,
                conv_uuid,
                message=message,
                request_id=request_id,
                sources=sources,
                session_id=session_id,
                tool_calls=tool_calls,
            )
        except Exception as exc:
            logger.exception(
                "conversation_reply: failed to persist reply for conversation %s", conv_uuid
            )
            return {"status": "error", "error": f"Failed to persist reply: {exc}"}

        if msg is None:
            return {
                "status": "error",
                "error": f"Conversation {conv_uuid} does not exist",
            }

        return {"status": "ok", "message_id": str(msg["id"]), "conversation_id": str(conv_uuid)}
