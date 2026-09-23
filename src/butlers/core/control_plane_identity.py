"""Receiver-owned daemon identity probes, shared by Dashboard and Switchboard.

This module is deliberately below both callers.  It never reads a registry URL,
changes route eligibility, or writes operator policy.  The only writes are the
fixed L1 reserve/record operations, whose database locks fence cross-process
and cross-boot races.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx

IDENTITY_PATH = "/internal/control-plane/identity"
IDENTITY_SCHEMA = "butler.control.v1"
MAX_IDENTITY_BYTES = 2048
PROBE_DEADLINE_S = 3.0
MAX_PROBE_FANOUT = 4
SUPPORTED_ROUTE_CONTRACT = 1

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExpectedDaemon:
    """Exact Git-roster identity with a deployment-owned backend hostname."""

    name: str
    port: int
    host: str

    @property
    def url(self) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", self.host):
            raise ValueError("invalid backend host")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("invalid roster port")
        return f"http://{self.host}:{self.port}{IDENTITY_PATH}"


def expected_from_roster(configs: list[Any]) -> tuple[ExpectedDaemon, ...]:
    """Freeze the expected set from Git-discovered configs, never DB rows."""
    host = os.environ.get("BUTLERS_HOST", "localhost")
    targets = tuple(
        ExpectedDaemon(
            name=config.name,
            port=config.port,
            host=host,
        )
        for config in configs
    )
    if len({target.name for target in targets}) != len(targets):
        raise ValueError("duplicate roster name")
    if len({target.port for target in targets}) != len(targets):
        raise ValueError("duplicate roster port")
    for target in targets:
        target.url  # Validate the complete trusted endpoint before any I/O.
    return targets


@dataclass(frozen=True)
class VerifiedIdentity:
    name: str
    boot_instance_id: uuid.UUID
    boot_epoch: int
    route_contract_min: int
    route_contract_max: int
    accepting_routes: bool


class ProbeFailure(ValueError):
    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


def verify_identity(
    payload: object, expected: ExpectedDaemon, epoch: int, registered_instance: uuid.UUID
) -> VerifiedIdentity:
    """Reject extra fields, timestamps, wrong generation, and incompatible work."""
    required = {
        "schema_version",
        "butler_name",
        "boot_instance_id",
        "boot_epoch",
        "route_contract",
        "accepting_routes",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ProbeFailure("invalid_identity")
    if payload["schema_version"] != IDENTITY_SCHEMA or payload["butler_name"] != expected.name:
        raise ProbeFailure("invalid_identity")
    raw_uuid = payload["boot_instance_id"]
    try:
        instance_id = uuid.UUID(raw_uuid) if isinstance(raw_uuid, str) else None
    except ValueError as exc:
        raise ProbeFailure("invalid_identity") from exc
    if (
        instance_id is None
        or instance_id.version != 7
        or str(instance_id) != raw_uuid
        or instance_id != registered_instance
    ):
        raise ProbeFailure("invalid_identity")
    reported_epoch = payload["boot_epoch"]
    if type(reported_epoch) is not int or reported_epoch != epoch or epoch <= 0:
        raise ProbeFailure("invalid_identity")
    contract = payload["route_contract"]
    if not isinstance(contract, dict) or set(contract) != {"min", "max"}:
        raise ProbeFailure("invalid_contract")
    minimum, maximum = contract["min"], contract["max"]
    if (
        type(minimum) is not int
        or type(maximum) is not int
        or minimum <= 0
        or minimum > maximum
        or not minimum <= SUPPORTED_ROUTE_CONTRACT <= maximum
    ):
        raise ProbeFailure("invalid_contract")
    accepting = payload["accepting_routes"]
    if type(accepting) is not bool:
        raise ProbeFailure("invalid_identity")
    if not accepting:
        raise ProbeFailure("not_accepting")
    return VerifiedIdentity(expected.name, instance_id, epoch, minimum, maximum, accepting)


async def _read_identity(client: httpx.AsyncClient, expected: ExpectedDaemon) -> object:
    """Stream and cap response bytes; never follow a redirect off the roster URL."""
    url = expected.url
    async with client.stream("GET", url, follow_redirects=False) as response:
        if response.status_code != 200 or str(response.url) != url:
            raise ProbeFailure("invalid_identity")
        content_length = response.headers.get("content-length")
        if content_length and (
            not content_length.isdecimal() or int(content_length) > MAX_IDENTITY_BYTES
        ):
            raise ProbeFailure("invalid_identity")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_IDENTITY_BYTES:
                raise ProbeFailure("invalid_identity")
    try:

        def closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
            value: dict[str, object] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate identity field")
                value[key] = item
            return value

        return json.loads(body, object_pairs_hook=closed_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProbeFailure("invalid_identity") from exc


@dataclass(frozen=True)
class ProbeOutcome:
    category: str
    recorded: bool
    boot_epoch: int | None = None
    probe_sequence: int | None = None


class DashboardProbeRoleView:
    """Run each Dashboard observer query under Switchboard's narrow role.

    The dashboard's general pool login owns many API tables.  The L2 loop must
    not carry that broader effective authority into probe operations.  Each
    operation acquires its own connection so bounded fanout remains safe.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def _call(self, method: str, statement: str, *args: Any) -> Any:
        async with self._pool.acquire() as conn:
            try:
                await conn.execute('SET ROLE "butler_switchboard_rw"')
                return await getattr(conn, method)(statement, *args)
            finally:
                await conn.execute("RESET ROLE")

    async def fetchrow(self, statement: str, *args: Any) -> Any:
        return await self._call("fetchrow", statement, *args)

    async def fetchval(self, statement: str, *args: Any) -> Any:
        return await self._call("fetchval", statement, *args)

    async def fetch(self, statement: str, *args: Any) -> Any:
        return await self._call("fetch", statement, *args)


async def probe_once(
    pool: Any,
    expected: ExpectedDaemon,
    *,
    client: httpx.AsyncClient,
    deadline_s: float = PROBE_DEADLINE_S,
) -> ProbeOutcome:
    """Reserve before I/O and conditionally record only receiver-verifiable facts."""
    epoch: int | None = None
    sequence: int | None = None
    category = "observer_error"
    try:
        async with asyncio.timeout(deadline_s):
            row = await pool.fetchrow(
                "SELECT boot_epoch, probe_sequence FROM public.reserve_butler_probe($1)",
                expected.name,
            )
            if row is None:
                return ProbeOutcome("invalid_identity", False)
            epoch, sequence = int(row["boot_epoch"]), int(row["probe_sequence"])
            registered_instance = await pool.fetchval(
                "SELECT boot_instance_id FROM switchboard.butler_registry_control_plane "
                "WHERE name = $1 AND boot_epoch = $2",
                expected.name,
                epoch,
            )
            if not isinstance(registered_instance, uuid.UUID):
                raise ProbeFailure("invalid_identity")
            payload = await _read_identity(client, expected)
            verify_identity(payload, expected, epoch, registered_instance)
            category = "healthy"
    except (TimeoutError, httpx.TimeoutException):
        category = "timeout"
    except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError):
        category = "connection"
    except ProbeFailure as exc:
        category = exc.category
    except Exception:
        logger.warning("Control-plane probe unavailable (category=observer_error)")

    if epoch is None or sequence is None:
        return ProbeOutcome(category, False)
    try:
        async with asyncio.timeout(deadline_s):
            recorded = bool(
                await pool.fetchval(
                    "SELECT public.record_butler_probe($1, $2, $3, $4, $5, $6, $7)",
                    expected.name,
                    epoch,
                    sequence,
                    category == "healthy",
                    True if category == "healthy" else None,
                    True if category == "healthy" else None,
                    None if category == "healthy" else category,
                )
            )
    except Exception:
        return ProbeOutcome("observer_error", False, epoch, sequence)
    return ProbeOutcome(category if recorded else "superseded", recorded, epoch, sequence)


class SingleFlightProber:
    """Coalesce same-process probes; the DB sequence also fences other processes."""

    def __init__(self, pool: Any, *, client: httpx.AsyncClient) -> None:
        self.pool = pool
        self.client = client
        self._lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Task[ProbeOutcome]] = {}

    async def probe(self, expected: ExpectedDaemon) -> ProbeOutcome:
        async with self._lock:
            task = self._inflight.get(expected.name)
            if task is not None and task.done():
                del self._inflight[expected.name]
                task = None
            if task is None:
                task = asyncio.create_task(probe_once(self.pool, expected, client=self.client))
                self._inflight[expected.name] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                async with self._lock:
                    if self._inflight.get(expected.name) is task:
                        del self._inflight[expected.name]

    async def cancel_all(self) -> None:
        """Stop bounded work before the owning observer closes its HTTP client."""
        async with self._lock:
            tasks = tuple(self._inflight.values())
            self._inflight.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


@dataclass(frozen=True)
class ShadowCycle:
    complete: bool
    expected_count: int
    recorded_count: int
    mismatch_count: int | None


async def run_shadow_cycle(
    pool: Any,
    expected: tuple[ExpectedDaemon, ...],
    prober: SingleFlightProber,
) -> ShadowCycle:
    """Compare new evidence with legacy authority; do not change route decisions."""
    if not expected:
        return ShadowCycle(False, 0, 0, None)
    semaphore = asyncio.Semaphore(MAX_PROBE_FANOUT)

    async def bounded(target: ExpectedDaemon) -> ProbeOutcome:
        async with semaphore:
            return await prober.probe(target)

    outcomes = await asyncio.gather(*(bounded(target) for target in expected))
    recorded_count = sum(outcome.recorded for outcome in outcomes)
    try:
        rows = await pool.fetch(
            """
            SELECT c.name, c.policy_state, c.observed_state, c.boot_epoch,
                   c.observed_boot_epoch, c.route_compatible, c.accepting_routes,
                   c.healthy_observed_at, r.eligibility_state,
                   r.liveness_ttl_seconds
            FROM switchboard.butler_registry_control_plane AS c
            JOIN switchboard.butler_registry AS r USING (name)
            WHERE c.name = ANY($1::text[])
            """,
            [target.name for target in expected],
        )
        by_name = {row["name"]: row for row in rows}
        now = await pool.fetchval("SELECT clock_timestamp()")
        mismatches = 0
        for target in expected:
            row = by_name.get(target.name)
            if row is None:
                mismatches += 1
                continue
            observed_at = row["healthy_observed_at"]
            shadow_ready = (
                row["policy_state"] == "active"
                and row["observed_state"] == "healthy"
                and row["observed_boot_epoch"] == row["boot_epoch"]
                and row["route_compatible"] is True
                and row["accepting_routes"] is True
                and observed_at is not None
                and observed_at + timedelta(seconds=row["liveness_ttl_seconds"]) >= now
            )
            if shadow_ready != (row["eligibility_state"] == "active"):
                mismatches += 1
    except Exception:
        return ShadowCycle(False, len(expected), recorded_count, None)
    return ShadowCycle(recorded_count == len(expected), len(expected), recorded_count, mismatches)


async def _effective_observer_interval_s(pool: Any, expected: tuple[ExpectedDaemon, ...]) -> float:
    """Use the current operator-tuned TTL, never a possibly stale Git seed."""
    rows = await pool.fetch(
        "SELECT name, liveness_ttl_seconds FROM switchboard.butler_registry "
        "WHERE name = ANY($1::text[])",
        [target.name for target in expected],
    )
    ttls = {row["name"]: row["liveness_ttl_seconds"] for row in rows}
    if len(ttls) != len(expected) or any(
        type(ttls.get(target.name)) is not int or ttls[target.name] <= 0 for target in expected
    ):
        raise RuntimeError("incomplete fleet liveness TTL configuration")
    return min(ttls.values()) / 2


async def run_shadow_observer_loop(pool: Any, configs: list[Any]) -> None:
    """Supervised periodic observer. A partial cycle never claims an all-clear."""
    expected = expected_from_roster(configs)
    if not expected:
        raise RuntimeError("no exact Git-roster targets for shadow observer")
    role_view = DashboardProbeRoleView(pool)
    async with httpx.AsyncClient(trust_env=False, timeout=PROBE_DEADLINE_S) as client:
        prober = SingleFlightProber(role_view, client=client)
        try:
            last_cycle_at = asyncio.get_running_loop().time()
            while True:
                interval_s = await _effective_observer_interval_s(role_view, expected)
                remaining = last_cycle_at + interval_s - asyncio.get_running_loop().time()
                if remaining > 0:
                    # Re-read the effective TTL often enough that an operator
                    # reduction cannot leave the old long sleep in force.
                    await asyncio.sleep(min(remaining, 5.0))
                    continue
                cycle = await run_shadow_cycle(role_view, expected, prober)
                last_cycle_at = asyncio.get_running_loop().time()
                logger.info(
                    "Shadow fleet observation: complete=%s expected=%d recorded=%d mismatches=%s",
                    cycle.complete,
                    cycle.expected_count,
                    cycle.recorded_count,
                    cycle.mismatch_count if cycle.mismatch_count is not None else "unavailable",
                )
        finally:
            await prober.cancel_all()
