"""Broker PARK consumer over the existing purpose-bound private MCP transport.

Clients belong to the isolated control resource owner, not this adapter. Nothing
constructs a listener, reads TLS material, enables a profile, or supplies the
separately required owning SQL producer. A syntactically valid software receipt
is not evidence that such a producer has been deployed or verified.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from uuid import uuid4

from butlers.core.capability_json import arguments_digest, canonical_bytes, parse_arguments

from .contracts import BrokerRefused, Disposition, Reason, Reservation
from .domains import ParkReceipt
from .transport.client import PrivateClient
from .transport.protocol import PROTOCOL, reply


class PrivateApprovals:
    """Forward a committed original PARK intent once to its exact configured owner.

    The actual owning producer must validate D7 and commit its matching action,
    gate and receipt before responding. Only that peer's exact original action
    reference can be attached centrally; malformed/refused/unknown responses
    propagate into the broker's existing UNKNOWN settlement, never a fallback.
    """

    def __init__(self, clients: Mapping[str, PrivateClient]):
        fixed = dict(clients)
        if any(
            type(client) is not PrivateClient or client.config.peer.domain != domain
            for domain, client in fixed.items()
        ):
            raise ValueError("private_approval_peer_required")
        self.clients = MappingProxyType(fixed)

    async def park(self, reservation: Reservation, args: dict, assertion: str) -> ParkReceipt:
        # Detach before the first await, including any private-client lock wait.
        snapshot = parse_arguments(canonical_bytes(args))
        if (
            type(reservation) is not Reservation
            or reservation.decision.disposition is not Disposition.PARK
            or reservation.effect_id is None
            or not reservation.capability.approvals_available
            or arguments_digest(snapshot) != reservation.args_digest
        ):
            raise BrokerRefused(Reason.AUTHENTICATION)
        client = self.clients.get(reservation.capability.domain)
        if client is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        effect = str(reservation.effect_id)
        outgoing = {
            "protocol": PROTOCOL,
            "operation": "PARK",
            "request_id": str(uuid4()),
            "identity": {
                "goal_id": str(reservation.principal.goal_id),
                "effect_id": effect,
                "owning_domain": reservation.capability.domain,
                "action_id": effect,
                "capability": reservation.capability.identity,
                "args_digest": reservation.args_digest,
            },
            "arguments": snapshot,
            "allow_standing_rule": reservation.decision.allow_standing_rule,
        }
        observed = reply(await client.call(outgoing, assertion=assertion), outgoing)
        if observed["outcome"] != "parked" or observed["receipt"]["approval_ref"] != effect:
            raise BrokerRefused(Reason.UNAVAILABLE)
        return ParkReceipt(effect)
