"""Broker PARK consumer over the existing purpose-bound private MCP transport.

Clients belong to the isolated control resource owner, not this adapter. Fixed
purpose operations carry signed selected-Grant provenance to the owning019
repository. A syntactically valid response alone never proves SQL durability,
deployment, runner authority or a committed external effect.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from uuid import uuid4

from butlers.core.capability_json import arguments_digest, canonical_bytes, parse_arguments

from .contracts import BrokerRefused, Disposition, Reason, Reservation
from .domains import ParkReceipt
from .transport.client import PrivateClient
from .transport.protocol import reply


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
            "protocol": "broker-approvals-v2",
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
            "handoff": assertion,
        }
        observed = reply(await client.call(outgoing), outgoing)
        if observed["outcome"] != "parked" or observed["receipt"]["approval_ref"] != effect:
            raise BrokerRefused(Reason.UNAVAILABLE)
        return ParkReceipt(effect)

    async def lookup(self, identity):
        """Read exact committed owning evidence; never grant permission to execute."""
        from butlers.core.goal_stop import CancelIdentity

        from .transport.protocol import PROTOCOL

        if type(identity) is not CancelIdentity:
            raise BrokerRefused(Reason.AUTHENTICATION)
        client = self.clients.get(identity.owning_domain)
        if client is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        outgoing = {
            "protocol": PROTOCOL,
            "operation": "LOOKUP",
            "request_id": str(uuid4()),
            "identity": {
                key: str(getattr(identity, key))
                for key in (
                    "goal_id",
                    "effect_id",
                    "owning_domain",
                    "action_id",
                    "capability",
                    "args_digest",
                )
            },
        }
        return reply(await client.call(outgoing), outgoing)

    async def operation(
        self,
        reservation,
        *,
        signer,
        purpose,
        issued_at,
        approval_ref=None,
        original_args_digest=None,
        snapshot_version=None,
    ):
        """Fixed operation with no caller-selected endpoint or replacement args."""
        from .handoff import sign_handoff

        client = self.clients.get(reservation.capability.domain)
        if client is None or reservation.effect_id is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        outgoing = {
            "protocol": "broker-approvals-v2",
            "operation": purpose,
            "request_id": str(uuid4()),
            "identity": {
                "goal_id": str(reservation.principal.goal_id),
                "effect_id": str(reservation.effect_id),
                "owning_domain": reservation.capability.domain,
                "action_id": str(reservation.effect_id),
                "capability": reservation.capability.identity,
                "args_digest": original_args_digest or reservation.args_digest,
            },
            "handoff": sign_handoff(
                signer,
                reservation,
                purpose=purpose,
                issued_at=issued_at,
                approval_ref=approval_ref,
                original_args_digest=original_args_digest,
                snapshot_version=snapshot_version,
            ),
        }
        return reply(await client.call(outgoing), outgoing)
