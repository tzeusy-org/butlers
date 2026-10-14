"""Dashboard system questions use Concierge read tools, never staffer sessions."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from butlers.core.permissions import CROSS_BUTLER_PERMISSION, check_permission

logger = logging.getLogger(__name__)

_READ_TOOLS = frozenset(
    "dashboard_read_" + name
    for name in (
        "fleet_status",
        "butler_detail",
        "sessions_recent",
        "session_detail",
        "sessions_aggregate",
        "sessions_trigger_breakdown",
        "fleet_errors_recent",
        "fleet_search",
        "timeline_recent",
        "butler_activity",
        "spend_summary",
        "spend_daily",
        "spend_top_sessions",
        "spend_breakdown_by_butler",
        "spend_breakdown_by_model",
        "insight_delivery_state",
    )
)
_SOURCE_REFS = frozenset(
    {"concierge.v_fleet_sessions", "concierge.v_fleet_spend", "public.insight_candidates"}
)
_READ_TIMEOUT_S = 15.0


class _PendingPublication(Exception):
    """Roll back this publication attempt while durable sessions remain live."""

    def __init__(self, *, cancelled: bool):
        self.cancelled = cancelled
        super().__init__()


def _valid_source(source: Any) -> bool:
    if (
        not isinstance(source, dict)
        or not isinstance(source.get("ref"), str)
        or source["ref"] not in _SOURCE_REFS
    ):
        return False
    expected_kind = "table" if source["ref"] == "public.insight_candidates" else "view"
    if source.get("kind") != expected_kind or not isinstance(source.get("as_of"), str):
        return False
    try:
        return datetime.fromisoformat(source["as_of"]).utcoffset() is not None
    except ValueError:
        return False


async def read_system_answer(
    *,
    pool: Any,
    route: Any,
    source_butler: str,
    routing_ctx: dict[str, Any],
    question: str,
    tool_name: str | None,
    tool_args: dict[str, Any] | None,
    decline: Any,
    validate_read_arguments: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    """Fetch one allowlisted read and return evidence to the classification LLM."""
    from butlers.core.dashboard_turns import dispatch_status
    from butlers.core_tools._switchboard import _dashboard_turn_id_from_context

    async def unavailable(reason: str) -> dict[str, Any]:
        return await decline(
            routing_ctx=routing_ctx,
            question_summary=question,
            scope_checked=[
                "system",
                "concierge",
                *([tool_name] if tool_name in _READ_TOOLS else []),
            ],
            reason=reason,
        )

    if tool_name not in _READ_TOOLS or (tool_args is not None and not isinstance(tool_args, dict)):
        return await unavailable("No supported Concierge read tool and arguments were selected.")
    try:
        validate_read_arguments(tool_name, tool_args or {})
    except (TypeError, ValueError, KeyError):
        return await unavailable("The selected Concierge read arguments are invalid or incomplete.")
    except Exception:
        logger.warning("Concierge dashboard read declarations unavailable")
        return await unavailable("Concierge read declarations unavailable; no answer was inferred.")

    try:
        permission = await check_permission(pool, source_butler, CROSS_BUTLER_PERMISSION)
        if not permission.allowed:
            return await unavailable("Switchboard is not authorized to read Concierge tools.")
        message_id = _dashboard_turn_id_from_context(
            routing_ctx.get("source_metadata") or {}, routing_ctx.get("dashboard_context")
        )
        if message_id is not None:
            control = await dispatch_status(pool, message_id=message_id)
            if control.cancellation_requested:
                return {"status": "cancelled", "answered": False, "cancelled": True}
            if control.outcome != "active":
                return {"status": "refused", "answered": False, "reason": "dashboard_turn_terminal"}
        from butlers.tools.switchboard.routing.route import _call_butler_tool

        async def call_read(endpoint: str, name: str, args: dict[str, Any]) -> Any:
            # route() adds its trace carrier to arbitrary tool kwargs. Read tools
            # have strict provider signatures; keep that carrier in the router's
            # span instead of forwarding it as an unsupported MCP argument.
            read_args = {key: value for key, value in args.items() if key != "trace_context"}
            return await _call_butler_tool(endpoint, name, read_args)

        async with asyncio.timeout(_READ_TIMEOUT_S):
            result = await route(
                pool,
                target_butler="concierge",
                tool_name=tool_name,
                args=tool_args or {},
                source_butler=source_butler,
                call_fn=call_read,
            )
        data = (
            result.get("result") if isinstance(result, dict) and not result.get("error") else None
        )
        if not isinstance(data, dict) or data.get("error") or not _valid_source(data.get("source")):
            return await unavailable(
                "Concierge did not return available, source-attributed evidence."
            )
        return {"status": "ok", "answered": True, "tool": tool_name, "data": data}
    except Exception:
        # MCP/DB errors can contain secrets or user input; use a fixed category.
        logger.warning("Concierge dashboard read unavailable")
        return await unavailable("Concierge read unavailable or timed out; no answer was inferred.")


def extract_system_answer(calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Recognize successful evidence only, including provider-qualified tool names."""
    for call in calls:
        if not re.search(r"(?:^|[^a-z0-9])answer_question$", str(call.get("name", "")), re.I):
            continue
        result = call.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                continue
        if not isinstance(result, dict):
            continue
        if result.get("status") == "cancelled":
            args = call.get("input") or call.get("args") or call.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            if isinstance(args, dict) and args.get("scope") == "system":
                return {"status": "cancelled"}
        if result.get("status") != "ok":
            continue
        data = result.get("data")
        if (
            result.get("answered") is True
            and isinstance(result.get("tool"), str)
            and result["tool"] in _READ_TOOLS
            and isinstance(data, dict)
            and _valid_source(data.get("source"))
        ):
            return result
    return None


async def persist_system_answer(
    pool: Any,
    *,
    dashboard_context: dict[str, Any] | None,
    message_id: UUID | None,
    request_id: UUID,
    answer: str,
    evidence: dict[str, Any],
) -> str:
    """Publish the existing question session's answer and settle Stop atomically."""
    from butlers.api.conversations import conversation_reply_create
    from butlers.core.citations import normalize_citations
    from butlers.core.dashboard_turns import dispatch_status, mark_terminal

    if not answer.strip():
        # Evidence is real but the LLM produced no answer. Expose the actual read,
        # rather than fabricate prose or silently drop the owner's reply.
        answer = "Concierge returned: " + json.dumps(evidence["data"], ensure_ascii=False)
    try:
        async with pool.acquire() as conn, conn.transaction():
            if message_id is not None:
                # Serialize repeated publishers before checking terminal state.
                # Stop keeps its own control-row authority; no read model changes.
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    "dashboard-system-answer:" + str(message_id),
                )
                before = await dispatch_status(conn, message_id=message_id)
                if before.cancellation_requested:
                    return "cancelled"
                if before.outcome != "active":
                    return "refused"
                # This function locks the control row until the reply commits.
                control = await mark_terminal(conn, message_id=message_id, state="completed")
                if control.outcome == "pending":
                    # Session completion may have failed after the classifier
                    # returned. Undo our pending completed intent: it must not
                    # settle that live session later without a committed reply.
                    raise _PendingPublication(cancelled=control.cancellation_requested)
                if control.cancellation_requested:
                    return "cancelled"
                if control.outcome != "finished":
                    raise RuntimeError("Dashboard terminal transition unavailable")
            source = evidence["data"]["source"]
            normalization = normalize_citations([f"{source['ref']} (as of {source['as_of']})"])
            reply = await conversation_reply_create(
                conn,
                UUID(dashboard_context["conversation_id"]),
                message=answer,
                request_id=request_id,
                sources=normalization.legacy_sources,
                citations=normalization.citations,
            )
            if reply is None:
                raise RuntimeError("Dashboard conversation unavailable")
        return "completed"
    except _PendingPublication as pending:
        return "cancelled" if pending.cancelled else "pending"
    except Exception:
        logger.warning("Concierge dashboard answer persistence unavailable")
        return "error"
