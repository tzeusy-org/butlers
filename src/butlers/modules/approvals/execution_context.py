"""Ambient, server-derived context for an approved tool execution."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from butlers.core.goal_lineage import approving_lineage


@dataclass(frozen=True, slots=True)
class ApprovalExecutionContext:
    """Trusted approval lineage available only while the executor calls a tool."""

    action_id: uuid.UUID
    session_id: uuid.UUID | None
    actor: str
    tool_name: str
    tool_args_digest: str
    authorized_task: asyncio.Task[Any]


_current_execution: contextvars.ContextVar[ApprovalExecutionContext | None] = (
    contextvars.ContextVar("approval_execution_context", default=None)
)


def approval_tool_args_digest(tool_args: dict[str, Any]) -> str:
    """Return a stable, content-blind binding for approved arguments."""
    encoded = json.dumps(
        tool_args,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def get_approval_execution_context(
    *,
    tool_name: str | None = None,
    tool_args: dict[str, Any] | None = None,
) -> ApprovalExecutionContext | None:
    """Return authority only for the exact task, tool, and arguments approved.

    A broker-forwarded call (RFC 0038 D7) carries its approval in the verified
    goal lineage: an ``approval_ref`` whose assertion covers this exact tool
    and argument digest, in the task it was verified in, is the approval. It
    has no spawner session; ``session_id`` is ``None``.
    """
    lineage = approving_lineage(tool_name, tool_args)
    if lineage is not None and tool_name is not None and tool_args is not None:
        try:
            action_id = uuid.UUID(str(lineage.approval_ref))
        except ValueError:
            action_id = None
        task = asyncio.current_task()
        if action_id is not None and task is not None:
            return ApprovalExecutionContext(
                action_id=action_id,
                session_id=None,
                actor=f"goal:{lineage.goal_id}",
                tool_name=tool_name,
                tool_args_digest=approval_tool_args_digest(tool_args),
                authorized_task=task,
            )
    context = _current_execution.get()
    if context is None or tool_name is None or tool_args is None:
        return None
    try:
        current_task = asyncio.current_task()
    except RuntimeError:
        return None
    if current_task is not context.authorized_task:
        return None
    if tool_name != context.tool_name:
        return None
    if approval_tool_args_digest(tool_args) != context.tool_args_digest:
        return None
    return context


def set_approval_execution_context(
    context: ApprovalExecutionContext,
) -> contextvars.Token[ApprovalExecutionContext | None]:
    """Bind trusted approval lineage around one executor-owned tool call."""
    return _current_execution.set(context)


def reset_approval_execution_context(
    token: contextvars.Token[ApprovalExecutionContext | None],
) -> None:
    """Restore the previous approved-action lineage."""
    _current_execution.reset(token)


__all__ = [
    "ApprovalExecutionContext",
    "approval_tool_args_digest",
    "get_approval_execution_context",
    "reset_approval_execution_context",
    "set_approval_execution_context",
]
