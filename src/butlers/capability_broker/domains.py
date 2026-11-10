"""The broker's seam to owning domains: forward, park and cancel.

``Domains`` is a trusted constructor input. Parking always goes through the
owning domain's approvals module; ``ApprovalsParking`` is the in-process
adapter over that module's goal-park API. Dispatch goes through
``forwarder.HttpForwarder``, which carries ``X-Butlers-Assertion`` to the
domain's verification middleware (``verification``); nothing constructs either
until the M6 switch.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from butlers.modules.approvals.goal_park import (
    GoalLineage,
    cancel_goal_effects,
    goal_action_states,
    park_goal_effect,
    record_goal_outcome,
)

from .contracts import BrokerRefused, Reason, Reservation


@dataclass(frozen=True, slots=True)
class Delivery:
    """Where a committed communication landed, as its domain reports it (S2.1, S2.2).

    ``ref`` is the provider's delivery reference, the key a reply to it binds to.
    ``prompt_expires_at`` is set only for a message that asks for an answer and accepts one until
    then; the broker honours it only for an owner delivery. The recipient and channel are not the
    domain's to report: the broker recorded them at reserve. Both are written once, at commit.
    """

    ref: str
    prompt_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    """What a domain says about where a committed message landed (a claim, never an identity).

    The broker turns a report into a ``Delivery`` itself at settle: the reply ref is derived from
    this scope and the owner entity and channel the broker recorded at reserve, so a domain cannot
    steer which identity a reply binds to. ``prompt_expires_at`` is a ceiling the broker clamps to
    the call's own ``awaits_reply_seconds``.
    """

    transport: str
    endpoint: str
    conversation: str
    locator: str
    prompt_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DomainReceipt:
    # ``not_received`` needs affirmative transport evidence; a timeout is unknown.
    status: Literal["committed", "failed", "unknown", "not_received"]
    receipt_ref: str | None = None
    value: object = field(default=None, repr=False)
    # The owning store's complete stamp account of stamped output (D6, S1):
    # every returned item is owner-authoritative and untainted, except those
    # whose writer pointer (taint_goal_id, taint_invocation_id) is listed in
    # ``stamped_writers``. Without the account the output is of unknown
    # provenance and sensitivity.
    stamped_owner_untainted: bool = False
    stamped_writers: tuple[tuple[Any, Any], ...] = ()
    # A communication's delivery binding (owner deliveries only are recorded as a reply target).
    delivery: Delivery | None = None
    # A domain's untrusted account of the delivery; the broker resolves it to a ``Delivery``.
    report: DeliveryReport | None = None


@dataclass(frozen=True, slots=True)
class ParkReceipt:
    approval_ref: str
    # A standing rule the owner created matched at park time (D13).
    approved_by_rule: bool = False


class Domains(Protocol):
    async def dispatch(
        self,
        reservation: Reservation,
        args: dict[str, Any],
        assertion: str,
        *,
        idempotency_key: str | None,
    ) -> DomainReceipt:
        """Forward once. Never retries an ambiguous delivery itself."""
        ...

    async def park(
        self, reservation: Reservation, args: dict[str, Any], assertion: str
    ) -> ParkReceipt: ...

    async def cancel(self, domain: str, goal_id: UUID, approval_refs: list[str]) -> None: ...

    async def record_outcome(
        self,
        domain: str,
        goal_id: UUID,
        approval_ref: str,
        status: str,
        receipt_ref: str | None,
    ) -> None:
        """Write a settled approved effect back to its domain action."""
        ...

    async def action_states(
        self, domain: str, goal_id: UUID, approval_refs: list[str]
    ) -> dict[str, str]:
        """The domain's current decision state for each named approval."""
        ...


def _action_ids(approval_refs: list[str]) -> list[UUID]:
    action_ids = []
    for ref in approval_refs:
        try:
            action_ids.append(UUID(ref))
        except (TypeError, ValueError):
            continue  # Not an action the approvals module issued.
    return action_ids


class ApprovalsParking:
    """Park and cancel through each owning domain's approvals module.

    ``pools`` maps a domain name to a pool on that domain's schema. Dispatch
    is delegated to ``forwarder``; without one, dispatch refuses.
    """

    def __init__(self, pools: Mapping[str, Any], forwarder: Any = None):
        self._pools = dict(pools)
        self._forwarder = forwarder

    def _pool(self, domain: str):
        pool = self._pools.get(domain)
        if pool is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        return pool

    async def dispatch(self, reservation, args, assertion, *, idempotency_key):
        if self._forwarder is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        return await self._forwarder.dispatch(
            reservation, args, assertion, idempotency_key=idempotency_key
        )

    async def park(self, reservation, args, assertion) -> ParkReceipt:
        principal, capability = reservation.principal, reservation.capability
        outcome = await park_goal_effect(
            self._pool(capability.domain),
            lineage=GoalLineage(
                goal_id=principal.goal_id,
                invocation_id=principal.invocation_id,
                depth=principal.depth,
                exposed=principal.exposed,
                origin=principal.origin,
                effect_id=reservation.effect_id,
            ),
            tool_name=capability.name,
            tool_args=args,
            allow_standing_rule=reservation.decision.allow_standing_rule,
            recipient_is_owner=reservation.recipient == "owner",
            recipient_args=capability.recipient_params.argument_names,
            safety_args=capability.safety_args,
            origin_butler=capability.domain,
        )
        return ParkReceipt(str(outcome.action_id), outcome.rule_id is not None)

    async def cancel(self, domain: str, goal_id: UUID, approval_refs: list[str]) -> None:
        # Exactly the refs whose effects the broker cancelled: an action whose
        # effect was dispatched or settled is never ended here (D14).
        await cancel_goal_effects(self._pool(domain), goal_id, _action_ids(approval_refs))

    async def record_outcome(self, domain, goal_id, approval_ref, status, receipt_ref) -> None:
        for action_id in _action_ids([approval_ref]):
            await record_goal_outcome(
                self._pool(domain), goal_id, action_id, status=status, receipt_ref=receipt_ref
            )

    async def action_states(self, domain, goal_id, approval_refs) -> dict[str, str]:
        states = await goal_action_states(self._pool(domain), goal_id, _action_ids(approval_refs))
        return {str(action_id): status for action_id, status in states.items()}
