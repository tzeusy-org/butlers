"""Owning-domain boundaries; no unsigned transport or automatic retry fallback.

Production adapters must use the final wrapped catalog and the owning approvals
module. This module does not import another worktree's unpublished integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

import asyncpg

from butlers.core.goal_admission import Grant

from .contracts import Capability, PolicyEvidence, Principal, Reservation


@dataclass(frozen=True, slots=True)
class Catalog:
    digest: str
    capabilities: tuple[Capability, ...]

    def __post_init__(self):
        names = [c.identity for c in self.capabilities]
        if not self.digest or len(set(names)) != len(names):
            raise ValueError("invalid_catalog")


class Authorities(Protocol):
    """Local durable reads on the *same locked connection*, never network I/O.

    The production catalog adapter is not yet supplied. Missing selection or
    policy authority is an error, never an unbound scope or owner default.
    """

    async def catalog(self, connection: asyncpg.Connection, principal: Principal) -> Catalog: ...

    async def selection(
        self,
        connection: asyncpg.Connection,
        principal: Principal,
        capability: Capability,
        grant: Grant,
    ) -> bool:
        """Prove active owner admission association and exact scope, locking revoke."""
        ...

    async def policy(
        self,
        connection: asyncpg.Connection,
        principal: Principal,
        capability: Capability,
        args: dict,
    ) -> PolicyEvidence:
        """Bind every party to exact raw args/capability on this locked connection.

        recipient_binding requires the unique live, unmerged, undeleted owner
        and native selectors/default/inherited recipients, not parsed message
        claims. Unknown/ambiguous targets stay None. Read real owner-created
        rules and risk here. Missing implementation refuses; no owner default.
        """
        ...


@dataclass(frozen=True, slots=True)
class DomainReceipt:
    status: Literal["committed", "failed", "unknown", "not_received"]
    # `not_received` requires affirmative transport evidence; timeout is unknown.
    receipt_ref: str | None = None
    value: object = field(default=None, repr=False)
    # Stamped outputs need an owning-store parser; absence always exposes.
    stamped_owner_untainted: bool = False

    def __post_init__(self):
        if self.status not in {"committed", "failed", "unknown", "not_received"}:
            raise ValueError("closed_receipt_required")
        if type(self.stamped_owner_untainted) is not bool:
            raise ValueError("closed_stamp_required")
        if self.receipt_ref is not None and (
            type(self.receipt_ref) is not str or not self.receipt_ref
        ):
            raise ValueError("closed_receipt_reference_required")


@dataclass(frozen=True, slots=True)
class ParkReceipt:
    approval_ref: str

    def __post_init__(self):
        if type(self.approval_ref) is not str or not self.approval_ref:
            raise ValueError("approval_reference_required")


class Domains(Protocol):
    async def dispatch(
        self, reservation: Reservation, args: dict, assertion: str, *, idempotency_key: str | None
    ) -> DomainReceipt:
        """Forward once to exact owner. No internal retry on ambiguous delivery."""
        ...

    async def park(self, reservation: Reservation, args: dict, assertion: str) -> ParkReceipt:
        """Persist exact args/lineage in owning approvals and apply allowed rules there.

        The reservation's allow_standing_rule is a restriction, never authority
        to autoapprove a park-only or always-per-event grant.
        """
        ...
