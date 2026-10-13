"""Immutable identity for ordinary Switchboard ingestion target deliveries.

This is a trusted-host assertion carried over the existing trusted-route-caller
boundary, not a new authentication credential. Retries do not mint identities.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

SegmentId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
ButlerName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
PayloadDigest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class DeliveryBinding(BaseModel):
    """Content-blind stable binding; ``lookup`` never admits work."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    ingestion_event_id: UUID
    target_butler: ButlerName
    segment_id: SegmentId
    payload_digest: PayloadDigest
    operation: Literal["accept", "lookup"] = "accept"

    @field_validator("ingestion_event_id", mode="before")
    @classmethod
    def _event_id(cls, value: Any) -> UUID:
        value = UUID(value) if isinstance(value, str) else value
        if not isinstance(value, UUID) or value.version != 7:
            raise ValueError("delivery identity requires a UUID7 ingestion event")
        return value

    @classmethod
    def for_envelope(cls, envelope: Mapping[str, Any]) -> DeliveryBinding:
        """Construct the first binding from a normalized route with explicit lineage."""
        return cls(
            ingestion_event_id=envelope["request_context"]["request_id"],
            target_butler=envelope["target"]["butler"],
            segment_id=envelope["subrequest"]["segment_id"],
            payload_digest=canonical_payload_digest(envelope),
        )


def canonical_payload_digest(envelope: Mapping[str, Any]) -> str:
    """SHA256 of normalized immutable route.v1 work, encoded as canonical JSON.

    Includes all input, source, target and request/subrequest lineage fields.
    Receipt time and tracing are transport metadata; they are the only excluded
    route fields besides the delivery binding itself. JSON is sorted, UTF-8,
    compact, and rejects NaN/Infinity. Nested input dictionaries are canonicalized.
    """
    from butlers.tools.switchboard.routing.contracts import parse_route_envelope

    payload = dict(envelope)
    payload.pop("delivery", None)
    canonical = parse_route_envelope(payload).model_dump(mode="json")
    canonical.pop("delivery", None)
    canonical.pop("trace_context", None)
    canonical["request_context"].pop("received_at", None)
    canonical["request_context"].pop("trace_context", None)
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_delivery(
    envelope: Mapping[str, Any], *, receiving_butler: str, trusted_callers: Collection[str]
) -> DeliveryBinding:
    """Refuse malformed or cross-lane bindings before database access.

    Lookup checks the asserted digest against the ledger rather than computing
    it from the placeholder input, so callers need not disclose retained content.
    """
    from butlers.tools.switchboard.routing.contracts import parse_route_envelope

    parsed = parse_route_envelope(envelope)
    binding = parsed.delivery
    context = parsed.request_context
    if (
        binding is None
        or context.source_channel == "dashboard"
        or receiving_butler in {"messenger", "switchboard"}
        or context.source_endpoint_identity != "switchboard"
        or "switchboard" not in trusted_callers
        or parsed.target is None
        or parsed.target.butler != receiving_butler
        or binding.target_butler != receiving_butler
        or binding.ingestion_event_id != context.request_id
        or parsed.subrequest is None
        or binding.segment_id != parsed.subrequest.segment_id
        or context.segment_id != binding.segment_id
        or context.subrequest_id != parsed.subrequest.subrequest_id
    ):
        raise ValueError("ingestion delivery binding rejected")
    if binding.operation == "accept" and binding.payload_digest != canonical_payload_digest(
        envelope
    ):
        raise ValueError("ingestion delivery binding rejected")
    return binding
