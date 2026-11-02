"""Content-blind semantic fleet readiness for public ``GET /ready`` (REQ-dashboard-api-063).

A separately supervised Dashboard controller samples every input on a bounded
interval and keeps one in-memory snapshot. The public request reads only that
snapshot: it never queries PostgreSQL, calls a daemon, triggers the Switchboard
route preflight, or writes evidence. A missing or stale snapshot fails closed.

The fixed Boolean checks (RFC 0007 2026-09-23 amendment):

``postgres``
    The Switchboard database answers a trivial read.
``roster``
    Every Git-roster expected daemon has a registry and control-plane record.
``observer``
    The receiver observer finished a complete cycle within the shortest
    configured liveness TTL (two observer intervals).
``fleet``
    Every non-paused expected daemon is active by owner policy and freshly
    receiver-verified at its current boot epoch, compatible, and accepting.
    ``paused`` is an intentional exclusion; quarantine and review-required are
    failures.
``qa_patrol``
    A qualifying scheduled QA patrol (see ``butlers.core.qa.patrol_provenance``)
    completed under the current enabled-source configuration within its
    overdue bound. A newer nonqualifying row neither renews nor erases it.
``supervisors``
    The process-fenced supervised-job health projection (``bu-c6wjr``) is
    available and reports each named readiness control loop ``ok``. It covers
    only Dashboard lifespan loops; Switchboard's runtime-attention delivery
    worker keeps its own condition/outbox status.
``route_canary``
    Switchboard's effect-free internal route preflight answered ready. That
    proves fixed-target selection and reachability, never a transactional
    ``route.execute`` acceptance or downstream success.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final

import httpx

from butlers.core.control_plane_identity import (
    FLEET_FACTS_SQL,
    PROBE_DEADLINE_S,
    DashboardProbeRoleView,
    ExpectedDaemon,
    ShadowCycle,
    expected_from_roster,
)
from butlers.core.job_health import JobHealthStore, record_tick
from butlers.core.qa.patrol_provenance import (
    LAST_QUALIFYING_PATROL_SQL,
    QUALIFYING_PATROL_STATUSES,
    qa_patrol_contract,
)

logger = logging.getLogger(__name__)

CHECK_KEYS: Final[tuple[str, ...]] = (
    "postgres",
    "roster",
    "observer",
    "fleet",
    "qa_patrol",
    "supervisors",
    "route_canary",
)
#: Dashboard lifespan control loops whose supervised health readiness requires.
READINESS_CONTROL_LOOPS: Final[tuple[str, ...]] = ("fleet_shadow_observer",)
READINESS_JOB_NAME: Final[str] = "fleet_readiness"
SAMPLE_INTERVAL_S: Final[float] = 30.0
#: A snapshot older than this many sample intervals is not evidence.
SNAPSHOT_MAX_AGE_INTERVALS: Final[int] = 3
PREFLIGHT_PATH: Final[str] = "/internal/control-plane/route-preflight"
MAX_PREFLIGHT_BYTES: Final[int] = 512
_POLICY_STATES: Final[frozenset[str]] = frozenset(
    {"active", "paused", "quarantined", "review_required"}
)


@dataclass(frozen=True)
class ReadinessSnapshot:
    checks: Mapping[str, bool]
    sampled_at: float

    @property
    def ready(self) -> bool:
        return all(self.checks.get(key) is True for key in CHECK_KEYS)


def _all_false() -> dict[str, bool]:
    return dict.fromkeys(CHECK_KEYS, False)


def receiver_ready(row: Mapping[str, Any], now: Any) -> bool:
    """One daemon's receiver evidence is healthy, current-epoch, and within TTL."""
    observed_at = row["healthy_observed_at"]
    ttl = row["liveness_ttl_seconds"]
    return (
        row["observed_state"] == "healthy"
        and row["observed_boot_epoch"] is not None
        and row["observed_boot_epoch"] == row["boot_epoch"]
        and row["route_compatible"] is True
        and row["accepting_routes"] is True
        and observed_at is not None
        and now is not None
        and type(ttl) is int
        and ttl > 0
        and observed_at + timedelta(seconds=ttl) >= now
    )


class FleetReadiness:
    """Hold the controller's last readiness snapshot; sample only under supervision."""

    def __init__(
        self,
        pool: Callable[[], Any],
        configs: list[Any],
        job_health: JobHealthStore | None,
        *,
        clock: Callable[[], float] = time.monotonic,
        client_factory: Callable[[], Any] | None = None,
        sample_interval_s: float = SAMPLE_INTERVAL_S,
    ) -> None:
        self._pool = pool
        self._expected = expected_from_roster(configs)
        self._switchboard: ExpectedDaemon | None = next(
            (target for target in self._expected if target.name == "switchboard"), None
        )
        self._qa_contract = qa_patrol_contract(configs)
        self._job_health = job_health
        self._clock = clock
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(trust_env=False, timeout=PROBE_DEADLINE_S + 1.0)
        )
        self.sample_interval_s = sample_interval_s
        self._last_cycle: tuple[bool, float] | None = None
        self._snapshot: ReadinessSnapshot | None = None

    # -- inputs ---------------------------------------------------------------

    def note_cycle(self, cycle: ShadowCycle) -> None:
        """Record the receiver observer's latest cycle; never raises."""
        self._last_cycle = (bool(cycle.complete), self._clock())

    # -- public read ----------------------------------------------------------

    def verdict(self) -> tuple[bool, dict[str, bool]]:
        """Return the cached verdict. Missing or stale evidence is all-false."""
        snapshot = self._snapshot
        max_age = self.sample_interval_s * SNAPSHOT_MAX_AGE_INTERVALS
        if snapshot is None or self._clock() - snapshot.sampled_at > max_age:
            return False, _all_false()
        return snapshot.ready, {key: snapshot.checks.get(key) is True for key in CHECK_KEYS}

    # -- controller sample ----------------------------------------------------

    async def sample(self) -> ReadinessSnapshot:
        checks = _all_false()
        checks["postgres"] = await self._guard("postgres", self._postgres())
        roster, fleet, min_ttl = await self._guard_fleet()
        checks["roster"], checks["fleet"] = roster, fleet
        checks["observer"] = self._observer_fresh(min_ttl)
        checks["qa_patrol"] = await self._guard("qa_patrol", self._qa_patrol())
        checks["supervisors"] = await self._guard("supervisors", self._supervisors())
        checks["route_canary"] = await self._guard("route_canary", self._route_canary())
        snapshot = ReadinessSnapshot(checks, self._clock())
        self._snapshot = snapshot
        return snapshot

    async def _guard(self, category: str, check: Any) -> bool:
        try:
            return (await check) is True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reduced to a typed log
            logger.warning(
                "Readiness check failed (check=%s category=%s)", category, type(exc).__name__
            )
            return False

    async def _postgres(self) -> bool:
        return await self._pool().fetchval("SELECT 1") == 1

    async def _guard_fleet(self) -> tuple[bool, bool, float | None]:
        try:
            return await self._fleet()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reduced to a typed log
            logger.warning("Readiness check failed (check=fleet category=%s)", type(exc).__name__)
            return False, False, None

    async def _fleet(self) -> tuple[bool, bool, float | None]:
        if not self._expected:
            return False, False, None
        rows = await DashboardProbeRoleView(self._pool()).fetch(
            FLEET_FACTS_SQL, [target.name for target in self._expected]
        )
        by_name = {row["name"]: row for row in rows}
        roster = all(
            target.name in by_name and by_name[target.name]["policy_state"] in _POLICY_STATES
            for target in self._expected
        )
        if not roster:
            return False, False, None
        ttls = [by_name[target.name]["liveness_ttl_seconds"] for target in self._expected]
        min_ttl = float(min(ttls)) if all(type(ttl) is int and ttl > 0 for ttl in ttls) else None
        fleet = True
        for target in self._expected:
            row = by_name[target.name]
            if row["policy_state"] == "paused":
                continue  # intentional owner exclusion
            if row["policy_state"] != "active" or not receiver_ready(row, row["server_now"]):
                fleet = False
        return True, fleet, min_ttl

    def _observer_fresh(self, min_ttl: float | None) -> bool:
        last = self._last_cycle
        if last is None or min_ttl is None:
            return False
        complete, at = last
        return complete and self._clock() - at <= min_ttl

    async def _qa_patrol(self) -> bool:
        contract = self._qa_contract
        if contract is None:
            return False
        pool = self._pool()
        last = await pool.fetchval(
            LAST_QUALIFYING_PATROL_SQL, list(QUALIFYING_PATROL_STATUSES), contract.digest
        )
        if last is None:
            return False
        now = await pool.fetchval("SELECT clock_timestamp()")
        return (now - last).total_seconds() <= contract.overdue_after_s

    async def _supervisors(self) -> bool:
        if self._job_health is None:
            return False
        projection = await self._job_health.read()
        if projection.get("jobs_available") is not True:
            return False
        states = {job["name"]: job["state"] for job in projection.get("jobs", [])}
        return all(states.get(name) == "ok" for name in READINESS_CONTROL_LOOPS)

    async def _route_canary(self) -> bool:
        target = self._switchboard
        if target is None:
            return False
        url = f"http://{target.host}:{target.port}{PREFLIGHT_PATH}"
        async with asyncio.timeout(PROBE_DEADLINE_S + 1.0), self._client_factory() as client:
            async with client.stream("GET", url, follow_redirects=False) as response:
                if response.status_code != 200:
                    return False
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_PREFLIGHT_BYTES:
                        return False
        payload = json.loads(body)
        return isinstance(payload, dict) and payload.get("ready") is True


async def run_readiness_sampler_loop(readiness: FleetReadiness) -> None:
    """Refresh the cached snapshot on a bounded interval under the supervisor.

    A not-ready verdict is a successful sample; only a sampling crash is a
    failed tick. The public ``/ready`` handler never calls this.
    """
    while True:
        await readiness.sample()
        await record_tick(interval_s=readiness.sample_interval_s)
        await asyncio.sleep(readiness.sample_interval_s)
