"""The dashboard service-call capability (RFC 0038 D7, mechanism A).

A dashboard call to a model-reachable tool does not go to the butler directly
once ``BUTLERS_SERVICE_PLANE_AUTH`` is on. The dashboard signs one capability
with the runtime-probe control signer it already holds, under a separate
audience, and Switchboard's service listener verifies it before it routes the
call. The capability binds:

* ``caller`` --- always ``dashboard``; nothing else signs here,
* ``butler`` and ``tool`` --- the one target the call may reach,
* ``args_digest`` --- the RFC 8785 digest of the exact arguments,

inside the shared envelope (``aud``, ``iat``, ``exp`` at most 60 s later, and a
256-bit ``nonce`` Switchboard consumes through a durable receipt). A capability
for one call cannot carry another call, a runtime-probe capability is not a
service call, and a service call is not a runtime-probe capability.

It lives in this package because verification goes through the package: the
route reaches the deployment keyring only through
:func:`verify_deployed_service_call`. Which tools take this path is
``butlers.core.service_plane.dashboard_call.routes_through_control_plane``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from butlers.core.runtime_probe_control.capability import (
    DEFAULT_LIFETIME,
    CapabilityRejected,
    sign_claims,
    verify_claims,
)
from butlers.core.runtime_probe_control.keys import (
    SERVICE_CALL_AUDIENCE,
    SignerKey,
    VerifierKeyring,
    VerifierSnapshot,
    verifier_snapshot,
)

#: The one caller class. A scheduler or a butler never signs a service call.
SERVICE_CALL_CALLER: Final = "dashboard"

_CLAIM_FIELDS: Final = frozenset({"caller", "butler", "tool", "args_digest"})
_BUTLER_PATTERN: Final = re.compile(r"\A[a-z][a-z0-9_-]{0,63}\Z")
_TOOL_PATTERN: Final = re.compile(r"\A[A-Za-z0-9_.-]{1,128}\Z")
_DIGEST_PATTERN: Final = re.compile(r"\A[0-9a-f]{64}\Z")
_DIGEST_DOMAIN: Final = b"butlers:service-call:v1\0"
_MAX_ARGS_BYTES: Final = 1 << 20


@dataclass(frozen=True, slots=True)
class VerifiedServiceCall:
    """What survived verification; ``repr`` is redacted for the nonce."""

    kid: str
    butler: str
    tool: str
    args_digest: str
    issued_at: datetime
    expires_at: datetime
    nonce: bytes

    def __repr__(self) -> str:  # pragma: no cover - trivial redaction
        return "VerifiedServiceCall(<redacted>)"


def _closed_json(value: Any) -> bool:
    if value is None or type(value) in (str, int, bool):
        return True
    if type(value) is float:
        return value == value and value not in (float("inf"), float("-inf"))
    if type(value) is list:
        return all(_closed_json(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _closed_json(item) for key, item in value.items())
    return False


def args_digest(args: Any) -> str:
    """The RFC 8785 digest of the exact arguments, domain-separated.

    Raises ``ValueError`` for anything that is not a closed JSON object: the
    digest must mean the same bytes on both ends of the wire.
    """
    import rfc8785

    if type(args) is not dict or not _closed_json(args):
        raise ValueError("service-call arguments are not a closed JSON object")
    encoded = rfc8785.dumps(args)
    if len(encoded) > _MAX_ARGS_BYTES:
        raise ValueError("service-call arguments are too large")
    return hashlib.sha256(_DIGEST_DOMAIN + encoded).hexdigest()


def _check_target(butler: Any, tool: Any) -> tuple[str, str]:
    if not isinstance(butler, str) or _BUTLER_PATTERN.match(butler) is None:
        raise CapabilityRejected("service call names no valid butler")
    if not isinstance(tool, str) or _TOOL_PATTERN.match(tool) is None:
        raise CapabilityRejected("service call names no valid tool")
    return butler, tool


def sign_service_call(
    signer: SignerKey,
    *,
    butler: str,
    tool: str,
    args: dict[str, Any],
    now: datetime,
    lifetime: timedelta = DEFAULT_LIFETIME,
) -> str:
    """Mint one service-call capability for exactly this call, or refuse."""
    _check_target(butler, tool)
    return sign_claims(
        signer,
        audience=SERVICE_CALL_AUDIENCE,
        claims={
            "caller": SERVICE_CALL_CALLER,
            "butler": butler,
            "tool": tool,
            "args_digest": args_digest(args),
        },
        now=now,
        lifetime=lifetime,
    )


def verify_service_call(
    compact: str | None, *, keyring: VerifierKeyring, now: datetime
) -> VerifiedServiceCall:
    """Verify one service-call capability; every failure is ``CapabilityRejected``."""
    signed = verify_claims(
        compact,
        keyring=keyring,
        now=now,
        audience=SERVICE_CALL_AUDIENCE,
        claim_fields=_CLAIM_FIELDS,
    )
    claims = signed.claims
    if claims["caller"] != SERVICE_CALL_CALLER:
        raise CapabilityRejected("service call caller is not the dashboard")
    butler, tool = _check_target(claims["butler"], claims["tool"])
    digest = claims["args_digest"]
    if not isinstance(digest, str) or _DIGEST_PATTERN.match(digest) is None:
        raise CapabilityRejected("service call argument digest is not a SHA-256 hex digest")
    return VerifiedServiceCall(
        kid=signed.kid,
        butler=butler,
        tool=tool,
        args_digest=digest,
        issued_at=signed.issued_at,
        expires_at=signed.expires_at,
        nonce=signed.nonce,
    )


class ServiceCallVerifierUnavailable(Exception):
    """No verifier keyring is mounted here; the caller may retry elsewhere or later."""


def verify_deployed_service_call(
    compact: str | None,
    *,
    now: datetime,
    verifier: Callable[[], VerifierSnapshot] = verifier_snapshot,
) -> VerifiedServiceCall:
    """Verify against this process's frozen deployment keyring.

    Raises :class:`ServiceCallVerifierUnavailable` before reading the
    capability when no keyring is mounted, so a mountless process is not an
    oracle for which capabilities would verify.
    """
    snapshot = verifier()
    if not snapshot.available or snapshot.keyring is None:
        raise ServiceCallVerifierUnavailable("no verifier keyring is mounted")
    return verify_service_call(compact, keyring=snapshot.keyring, now=now)
