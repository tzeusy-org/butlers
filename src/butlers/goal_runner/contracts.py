"""Value types at the runner's boundaries (RFC 0038 D3, D9, D14, D15).

The session host is a seam: the runner hands it one finite launch and learns
how the invocation ended. Nothing here authenticates a caller; the broker's
token does that for every tool call the session makes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from butlers.core.goal_admission import Grant
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


class HostAtCapacity(NotInvoked, RuntimeError):
    """The session host had no room for the launch, so nothing of it ran.

    A transient refusal, not a failure of the goal: the runner ends the
    invocation ``launch_failed``, returns its invocation to the budget and leaves
    a lead's goal for the supervise sweep to launch again (bu-dhloi1.265).
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
class PlanSummary:
    """The goal's latest plan version (RFC 0038 S2): goal-owned references, never text.

    Each reference is the canonical ``<kind>:<id>`` the ledger validated when ``goal.plan``
    recorded it (``effect:``, ``inbox:``, ``child:`` with a UUID; ``plan:`` with a version).
    """

    version: int
    premise: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LedgerSummary:
    """What a replacement or escalated lead starts with (D14, D16): identifiers only."""

    effects: tuple[tuple[str, str, str], ...] = ()
    """(effect_id, capability, status) of every effect of the goal."""
    children: tuple[tuple[str, str], ...] = ()
    """(child invocation_id, status) of every child of the goal."""
    inbox: tuple[str, ...] = ()
    """Ids of the goal's oldest unconsumed inbox replies (the ledger lists a bounded few)."""
    inbox_pending: int = 0
    """How many inbox replies are unconsumed; the lead reads them with ``goal.inbox``."""
    plan: PlanSummary | None = None
    """The goal's latest recorded plan version, if a lead recorded one with ``goal.plan``."""


@dataclass(frozen=True, slots=True)
class InvocationShare:
    """The launching invocation's own limits, as its ledger row holds them (D10)."""

    max_calls: int | None
    used_calls: int
    max_turns: int
    max_tokens: int
    seconds_left: int | None


@dataclass(frozen=True, slots=True)
class GoalBudgetView:
    """What the goal has left, read at launch (D10)."""

    max_calls: int
    used_calls: int
    max_tokens: int
    used_tokens: int
    seconds_left: int


@dataclass(frozen=True, slots=True)
class LaunchFacts:
    """Server-held facts a packet states: the invocation's grant, share and budget."""

    grants: tuple[Grant, ...]
    share: InvocationShare
    budget: GoalBudgetView
    result_schema: dict[str, Any] | None = None
    """A child's fixed result schema (D9); ``None`` for a lead."""


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
    facts: LaunchFacts | None = None


class PromptComposer(Protocol):
    """D15 starting context: ``PacketComposer`` builds the four-section packet."""

    def compose(self, context: LaunchContext) -> str: ...
