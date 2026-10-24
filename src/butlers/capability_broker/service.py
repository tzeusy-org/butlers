"""Ordered broker calls: durable admission, one forward, durable settlement.

Authorities and owning-domain approval bridges remain explicit constructor
inputs. PublishedAuthorities reads the delivered current catalog and supports
no-party reads; it does not fabricate missing selection or effect policy.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from uuid import UUID

import asyncpg

from butlers.core.capability_json import arguments_digest, canonical_bytes, parse_arguments

from .assertions import AssertionSigner
from .contracts import BrokerRefused, BrokerResult, Capability, Disposition, Reason, Reservation
from .domains import Authorities, DomainReceipt, Domains, ParkReceipt
from .ledger import BrokerLedger
from .owning import PrivateApprovals


class Broker:
    def __init__(
        self,
        pool: asyncpg.Pool,
        authorities: Authorities,
        domains: Domains,
        signer: AssertionSigner | None,
        *,
        approvals: PrivateApprovals | None = None,
        clock: Callable[[], float] = time.time,
        transaction_timeout: float = 5,
        forward_timeout: float = 30,
    ):
        if not 0 < transaction_timeout <= 5 or not 0 < forward_timeout <= 60:
            raise ValueError("finite_broker_bounds_required")
        self.pool, self.authorities, self.domains, self.signer = pool, authorities, domains, signer
        if approvals is not None and type(approvals) is not PrivateApprovals:
            raise ValueError("private_approvals_required")
        self.approvals = approvals
        self.clock = clock
        self.transaction_timeout, self.forward_timeout = transaction_timeout, forward_timeout

    def require_available(self):
        if self.signer is None or self.authorities is None or self.domains is None:
            raise BrokerRefused(Reason.UNAVAILABLE)

    async def issue_invocation_token(self, goal_id: UUID, invocation_id: UUID) -> str:
        """Private runner integration; never an MCP/HTTP token-issuance route.

        The runner must first commit its authenticated invocation/goal mapping.
        Only the resulting hash is persisted, and plaintext leaves this method
        only after confirmed commit. No automatic retry follows commit loss.
        """
        self.require_available()
        try:
            async with asyncio.timeout(self.transaction_timeout):
                async with self.pool.acquire() as connection, connection.transaction():
                    token = await BrokerLedger(connection).issue_token(goal_id, invocation_id)
            return token
        except BrokerRefused:
            raise
        except Exception:
            raise BrokerRefused(Reason.UNAVAILABLE) from None

    async def revoke_invocation_token(self, goal_id: UUID, invocation_id: UUID) -> None:
        """Private runner completion hook; failed persistence remains a refusal."""
        try:
            async with asyncio.timeout(self.transaction_timeout):
                async with self.pool.acquire() as connection, connection.transaction():
                    await BrokerLedger(connection).revoke_token(goal_id, invocation_id)
        except BrokerRefused:
            raise
        except Exception:
            raise BrokerRefused(Reason.UNAVAILABLE) from None

    async def authenticate(self, goal_id: UUID, token: str) -> None:
        self.require_available()
        async with asyncio.timeout(self.transaction_timeout):
            async with self.pool.acquire() as connection, connection.transaction():
                await BrokerLedger(connection).authenticate(goal_id, token)

    async def list_tools(self, goal_id: UUID, token: str) -> tuple[Capability, ...]:
        self.require_available()
        async with asyncio.timeout(self.transaction_timeout):
            async with self.pool.acquire() as connection, connection.transaction():
                ledger = BrokerLedger(connection)
                principal = await ledger.authenticate(goal_id, token)
                return await ledger.listing(principal, self.authorities)

    async def _settle(self, reservation: Reservation, status: str, **kwargs) -> str:
        async with asyncio.timeout(self.transaction_timeout):
            async with self.pool.acquire() as connection, connection.transaction():
                return await BrokerLedger(connection).settle(reservation, status, **kwargs)

    async def call(self, goal_id: UUID, token: str, name: str, arguments: dict) -> BrokerResult:
        self.require_available()
        # A detached snapshot prevents another coroutine changing args after authorization.
        args = parse_arguments(canonical_bytes(arguments))
        digest = arguments_digest(args)
        async with asyncio.timeout(self.transaction_timeout):
            async with self.pool.acquire() as connection, connection.transaction():
                ledger = BrokerLedger(connection)
                principal = await ledger.authenticate(goal_id, token)
                reserved = await ledger.reserve(principal, name, args, digest, self.authorities)
                if isinstance(reserved, Reservation):
                    # Commit the dispatch intent before any request can reach a domain.
                    if reserved.decision.disposition is Disposition.DISPATCH:
                        await ledger.dispatching(reserved)
        if isinstance(reserved, BrokerRefused):
            raise reserved
        # Commit uncertainty above exits with an exception and can never forward.
        try:
            assertion = self.signer.sign(reserved, issued_at=int(self.clock()))
        except Exception:
            await self._settle(reserved, "failed")
            raise BrokerRefused(Reason.UNAVAILABLE) from None
        try:
            async with asyncio.timeout(self.forward_timeout):
                if reserved.decision.disposition is Disposition.PARK:
                    approvals = self.approvals if self.approvals is not None else self.domains
                    if self.approvals is not None:
                        from .handoff import sign_handoff

                        assertion = sign_handoff(
                            self.signer, reserved, purpose="PARK", issued_at=int(self.clock())
                        )
                    parked = await approvals.park(reserved, args, assertion)
                    if not isinstance(parked, ParkReceipt):
                        raise BrokerRefused(Reason.UNAVAILABLE)
                else:
                    receipt = await self.domains.dispatch(
                        reserved,
                        args,
                        assertion,
                        idempotency_key=(
                            str(reserved.effect_id)
                            if reserved.capability.idempotency == "key_required"
                            and reserved.effect_id
                            else None
                        ),
                    )
                    if not isinstance(receipt, DomainReceipt):
                        raise BrokerRefused(Reason.UNAVAILABLE)
        except asyncio.CancelledError:
            # Intent remains dispatched/requested, preventing blind duplicate dispatch.
            # Cancellation is never translated into a successful response or retry.
            raise
        except Exception:
            await self._settle(reserved, "unknown")
            return BrokerResult("unknown", reserved.effect_id)
        if reserved.decision.disposition is Disposition.PARK:
            # A settlement/commit failure is not a transport failure. Never
            # issue a contradictory second write after an uncertain commit.
            settled = await self._settle(reserved, "parked", reference=parked.approval_ref)
            if settled != "parked":
                return BrokerResult("unknown", reserved.effect_id)
            return BrokerResult("parked", reserved.effect_id, parked.approval_ref)
        expose = reserved.capability.output_trust == "untrusted" or (
            reserved.capability.output_trust == "stamped" and not receipt.stamped_owner_untainted
        )
        status = "refused" if receipt.status == "not_received" else receipt.status
        # Failure to persist exposure or settlement returns no downstream content.
        await self._settle(reserved, status, reference=receipt.receipt_ref, expose=expose)
        return BrokerResult(
            "returned"
            if reserved.effect_id is None and receipt.status == "committed"
            else receipt.status,
            reserved.effect_id,
            value=receipt.value,
        )

    async def reconcile_parked(self, goal_id: UUID, effect_id: UUID):
        """Observe a fixed owner's decision before the separate approved replay.

        LOOKUP can settle a negative observation only. It never grants execution
        permission, even when the response reports approved. No pool connection
        remains held during the owning call, and no failed call is retried here.
        """
        from butlers.core.goal_stop import GoalStop

        from .approved import parked_identity, settle_negative

        self.require_available()
        if self.approvals is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        async with GoalStop(self.pool, timeout=self.transaction_timeout)._transaction() as conn:
            identity, status = await parked_identity(conn, goal_id, effect_id)
        if status in {"rejected", "expired"}:
            return status
        async with asyncio.timeout(self.forward_timeout):
            observed = await self.approvals.lookup(identity)
        if observed["outcome"] in {"rejected", "expired"}:
            async with GoalStop(self.pool, timeout=self.transaction_timeout)._transaction() as conn:
                await settle_negative(conn, identity, observed["receipt"])
            return observed["outcome"]
        if observed["outcome"] == "approved":
            return await self.replay_approved(goal_id, effect_id)
        return None

    async def replay_approved(self, goal_id: UUID, effect_id: UUID | None = None):
        """Private stored-call replay; locators never provide approval authority.

        Every network phase releases the central pool. Only an authenticated
        owning snapshot can reach prepare, and only a committed owning witness
        plus central attachment can reach one EXECUTE. No execution retry.
        """
        from butlers.core.goal_stop import GoalStop
        from butlers.modules.approvals.goal_dispatch import call_wire, decode_call

        from .approved import attach_owning_witness, parked_reservation

        self.require_available()
        if self.approvals is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        async with GoalStop(self.pool, timeout=self.transaction_timeout)._transaction() as conn:
            original = await parked_reservation(conn, goal_id, effect_id, self.authorities)

        async def operation(reservation, purpose, **kwargs):
            async with asyncio.timeout(self.forward_timeout):
                return await self.approvals.operation(
                    reservation,
                    signer=self.signer,
                    purpose=purpose,
                    issued_at=int(self.clock()),
                    **kwargs,
                )

        snapshot = await operation(original, "SNAPSHOT")
        if snapshot["outcome"] != "approved":
            raise BrokerRefused(Reason.UNAVAILABLE)
        call = decode_call(snapshot["receipt"]["call"])
        if (
            call.lineage.goal_id != goal_id
            or call.approval_ref != effect_id
            or call.lineage.goal_args_digest != original.args_digest
        ):
            raise BrokerRefused(Reason.AUTHENTICATION)
        prepared = await self.prepare_approved_dispatch(call)
        kwargs = {
            "approval_ref": prepared.approval_ref,
            "original_args_digest": prepared.original_args_digest,
            "snapshot_version": snapshot["receipt"]["version"],
        }
        try:
            try:
                claim = await operation(prepared.reservation, "CLAIM", **kwargs)
            except Exception:
                # A separate authenticated read may prove this exact committed
                # witness; it never consumes or supplies a second execution.
                claim = await operation(prepared.reservation, "WITNESS", **kwargs)
            witness = claim["receipt"]
            if (
                claim["outcome"] != "claimed"
                or witness["intent_id"] != str(prepared.reservation.call_id)
                or witness["snapshot_digest"] != arguments_digest(call_wire(call))
            ):
                raise BrokerRefused(Reason.AUTHENTICATION)
            async with GoalStop(self.pool, timeout=self.transaction_timeout)._transaction() as conn:
                await attach_owning_witness(conn, prepared, witness)
            # Exactly one attempt. An unknown response is never replayed.
            await operation(prepared.reservation, "EXECUTE", **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:
            return BrokerResult("unknown", effect_id)
        return await self.settle_approved_dispatch(prepared, DomainReceipt("unknown"))

    async def prepare_approved_dispatch(self, stored_call):
        """Private central half, consumed by fixed authenticated replay orchestration.

        This method remains absent from model-facing listeners. A supplied DTO
        alone is not approval authority; the caller must first read the actual
        owning snapshot. Return only after confirmed central commit, never after
        commit loss. The owning witness and one-time consume are separate steps.
        """
        from butlers.core.goal_stop import GoalStop

        from .approved import prepare_approved

        self.require_available()
        try:
            # The existing central transaction verifies sw047 AND all sw048
            # runtime RLS/ACL policies and carries one acquire-through-commit
            # deadline. Reuse it without another pool/transaction/lock order.
            async with GoalStop(self.pool, timeout=self.transaction_timeout)._transaction() as conn:
                prepared = await prepare_approved(conn, stored_call, self.authorities)
            return prepared
        except BrokerRefused:
            raise
        except Exception:
            raise BrokerRefused(Reason.UNAVAILABLE) from None

    async def settle_approved_dispatch(self, prepared, receipt: DomainReceipt) -> BrokerResult:
        """Private central settlement; no authenticated owning result is invented.

        Never return downstream value until original effect/intent/exposure and
        the actual transaction commit are confirmed. An uncertain commit is a
        refusal, with no contradictory retry or second dispatch.
        """
        from butlers.core.goal_stop import GoalStop

        from .approved import settle_approved

        self.require_available()
        try:
            async with GoalStop(self.pool, timeout=self.transaction_timeout)._transaction() as conn:
                await settle_approved(conn, prepared, receipt)
            return BrokerResult(receipt.status, prepared.reservation.effect_id, value=receipt.value)
        except BrokerRefused:
            raise
        except Exception:
            raise BrokerRefused(Reason.UNAVAILABLE) from None
