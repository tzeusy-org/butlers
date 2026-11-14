"""The runtime-probe control capability: one compact JWS, signed and verified.

REQ-core-credentials-002 fixes the wire shape.  A capability is a compact JWS
whose protected header is exactly ``{"alg": "EdDSA", "kid": ...}`` and whose
payload is exactly ``aud``, ``caller``, ``catalog_entry_id``, ``iat``, ``exp``,
and ``nonce``.  Nothing else is accepted --- not an extra header, not an extra
claim, not a differently-spelled UUID, not a padded nonce.

Two properties are worth stating explicitly because they are what make this
different from "we parsed a JWT":

*No token-selected algorithm.*  ``alg`` is not consulted to *choose* a
verifier; it is checked for equality with ``EdDSA`` and then ignored.  The key
comes from the process's immutable deployment keyring, resolved by ``kid``, and
there is no code path that fetches a key from anywhere else --- which is why
``jku``, ``x5u``, and ``jwk`` need no special handling beyond the exact-header
rule that already rejects them.

*Verification is total, and it happens before any side effect.*  Every
rejection below runs before the coordinator claims a receipt, resolves a
catalog entry, launches a runtime, or persists verification evidence.  A
capability that fails here has left no trace but a counter.

Diagnostics are fixed strings chosen from this file.  No segment, claim value,
nonce, signature, or key ever reaches a message, because these messages reach
logs.
"""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Final
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

from butlers.core.runtime_probe_control.keys import (
    ALGORITHM,
    CLOCK_SKEW,
    CONTROL_AUDIENCE,
    KEY_ID_PATTERN,
    MAX_CAPABILITY_LIFETIME,
    SignerKey,
    VerifierKey,
    VerifierKeyring,
)

#: The two registered caller classes.  A capability naming anything else --- a
#: connector, a butler, a model session --- is not a control-plane caller.
CALLERS: Final = frozenset({"dashboard", "scheduler"})

#: 256 bits of randomness, the unit the durable replay receipt digests.
NONCE_BYTES: Final = 32

#: What the client asks for unless a caller narrows it; also the hard ceiling.
DEFAULT_LIFETIME: Final = MAX_CAPABILITY_LIFETIME


@dataclass(frozen=True, slots=True)
class HeaderProfile:
    """One protocol's exact protected-header shape (RFC 0039 D7: per-protocol header sets).

    ``fields`` is the exact field set; ``typ``, when set, is required and compared exactly.
    No profile accepts another's header, so a token of one protocol never verifies as the
    other's, and ``typ`` never becomes optional.
    """

    fields: frozenset[str]
    typ: str | None = None


#: Runtime-probe and service-call capabilities: exactly ``{alg, kid}``.
PROBE_HEADER: Final = HeaderProfile(frozenset({"alg", "kid"}))

#: The Worker's public-MCP assertion (RFC 0039 D7): exactly ``{alg, kid, typ}``.
ASSERTION_HEADER: Final = HeaderProfile(
    frozenset({"alg", "kid", "typ"}), typ="butlers-mcp-assertion+v1"
)

#: The signed envelope every audience shares; each audience adds its claims.
_ENVELOPE_FIELDS: Final = frozenset({"aud", "iat", "exp", "nonce"})
_PROBE_CLAIM_FIELDS: Final = frozenset({"caller", "catalog_entry_id"})

#: Unpadded base64url, and nothing else: no padding, no standard alphabet, no
#: whitespace.  Applied to every segment before it is decoded.
_SEGMENT_PATTERN: Final = re.compile(r"\A[A-Za-z0-9_-]+\Z")

#: A 32-byte nonce is exactly 43 unpadded base64url characters.
_NONCE_PATTERN: Final = re.compile(r"\A[A-Za-z0-9_-]{43}\Z")

#: Bound each segment so a hostile body cannot make the parser do real work.
_MAX_SEGMENT_CHARS: Final = 1024

#: NumericDate values outside this band are not plausible seconds-since-epoch
#: and would raise deep inside ``datetime`` rather than at the claim check.
_MIN_NUMERIC_DATE: Final = 0
_MAX_NUMERIC_DATE: Final = 1 << 40


class CapabilityRejected(Exception):
    """A capability is not acceptable.

    The message is always one of the fixed strings in this module.  It never
    interpolates a segment, claim, nonce, signature, or key, because callers
    log it.
    """


@dataclass(frozen=True, slots=True)
class VerifiedCapability:
    """What survived verification.

    ``nonce`` is the only field that must not be logged; the dataclass redacts
    its whole ``repr`` rather than relying on every call site to remember which
    field is sensitive.
    """

    kid: str
    caller: str
    catalog_entry_id: UUID
    issued_at: datetime
    expires_at: datetime
    nonce: bytes

    def __repr__(self) -> str:  # pragma: no cover - trivial redaction
        return "VerifiedCapability(<redacted>)"


@dataclass(frozen=True, slots=True)
class SignedClaims:
    """A verified envelope and its audience's claims, not yet interpreted.

    The audience's own verifier checks ``claims``; this type only proves they
    were signed under an acceptable deployment key, for this audience, within
    the lifetime and skew bounds.  ``repr`` is redacted for the nonce.
    """

    kid: str
    claims: Mapping[str, Any]
    issued_at: datetime
    expires_at: datetime
    nonce: bytes

    def __repr__(self) -> str:  # pragma: no cover - trivial redaction
        return "SignedClaims(<redacted>)"


# ---------------------------------------------------------------------------
# Encoding primitives
# ---------------------------------------------------------------------------


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(segment: str, *, reason: str, max_chars: int = _MAX_SEGMENT_CHARS) -> bytes:
    """Decode one canonical unpadded base64url segment."""
    if len(segment) > max_chars or _SEGMENT_PATTERN.match(segment) is None:
        raise CapabilityRejected(reason)
    try:
        decoded = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (ValueError, TypeError) as exc:
        raise CapabilityRejected(reason) from exc
    if _b64u_encode(decoded) != segment:
        # A trailing character carrying bits that decode to nothing is a second
        # spelling of the same bytes; only one spelling is the capability.
        raise CapabilityRejected(reason)
    return decoded


#: Public names for other protocols that reuse this codec (RFC 0039 D7).
b64u_encode = _b64u_encode
b64u_decode = _b64u_decode


def _reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for field, value in pairs:
        if field in document:
            raise CapabilityRejected("capability repeats a field")
        document[field] = value
    return document


def _reject_json_constant(_token: str) -> Any:
    raise CapabilityRejected("capability contains a non-finite number")


def strict_object(raw: bytes, *, exact_fields: frozenset[str], reason: str) -> dict[str, Any]:
    """Decode strict UTF-8 JSON with exactly ``exact_fields`` and no duplicates."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CapabilityRejected(reason) from exc
    try:
        document = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_fields,
            parse_constant=_reject_json_constant,
        )
    except CapabilityRejected:
        raise
    except ValueError as exc:
        raise CapabilityRejected(reason) from exc
    if not isinstance(document, dict) or frozenset(document) != exact_fields:
        raise CapabilityRejected(reason)
    return document


def _exact_string(value: Any, expected: str, *, reason: str) -> str:
    if not isinstance(value, str) or value != expected:
        raise CapabilityRejected(reason)
    return value


def _key_id(value: Any) -> str:
    if not isinstance(value, str) or KEY_ID_PATTERN.match(value) is None:
        raise CapabilityRejected("capability key id does not match the approved grammar")
    return value


def _numeric_date(value: Any) -> int:
    # ``True`` is an ``int`` in Python and would otherwise pass every check.
    if type(value) is not int or not (_MIN_NUMERIC_DATE <= value <= _MAX_NUMERIC_DATE):
        raise CapabilityRejected("capability time claim is not an integer NumericDate")
    return value


def _canonical_uuid(value: Any) -> UUID:
    """Accept only the canonical lowercase hyphenated spelling."""
    if not isinstance(value, str):
        raise CapabilityRejected("capability catalog entry id is not a canonical UUID")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise CapabilityRejected("capability catalog entry id is not a canonical UUID") from exc
    if str(parsed) != value:
        raise CapabilityRejected("capability catalog entry id is not a canonical UUID")
    return parsed


def _nonce(value: Any) -> bytes:
    if not isinstance(value, str) or _NONCE_PATTERN.match(value) is None:
        raise CapabilityRejected("capability nonce is not 32 unpadded base64url bytes")
    decoded = _b64u_decode(value, reason="capability nonce is not 32 unpadded base64url bytes")
    if len(decoded) != NONCE_BYTES:
        raise CapabilityRejected("capability nonce is not 32 unpadded base64url bytes")
    return decoded


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def _sign_raw_segments(signer: SignerKey, *, header: bytes, payload: bytes) -> str:
    """Sign already-encoded documents.  Used by ``sign_capability`` and by tests
    that need to put a deliberately malformed document behind a real signature."""
    signing_input = f"{_b64u_encode(header)}.{_b64u_encode(payload)}".encode("ascii")
    signature = signer.private_key.sign(signing_input)
    return f"{signing_input.decode('ascii')}.{_b64u_encode(signature)}"


def _sign_segments(signer: SignerKey, *, header: Any, payload: Any) -> str:
    return _sign_raw_segments(
        signer,
        header=json.dumps(header).encode("utf-8"),
        payload=json.dumps(payload).encode("utf-8"),
    )


def sign_claims(
    signer: SignerKey,
    *,
    audience: str,
    claims: Mapping[str, Any],
    now: datetime,
    lifetime: timedelta = DEFAULT_LIFETIME,
) -> str:
    """Mint one capability for *audience* carrying *claims*, or refuse.

    Every audience this keyring signs shares the envelope: ``aud``, ``iat``,
    ``exp`` and a fresh 256-bit ``nonce`` around the audience's own claims.
    The signer refuses the same things the verifier would refuse --- a
    lifetime outside the protocol bound, or an instant outside the signer's
    ``sign_from`` / ``sign_until`` window --- so a misconfigured client fails
    locally instead of emitting a capability Switchboard will reject.
    """
    if _ENVELOPE_FIELDS & set(claims):
        raise CapabilityRejected("capability claims overlap the signed envelope")
    lifetime_seconds = int(lifetime.total_seconds())
    if lifetime != timedelta(seconds=lifetime_seconds):
        raise CapabilityRejected("capability lifetime is not a whole number of seconds")
    if not 0 < lifetime_seconds <= int(MAX_CAPABILITY_LIFETIME.total_seconds()):
        raise CapabilityRejected("capability lifetime is outside the accepted bound")
    if not signer.may_issue_at(now):
        raise CapabilityRejected("signer may not issue a capability at this instant")

    issued_at = int(now.astimezone(UTC).timestamp())
    return _sign_segments(
        signer,
        header={"alg": ALGORITHM, "kid": signer.kid},
        payload={
            "aud": audience,
            **claims,
            "iat": issued_at,
            "exp": issued_at + lifetime_seconds,
            "nonce": _b64u_encode(os.urandom(NONCE_BYTES)),
        },
    )


def sign_capability(
    signer: SignerKey,
    *,
    caller: str,
    catalog_entry_id: UUID,
    now: datetime,
    lifetime: timedelta = DEFAULT_LIFETIME,
) -> str:
    """Mint one runtime-probe capability, or refuse.

    Beyond :func:`sign_claims`, an unregistered caller class is refused here.
    """
    if caller not in CALLERS:
        raise CapabilityRejected("capability caller is not a registered control caller")
    return sign_claims(
        signer,
        audience=CONTROL_AUDIENCE,
        claims={"caller": caller, "catalog_entry_id": str(catalog_entry_id)},
        now=now,
        lifetime=lifetime,
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_capability(
    compact: str | None,
    *,
    keyring: VerifierKeyring,
    now: datetime,
) -> VerifiedCapability:
    """Verify one runtime-probe capability against the deployment keyring."""
    signed = verify_claims(
        compact,
        keyring=keyring,
        now=now,
        audience=CONTROL_AUDIENCE,
        claim_fields=_PROBE_CLAIM_FIELDS,
    )
    caller = signed.claims["caller"]
    if not isinstance(caller, str) or caller not in CALLERS:
        raise CapabilityRejected("capability caller is not a registered control caller")
    return VerifiedCapability(
        kid=signed.kid,
        caller=caller,
        catalog_entry_id=_canonical_uuid(signed.claims["catalog_entry_id"]),
        issued_at=signed.issued_at,
        expires_at=signed.expires_at,
        nonce=signed.nonce,
    )


def verify_claims(
    compact: str | None,
    *,
    keyring: VerifierKeyring,
    now: datetime,
    audience: str,
    claim_fields: frozenset[str],
) -> SignedClaims:
    """Verify one compact JWS for *audience* against the deployment keyring.

    Order matters.  The header is parsed and the key is resolved from the
    *keyring* first, then the signature is checked, and only then are the
    claims read --- so no unsigned byte ever influences a decision.  The
    payload must be exactly the envelope plus *claim_fields*; the audience's
    own claims come back unread for its verifier to check.  Every branch
    raises :class:`CapabilityRejected`; there is no ``None`` return and no
    partial success.
    """
    signed = verify_signature(compact, keyring=keyring, now=now, profile=PROBE_HEADER)
    entry = signed.entry

    payload = strict_object(
        signed.payload,
        exact_fields=_ENVELOPE_FIELDS | claim_fields,
        reason="capability payload is not the approved shape",
    )
    _exact_string(payload["aud"], audience, reason="capability audience is not this plane")
    nonce = _nonce(payload["nonce"])

    issued_at = _numeric_date(payload["iat"])
    expires_at = _numeric_date(payload["exp"])
    lifetime = expires_at - issued_at
    if not 0 < lifetime <= int(MAX_CAPABILITY_LIFETIME.total_seconds()):
        raise CapabilityRejected("capability lifetime is outside the accepted bound")

    skew = int(CLOCK_SKEW.total_seconds())
    second = int(now.astimezone(UTC).timestamp())
    if issued_at > second + skew:
        raise CapabilityRejected("capability was issued too far in the future")
    if expires_at < second - skew:
        raise CapabilityRejected("capability has expired")

    issued_instant = datetime.fromtimestamp(issued_at, UTC)
    check_issued_under(entry, issued_instant)

    return SignedClaims(
        kid=signed.kid,
        claims=MappingProxyType({field: payload[field] for field in claim_fields}),
        issued_at=issued_instant,
        expires_at=datetime.fromtimestamp(expires_at, UTC),
        nonce=nonce,
    )


@dataclass(frozen=True, slots=True)
class SignedSegments:
    """A compact token whose header matched its profile and whose signature verified.

    ``payload`` is the signed payload bytes, not yet parsed: each protocol owns its claims.
    """

    kid: str
    entry: VerifierKey
    payload: bytes

    def __repr__(self) -> str:  # pragma: no cover - trivial redaction
        return "SignedSegments(<redacted>)"


def verify_signature(
    compact: str | None,
    *,
    keyring: VerifierKeyring,
    now: datetime,
    profile: HeaderProfile,
    max_segment_chars: int = _MAX_SEGMENT_CHARS,
) -> SignedSegments:
    """Check the header against *profile*, resolve ``kid`` and verify the Ed25519 signature.

    The header is parsed and the key is resolved from the *keyring* first, then the
    signature is checked; no payload byte is read before the signature verifies.
    """
    header_segment, payload_segment, signature_segment = _split(compact)
    if max(len(header_segment), len(payload_segment)) > max_segment_chars:
        raise CapabilityRejected("capability segment is longer than the accepted bound")

    header = strict_object(
        _b64u_decode(header_segment, reason="capability header is not base64url"),
        exact_fields=profile.fields,
        reason="capability protected header is not the approved shape",
    )
    _exact_string(header["alg"], ALGORITHM, reason="capability algorithm is not EdDSA")
    if profile.typ is not None:
        _exact_string(header["typ"], profile.typ, reason="capability type is not this protocol")
    kid = _key_id(header["kid"])

    # Resolved from the immutable snapshot only.  There is no remote or
    # token-directed key source anywhere in this function.
    entry = keyring.acceptable_entry(kid, now)
    if entry is None:
        raise CapabilityRejected("capability key id is not an acceptable deployment key")

    signature = _b64u_decode(signature_segment, reason="capability signature is not base64url")
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(entry.public_key).verify(
            signature, f"{header_segment}.{payload_segment}".encode("ascii")
        )
    except (InvalidSignature, ValueError) as exc:
        raise CapabilityRejected("capability signature does not verify") from exc
    payload = _b64u_decode(
        payload_segment,
        reason="capability payload is not base64url",
        max_chars=max_segment_chars,
    )
    return SignedSegments(kid=kid, entry=entry, payload=payload)


def check_issued_under(entry: VerifierKey, issued_instant: datetime) -> None:
    """Refuse an ``iat`` outside the signing window of the key that verified it."""
    if entry.is_retiring:
        # The retiring key stops issuing at ``sign_until`` exactly.  Request
        # skew does not extend it: a capability minted after cutover was minted
        # by a signer that should already have stopped.
        assert entry.sign_until is not None
        if issued_instant > entry.sign_until:
            raise CapabilityRejected("capability was issued after the retiring key's cutover")
    elif issued_instant < entry.sign_from:
        raise CapabilityRejected("capability was issued before its key became current")


def _split(compact: str | None) -> tuple[str, str, str]:
    if not isinstance(compact, str) or not compact:
        raise CapabilityRejected("capability is absent")
    if not compact.isascii():
        raise CapabilityRejected("capability is not a compact JWS")
    segments = compact.split(".")
    if len(segments) != 3 or not all(segments):
        raise CapabilityRejected("capability is not a compact JWS")
    return segments[0], segments[1], segments[2]
