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

A mounted signer is necessary but not sufficient.  REQ-core-credentials-002
lets the canonical launcher start Dashboard before all-butlers, so this client
also has to answer a positive question before it signs: *has Switchboard said
it can verify my key?*  It asks that at
``GET /_control/runtime-probe/v1/readiness?kid=<kid>`` and requires the exact
``200``/``{"status":"ready"}`` answer.  Anything else --- a not-ready body, an
absent route, an unreachable host --- is an ordinary startup state, not a
signal to sign anyway.

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
from butlers.core.runtime_probe_control.endpoint import (
    CONTROL_PATH,
    READINESS_KEY_ID_PARAM,
    READINESS_PATH,
    READY_STATUS,
)
from butlers.core.runtime_probe_control.keys import SignerSnapshot, signer_snapshot

logger = logging.getLogger(__name__)

#: Comfortably past Switchboard's own 30-second probe deadline, so a probe that
#: times out server-side is reported as ``504`` rather than as a client stall.
DEFAULT_REQUEST_TIMEOUT_S: Final = 45.0

#: The readiness gate answers from a frozen in-process snapshot, so it is fast
#: or it is broken.  A short bound of its own keeps a stalled gate from eating
#: the probe budget it is supposed to protect.
DEFAULT_READINESS_TIMEOUT_S: Final = 5.0


class _ReadinessAccessLogFilter(logging.Filter):
    """Drop httpx's access-log line for readiness requests.

    httpx logs every request at ``INFO`` on its own logger, rendering the full
    URL --- which for this one route carries a ``kid``.  A key id is not key
    material, which is exactly why the query parameter is permitted at all, but
    the rule that nothing from this plane reaches a log stays absolute rather
    than depending on a deployment's log level.

    The predicate names the private readiness path, so nothing else a caller
    might be debugging can match it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return READINESS_PATH not in record.getMessage()


# Installed once, at import of the only module that makes the request.  A
# filter on the ``httpx`` logger runs before propagation, so no handler
# anywhere sees the record.
logging.getLogger("httpx").addFilter(_ReadinessAccessLogFilter())

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
        readiness_timeout: float = DEFAULT_READINESS_TIMEOUT_S,
    ) -> None:
        if caller not in CALLERS:
            raise CapabilityRejected("capability caller is not a registered control caller")
        root = base_url.rstrip("/")
        self._url = f"{root}{CONTROL_PATH}"
        self._readiness_url = f"{root}{READINESS_PATH}"
        self._caller = caller
        self._signer = signer
        self._transport = transport
        self._timeout = timeout
        self._readiness_timeout = readiness_timeout
        self._ready = False

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

        if not await self._verifier_is_ready(snapshot.signer.kid):
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

    async def _verifier_is_ready(self, kid: str) -> bool:
        """Whether Switchboard has confirmed it can verify *kid* --- asked once.

        A first "no" is an ordinary startup state, not a failure: Dashboard may
        legitimately be up before all-butlers, so the next probe asks again.  A
        "yes" latches, because both sides freeze their key snapshots at startup
        and rotation is restart-driven --- the answer cannot change under a
        running process, and a restart resets this latch with it.
        """
        if self._ready:
            return True

        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._readiness_timeout
            ) as client:
                response = await client.get(
                    self._readiness_url, params={READINESS_KEY_ID_PARAM: kid}
                )
        except httpx.HTTPError:
            # Logged without the exception object: httpx renders the request
            # URL, and this one carries a key id.  A key id is not key material
            # --- it is why the query parameter is allowed at all --- but the
            # rule that nothing from this plane reaches a log stays absolute.
            logger.warning("runtime-probe control readiness could not be confirmed")
            return False

        if response.status_code != 200 or _readiness_payload(response) != {"status": READY_STATUS}:
            logger.warning("runtime-probe control is not ready to verify the configured signer")
            return False

        self._ready = True
        return True

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


def _readiness_payload(response: httpx.Response) -> Any:
    """The parsed readiness body, or ``None`` if it was not JSON at all."""
    try:
        return response.json()
    except ValueError:
        return None


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
