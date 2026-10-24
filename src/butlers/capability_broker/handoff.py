"""Purpose-separated owning approval authority, never an ordinary D7 assertion.

Only the central ledger selects a Grant. TLS authenticates the fixed service,
while these signed bytes bind that selection and every operation input. Durable
owning claims, not this codec, serialize PARK and one-time EXECUTE.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from butlers.core.capability_json import canonical_bytes, parse_arguments
from butlers.core.goal_admission import decode_grants, encode_grants

from .assertions import _decode, _encode, _payload
from .contracts import BrokerRefused, Disposition, Reason

PURPOSE_PROTOCOL = "broker-owning-authority-v1"
PURPOSES = frozenset({"PARK", "SNAPSHOT", "CLAIM", "WITNESS", "EXECUTE"})
FIELDS = frozenset(
    {
        "protocol",
        "purpose",
        "d7",
        "grant",
        "catalog_digest",
        "registration_digest",
        "central_rule_match",
        "approval_mode",
        "original_args_digest",
        "snapshot_version",
        "exp",
    }
)


def registration_digest(capability):
    # Dataclass tuples are deliberately rendered to I-JSON arrays first.
    import json

    return hashlib.sha256(canonical_bytes(json.loads(json.dumps(asdict(capability))))).hexdigest()


@dataclass(frozen=True, slots=True)
class VerifiedHandoff:
    wire: bytes = field(repr=False)

    @property
    def payload(self):
        return parse_arguments(self.wire)

    @property
    def grant(self):
        return decode_grants([self.payload["grant"]])[0]

    @property
    def central_rule_match(self):
        return self.payload["central_rule_match"]

    @property
    def args_digest(self):
        return self.payload["d7"]["args_digest"]

    @property
    def decision(self):
        return Disposition.PARK if self.payload["purpose"] == "PARK" else Disposition.DISPATCH

    @property
    def digest(self):
        return hashlib.sha256(self.wire).hexdigest()


def sign_handoff(
    signer,
    reservation,
    *,
    purpose,
    issued_at,
    approval_ref=None,
    original_args_digest=None,
    snapshot_version=None,
):
    grant = reservation.selected_grant
    if grant is None or grant.capability != reservation.capability.identity:
        raise BrokerRefused(Reason.AUTHENTICATION)
    d7 = parse_arguments(
        _decode(
            signer.sign(reservation, issued_at=issued_at, approval_ref=approval_ref).split(".")[1]
        )
    )
    value = {
        "protocol": PURPOSE_PROTOCOL,
        "purpose": purpose,
        "d7": d7,
        "grant": encode_grants((grant,))[0],
        "catalog_digest": reservation.principal.catalog_digest,
        "registration_digest": registration_digest(reservation.capability),
        "central_rule_match": reservation.decision.allow_standing_rule,
        "approval_mode": "always_per_event"
        if reservation.capability.always_per_event
        else "default",
        "original_args_digest": original_args_digest or reservation.args_digest,
        "snapshot_version": snapshot_version,
        "exp": d7["exp"],
    }
    _validate(value)
    header = _encode(canonical_bytes({"alg": "EdDSA", "kid": signer.kid, "typ": PURPOSE_PROTOCOL}))
    body = _encode(canonical_bytes(value))
    return f"{header}.{body}.{_encode(signer.private_key.sign(f'{header}.{body}'.encode('ascii')))}"


def _validate(value):
    if type(value) is not dict or set(value) != FIELDS:
        raise ValueError("handoff_shape")
    _payload(value["d7"])
    if (
        value["protocol"] != PURPOSE_PROTOCOL
        or value["purpose"] not in PURPOSES
        or value["approval_mode"] not in {"default", "always_per_event"}
        or type(value["central_rule_match"]) is not bool
        or type(value["exp"]) is not int
        or value["exp"] != value["d7"]["exp"]
        or type(value["catalog_digest"]) is not str
        or not value["catalog_digest"]
    ):
        raise ValueError("handoff_binding")
    import re

    if any(
        type(value[k]) is not str or re.fullmatch(r"[0-9a-f]{64}", value[k]) is None
        for k in ("registration_digest", "original_args_digest")
    ):
        raise ValueError("handoff_digest")
    grant = value["grant"]
    if type(grant) is not dict or set(grant) != {"capability", "resources", "park_only"}:
        raise ValueError("handoff_grant")
    if type(grant["resources"]) is not dict or any(
        type(v) is not list for v in grant["resources"].values()
    ):
        raise ValueError("handoff_grant")
    decoded = decode_grants([grant])[0]
    if encode_grants((decoded,))[0] != grant or decoded.capability != (
        value["d7"]["aud"] + "__" + value["d7"]["capability"]
    ):
        raise ValueError("handoff_grant")
    if value["d7"]["effect_id"] is None:
        raise ValueError("handoff_effect")
    version = value["snapshot_version"]
    if value["purpose"] in {"CLAIM", "WITNESS", "EXECUTE"}:
        if (
            type(version) is not int
            or version < 1
            or value["d7"]["approval_ref"] != value["d7"]["effect_id"]
        ):
            raise ValueError("handoff_snapshot")
    elif version is not None or value["d7"]["approval_ref"] is not None:
        raise ValueError("handoff_snapshot")
    if value["purpose"] == "PARK" and (
        value["original_args_digest"] != value["d7"]["args_digest"]
        or (decoded.park_only and value["central_rule_match"])
    ):
        raise ValueError("handoff_park")


def verify_handoff(token, *, keys, audience, purpose, now):
    """Verify exact canonical bytes; durable operation claims remain mandatory."""
    try:
        if type(token) is not str:
            raise ValueError("handoff_size")
        a, b, c = token.split(".")
        raw_header, wire = _decode(a), _decode(b)
        header, value = parse_arguments(raw_header), parse_arguments(wire)
        if (
            set(header) != {"alg", "kid", "typ"}
            or header["alg"] != "EdDSA"
            or header["typ"] != PURPOSE_PROTOCOL
            or type(header["kid"]) is not str
            or canonical_bytes(header) != raw_header
            or canonical_bytes(value) != wire
        ):
            raise ValueError("handoff_header")
        key = keys.get(header["kid"])
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("handoff_key")
        key.verify(_decode(c), f"{a}.{b}".encode("ascii"))
        _validate(value)
        if (
            type(now) is not int
            or not now < value["exp"] <= now + 60
            or value["purpose"] != purpose
            or value["d7"]["aud"] != audience
        ):
            raise ValueError("handoff_target")
        return VerifiedHandoff(wire)
    except (ValueError, TypeError, KeyError, AttributeError, InvalidSignature):
        raise BrokerRefused(Reason.AUTHENTICATION) from None
