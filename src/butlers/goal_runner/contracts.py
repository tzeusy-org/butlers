"""Value types at the runner's boundaries (RFC 0038 D3, D9, D14, D15).

The session host is a seam: the runner hands it one finite launch and learns
how the invocation ended. Nothing here authenticates a caller; the broker's
token does that for every tool call the session makes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from butlers.core.model_call import NotInvoked

Role = Literal["lead", "child", "monitor"]


class GoalEnded(NotInvoked, RuntimeError):
    """The goal is no longer running, so no invocation may start.

    Raised only by ledger opens, before any model runs, so the kernel records
    it as a non-model outcome and never relaunches it.
    """


class InvocationBudgetExhausted(NotInvoked, RuntimeError):
    """D10: the goal's invocation budget has no room for another launch.

    Raised when a ledger open reserves an invocation, before any model runs, so
    the kernel records it as a non-model outcome. ``delegate`` handles its own.
    """


@dataclass(frozen=True, slots=True)
class Launch:
    """One finite invocation for the session host (RFC 0038 D3)."""

    goal_id: UUID
    invocation_id: UUID
    token: str = field(repr=False)
    runtime: str = ""
    model: str = ""
    prompt: str = field(default="", repr=False)
    timeout_s: float = 300


@dataclass(frozen=True, slots=True)
class HostOutcome:
    """How a launched invocation ended.

    ``failure`` is the runtime failure the existing taxonomy classifies (D14);
    ``None`` means the process exited cleanly. ``usage`` is the runtime's
    final usage report, when it gave one.
    """

    failure: BaseException | None = None
    usage: dict[str, Any] | None = None


class SessionHost(Protocol):
    """The launch protocol to the session host (bu-kiuh8v owns the real one)."""

    async def launch(self, launch: Launch) -> HostOutcome:
        """Run the invocation to its end; a failure is reported, never raised."""
        ...

    async def terminate(self, invocation_id: UUID) -> None:
        """Terminate the invocation's process domain (D14). Idempotent."""
        ...


@dataclass(frozen=True, slots=True)
class Brief:
    """A child's D9 brief: lead-authored, fenced into the child's prompt only.

    The ledger keeps the result schema (sw_058), never the brief text.
    """

    objective: str
    success_conditions: tuple[str, ...] = ()
    context_refs: tuple[str, ...] = ()
    guidance_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LedgerSummary:
    """What a replacement or escalated lead starts with (D14, D16): identifiers only."""

    effects: tuple[tuple[str, str, str], ...] = ()
    """(effect_id, capability, status) of every effect of the goal."""
    children: tuple[tuple[str, str], ...] = ()
    """(child invocation_id, status) of every child of the goal."""


@dataclass(frozen=True, slots=True)
class LaunchContext:
    """Everything a prompt composer may use for one launch."""

    goal_id: UUID
    invocation_id: UUID
    role: Role
    depth: int
    epoch: int
    origin: str
    exposed: bool
    brief: Brief | None = None
    ledger: LedgerSummary | None = None


class PromptComposer(Protocol):
    """D15 starting context. The four-section packet is its own bead."""

    def compose(self, context: LaunchContext) -> str: ...


class MinimalComposer:
    """Identifiers and the child's fenced brief; no recall, no situation frame."""

    def compose(self, context: LaunchContext) -> str:
        lines = [
            f"goal_id: {context.goal_id}",
            f"invocation_id: {context.invocation_id}",
            f"role: {context.role}",
            f"origin: {context.origin}",
        ]
        if context.brief is not None:
            brief = context.brief
            lines.append("<brief>")
            lines.append(f"objective: {brief.objective}")
            lines.extend(f"success: {item}" for item in brief.success_conditions)
            lines.extend(f"context_ref: {item}" for item in brief.context_refs)
            lines.extend(f"guidance_ref: {item}" for item in brief.guidance_refs)
            lines.append("</brief>")
        if context.ledger is not None:
            lines.append("<ledger>")
            lines.extend(f"effect: {e} {c} {s}" for e, c, s in context.ledger.effects)
            lines.extend(f"child: {c} {s}" for c, s in context.ledger.children)
            lines.append("</ledger>")
        return "\n".join(lines)
