"""D7 compact JWS, shared by issuer and the separately owned domain verifier.

Header: exactly alg=EdDSA and kid. Payload: exactly the D7 fields. Both use
RFC8785 UTF-8, encoded unpadded base64url; Ed25519 signs ASCII header.payload.
No keys are loaded here. The verifier must supply current/next public keys and
an atomic replay claim that also enforces its restart-window policy.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from butlers.core.capability_json import arguments_digest, canonical_bytes, parse_arguments

from .contracts import BrokerRefused, Reason, Reservation

FIELDS = frozenset(
    {
        "aud",
        "goal_id",
        "invocation_id",
        "depth",
        "origin",
        "exposed",
        "capability",
        "args_digest",
        "call_id",
        "effect_id",
        "approval_ref",
        "exp",
    }
)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("noncanonical_base64")
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if _encode(raw) != value:
        raise ValueError("noncanonical_base64")
    return raw


def _payload(value: dict) -> None:
    if set(value) != FIELDS:
        raise ValueError("assertion_shape")
    if type(value["depth"]) is not int or value["depth"] not in (0, 1):
        raise ValueError("assertion_depth")
    if value["origin"] not in {"owner", "obligation", "third_party", "staffer"}:
        raise ValueError("assertion_origin")
    if type(value["exposed"]) is not bool or type(value["exp"]) is not int:
        raise ValueError("assertion_types")
    for key in ("aud", "capability"):
        if type(value[key]) is not str or not value[key]:
            raise ValueError("assertion_identity")
    if not isinstance(value["args_digest"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", value["args_digest"]
    ):
        raise ValueError("assertion_digest")
    for key in ("goal_id", "invocation_id", "call_id", "effect_id"):
        if key == "effect_id" and value[key] is None:
            continue
        if type(value[key]) is not str or str(UUID(value[key])) != value[key]:
            raise ValueError("assertion_uuid")
    if value["approval_ref"] is not None and (
        type(value["approval_ref"]) is not str
        or not value["approval_ref"]
        or value["effect_id"] is None
    ):
        raise ValueError("assertion_approval")


@dataclass(frozen=True, slots=True)
class AssertionSigner:
    kid: str
    private_key: Ed25519PrivateKey = field(repr=False)

    def __post_init__(self):
        if not self.kid or not isinstance(self.private_key, Ed25519PrivateKey):
            raise BrokerRefused(Reason.UNAVAILABLE)

    def sign(self, reservation: Reservation, *, issued_at: int, approval_ref: str | None = None):
        if type(issued_at) is not int or issued_at < 0:
            raise BrokerRefused(Reason.UNAVAILABLE)
        principal, capability = reservation.principal, reservation.capability
        payload = {
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
            "exp": issued_at + 60,
        }
        _payload(payload)
        protected = _encode(canonical_bytes({"alg": "EdDSA", "kid": self.kid}))
        content = _encode(canonical_bytes(payload))
        signing_input = f"{protected}.{content}".encode("ascii")
        return f"{protected}.{content}.{_encode(self.private_key.sign(signing_input))}"


def verify_assertion(
    assertion: str,
    *,
    keys: Mapping[str, Ed25519PublicKey],
    audience: str,
    capability: str,
    arguments: dict,
    now: int,
    claim_call_id: Callable[[UUID, int], bool],
) -> dict:
    """Verify before coercion; atomically claim replay only after every check.

    Supplying a cache does not itself prove restart safety. Domain wiring must
    provide persistent claims or refuse assertions from the pre-start window.
    This pure boundary does not install ambient context or activate enforcement.
    """
    try:
        header_part, body_part, signature_part = assertion.split(".")
        header_raw, body_raw = _decode(header_part), _decode(body_part)
        header, payload = parse_arguments(header_raw), parse_arguments(body_raw)
        if (
            set(header) != {"alg", "kid"}
            or header["alg"] != "EdDSA"
            or type(header["kid"]) is not str
            or canonical_bytes(header) != header_raw
            or canonical_bytes(payload) != body_raw
        ):
            raise ValueError("assertion_header")
        key = keys.get(header["kid"])
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("unknown_key")
        key.verify(_decode(signature_part), f"{header_part}.{body_part}".encode("ascii"))
        _payload(payload)
        if (
            type(now) is not int
            or not now < payload["exp"] <= now + 60
            or payload["aud"] != audience
            or payload["capability"] != capability
            or payload["args_digest"] != arguments_digest(arguments)
        ):
            raise ValueError("assertion_binding")
        if claim_call_id(UUID(payload["call_id"]), payload["exp"]) is not True:
            raise ValueError("assertion_replay")
        return payload
    except (ValueError, TypeError, KeyError, InvalidSignature):
        raise BrokerRefused(Reason.AUTHENTICATION) from None
