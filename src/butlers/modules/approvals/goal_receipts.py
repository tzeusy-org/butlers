"""Closed owning receipt projection from committed SQL rows, not MCP text.

The caller returns these only after its actual transaction commits. Receipt
hashes bind values; they do not establish that a store committed or authenticate
an ingress. No external-effect outcome is inferred from approval status.
"""

from uuid import UUID

from butlers.capability_broker.transport.protocol import RECEIPT_PROTOCOL, receipt_digest
from butlers.core.goal_stop import CancelIdentity


def identity_fields(value: CancelIdentity) -> dict:
    if type(value) is not CancelIdentity:
        raise ValueError("goal_identity_required")
    return {
        "goal_id": str(value.goal_id),
        "effect_id": str(value.effect_id),
        "owning_domain": value.owning_domain,
        "action_id": str(value.action_id),
        "capability": value.capability,
        "args_digest": value.args_digest,
    }


def owning_receipt(
    receipt_id: UUID, identity: CancelIdentity, outcome: str, reference: UUID | None
):
    if not isinstance(receipt_id, UUID) or outcome not in {
        "parked",
        "pending",
        "approved",
        "rejected",
        "expired",
        "cancelled",
        "fenced_absent",
    }:
        raise ValueError("invalid_owning_receipt")
    if (outcome == "fenced_absent") != (reference is None) or (
        reference is not None and reference != identity.action_id
    ):
        raise ValueError("invalid_owning_reference")
    value = {
        "protocol": RECEIPT_PROTOCOL,
        "receipt_id": str(receipt_id),
        "identity": identity_fields(identity),
        "outcome": outcome,
        "approval_ref": str(reference) if reference is not None else None,
    }
    return value | {"digest": receipt_digest(value)}


def stored_receipt(row, identity: CancelIdentity, outcome: str) -> dict:
    value = owning_receipt(row["receipt_id"], identity, outcome, row["pending_action_id"])
    if any(row[k] != getattr(identity, k) for k in identity_fields(identity)) or (
        row["outcome"] != outcome or row["digest"] != value["digest"]
    ):
        raise ValueError("stored_goal_receipt_mismatch")
    return value
