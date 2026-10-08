"""Fixed accepted-ingress producer over the existing owning registry/MCP route.

This private constructor object never accepts a source body, actor, owner,
generation, verifier URL or callback from a model. The native accepted-row
dispatch path must invoke it after durable acceptance. Its presence is not
proof that that dispatch path or the owning publisher has been installed.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import asyncpg
from fastmcp import Client
from opentelemetry.instrumentation.utils import suppress_instrumentation

from butlers.core.custody_admission import CustodyAdmission, CustodyWriter
from butlers.core.custody_control import CustodyControlTransport, PreparedCustodyCommand
from butlers.core.custody_ingress import CustodyAcceptedIngress, parse_explicit_lock
from butlers.core.custody_source import CustodyError, canonical_uuid, closed_object
from butlers.core.mcp_urls import canonical_runtime_mcp_url, resolve_cross_container_mcp_url

# Private actual-pool allocation. Request fields, roles and actor/context
# strings cannot select or install this source-owned producer.
_installed_producers: dict[asyncpg.Pool, CustodyAcceptedProducer] = {}
logger = logging.getLogger(__name__)


def install_accepted_producer(pool: asyncpg.Pool, producer: CustodyAcceptedProducer) -> None:
    if (
        type(producer) is not CustodyAcceptedProducer
        or producer._pool is not pool
        or producer._admission._pool is not pool
        or not producer._admission._ready
    ):
        raise CustodyError("refused")
    current = _installed_producers.get(pool)
    if current is not None and current is not producer:
        raise CustodyError("conflict")
    _installed_producers[pool] = producer


def remove_accepted_producer(pool: asyncpg.Pool, producer: CustodyAcceptedProducer) -> None:
    if isinstance(pool, asyncpg.Pool) and not pool._closed:
        return
    if _installed_producers.get(pool) is producer:
        del _installed_producers[pool]


@asynccontextmanager
async def native_ingest_writer(
    pool: asyncpg.Pool, connection: asyncpg.Connection
) -> AsyncIterator[CustodyWriter | None]:
    """Enter BEFORE the native ingest transaction and any advisory/domain lock.

    The actual INSERT trigger freezes birth on this same writer. The caller's
    existing transaction becomes a savepoint and retains all its dedupe/event
    rollback behavior. Legacy unallocated pools receive no custody stamp; a
    stopped/failed allocated producer refuses instead of falling back while
    revocation is in flight. No locator or caller callback supplies authority.
    """
    producer = _installed_producers.get(pool)
    if producer is None:
        yield None
    else:
        async with producer._admission.bound_writer(connection) as writer:
            yield writer


class CustodyAcceptedProducer:
    """Source-owned actual-row capture; all MCP work is outside transactions."""

    def __init__(self, admission: CustodyAdmission, pool: asyncpg.Pool) -> None:
        if admission.profile.actor != "switchboard" or admission._pool is not pool:
            raise CustodyError("refused")
        self._admission, self._pool = admission, pool
        self._ingress = CustodyAcceptedIngress(admission)

    async def _endpoint(self, actor: str) -> str:
        # The only two callers below choose these fixed actors. No source or
        # caller payload selects an audience, URL or private verifier route.
        if actor not in {"relationship", "switchboard"}:
            raise CustodyError("refused")
        from butlers.tools.switchboard.registry.registry import resolve_routing_target

        with suppress_instrumentation():
            row, _ = await resolve_routing_target(self._pool, actor)
        if row is None:
            raise CustodyError("unavailable")
        endpoint = resolve_cross_container_mcp_url(canonical_runtime_mcp_url(row["endpoint_url"]))
        address = urlsplit(endpoint)
        if (
            address.scheme not in {"http", "https"}
            or not address.hostname
            or address.username is not None
            or address.password is not None
            or address.query
            or address.fragment
        ):
            raise CustodyError("unavailable")
        return endpoint

    async def prepare_exact_lock(self, record_id: uuid.UUID) -> PreparedCustodyCommand | None:
        """Read genuine accepted text, publish current mapping, recapture it.

        The native owning resolver must wrap its canonical read with
        CustodyChannelBindings.observe_channels. Its existing minimized DTO is
        ignored here; the SQL compiler reads only protected published metadata.
        An original row changed during the MCP call refuses rather than lending
        that observation to the changed report. This is the exact-ID branch;
        same-source natural-language selection remains separate mandatory work.
        """
        try:
            async with self._admission.writer() as writer:
                original = await self._ingress.read(writer, record_id)
                requested = parse_explicit_lock(original)
            if requested is None or not requested.exact_target_ids:
                return None
            endpoint = await self._endpoint("relationship")
            async with asyncio.timeout(10), Client(endpoint) as client:
                with suppress_instrumentation():
                    result = await client.call_tool(
                        "identity_resolve_channels",
                        {
                            "channel_type": original.channel_type,
                            "channel_values": [original.sender_identity],
                        },
                    )
                if result.is_error:
                    raise CustodyError("unavailable")
            return await self._ingress.capture_lock(record_id, original=original)
        except CustodyError:
            raise
        except Exception:
            # No raw identity, registry/provider URL, private source or SDK
            # exception escapes into a pipeline log or model response.
            raise CustodyError("unavailable") from None

    async def dispatch(self, source: PreparedCustodyCommand) -> dict:
        """Actual registered challenge/apply; unknown never causes a resend."""
        if type(source) is not PreparedCustodyCommand or source.operation != "hold":
            raise CustodyError("refused")
        try:
            endpoint = await self._endpoint("switchboard")
            return await CustodyControlTransport(self._admission, Client(endpoint)).execute(source)
        except CustodyError:
            raise
        except Exception:
            raise CustodyError("unknown") from None


class CustodyAcceptedWorker:
    """Fixed source reconciliation; every network call is after its DB COMMIT.

    The host helper is constructor-owned and never a public callback/URL. Its
    only payloads select this enrolled process and a protected accepted-work
    claim. They cannot supply a report, owner, generation or source projection.
    Attempt evidence is deliberately distinct from actual provider-start.
    """

    def __init__(
        self,
        producer: CustodyAcceptedProducer,
        host_call: Callable[[str, dict], Awaitable[dict]],
        process_id: str,
    ) -> None:
        if type(producer) is not CustodyAcceptedProducer:
            raise CustodyError("refused")
        self._producer = producer
        self._host_call = host_call
        self._process_id = canonical_uuid(process_id)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None:
            raise CustodyError("conflict")
        self._task = asyncio.create_task(self._run(), name="custody-accepted-source")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _call(self, action: str, claim: dict | None = None, **fields) -> dict:
        payload = {"process_id": self._process_id}
        if claim is not None:
            payload.update(record_id=claim["record_id"], claim_ref=claim["claim_ref"])
        payload.update(fields)
        async with asyncio.timeout(10):
            return await self._host_call(action, payload)

    async def run_once(self) -> str:
        """One original claim; lost attempt ACK never crosses the network."""
        claim = await self._call("claim")
        if claim.get("status") in {"idle", "reconciled"}:
            closed_object(claim, required={"status"})
            return claim["status"]
        closed_object(claim, required={"status", "record_id", "claim_ref", "source_ref"})
        if claim["status"] != "claimed":
            raise CustodyError("unavailable")
        record_id = uuid.UUID(canonical_uuid(claim["record_id"]))
        canonical_uuid(claim["claim_ref"])
        if claim["source_ref"] is not None:
            canonical_uuid(claim["source_ref"])
        try:
            async with self._producer._admission.writer() as writer:
                report = await self._producer._ingress.read(writer, record_id)
                request = parse_explicit_lock(report)
            if request is None:
                await self._call("ignore", claim)
                return "ignored"
            if not request.exact_target_ids:
                # Native selection confirmation remains mandatory work. The
                # durable status does not claim a prompt/case was delivered.
                await self._call("selection", claim)
                return "awaiting_selection"
            source = await self._producer.prepare_exact_lock(record_id)
            if source is None or (
                claim["source_ref"] is not None and claim["source_ref"] != str(source.source_ref)
            ):
                raise CustodyError("refused")
            prepared = await self._call("prepared", claim, source_ref=str(source.source_ref))
            closed_object(prepared, required={"status", "command_id"})
            if prepared != {"status": "prepared", "command_id": str(source.command_id)}:
                raise CustodyError("refused")
        except CustodyError as exc:
            # No provider/MCP effect was attempted. If preparation's ACK was
            # unknown, the helper refuses this settlement and the original
            # finite claim is reconciled, preserving its source/id on restart.
            if exc.code in {"refused", "invalid", "expired"}:
                try:
                    await self._call("unavailable", claim)
                except (CustodyError, TimeoutError):
                    pass
            # Startup/network/current owning-observation unavailability is not
            # a permanent source verdict. Leave the original finite claim for
            # recovery, without extending source expiry or creating a new ID.
            return "unavailable"
        # Unknown ACK here is a possible durable attempt. Do not send, retry,
        # remint or settle it as definitely unstarted; restart reconciles it.
        attempted = await self._call("attempt", claim)
        closed_object(attempted, required={"status", "command_id"})
        if attempted != {"status": "attempted", "command_id": str(source.command_id)}:
            raise CustodyError("unknown")
        try:
            async with asyncio.timeout(20):
                await self._producer.dispatch(source)
        except (CustodyError, TimeoutError):
            # SQL receipt decides the verdict, including a lost remote ACK.
            pass
        finished = await self._call("finish", claim)
        closed_object(finished, required={"status"})
        if finished["status"] not in {"committed", "unknown"}:
            raise CustodyError("unknown")
        return finished["status"]

    async def _run(self) -> None:
        while self._producer._admission._ready:
            try:
                status = await self.run_once()
            except (CustodyError, TimeoutError):
                # Protected work/lease remains durable; no raw driver, source,
                # provider/identity or token is logged or exposed to a model.
                status = "unavailable"
            except Exception:
                # Unexpected implementation/transport failure cannot leave a
                # successful-looking daemon with a silently dead dispatcher.
                # Stop all local admitted writers; protected work remains for
                # bounded recovery. Never render the exception or its payload.
                self._producer._admission._ready = False
                logger.error("Custody source reconciliation stopped; durable work is unresolved")
                return
            await asyncio.sleep(1 if status == "idle" else 0.1)
