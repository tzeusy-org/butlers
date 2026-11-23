"""D16 model selection: role ladders and the escalation step.

A goal invocation resolves from ``public.model_catalog`` with its role
(``lead``, ``child``, ``monitor``) as the resolution key and exactly its ladder
tier: the canonical fallthrough is refused (``CallSpec.exact_tier``), so an
escalation can never land at or below the tier it escalated from. The ladders
are starting defaults; M5 replay evaluation sets the real ones (owner
decision 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from butlers.core.model_routing import resolve_model_with_effective_tier

from .contracts import Role


@dataclass(frozen=True, slots=True)
class ModelLadders:
    """Each role's finite ladder, lowest tier first."""

    lead: tuple[str, ...] = ("workhorse", "reasoning")
    monitor: tuple[str, ...] = ("cheap", "workhorse", "reasoning")
    child_tiers: tuple[str, ...] = ("cheap", "workhorse")

    def __post_init__(self) -> None:
        for ladder in (self.lead, self.monitor, self.child_tiers):
            if not ladder or len(set(ladder)) != len(ladder):
                raise ValueError("a ladder is a finite, non-empty set of distinct tiers")

    def ladder(self, role: Role) -> tuple[str, ...]:
        if role == "lead":
            return self.lead
        if role == "monitor":
            return self.monitor
        return ()  # A child never escalates (D9: only a lead holds goal.escalate).

    def first(self, role: Role) -> str:
        if role == "child":
            raise ValueError("a child's tier comes from its brief")
        return self.ladder(role)[0]

    def above(self, role: Role, tier: str) -> tuple[str, ...]:
        """The ladder's tiers strictly above ``tier``, nearest first."""
        ladder = self.ladder(role)
        if tier not in ladder:
            return ()
        return ladder[ladder.index(tier) + 1 :]


async def qualifies(pool: Any, role: Role, tier: str) -> bool:
    """Whether ``tier`` itself resolves for ``role`` (no fallthrough)."""
    resolved = await resolve_model_with_effective_tier(
        pool, role, tier, allow_tier_fallthrough=False
    )
    return resolved is not None and resolved[-1] == tier


async def next_tier(pool: Any, ladders: ModelLadders, role: Role, tier: str) -> str | None:
    """The nearest ladder tier above ``tier`` that qualifies, or ``None`` (D16)."""
    for candidate in ladders.above(role, tier):
        if await qualifies(pool, role, candidate):
            return candidate
    return None
