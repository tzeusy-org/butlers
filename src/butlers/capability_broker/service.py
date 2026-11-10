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
from butlers.core.goal_labels import Label, output_label
from butlers.core.owner_policy import PolicyHold

from .actuation import rfc0028_actuation_risk
from .aliases import AliasResolver, Resolution, canonical_policy, resolve_args
from .assertions import GOAL_PRINCIPAL, LIFETIME_SECONDS, AssertionSigner
from .boundaries import BoundaryLedger, Extractor, content_bytes, extraction_key
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
from .ledger import UNPUBLISHED_CAPABILITY, BrokerLedger
from .policy import PER_EVENT, decide, listable
from .recipients import classify_recipients, communication_channel
from .typed import TypedRefused, parse_schema, typed_value_label, validate_value

logger = logging.getLogger(__name__)

# The goal tools' namespaces (RFC 0038 D8) are served by the runner, never by a domain.
RESERVED_DOMAINS = frozenset({"goal", "context"})

ActuationRiskMap = Callable[[Capability, dict[str, Any]], ActuationRisk]

# A domain action's terminal decision and the parked effect state it ends in.
# Joined replies one goal.inbox call delivers; the rest wait for the next call.
_INBOX_BATCH = 20
_PARKED_END = {"rejected": "rejected", "expired": "expired", "cancelled": "cancelled"}


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


def _holds(reader: Label, output: Label) -> bool:
    """Whether a result must be quarantined: it would newly expose its reader (M7).

    Categories alone never quarantine (they are not exposure); a reader that
    already holds every principal of the output gains nothing from a handle.
    """
    return output.exposed and not (reader.instr >= output.instr and reader.data >= output.data)


def _opt_str(value: Any) -> str | None:
    """A reference as text, or None when the row kind carries none (never the string "None")."""
    return None if value is None else str(value)


class Broker:
    def __init__(
        self,
        pool: Any,
        catalog: CatalogSource | None,
        domains: Domains | None,
        signer: AssertionSigner | None,
        *,
        actuation_risk: ActuationRiskMap = rfc0028_actuation_risk,
        clock: Callable[[], float] = time.time,
        transaction_timeout: float = 5,
        forward_timeout: float = 30,
        extractor: Extractor | None = None,
        quarantine_reads: bool = False,
        aliases: AliasResolver | None = None,
        alias_timeout: float = 2,
    ):
        self.pool, self.catalog, self.domains, self.signer = pool, catalog, domains, signer
        self.actuation_risk = actuation_risk
        # M7 (bu-4c7nvp.1.2), dark: the quarantined extraction model, and whether
        # a committed result that would newly expose its reader is held behind a
        # handle. Off, the first-switch read path is unchanged.
        self.extractor = extractor
        self.quarantine_reads = quarantine_reads
        # bu-dhloi1.30: the domains' alias resolution before owner law. Without
        # it a narrowing on an alias-resolved param cannot be trusted and selects.
        self.aliases = aliases
        self.alias_timeout = alias_timeout
        self.clock = clock
        self.transaction_timeout = transaction_timeout
        self.forward_timeout = forward_timeout

    def _require_available(self) -> None:
        if self.signer is None or self.catalog is None or self.domains is None:
            raise BrokerRefused(Reason.UNAVAILABLE)

    @property
    def _live_catalog(self) -> CatalogSource:
        if self.catalog is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        return self.catalog

    @property
    def _live_domains(self) -> Domains:
        if self.domains is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        return self.domains

    @property
    def _live_signer(self) -> AssertionSigner:
        if self.signer is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        return self.signer

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

    async def consume_inbox(self, goal_id: UUID, invocation_id: UUID, inbox_id: UUID) -> bool:
        """Deliver an inbox row to a running invocation, joining its label first (S1)."""
        async with self._transaction() as ledger:
            return await ledger.consume_inbox(goal_id, invocation_id, inbox_id)

    async def read_child_result(self, goal_id: UUID, lead_id: UUID, child_id: UUID) -> Label:
        """A returned child contaminates the lead that reads its result (D6, S1)."""
        async with self._transaction() as ledger:
            return await ledger.read_child_result(goal_id, lead_id, child_id)

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
            catalog = await self._live_catalog.load(ledger.connection)
            if catalog.digest != principal.catalog_digest:
                raise BrokerRefused(Reason.CATALOG)
            return tuple(
                capability
                for grant in principal.grants
                if (capability := catalog.get(grant.capability)) is not None
                and listable(capability, principal.depth)
                and capability.domain not in RESERVED_DOMAINS
                and not (capability.selection_bound - {k for k, _ in grant.resources})
            )

    async def call(
        self, goal_id: UUID, token: str, name: str, arguments: dict[str, Any]
    ) -> BrokerResult:
        principal: Principal | None = None
        try:
            self._require_available()
            try:
                # A detached snapshot: nothing can change the arguments after the digest.
                args = parse_json_object(canonical_bytes(arguments))
            except InvalidArguments:
                raise BrokerRefused(Reason.POLICY) from None
            async with self._transaction() as ledger:
                principal = await ledger.authenticate(goal_id, token)
                reserved, args = await self._authorize(ledger, principal, name, args)
            if isinstance(reserved, BrokerRefused):
                raise reserved
            if reserved.decision.disposition is Disposition.PARK:
                return await self._park(reserved, args)
            return await self._forward(reserved, args)
        except BrokerRefused as refusal:
            await self._record_refusal(principal, name, refusal)
            raise

    async def _record_refusal(
        self, principal: Principal | None, name: str, refusal: BrokerRefused
    ) -> None:
        """Durable D7 record in its own transaction: the call's own rolled back.

        A record that cannot be written never changes the refusal the session
        sees; the content-blind log line in ``_refused`` remains.
        """
        try:
            async with self._transaction() as ledger:
                recorded = await self._catalog_identity(ledger, name)
                await ledger.record_refusal(
                    principal, recorded, refusal.reason, refusal.effect_id, refusal.hold
                )
        except BrokerRefused:
            logger.warning("broker refusal record unavailable reason=%s", refusal.reason.value)

    async def _catalog_identity(self, ledger: BrokerLedger, name: str) -> str:
        """The requested name if the live catalog publishes it, else a fixed marker.

        A refused name is session-chosen text; an immutable record keeps only a
        published capability identity (D17: refusal records stay content-blind).
        """
        try:
            catalog = await self._live_catalog.load(ledger.connection)
        except Exception:
            return UNPUBLISHED_CAPABILITY
        return name if catalog.get(name) is not None else UNPUBLISHED_CAPABILITY

    async def _resolve(self, capability: Capability, args: dict[str, Any]) -> Resolution:
        return await resolve_args(self.aliases, capability, args, self.alias_timeout)

    async def _owner_law(
        self, ledger: BrokerLedger, capability: Capability, resolution: Resolution
    ) -> PolicyHold | None:
        async def canonical(policy):
            return await canonical_policy(self.aliases, capability, policy, self.alias_timeout)

        return await ledger.owner_law(
            capability, resolution.matched, resolved=resolution.resolved, canonical=canonical
        )

    async def _authorize(
        self, ledger: BrokerLedger, principal: Principal, name: str, args
    ) -> tuple[Reservation | BrokerRefused, dict[str, Any]]:
        """D7 steps 2-4 inside the call's transaction, in the RFC's order.

        Returns the reservation and the arguments the call runs with: past the
        grant scope check every declared alias of a pinned kind is its canonical
        id (``aliases``), and the digest the domain verifies is over exactly those
        arguments.
        """
        catalog = await self._live_catalog.load(ledger.connection)
        if catalog.digest != principal.catalog_digest:
            raise self._refused(principal, name, Reason.CATALOG)
        capability = catalog.get(name)
        grant = principal.grant_for(name)
        if (
            capability is None
            or grant is None
            or not listable(capability, principal.depth)
            # The goal tools' namespace (D8) is reserved: never a domain capability.
            or capability.domain in RESERVED_DOMAINS
        ):
            raise self._refused(principal, name, Reason.CAPABILITY)
        if not _in_scope(capability, grant, args):
            raise self._refused(principal, name, Reason.RESOURCE)
        resolution = await self._resolve(capability, args)
        args = resolution.args
        recipient = await classify_recipients(ledger.connection, capability, args)
        risk = (
            self.actuation_risk(capability, args)
            if capability.effect_class == "actuate"
            else "safe"
        )
        decision = decide(principal, capability, grant, recipient, risk)
        if decision.disposition is Disposition.REFUSE:
            raise self._refused(principal, name, Reason.POLICY)
        # Owner law (S1 order: after catalog/grant and exposure). It only
        # narrows: never refuses, always_ask parks the exact otherwise
        # permitted effect for per-event approval. A read has no effect to
        # park yet, so an always_ask term refuses it with its own reason.
        hold = await self._owner_law(ledger, capability, resolution)
        if hold is not None:
            if hold.constraint == "never":
                raise self._refused(principal, name, Reason.POLICY, hold)
            if capability.effect_class == "read":
                raise self._refused(principal, name, Reason.ASK_ON_READ, hold)
            decision = PER_EVENT
        if decision.disposition is Disposition.PARK and not capability.approvals_enabled:
            raise self._refused(principal, name, Reason.UNAVAILABLE, hold)
        reserved = await ledger.reserve(
            principal,
            capability,
            decision,
            arguments_digest(args),
            recipient,
            communication_channel(capability, args),
        )
        if hold is not None and isinstance(reserved, Reservation):
            await ledger.record_hold(
                principal, capability.identity, "parked", hold, effect_id=reserved.effect_id
            )
        return reserved, args

    @staticmethod
    def _refused(
        principal: Principal, name: str, reason: Reason, hold: PolicyHold | None = None
    ) -> BrokerRefused:
        # Content-blind audit line: identities and the closed reason only.
        logger.info(
            "broker refused call goal=%s invocation=%s capability=%s reason=%s",
            principal.goal_id,
            principal.invocation_id,
            name,
            reason.value,
        )
        return BrokerRefused(reason, hold=hold)

    def _assertion(self, reservation: Reservation, approval_ref: str | None) -> str:
        principal, capability = reservation.principal, reservation.capability
        return self._live_signer.sign(
            {
                "aud": capability.domain,
                "principal": GOAL_PRINCIPAL,
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
                receipt = await self._live_domains.park(reserved, args, assertion)
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
            await self._live_domains.cancel(
                reserved.capability.domain, reserved.principal.goal_id, [receipt.approval_ref]
            )
            raise BrokerRefused(Reason.GOAL)
        if receipt.approved_by_rule and reserved.effect_id is not None:
            return await self.execute_approved(
                reserved.principal.goal_id, reserved.effect_id, receipt.approval_ref, args
            )
        return BrokerResult("parked", reserved.effect_id, receipt.approval_ref)

    async def _dispatch(
        self, reserved: Reservation, args: dict[str, Any], assertion: str, key: str | None
    ) -> DomainReceipt:
        receipt = await self._live_domains.dispatch(reserved, args, assertion, idempotency_key=key)
        if not isinstance(receipt, DomainReceipt):
            raise TypeError("domain_receipt")
        return receipt

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
        try:
            async with asyncio.timeout(self.forward_timeout):
                receipt = await self._dispatch(reserved, args, assertion, key)
                # D12: one transport retry, only when the domain provably did not receive it.
                if receipt.status == "not_received":
                    receipt = await self._dispatch(reserved, args, assertion, key)
        except asyncio.CancelledError:
            raise  # The effect stays dispatched: never silently re-dispatched.
        except Exception:
            await self._settle(reserved, "unknown")
            return BrokerResult("unknown", reserved.effect_id)
        status = "failed" if receipt.status == "not_received" else receipt.status
        capability = reserved.capability
        # D6/S1: a committed result's label joins the reader before it is returned.
        output = (
            output_label(
                capability.output_trust, receipt.stamped_owner_untainted, capability.categories
            )
            if status == "committed"
            else None
        )
        writers = receipt.stamped_writers if capability.output_trust == "stamped" else ()
        handle = None
        if output is not None and self.quarantine_reads:
            handle = await self._settle_holding(reserved, status, receipt, output, writers, args)
        else:
            await self._settle(
                reserved,
                status,
                receipt_ref=receipt.receipt_ref,
                output=output,
                writers=writers,
                delivery=receipt.delivery,
                report=receipt.report,
                awaits_reply_seconds=args.get("awaits_reply_seconds"),
            )
        if approval_ref is not None:
            await self._write_back(reserved, approval_ref, status, receipt.receipt_ref)
        if status != "committed":
            return BrokerResult(status, reserved.effect_id)
        return BrokerResult(
            "returned" if reserved.effect_id is None else "committed",
            reserved.effect_id,
            approval_ref,
            receipt.value if handle is None else {"handle": str(handle)},
        )

    async def _settle_holding(
        self,
        reserved: Reservation,
        status: str,
        receipt: DomainReceipt,
        output: Label,
        writers: tuple[tuple[Any, Any], ...],
        args: dict[str, Any],
    ) -> UUID | None:
        """Settle a committed result, quarantining it if it would expose its reader.

        The full output label (with every stamped writer's) is computed under
        the goal lock. A result the reader already covers, or content that has
        no canonical form to hold, takes the ordinary join instead; either way
        the receipt and its label commit together.
        """
        try:
            raw = content_bytes(receipt.value)
        except InvalidArguments:
            raw = None
        async with self._transaction() as ledger:
            await ledger._lock_goal(reserved.principal.goal_id)
            label = output
            for pointer in writers:
                label = label.join(await ledger.writer_label(pointer))
            if raw is None or not _holds(reserved.principal.label, label):
                await ledger.settle(
                    reserved,
                    status,
                    receipt_ref=receipt.receipt_ref,
                    output=label,
                    delivery=receipt.delivery,
                    report=receipt.report,
                    awaits_reply_seconds=args.get("awaits_reply_seconds"),
                )
                return None
            await ledger.settle(
                reserved,
                status,
                receipt_ref=receipt.receipt_ref,
                delivery=receipt.delivery,
                report=receipt.report,
                awaits_reply_seconds=args.get("awaits_reply_seconds"),
            )
            return await BoundaryLedger(ledger).quarantine(
                reserved.principal, reserved.effect_id, label, raw
            )

    # -- M7 typed boundaries: goal tools (D8, bu-4c7nvp.1.2) --------------------

    async def read_handle(self, goal_id: UUID, token: str, handle_id: Any) -> object:
        """Free-text dereference: the reader joins the handle's label first."""
        self._require_available()
        async with self._transaction() as ledger:
            principal = await ledger.authenticate(goal_id, token)
            boundary = BoundaryLedger(ledger)
            content, _, label = await boundary.handle(principal, handle_id)
            exhausted = await boundary.charge(principal)
            if exhausted is None:
                await boundary.join(principal, label, "read")
        if exhausted is not None:
            raise exhausted
        return content

    async def extract(
        self, goal_id: UUID, token: str, handle_id: Any, schema_document: Any
    ) -> dict[str, Any]:
        """Quarantined extraction of one closed-shape value from a handle.

        The extraction model sees only the handle's content and the schema. Its
        output must match the schema exactly or the call refuses (and nothing is
        cached). The caller joins the handle's whole label before the extractor
        runs, so every outcome, refusals included, is accounted for; the typed
        value carries that label too (relaxation held on bu-4c7nvp.1.1).
        Refusals before any content is touched (schema, handle slots, a foreign
        handle, budget) join nothing.
        """
        self._require_available()
        extractor = self.extractor
        if extractor is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        try:
            schema = parse_schema(schema_document)
        except TypedRefused:
            raise BrokerRefused(Reason.POLICY) from None
        if schema.has_handles:
            # Content may not select which handle a reader dereferences next.
            raise BrokerRefused(Reason.POLICY)
        async with self._transaction() as ledger:
            principal = await ledger.authenticate(goal_id, token)
            boundary = BoundaryLedger(ledger)
            content, content_digest, label = await boundary.handle(principal, handle_id)
            key = extraction_key(content_digest, label, schema, extractor.identity)
            exhausted = await boundary.charge(principal)
            cached = await boundary.cached(key) if exhausted is None else None
            if exhausted is None:
                # Joined before the content reaches the extractor, with the charge:
                # whatever the outcome (a value, a refusal, a raise or a timeout)
                # depends on content the caller is now accountable for. A refusal
                # must not carry an adversary-chosen bit to an unexposed reader.
                seen = label if cached is None else label.join(cached[1])
                await boundary.join(principal, typed_value_label(seen), "read")
        if exhausted is not None:
            raise exhausted
        if cached is not None:
            return cached[0]
        try:
            async with asyncio.timeout(self.forward_timeout):
                candidate = await extractor.extract(content, schema.document)
            if type(candidate) is not dict:
                raise TypedRefused("value_outside_schema")
            value = validate_value(schema, parse_json_object(canonical_bytes(candidate)))
        except asyncio.CancelledError:
            raise
        except (TypedRefused, InvalidArguments, TypeError):
            raise BrokerRefused(Reason.POLICY) from None
        except Exception:
            raise BrokerRefused(Reason.UNAVAILABLE) from None
        async with self._transaction() as ledger:
            principal = await ledger.authenticate(goal_id, token)
            boundary = BoundaryLedger(ledger)
            _, _, label = await boundary.handle(principal, handle_id)
            stored, stored_label = await boundary.remember(
                key, schema, extractor.identity, value, label
            )
            await boundary.join(principal, typed_value_label(label.join(stored_label)), "read")
        return stored

    async def fix_child_result_schema(
        self, goal_id: UUID, child_id: UUID, schema_document: Any
    ) -> None:
        """Runner-private, at child launch: the brief's expected result schema (D9)."""
        try:
            schema = parse_schema(schema_document)
        except TypedRefused:
            raise BrokerRefused(Reason.POLICY) from None
        async with self._transaction() as ledger:
            await BoundaryLedger(ledger).fix_result_schema(goal_id, child_id, schema)

    async def goal_return(self, goal_id: UUID, token: str, value: Any) -> None:
        """``goal.return``: a child's result, only in its fixed closed schema, ends it."""
        self._require_available()
        try:
            snapshot = parse_json_object(canonical_bytes(value))
        except (InvalidArguments, TypeError):
            raise BrokerRefused(Reason.POLICY) from None
        async with self._transaction() as ledger:
            principal = await ledger.authenticate(goal_id, token)
            if principal.depth != 1:
                raise BrokerRefused(Reason.CAPABILITY)
            boundary = BoundaryLedger(ledger)
            schema = await boundary.result_schema(principal)
            try:
                validate_value(schema, snapshot)
            except TypedRefused:
                raise BrokerRefused(Reason.POLICY) from None
            if not await boundary.goal_handles(goal_id, schema.handle_refs(snapshot)):
                raise BrokerRefused(Reason.RESOURCE)
            exhausted = await boundary.charge(principal)
            if exhausted is None:
                await boundary.record_return(principal, snapshot)
        if exhausted is not None:
            raise exhausted

    async def goal_await(self, goal_id: UUID, token: str, child_id: Any) -> dict[str, Any]:
        """``goal.await``: the lead reads its child's status, result and effects.

        Whatever the lead learns about a child (its result, its effects' statuses,
        even that it is still running) comes from that child's behaviour, so the
        lead joins the child's label on every read (D6 cause ``child``).
        """
        self._require_available()
        async with self._transaction() as ledger:
            principal = await ledger.authenticate(goal_id, token)
            if principal.depth != 0:
                raise BrokerRefused(Reason.CAPABILITY)
            boundary = BoundaryLedger(ledger)
            status, end_reason, result, label, effects = await boundary.child_outcome(
                principal, child_id
            )
            exhausted = await boundary.charge(principal)
            if exhausted is None:
                await boundary.join(principal, label, "child")
        if exhausted is not None:
            raise exhausted
        return {
            "status": status,
            # Closed and content-blind: ``launch_failed`` says the child never ran.
            "end_reason": end_reason,
            "result": result if status == "returned" else None,
            "effects": effects,
        }

    async def goal_inbox(self, goal_id: UUID, token: str) -> dict[str, Any]:
        """``goal.inbox``: the lead takes its goal's joined replies, between tool calls.

        Each row is consumed once, by the calling lead, in the same transaction
        that joins its label into that lead (S1 cause ``inbox``), so the lead is
        exposed before it learns of the reply. The token fences the epoch: a
        superseded or Stopped lead fails authentication and consumes nothing.
        """
        self._require_available()
        async with self._transaction() as ledger:
            principal = await ledger.authenticate(goal_id, token)
            if principal.depth != 0:
                raise BrokerRefused(Reason.CAPABILITY)
            exhausted = await BoundaryLedger(ledger).charge(principal)
            delivered: list[dict[str, Any]] = []
            if exhausted is None:
                for row in await ledger.pending_inbox(goal_id, _INBOX_BATCH):
                    if await ledger.consume_inbox(goal_id, principal.invocation_id, row["id"]):
                        item = {
                            "inbox_id": str(row["id"]),
                            "source_ref": row["source_ref"],
                            "kind": row["input_kind"],
                            "reply_effect_id": _opt_str(row["reply_effect_id"]),
                            "received_at": row["created_at"].isoformat(),
                        }
                        if row["input_kind"] == "condition_fire":
                            # Identifiers only: the lead reads what woke it, never a body.
                            item["envelope_id"] = str(row["envelope_id"])
                            item["event_id"] = str(row["event_id"])
                        delivered.append(item)
        if exhausted is not None:
            raise exhausted
        return {"replies": delivered}

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
        digest = arguments_digest(args)
        # D11: the approval commits on its own (parked -> approved) before dispatch.
        async with self._transaction() as ledger:
            effect = await ledger.effect_capability(goal_id, effect_id)
            refused = await ledger.approve_parked(goal_id, effect_id, approval_ref, digest)
        if refused is not None:
            if effect is not None:
                await self._live_domains.cancel(effect.split("__", 1)[0], goal_id, [approval_ref])
            raise refused
        held: tuple[Reservation, PolicyHold] | None = None
        try:
            async with self._transaction() as ledger:
                catalog = await self._live_catalog.load(ledger.connection)
                reserved = await ledger.begin_approved(
                    goal_id,
                    effect_id,
                    approval_ref,
                    digest,
                    catalog.get(effect) if effect else None,
                    catalog.digest,
                )
                if isinstance(reserved, Reservation):
                    # A never term committed after the park holds the effect
                    # at this fence; the owner's approval already answers
                    # always_ask. The dispatch transition rolls back.
                    # The stored or edited call is never rewritten after its
                    # approval: an alias in it resolves for matching only, and
                    # a param that needed pinning to its canonical id cannot
                    # release a term. A match-only kind needs no pin, so a merge
                    # since the park is matched on its survivor here.
                    resolution = await self._resolve(reserved.capability, args)
                    fixed = Resolution(
                        args,
                        frozenset(
                            name
                            for name in resolution.resolved
                            if resolution.args[name] == args[name]
                        ),
                        resolution.matched,
                    )
                    hold = await self._owner_law(ledger, reserved.capability, fixed)
                    if hold is not None and hold.constraint == "never":
                        held = (reserved, hold)
                        raise BrokerRefused(Reason.POLICY, effect_id=effect_id, hold=hold)
        except BrokerRefused:
            if held is None:
                raise
            async with self._transaction() as ledger:
                await ledger.record_hold(
                    held[0].principal,
                    held[0].capability.identity,
                    "refused",
                    held[1],
                    effect_id=effect_id,
                )
            raise
        if isinstance(reserved, BrokerRefused):
            # The goal ended: the effect is cancelled here, so cancel its approval too.
            if effect is not None:
                await self._live_domains.cancel(effect.split("__", 1)[0], goal_id, [approval_ref])
            raise reserved
        return await self._forward(reserved, args, approval_ref)

    async def _write_back(
        self, reserved: Reservation, approval_ref: str, status: str, receipt_ref: str | None
    ) -> None:
        """Best effort: the effect ledger is authoritative; the domain row mirrors it."""
        try:
            await self._live_domains.record_outcome(
                reserved.capability.domain,
                reserved.principal.goal_id,
                approval_ref,
                status,
                receipt_ref,
            )
        except Exception:
            logger.warning(
                "broker write-back failed goal=%s effect=%s",
                reserved.principal.goal_id,
                reserved.effect_id,
            )

    async def sync_parked(self, goal_id: UUID) -> int:
        """Mirror the domain's terminal decisions onto parked effects (D11).

        A parked effect whose domain action was rejected, expired or cancelled
        leaves ``parked``; an approval is never inferred here (it runs through
        ``execute_approved``). Returns the number of effects ended.
        """
        self._require_available()
        async with self._transaction() as ledger:
            parked = await ledger.parked_refs(goal_id)
        ended = 0
        for domain in sorted({capability.split("__", 1)[0] for _, capability, _ in parked}):
            mine = [
                (e, ref) for e, capability, ref in parked if capability.startswith(domain + "__")
            ]
            states = await self._live_domains.action_states(
                domain, goal_id, [ref for _, ref in mine]
            )
            for effect_id, ref in mine:
                status = _PARKED_END.get(states.get(ref, ""))
                if status is None:
                    continue
                async with self._transaction() as ledger:
                    ended += await ledger.end_parked(goal_id, effect_id, status)
        return ended

    async def cancel_parked(self, goal_id: UUID) -> int:
        """After Stop commits: cancel parked effects here and in their domains."""
        self._require_available()
        async with self._transaction() as ledger:
            cancelled = await ledger.cancel_parked(goal_id)
        for domain in sorted({capability.split("__", 1)[0] for capability, _ in cancelled}):
            refs = [ref for capability, ref in cancelled if capability.startswith(domain + "__")]
            await self._live_domains.cancel(domain, goal_id, refs)
        return len(cancelled)
