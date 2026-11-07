"""Domain-side verification of ``X-Butlers-Assertion`` (RFC 0038 D7, bu-pkuny2).

``AssertionVerificationMiddleware`` is the outermost FastMCP middleware of a
domain daemon. It sees each ``tools/call`` with its raw wire arguments, before
argument coercion and before any handler, and checks the broker's assertion
with ``assertions.verify_assertion``: signature under a keyring ``kid``,
``aud`` equal to this domain, ``exp``, capability equal to the called tool,
``args_digest`` over the raw arguments, and a ``call_id`` not seen within twice
the assertion lifetime. The verified lineage is then bound
(``core.goal_lineage``) for exactly that call, with an empty D6 stamp account
(``core.stamp_account``) that the call's store readers fill and that returns
to the broker in the result's ``_meta``, bound to the verified call.

Replay across a restart: the seen-call set lives in memory only, so an
assertion issued before this process started (``iat = exp - 60 s`` earlier
than the start second) is refused. The broker's D12 effect idempotency makes
the one retry that costs safe (decision recorded on bu-pkuny2).

Keys come from a ``version: 1`` verifier keyring parsed by the runtime-probe
keyring codec (one current entry, at most one retiring), mounted at its own
path: no other keyring is accepted for broker assertions. A missing or invalid
keyring leaves the verifier keyless, and a keyless verifier refuses every
call; there is no unsigned or shared-secret fallback.

Everything here is dark: ``attach_domain_verification`` installs nothing until
the default-off ``DomainVerificationSwitch`` is on.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware

from butlers.core.goal_lineage import VerifiedLineage, lineage_scope, set_enforcement
from butlers.core.runtime_probe_control.keys import (
    RuntimeProbeControlKeyError,
    VerifierKeyring,
    load_verifier_keyring,
)
from butlers.core.stamp_account import account_scope, attach_report

from .assertions import ASSERTION_HEADER, LIFETIME_SECONDS, verify_assertion
from .contracts import BrokerRefused, Reason

#: The broker-assertion verifier keyring (non-secret), distinct from every other keyring.
VERIFIER_KEYRING_PATH = Path("/run/secrets/capability_broker_assertion_verifiers")

#: Refusal text a caller sees; never says which check failed.
REFUSED = "assertion_refused"


@dataclass(frozen=True, slots=True)
class DomainVerificationSwitch:
    """The M6 switch for domain verification. Off until the cutover release."""

    enabled: bool = False


class DomainVerifier:
    """Per-domain assertion verifier with an in-memory replay set."""

    def __init__(
        self,
        audience: str,
        keyring: VerifierKeyring | None,
        *,
        clock: Callable[[], float] = time.time,
        started_at: int | None = None,
    ) -> None:
        self.audience = audience
        self._keyring = keyring
        self._clock = clock
        self.started_at = int(clock()) if started_at is None else started_at
        self._seen: dict[UUID, int] = {}

    def _keys(self, now: int) -> dict[str, Ed25519PublicKey]:
        if self._keyring is None:
            return {}
        instant = datetime.fromtimestamp(now, UTC)
        keys = {}
        for entry in (self._keyring.current, self._keyring.retiring):
            if entry is not None and entry.may_accept_at(instant):
                keys[entry.kid] = Ed25519PublicKey.from_public_bytes(entry.public_key)
        return keys

    def _claim(self, now: int) -> Callable[[UUID, int], bool]:
        def claim(call_id: UUID, exp: int) -> bool:
            if exp - LIFETIME_SECONDS < self.started_at:
                return False  # Issued before this process: its seen set is gone.
            horizon = now - 2 * LIFETIME_SECONDS
            for seen, seen_exp in list(self._seen.items()):
                if seen_exp < horizon:
                    del self._seen[seen]
            if call_id in self._seen:
                return False
            self._seen[call_id] = exp
            return True

        return claim

    def verify(self, assertion: str, tool_name: str, arguments: dict[str, Any]) -> VerifiedLineage:
        """Verify one assertion for this exact call, or raise ``BrokerRefused``."""
        now = int(self._clock())
        keys = self._keys(now)
        if not keys:
            raise BrokerRefused(Reason.AUTHENTICATION)
        payload = verify_assertion(
            assertion,
            keys=keys,
            audience=self.audience,
            capability=tool_name,
            arguments=arguments,
            now=now,
            claim_call_id=self._claim(now),
        )
        return VerifiedLineage.from_payload(payload)


def _assertion_headers() -> list[str]:
    """Every ``X-Butlers-Assertion`` value on the current HTTP request."""
    from fastmcp.server.dependencies import get_http_request

    try:
        request = get_http_request()
    except RuntimeError:
        return []
    return list(request.headers.getlist(ASSERTION_HEADER))


class AssertionVerificationMiddleware(Middleware):
    """Verify before coercion; bind lineage for exactly one call."""

    def __init__(self, verifier: DomainVerifier, *, service_tools: Iterable[str] = ()) -> None:
        self.verifier = verifier
        # Service-plane tools are not LLM-presentable and keep their callers (D7).
        self.service_tools = frozenset(service_tools)

    async def on_call_tool(self, context: Any, call_next: Any) -> Any:
        name = context.message.name
        if name in self.service_tools:
            with lineage_scope(None):
                return await call_next(context)
        values = _assertion_headers()
        try:
            if len(values) != 1:
                raise BrokerRefused(Reason.AUTHENTICATION)
            lineage = self.verifier.verify(values[0], name, dict(context.message.arguments or {}))
        except BrokerRefused:
            raise ToolError(REFUSED) from None
        with lineage_scope(lineage), account_scope() as account:
            result = await call_next(context)
        # D6: the readers' stamp account, bound to this verified call.
        return attach_report(result, account, lineage)


def attach_domain_verification(
    mcp: Any,
    switch: DomainVerificationSwitch,
    *,
    audience: str,
    keyring_path: Path = VERIFIER_KEYRING_PATH,
    service_tools: Iterable[str] = (),
    clock: Callable[[], float] = time.time,
) -> DomainVerifier | None:
    """Install verification as the outermost middleware, only when switched on."""
    if switch.enabled is not True:
        set_enforcement(False)
        return None
    try:
        keyring: VerifierKeyring | None = load_verifier_keyring(keyring_path)
    except RuntimeProbeControlKeyError:
        keyring = None  # Keyless: every model-reachable call is refused.
    verifier = DomainVerifier(audience, keyring, clock=clock)
    mcp.middleware.insert(0, AssertionVerificationMiddleware(verifier, service_tools=service_tools))
    set_enforcement(True)
    return verifier
