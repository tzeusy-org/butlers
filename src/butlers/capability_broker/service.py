"""The broker: one authorizing transaction, one forward, one settlement.

``Broker`` is constructed only by trusted control-plane wiring and fails closed
when its signing key, catalog or domain seam is missing, or when the ledger is
unreachable. Nothing here is mounted on a live listener; see ``server``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from butlers.core.capability_json import (
    InvalidArguments,
    arguments_digest,
    canonical_bytes,
    parse_json_object,
)
from butlers.core.goal_admission import Grant

from .assertions import LIFETIME_SECONDS, AssertionSigner
from .catalog import CatalogSource
from .contracts import (
    ActuationRisk,
    BrokerRefused,
    BrokerResult,
    Capability,
    Disposition,
    Principal,
    Reason,
    Reservation,
)
from .domains import DomainReceipt, Domains, ParkReceipt
from .ledger import BrokerLedger
from .policy import decide, listable
from .recipients import classify_recipients

logger = logging.getLogger(__name__)

ActuationRiskMap = Callable[[Capability, dict[str, Any]], ActuationRisk]


def _unknown_risk(capability: Capability, args: dict[str, Any]) -> ActuationRisk:
    return "unknown"


def _in_scope(capability: Capability, grant: Grant, args: dict[str, Any]) -> bool:
    """Every restricted resource argument names only granted identifiers.

    A selection-bound parameter without a grant restriction has an empty
    scope (D7): the capability is neither listed nor callable.
    """
    restrictions = dict(grant.resources)
    if not set(restrictions) <= set(capability.resource_params):
        return False
    if capability.selection_bound - set(restrictions):
        return False
    for name, permitted in restrictions.items():
        value = args.get(name)
        values = [value] if type(value) is str else value
        if type(values) is not list or not values:
            return False
        if any(type(item) is not str or item not in permitted for item in values):
            return False
    return True


class Broker:
    def __init__(
        self,
        pool: Any,
        catalog: CatalogSource | None,
        domains: Domains | None,
        signer: AssertionSigner | None,
        *,
        actuation_risk: ActuationRiskMap = _unknown_risk,
        clock: Callable[[], float] = time.time,
        transaction_timeout: float = 5,
        forward_timeout: float = 30,
    ):
        self.pool, self.catalog, self.domains, self.signer = pool, catalog, domains, signer
        self.actuation_risk = actuation_risk
        self.clock = clock
        self.transaction_timeout = transaction_timeout
        self.forward_timeout = forward_timeout

    def _require_available(self) -> None:
        if self.signer is None or self.catalog is None or self.domains is None:
            raise BrokerRefused(Reason.UNAVAILABLE)

    @asynccontextmanager
    async def _transaction(self):
        """Acquire-through-commit under one deadline; store errors become UNAVAILABLE."""
        try:
            async with asyncio.timeout(self.transaction_timeout):
                async with self.pool.acquire() as connection, connection.transaction():
                    yield BrokerLedger(connection)
        except BrokerRefused:
            raise
        except Exception:
            raise BrokerRefused(Reason.UNAVAILABLE) from None

    # -- runner-private token lifecycle -----------------------------------------

    async def issue_invocation_token(self, goal_id: UUID, invocation_id: UUID) -> str:
        self._require_available()
        async with self._transaction() as ledger:
            return await ledger.issue_token(goal_id, invocation_id)

    async def revoke_invocation_token(self, goal_id: UUID, invocation_id: UUID) -> None:
        async with self._transaction() as ledger:
            await ledger.revoke_token(goal_id, invocation_id)

    # -- session surface --------------------------------------------------------

    async def authenticate(self, goal_id: UUID, token: str) -> Principal:
        self._require_available()
        async with self._transaction() as ledger:
            return await ledger.authenticate(goal_id, token)

    async def list_tools(self, goal_id: UUID, token: str) -> tuple[Capability, ...]:
        """Grant-scoped discovery; every call is still checked on its own."""
        self._require_available()
        async with self._transaction() as ledger:
            principal = await ledger.authenticate(goal_id, token)
            catalog = await self.catalog.load(ledger.connection)
            if catalog.digest != principal.catalog_digest:
                raise BrokerRefused(Reason.CATALOG)
            return tuple(
                capability
                for grant in principal.grants
                if (capability := catalog.get(grant.capability)) is not None
                and listable(capability, principal.depth)
                and not (capability.selection_bound - {k for k, _ in grant.resources})
            )

    async def call(
        self, goal_id: UUID, token: str, name: str, arguments: dict[str, Any]
    ) -> BrokerResult:
        self._require_available()
        try:
            # A detached snapshot: nothing can change the arguments after the digest.
            args = parse_json_object(canonical_bytes(arguments))
        except InvalidArguments:
            raise BrokerRefused(Reason.POLICY) from None
        args_digest = arguments_digest(args)
        async with self._transaction() as ledger:
            principal = await ledger.authenticate(goal_id, token)
            reserved = await self._authorize(ledger, principal, name, args, args_digest)
        if isinstance(reserved, BrokerRefused):
            raise reserved
        if reserved.decision.disposition is Disposition.PARK:
            return await self._park(reserved, args)
        return await self._forward(reserved, args)

    async def _authorize(
        self, ledger: BrokerLedger, principal: Principal, name: str, args, args_digest: str
    ) -> Reservation | BrokerRefused:
        """D7 steps 2-4 inside the call's transaction, in the RFC's order."""
        catalog = await self.catalog.load(ledger.connection)
        if catalog.digest != principal.catalog_digest:
            raise self._refused(principal, name, Reason.CATALOG)
        capability = catalog.get(name)
        grant = principal.grant_for(name)
        if capability is None or grant is None or not listable(capability, principal.depth):
            raise self._refused(principal, name, Reason.CAPABILITY)
        if not _in_scope(capability, grant, args):
            raise self._refused(principal, name, Reason.RESOURCE)
        recipient = await classify_recipients(ledger.connection, capability, args)
        risk = (
            self.actuation_risk(capability, args)
            if capability.effect_class == "actuate"
            else "safe"
        )
        decision = decide(principal, capability, grant, recipient, risk)
        if decision.disposition is Disposition.REFUSE:
            raise self._refused(principal, name, Reason.POLICY)
        if decision.disposition is Disposition.PARK and not capability.approvals_enabled:
            raise self._refused(principal, name, Reason.UNAVAILABLE)
        return await ledger.reserve(principal, capability, decision, args_digest, recipient)

    @staticmethod
    def _refused(principal: Principal, name: str, reason: Reason) -> BrokerRefused:
        # Content-blind audit line: identities and the closed reason only.
        logger.info(
            "broker refused call goal=%s invocation=%s capability=%s reason=%s",
            principal.goal_id,
            principal.invocation_id,
            name,
            reason.value,
        )
        return BrokerRefused(reason)

    def _assertion(self, reservation: Reservation, approval_ref: str | None) -> str:
        principal, capability = reservation.principal, reservation.capability
        return self.signer.sign(
            {
                "aud": capability.domain,
                "goal_id": str(principal.goal_id),
                "invocation_id": str(principal.invocation_id),
                "depth": principal.depth,
                "origin": principal.origin,
                "exposed": principal.exposed,
                "capability": capability.name,
                "args_digest": reservation.args_digest,
                "call_id": str(reservation.call_id),
                "effect_id": str(reservation.effect_id) if reservation.effect_id else None,
                "approval_ref": approval_ref,
                "exp": int(self.clock()) + LIFETIME_SECONDS,
            }
        )

    async def _settle(self, reservation: Reservation, status: str, **kwargs) -> None:
        async with self._transaction() as ledger:
            await ledger.settle(reservation, status, **kwargs)

    async def _park(self, reserved: Reservation, args: dict[str, Any]) -> BrokerResult:
        try:
            assertion = self._assertion(reserved, None)
            async with asyncio.timeout(self.forward_timeout):
                receipt = await self.domains.park(reserved, args, assertion)
            if not isinstance(receipt, ParkReceipt):
                raise TypeError("park_receipt")
        except asyncio.CancelledError:
            raise
        except Exception:
            # The approvals module may or may not hold the action now.
            await self._settle(reserved, "unknown")
            return BrokerResult("unknown", reserved.effect_id)
        async with self._transaction() as ledger:
            parked = await ledger.mark_parked(reserved, receipt.approval_ref)
        if not parked:
            # Stop cancelled the effect while the park was in flight.
            await self.domains.cancel(
                reserved.capability.domain, reserved.principal.goal_id, [receipt.approval_ref]
            )
            raise BrokerRefused(Reason.GOAL)
        if receipt.approved_by_rule:
            return await self.execute_approved(
                reserved.principal.goal_id, reserved.effect_id, receipt.approval_ref, args
            )
        return BrokerResult("parked", reserved.effect_id, receipt.approval_ref)

    async def _forward(
        self, reserved: Reservation, args: dict[str, Any], approval_ref: str | None = None
    ) -> BrokerResult:
        key = (
            str(reserved.effect_id)
            if reserved.capability.idempotency == "key_required" and reserved.effect_id
            else None
        )
        try:
            assertion = self._assertion(reserved, approval_ref)
        except Exception:
            await self._settle(reserved, "failed")
            raise BrokerRefused(Reason.UNAVAILABLE) from None
        receipt = None
        try:
            async with asyncio.timeout(self.forward_timeout):
                # D12: one transport retry, only when the domain provably did not receive it.
                for _ in range(2):
                    receipt = await self.domains.dispatch(
                        reserved, args, assertion, idempotency_key=key
                    )
                    if not isinstance(receipt, DomainReceipt):
                        raise TypeError("domain_receipt")
                    if receipt.status != "not_received":
                        break
        except asyncio.CancelledError:
            raise  # The effect stays dispatched: never silently re-dispatched.
        except Exception:
            await self._settle(reserved, "unknown")
            return BrokerResult("unknown", reserved.effect_id)
        status = "failed" if receipt.status == "not_received" else receipt.status
        trust = reserved.capability.output_trust
        expose = status == "committed" and (
            trust == "untrusted" or (trust == "stamped" and not receipt.stamped_owner_untainted)
        )
        await self._settle(reserved, status, receipt_ref=receipt.receipt_ref, expose=expose)
        if status != "committed":
            return BrokerResult(status, reserved.effect_id)
        return BrokerResult(
            "returned" if reserved.effect_id is None else "committed",
            reserved.effect_id,
            approval_ref,
            receipt.value,
        )

    # -- trusted owner-decision path (D13) --------------------------------------

    async def execute_approved(
        self, goal_id: UUID, effect_id: UUID, approval_ref: str, arguments: dict[str, Any]
    ) -> BrokerResult:
        """Run the stored call, or the owner's edit of it, never a re-issued one.

        Called by control-plane wiring of the owning approvals decision (and
        by a park-time standing-rule match); never by a session.
        """
        self._require_available()
        args = parse_json_object(canonical_bytes(arguments))
        async with self._transaction() as ledger:
            effect = await ledger.effect_capability(goal_id, effect_id)
            catalog = await self.catalog.load(ledger.connection)
            reserved = await ledger.begin_approved(
                goal_id,
                effect_id,
                approval_ref,
                arguments_digest(args),
                catalog.get(effect) if effect else None,
                catalog.digest,
            )
        if isinstance(reserved, BrokerRefused):
            # The goal ended: the effect is cancelled here, so cancel its approval too.
            await self.domains.cancel(effect.split("__", 1)[0], goal_id, [approval_ref])
            raise reserved
        return await self._forward(reserved, args, approval_ref)

    async def cancel_parked(self, goal_id: UUID) -> int:
        """After Stop commits: cancel parked effects here and in their domains."""
        self._require_available()
        async with self._transaction() as ledger:
            cancelled = await ledger.cancel_parked(goal_id)
        for domain in sorted({capability.split("__", 1)[0] for capability, _ in cancelled}):
            refs = [ref for capability, ref in cancelled if capability.startswith(domain + "__")]
            await self.domains.cancel(domain, goal_id, refs)
        return len(cancelled)
