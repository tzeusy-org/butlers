"""Single-use owner-approved notify authority on the egress hop (RFC 0038 D13).

An owner who approves a parked ``notify`` approved that exact delivery, so the
Messenger egress gate must not park it a second time (RFC 0038: "an approved
call is never parked twice"). The approval cannot travel as a flag: a caller
could set one. It travels as an attestation that Switchboard forwards to
Messenger only after the owning source verified its own single-use proof
(``SourceApprovalAdmission.mint_approved_notify``) for the live approved,
unexecuted row and the exact notification digest.

Messenger honours the attestation only when it arrived on the kernel-admitted
Switchboard companion, binds it to the handling task, and accepts it only for
the exact routed ``notify_request`` it covers. Everything else re-enters the
ordinary recipient gate.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

APPROVED_NOTIFY_CONTEXT_KEY = "_trusted_approved_notify"


def notify_request_digest(payload: Mapping[str, Any]) -> str:
    """Canonical content binding for one ``notify.v1`` dictionary."""
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def approved_notify_request(origin_butler: str, tool_args: Mapping[str, Any]) -> dict[str, Any]:
    """Project a stored ``notify`` call into its canonical ``notify.v1`` envelope.

    Attribution comes only from the schema that owns the approved row, never
    from the stored arguments. The same projection is used by the executor that
    sends the envelope and by the source that mints and verifies its proof.
    """
    delivery: dict[str, Any] = {"intent": "send", "message": "", **dict(tool_args)}
    request_context = delivery.pop("request_context", None)
    notify_request: dict[str, Any] = {
        "schema_version": "notify.v1",
        "origin_butler": origin_butler,
        "delivery": delivery,
    }
    if request_context is not None:
        notify_request["request_context"] = request_context
    return json.loads(json.dumps(notify_request))


@dataclass(frozen=True, slots=True)
class ApprovedNotifyAttestation:
    """Switchboard's verified statement that one source approved one notification."""

    issuer: str
    action_id: uuid.UUID
    notify_digest: str

    def as_internal_dict(self) -> dict[str, str]:
        return {
            "issuer": self.issuer,
            "action_id": str(self.action_id),
            "notify_digest": self.notify_digest,
        }

    @classmethod
    def from_internal_dict(cls, raw: Any) -> ApprovedNotifyAttestation:
        if not isinstance(raw, dict) or set(raw) != {"issuer", "action_id", "notify_digest"}:
            raise ValueError("approved notify attestation is malformed")
        issuer, action_id, digest = raw["issuer"], raw["action_id"], raw["notify_digest"]
        if (
            not isinstance(issuer, str)
            or not issuer
            or not isinstance(action_id, str)
            or not isinstance(digest, str)
            or len(digest) != 64
        ):
            raise ValueError("approved notify attestation is malformed")
        return cls(issuer=issuer, action_id=uuid.UUID(action_id), notify_digest=digest)


@dataclass(frozen=True, slots=True)
class _BoundApprovedDelivery:
    attestation: ApprovedNotifyAttestation
    task: asyncio.Task[Any]


_current: contextvars.ContextVar[_BoundApprovedDelivery | None] = contextvars.ContextVar(
    "verified_approved_delivery", default=None
)


def bind_verified_approved_delivery(
    attestation: ApprovedNotifyAttestation,
) -> contextvars.Token[_BoundApprovedDelivery | None]:
    """Bind an already-authenticated attestation to the current task only."""
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("approved delivery binding requires an active asyncio task")
    return _current.set(_BoundApprovedDelivery(attestation, task))


def reset_verified_approved_delivery(
    token: contextvars.Token[_BoundApprovedDelivery | None],
) -> None:
    _current.reset(token)


def approved_delivery_covering(
    tool_name: str | None, tool_args: Any
) -> ApprovedNotifyAttestation | None:
    """Return the bound attestation only if it covers this exact routed delivery.

    The routed ``notify_request`` must hash to the attested digest, name the
    attested issuer as origin and route sender, and carry no recovery
    correlation. A child task, another tool or any changed byte gets nothing.
    """
    bound = _current.get()
    if bound is None or tool_name != "route.execute" or not isinstance(tool_args, dict):
        return None
    try:
        if asyncio.current_task() is not bound.task:
            return None
    except RuntimeError:
        return None
    attestation = bound.attestation
    request_context = tool_args.get("request_context")
    input_payload = tool_args.get("input")
    context = input_payload.get("context") if isinstance(input_payload, dict) else None
    notify_request = context.get("notify_request") if isinstance(context, dict) else None
    if (
        not isinstance(request_context, dict)
        or not isinstance(notify_request, dict)
        or request_context.get("source_endpoint_identity") != "switchboard"
        or request_context.get("source_sender_identity") != attestation.issuer
        or notify_request.get("origin_butler") != attestation.issuer
        or notify_request.get("recovery") is not None
    ):
        return None
    try:
        digest = notify_request_digest(notify_request)
    except (TypeError, ValueError):
        return None
    if digest != attestation.notify_digest:
        return None
    return attestation


__all__ = [
    "APPROVED_NOTIFY_CONTEXT_KEY",
    "ApprovedNotifyAttestation",
    "approved_delivery_covering",
    "approved_notify_request",
    "bind_verified_approved_delivery",
    "notify_request_digest",
    "reset_verified_approved_delivery",
]
