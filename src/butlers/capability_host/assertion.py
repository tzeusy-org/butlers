"""The Worker-signed per-call assertion (RFC 0039 D7), verified through the probe codec.

Verification follows D7 steps 3 to 7 in order. Every failure raises :class:`AuthRefused`
with a fixed cause token; the caller turns every cause into the same ``-32001`` refusal and
sends the cause only to the audit log and metrics.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import rfc8785

from butlers.core.runtime_probe_control.capability import (
    ASSERTION_HEADER,
    CapabilityRejected,
    b64u_decode,
    b64u_encode,
    check_issued_under,
    strict_object,
    verify_signature,
)
from butlers.core.runtime_probe_control.keys import VerifierKeyring

FIELDS = frozenset(
    {"v", "iss", "aud", "sub", "cid", "mth", "tool", "path", "bsha", "jti", "iat", "exp"}
)
_STRINGS = ("iss", "aud", "sub", "cid", "mth", "tool", "path", "bsha", "jti")
CLOCK_SKEW = 5
MAX_LIFETIME = 60
MAX_SEGMENT_CHARS = 8192
_JTI = re.compile(r"[A-Za-z0-9_-]{22}\Z")
_CODEC_CAUSES = {
    "capability key id is not an acceptable deployment key": "kid",
    "capability signature does not verify": "signature",
}


class AuthRefused(Exception):
    """An authorization refusal; ``cause`` is a fixed token for audit and metrics only."""

    def __init__(self, cause: str):
        super().__init__(cause)
        self.cause = cause


@dataclass(frozen=True)
class Assertion:
    kid: str
    sub: str
    cid: str
    jti: str
    iat: int
    exp: int

    def __repr__(self) -> str:  # pragma: no cover - trivial redaction
        return "Assertion(<redacted>)"


@dataclass(frozen=True)
class AssertionPolicy:
    issuer: str
    audience: str
    allowed_subs: frozenset[str]
    client_domains: frozenset[str]
    registered_clients: frozenset[str]
    process_start: float


class ReplaySet:
    """Seen ``jti`` values, each kept until its ``exp`` (D7 step 6). One replica only."""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def __contains__(self, jti: object) -> bool:
        return jti in self._seen

    def add(self, jti: str, exp: int, now: float) -> None:
        if len(self._seen) >= 1024:
            self._seen = {j: e for j, e in self._seen.items() if e >= now}
        self._seen[jti] = exp

    def __len__(self) -> int:
        return len(self._seen)


def client_allowed(cid: str, policy: AssertionPolicy) -> bool:
    """D7 step 4: a known-domain https CIMD URL or an exactly registered DCR id."""
    if not cid:
        return False
    if cid in policy.registered_clients:
        return True
    try:
        parts = urlsplit(cid)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or "@" in parts.netloc or port is not None:
        return False
    return (parts.hostname or "") in policy.client_domains


def client_label(cid: str | None) -> str | None:
    """The audit label of a client: its CIMD host, or a short digest of a DCR id."""
    if not cid:
        return None
    try:
        host = urlsplit(cid).hostname if cid.startswith("https://") else None
    except ValueError:
        host = None
    return host or "dcr:" + hashlib.sha256(cid.encode()).hexdigest()[:12]


def body_digest(body: bytes) -> str:
    return b64u_encode(hashlib.sha256(body).digest())


def _jti(value: str) -> bool:
    """b64u of exactly 16 bytes, canonically spelled."""
    if not _JTI.match(value):
        return False
    try:
        return len(b64u_decode(value, reason="jti")) == 16
    except CapabilityRejected:
        return False


def _int(value: Any) -> int:
    if type(value) is not int:
        raise AuthRefused("claims")
    return value


def verify_assertion(
    compact: str,
    *,
    keyring: VerifierKeyring | None,
    policy: AssertionPolicy,
    replay: ReplaySet,
    now: float,
    body: bytes,
    method: str,
    tool: str,
    query: str,
) -> Assertion:
    """Verify one assertion against the received request, then record its ``jti``."""
    if keyring is None:
        raise AuthRefused("keyring")
    instant = datetime.fromtimestamp(now, UTC)
    try:
        signed = verify_signature(
            compact,
            keyring=keyring,
            now=instant,
            profile=ASSERTION_HEADER,
            max_segment_chars=MAX_SEGMENT_CHARS,
        )
        claims = strict_object(signed.payload, exact_fields=FIELDS, reason="claims")
    except CapabilityRejected as exc:
        raise AuthRefused(_CODEC_CAUSES.get(str(exc), "malformed")) from None
    try:
        canonical = rfc8785.dumps(claims)
    except Exception:
        raise AuthRefused("noncanonical") from None
    if canonical != signed.payload:
        raise AuthRefused("noncanonical")
    if any(not isinstance(claims[name], str) for name in _STRINGS) or _int(claims["v"]) != 1:
        raise AuthRefused("claims")
    iat, exp = _int(claims["iat"]), _int(claims["exp"])
    if claims["iss"] != policy.issuer:
        raise AuthRefused("issuer")
    if claims["aud"] != policy.audience:
        raise AuthRefused("audience")
    if claims["sub"] not in policy.allowed_subs:
        raise AuthRefused("sub")
    if not client_allowed(claims["cid"], policy):
        raise AuthRefused("client")
    if iat > now + CLOCK_SKEW:
        raise AuthRefused("future")
    if exp < now:
        raise AuthRefused("expired")
    if not 0 < exp - iat <= MAX_LIFETIME:
        raise AuthRefused("lifetime")
    if iat < policy.process_start - CLOCK_SKEW:
        raise AuthRefused("restart")
    try:
        check_issued_under(signed.entry, datetime.fromtimestamp(iat, UTC))
    except CapabilityRejected:
        raise AuthRefused("kid") from None
    if not _jti(claims["jti"]):
        raise AuthRefused("claims")
    if claims["jti"] in replay:
        raise AuthRefused("replay")
    if (
        claims["bsha"] != body_digest(body)
        or claims["mth"] != method
        or claims["tool"] != tool
        or claims["path"] != query
    ):
        raise AuthRefused("binding")
    replay.add(claims["jti"], exp, now)
    return Assertion(signed.kid, claims["sub"], claims["cid"], claims["jti"], iat, exp)
