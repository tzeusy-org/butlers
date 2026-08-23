"""The Switchboard-owned runtime-probe coordinator.

A probe answers one question: *can this catalog entry's runtime actually be
invoked right now, in the environment a butler session would get?*  It answers
it by doing the real thing --- resolving the catalog entry, building the same
adapter with the same authority, and running a one-turn completion inside the
shared runtime home --- with everything that makes a session a session removed:
no domain MCP tools, no routed dispatch provenance, no session log.

Two rules shape the code more than anything else.

*The receipt comes first.*  REQ-database-security-008 requires the SHA-256
nonce receipt to commit before catalog resolution, runtime launch, or
verification persistence.  Admission control therefore runs *after* the
receipt, not before: if a request that lost the busy gate could keep its nonce,
one capability could be retried until a slot opened, and the capability would
no longer be single-use.  Losing a race costs the caller a capability, which is
the correct price.

*Only a probe that ran says anything about the model.*  Exactly one outcome ---
:attr:`ProbeStatus.COMPLETED` --- writes verification evidence.  Unauthorized,
replay, busy, unavailable, and timeout leave the catalog's verification history
exactly as it was.  A timeout is the interesting one: it is tempting to record
it as a failure, but a probe the coordinator abandoned at its own deadline is
evidence about the coordinator, and writing it would let a slow afternoon evict
a healthy model from routing.

Nothing here logs, returns, or persists capability material.  The only
capability-derived value that leaves this module is the receipt digest.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from butlers.core.runtime_probe_control.capability import (
    CapabilityRejected,
    VerifiedCapability,
    verify_capability,
)
from butlers.core.runtime_probe_control.keys import VerifierSnapshot, verifier_snapshot
from butlers.core.runtime_probe_control.receipts import RuntimeProbeControlReceipts
from butlers.core.runtime_probe_control.verification import (
    VERIFY_ERROR_TRUNCATE_LEN,
    RuntimeProbeVerificationPersistence,
)

logger = logging.getLogger(__name__)

#: The same one-token exchange the dashboard's verify-all sweep issues, so a
#: probe result and a sweep result mean the same thing.
PROBE_PROMPT: Final = "Reply with exactly: OK"
PROBE_SYSTEM_PROMPT: Final = "You are a test assistant. Reply concisely."

PROBE_TIMEOUT_S: Final = 30
GLOBAL_CONCURRENCY: Final = 8
PER_ENTRY_CONCURRENCY: Final = 1

_CATALOG_SQL: Final = """
    SELECT id, runtime_type, model_id, extra_args
    FROM public.model_catalog
    WHERE id = $1
"""


class ProbeStatus(StrEnum):
    """The closed outcome vocabulary of REQ-dashboard-model-settings-001.

    Each value maps to exactly one HTTP status in :data:`HTTP_STATUS`.  The
    distinctions matter to the caller: a dashboard that collapsed ``BUSY`` or
    ``TIMEOUT`` into "the provider failed" would show an outage as a broken
    model.
    """

    COMPLETED = "completed"
    UNAUTHORIZED = "unauthorized"
    REPLAY = "replay"
    BUSY = "busy"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"


HTTP_STATUS: Final[dict[ProbeStatus, int]] = {
    ProbeStatus.COMPLETED: 200,
    ProbeStatus.UNAUTHORIZED: 401,
    ProbeStatus.REPLAY: 409,
    ProbeStatus.BUSY: 429,
    ProbeStatus.UNAVAILABLE: 503,
    ProbeStatus.TIMEOUT: 504,
}


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """What a probe attempt produced.

    ``ok`` is set only when a probe actually ran: every other status leaves it
    ``None`` rather than ``False``, so a caller cannot mistake an outage for a
    verified failure.  Provider error text is deliberately absent --- it is
    persisted to the catalog row, but it never travels back over the control
    plane, where it would be a raw provider dump in an API payload.
    """

    status: ProbeStatus
    ok: bool | None = None
    latency_ms: int | None = None

    @property
    def http_status(self) -> int:
        return HTTP_STATUS[self.status]


def _coerce_extra_args(raw: Any) -> list[str]:
    """Coerce an asyncpg JSONB column to the runtime-argument list."""
    if isinstance(raw, list):
        return [str(value) for value in raw]
    if isinstance(raw, str):
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(value) for value in parsed]
    return []


async def build_probe_adapter(
    pool: Any,
    runtime_type: str,
    model_id: str,
    *,
    codex_auth_authority: Any = None,
) -> Any:
    """Build the adapter a daemon invocation of this entry would build.

    Deliberately identical in shape to the dashboard's verification adapter
    construction: Codex receives only the explicitly supplied authority, and
    every other runtime receives resolved provider configuration.  Imported
    lazily because the runtime adapters pull in the whole spawner surface,
    which the control plane's parsing layers must not depend on.
    """
    from butlers.core.runtimes.base import get_adapter
    from butlers.core.spawner import resolve_provider_config

    adapter_cls = get_adapter(runtime_type)
    if runtime_type == "codex":
        return adapter_cls(credential_store=codex_auth_authority)
    provider_config = await resolve_provider_config(pool, model_id)
    try:
        return adapter_cls(provider_config=provider_config)
    except TypeError:
        return adapter_cls()


class RuntimeProbeCoordinator:
    """Verify a capability, then run at most one bounded probe for its entry."""

    def __init__(
        self,
        pool: Any,
        *,
        verifier: Callable[[], VerifierSnapshot] = verifier_snapshot,
        receipts: Any = None,
        persistence: Any = None,
        adapter_factory: Callable[..., Any] = build_probe_adapter,
        codex_auth_authority: Any = None,
    ) -> None:
        self._pool = pool
        self._verifier = verifier
        self._receipts = receipts if receipts is not None else RuntimeProbeControlReceipts(pool)
        self._persistence = (
            persistence if persistence is not None else RuntimeProbeVerificationPersistence(pool)
        )
        self._adapter_factory = adapter_factory
        self._codex_authority = codex_auth_authority
        self._timeout_s: float = PROBE_TIMEOUT_S
        self._in_flight: set[UUID] = set()
        self._global_in_flight = 0

    async def run(self, compact: str | None, *, now: datetime | None = None) -> ProbeResult:
        """Run one probe request end to end.

        Returns a status for every path; raises only on a genuine programming
        error.  A caller may log the result verbatim.
        """
        instant = now or datetime.now(UTC)

        snapshot = self._verifier()
        if not snapshot.available or snapshot.keyring is None:
            # No verifier mount: the control plane is not deployed here, and
            # refusing before verification means a mountless daemon cannot even
            # be used as an oracle for which capabilities would have verified.
            logger.warning("runtime-probe control request refused: no verifier keyring is mounted")
            return ProbeResult(ProbeStatus.UNAVAILABLE)

        try:
            capability = verify_capability(compact, keyring=snapshot.keyring, now=instant)
        except CapabilityRejected as exc:
            # ``exc`` carries a fixed reason string and never quotes the
            # capability, so it is safe to log at this level of detail.
            logger.warning("runtime-probe control capability rejected: %s", exc)
            return ProbeResult(ProbeStatus.UNAUTHORIZED)

        claimed = await self._receipts.claim(
            nonce=capability.nonce,
            kid=capability.kid,
            expires_at=capability.expires_at,
        )
        if not claimed:
            logger.warning(
                "runtime-probe control capability was already consumed (caller=%s)",
                capability.caller,
            )
            return ProbeResult(ProbeStatus.REPLAY)

        return await self._probe(capability)

    async def _probe(self, capability: VerifiedCapability) -> ProbeResult:
        entry_id = capability.catalog_entry_id
        async with self._admit(entry_id) as admitted:
            if not admitted:
                logger.info("runtime-probe control request shed: probe capacity is saturated")
                return ProbeResult(ProbeStatus.BUSY)

            row = await self._pool.fetchrow(_CATALOG_SQL, entry_id)
            if row is None:
                logger.warning("runtime-probe control request named an unknown catalog entry")
                return ProbeResult(ProbeStatus.UNAVAILABLE)

            runtime_type = row["runtime_type"]
            model_id = row["model_id"]
            if runtime_type == "codex" and not getattr(
                self._codex_authority, "has_system_global_authority", False
            ):
                # Absent authority is missing infrastructure, not a broken
                # model; persisting a failure here would drop Codex out of
                # routing because a mount was late.
                logger.warning(
                    "runtime-probe control skipped a Codex entry: no system-global authority"
                )
                return ProbeResult(ProbeStatus.UNAVAILABLE)

            return await self._launch(
                entry_id,
                runtime_type=runtime_type,
                model_id=model_id,
                runtime_args=_coerce_extra_args(row["extra_args"]),
            )

    async def _launch(
        self,
        entry_id: UUID,
        *,
        runtime_type: str,
        model_id: str,
        runtime_args: list[str],
    ) -> ProbeResult:
        started = time.monotonic()
        try:
            adapter = await self._adapter_factory(
                self._pool,
                runtime_type,
                model_id,
                codex_auth_authority=self._codex_authority,
            )
        except Exception:
            logger.exception("runtime-probe control could not build a runtime adapter")
            return ProbeResult(ProbeStatus.UNAVAILABLE)

        ok = False
        error: str | None = None
        try:
            async with asyncio.timeout(self._timeout_s):
                # ``model`` carries the catalog's canonical identifier; each
                # adapter owns its own canonical-to-execution mapping, so the
                # probe must not pre-map it here.  ``env`` is the daemon's own
                # environment, which is how the adapter resolves the shared
                # runtime home.
                result_text, _, _ = await adapter.invoke(
                    prompt=PROBE_PROMPT,
                    system_prompt=PROBE_SYSTEM_PROMPT,
                    mcp_servers={},
                    env=dict(os.environ),
                    max_turns=1,
                    model=model_id,
                    runtime_args=runtime_args or None,
                    timeout=PROBE_TIMEOUT_S,
                )
        except TimeoutError:
            logger.warning("runtime-probe control probe exceeded its deadline")
            return ProbeResult(ProbeStatus.TIMEOUT)
        except Exception as exc:
            # A provider that refuses or errors is a genuine verification
            # failure, so it is recorded --- but the text stays out of the
            # response and only reaches the catalog row.
            error = str(exc)[:VERIFY_ERROR_TRUNCATE_LEN]
        else:
            ok = bool(result_text and result_text.strip())
            if not ok:
                error = "verification returned an empty response"

        latency_ms = int((time.monotonic() - started) * 1000)
        await self._persistence.record(
            catalog_entry_id=entry_id,
            ok=ok,
            latency_ms=latency_ms,
            error=error,
        )
        return ProbeResult(ProbeStatus.COMPLETED, ok=ok, latency_ms=latency_ms)

    @contextlib.asynccontextmanager
    async def _admit(self, entry_id: UUID) -> AsyncIterator[bool]:
        """Reserve a global slot and this entry's single slot, or refuse.

        Both reservations fail fast rather than queueing: a queued probe would
        outlive the one-minute capability that authorised it.  The check and
        the reservation contain no ``await``, so the event loop cannot
        interleave two callers between them.
        """
        # ``_in_flight`` is a set, so an entry is either absent or present
        # once --- PER_ENTRY_CONCURRENCY == 1 expressed in the data structure
        # rather than re-checked against it.
        admitted = self._global_in_flight < GLOBAL_CONCURRENCY and entry_id not in self._in_flight
        if admitted:
            self._global_in_flight += 1
            self._in_flight.add(entry_id)
        try:
            yield admitted
        finally:
            if admitted:
                self._global_in_flight -= 1
                self._in_flight.discard(entry_id)
