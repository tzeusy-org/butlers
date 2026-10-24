"""Bounded central cancellation consumer over the purpose-bound private MCP client.

Nothing starts or constructs transport on import. The isolated production owner
must still admit the real A2 transactional producer and provision the actual
private peer/material before installing this consumer. TLS and a valid receipt
shape do not themselves establish that the owning producer committed a fence.
"""

from __future__ import annotations

from collections.abc import Mapping
from time import monotonic
from types import MappingProxyType
from uuid import uuid4

from butlers.core.goal_stop import CancelIdentity, GoalStop

from .transport.client import PrivateClient
from .transport.config import OPERATION_SECONDS
from .transport.protocol import PROTOCOL, reply

_SQL_SECONDS = 5
_BOOKKEEPING_SECONDS = 1
_ITEM_SECONDS = 30
_BATCH_SECONDS = 125


class CancellationWorker:
    """One sequential finite batch; no background task, scheduler or hidden retry.

    Clients remain owned by the production resource owner. They must only be
    closed after this operation is terminal, never concurrently with a live
    private call. This consumer does not create/renew/release a claim itself.
    """

    def __init__(self, stop: GoalStop, clients: Mapping[str, PrivateClient]):
        fixed = dict(clients)
        if any(
            type(client) is not PrivateClient or client.config.peer.domain != domain
            for domain, client in fixed.items()
        ):
            raise ValueError("private_cancel_peer_required")
        self.stop, self.clients = stop, MappingProxyType(fixed)

    @staticmethod
    def _fits(deadline: float, phase: float, *, ack: bool = False) -> bool:
        # Admit the complete unchanged native phase; never pass a fictional
        # deadline argument or clip A1's retained call/cleanup with an outer wait.
        reserve = _SQL_SECONDS if ack else 0
        return deadline - monotonic() >= phase + reserve + _BOOKKEEPING_SECONDS

    async def process(self, identity: CancelIdentity, *, deadline: float | None = None) -> str:
        """Return a closed observation; only a committed ACK earns acknowledged."""
        if type(identity) is not CancelIdentity:
            raise ValueError("invalid_cancel_identity")
        end = monotonic() + _ITEM_SECONDS
        if deadline is not None:
            end = min(end, deadline)
        client = self.clients.get(identity.owning_domain)
        if client is None or not self._fits(end, _SQL_SECONDS):
            return "unavailable"
        try:
            claim = await self.stop.claim_cancel_work(identity, lease_seconds=60)
            if claim is None:
                return "unclaimed"
            # A returned claim is still rechecked under the actual DB lease by
            # ACK. Never accept a caller/peer-supplied replacement claim.
            if claim.identity != identity:
                return "unknown"
            operation = "LOOKUP" if claim.generation > 1 else "CANCEL"
            while True:
                if not self._fits(end, OPERATION_SECONDS, ack=True):
                    return "unknown"
                outgoing = {
                    "protocol": PROTOCOL,
                    "operation": operation,
                    "request_id": str(uuid4()),
                    "identity": {
                        "goal_id": str(identity.goal_id),
                        "effect_id": str(identity.effect_id),
                        "owning_domain": identity.owning_domain,
                        "action_id": str(identity.action_id),
                        "capability": identity.capability,
                        "args_digest": identity.args_digest,
                    },
                }
                observed = reply(await client.call(outgoing), outgoing)
                outcome = observed["outcome"]
                if outcome in {"cancelled", "fenced_absent"}:
                    if not self._fits(end, _SQL_SECONDS):
                        return "unknown"
                    await self.stop.ack_cancel_work(claim, observed["receipt"]["digest"], outcome)
                    return "acknowledged"
                if operation == "LOOKUP" and outcome in {
                    "pending",
                    "approved",
                    "rejected",
                    "expired",
                    "unknown",
                }:
                    # Fresh authenticated unknown/null is not absence or an ACK.
                    # One original-tuple CANCEL asks the actual A2 producer to
                    # revalidate atomically against dispatch/fence/current rows.
                    operation = "CANCEL"
                    continue
                return "unknown"
        except Exception:
            # Includes transport/parse/claim/ACK uncertainty. Never inspect raw
            # exception text or send another request following a failed phase.
            # BaseException cancellation propagates through A1 retained cleanup.
            return "unknown"

    async def run_batch(self) -> tuple[str, ...]:
        """Discover up to four hints, processing only complete admitted items."""
        deadline = monotonic() + _BATCH_SECONDS
        try:
            candidates = await self.stop.cancel_candidates(limit=4, domains=tuple(self.clients))
        except Exception:
            return ("unavailable",)
        results = []
        for identity in candidates:
            if not self._fits(deadline, _ITEM_SECONDS):
                break
            results.append(await self.process(identity, deadline=deadline))
        return tuple(results)
