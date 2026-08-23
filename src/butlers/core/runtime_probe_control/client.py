"""The dedicated Dashboard/Scheduler client for the runtime-probe control plane.

This is the only thing in the system that signs a runtime-probe capability, and
it is deliberately not general: it takes a catalog entry id and nothing else.
There is no way to ask it for a different prompt, a different model, different
runtime arguments, or a different audience, because those are the parameters an
attacker who reached the dashboard would want.

Fail-closed means what it says.  With no signer mounted --- which is the state
of every deployment in this phase, since no production signer mount exists yet
--- :meth:`RuntimeProbeControlClient.probe` signs nothing, opens no connection,
and returns :attr:`~ProbeStatus.UNAVAILABLE`.  It does not fall back to a
bearer token, a shared secret, or a local adapter.

Every outcome distinction survives the round trip.  A busy Switchboard, an
expired capability, and a model that genuinely failed are three different
things, and collapsing them would show an outage to the operator as a broken
model --- or worse, let an outage write a failure onto the catalog row.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

import httpx

from butlers.core.runtime_probe_control.capability import (
    CALLERS,
    CapabilityRejected,
    sign_capability,
)
from butlers.core.runtime_probe_control.coordinator import ProbeResult, ProbeStatus
from butlers.core.runtime_probe_control.endpoint import CONTROL_PATH
from butlers.core.runtime_probe_control.keys import SignerSnapshot, signer_snapshot

logger = logging.getLogger(__name__)

#: Comfortably past Switchboard's own 30-second probe deadline, so a probe that
#: times out server-side is reported as ``504`` rather than as a client stall.
DEFAULT_REQUEST_TIMEOUT_S: Final = 45.0

#: The statuses a caller may receive without a probe having run.  Anything
#: outside this map --- including a 500 --- is treated as unavailable rather
#: than guessed at.
_STATUS_BY_CODE: Final[dict[int, ProbeStatus]] = {
    401: ProbeStatus.UNAUTHORIZED,
    409: ProbeStatus.REPLAY,
    429: ProbeStatus.BUSY,
    503: ProbeStatus.UNAVAILABLE,
    504: ProbeStatus.TIMEOUT,
}


class RuntimeProbeControlClient:
    """Sign one capability and request one bounded probe from Switchboard."""

    def __init__(
        self,
        base_url: str,
        *,
        caller: str,
        signer: Callable[[], SignerSnapshot] = signer_snapshot,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_REQUEST_TIMEOUT_S,
    ) -> None:
        if caller not in CALLERS:
            raise CapabilityRejected("capability caller is not a registered control caller")
        self._url = f"{base_url.rstrip('/')}{CONTROL_PATH}"
        self._caller = caller
        self._signer = signer
        self._transport = transport
        self._timeout = timeout

    async def probe(self, catalog_entry_id: UUID, *, now: datetime | None = None) -> ProbeResult:
        """Request a probe of *catalog_entry_id*, or report why it could not be."""
        instant = now or datetime.now(UTC)

        snapshot = self._signer()
        if not snapshot.may_issue_at(instant) or snapshot.signer is None:
            logger.warning(
                "runtime-probe control client is unavailable: %s",
                snapshot.unavailable_reason or "the signing key may not issue at this instant",
            )
            return ProbeResult(ProbeStatus.UNAVAILABLE)

        try:
            compact = sign_capability(
                snapshot.signer,
                caller=self._caller,
                catalog_entry_id=catalog_entry_id,
                now=instant,
            )
        except CapabilityRejected as exc:
            logger.warning("runtime-probe control client could not sign a capability: %s", exc)
            return ProbeResult(ProbeStatus.UNAVAILABLE)

        return await self._request(compact)

    async def _request(self, compact: str) -> ProbeResult:
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout
            ) as client:
                response = await client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {compact}"},
                    content=b"",
                )
        except httpx.TimeoutException:
            # Logged without the exception object: httpx renders the request
            # URL, and while this URL carries no capability, keeping the rule
            # absolute is cheaper than auditing every httpx error string.
            logger.warning("runtime-probe control request timed out")
            return ProbeResult(ProbeStatus.TIMEOUT)
        except httpx.HTTPError:
            logger.warning("runtime-probe control request could not reach Switchboard")
            return ProbeResult(ProbeStatus.UNAVAILABLE)

        return _interpret(response)


def _interpret(response: httpx.Response) -> ProbeResult:
    """Map a control response onto the typed vocabulary, or refuse to guess."""
    known = _STATUS_BY_CODE.get(response.status_code)
    if known is not None:
        return ProbeResult(known)
    if response.status_code != 200:
        logger.warning(
            "runtime-probe control returned an undescribed status (%d)", response.status_code
        )
        return ProbeResult(ProbeStatus.UNAVAILABLE)

    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or payload.get("status") != ProbeStatus.COMPLETED.value:
        logger.warning("runtime-probe control returned an undescribed completion payload")
        return ProbeResult(ProbeStatus.UNAVAILABLE)

    ok = payload.get("ok")
    latency_ms = payload.get("latency_ms")
    if not isinstance(ok, bool):
        logger.warning("runtime-probe control completion carried no probe verdict")
        return ProbeResult(ProbeStatus.UNAVAILABLE)
    return ProbeResult(
        ProbeStatus.COMPLETED,
        ok=ok,
        latency_ms=latency_ms if isinstance(latency_ms, int) else None,
    )
