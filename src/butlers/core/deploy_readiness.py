"""Deployment completion waits for sustained semantic readiness (REQ-dashboard-api-064).

Process ``/health`` can turn green right after a restart while routing fails
minutes later. A deployment launcher therefore declares completion only after
:class:`SustainedReadinessGate` has seen, after its deployment window opened:

* every sampled public ``GET /ready`` verdict true for longer than the longest
  configured fleet liveness TTL (any false or unavailable sample restarts that
  sustained window);
* two distinct complete receiver-observer cycles: every expected non-paused
  daemon's healthy receiver observation advanced past the previous cycle's
  newest observation, so re-reading one cycle never counts twice;
* two distinct qualifying scheduled QA patrol completions with different
  durable IDs and strictly increasing completion times under the current
  enabled-source configuration (``QUALIFYING_PATROLS_SINCE_SQL``).

The wait is bounded. Its default deadline is the derived minimum, two patrol
cadences plus the longest TTL plus a ten-minute margin (35 minutes at the
current ten-minute cadence and five-minute TTL); a shorter override is refused
before any waiting. Timeout yields a :class:`ReadinessReceipt` naming only
fixed failure categories and a rollback classification, never success.

The gate only reads: ``/ready`` (Q4's sole public readiness route; no second
handler or auth exception), the shared fleet facts, and ``public.qa_patrols``.
It provisions no external functional monitor (REQ-dashboard-api-065) and does
not itself roll anything back.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal

import httpx

from butlers.core.control_plane_identity import (
    FLEET_FACTS_SQL,
    READINESS_CHECK_KEYS,
    DashboardProbeRoleView,
)
from butlers.core.qa.patrol_provenance import (
    QUALIFYING_PATROL_STATUSES,
    QUALIFYING_PATROLS_SINCE_SQL,
)

TIMEOUT_MARGIN_S: Final[float] = 600.0
REQUIRED_PATROL_CADENCES: Final[int] = 2
REQUIRED_OBSERVER_CYCLES: Final[int] = 2
REQUIRED_QA_PATROLS: Final[int] = 2
DEFAULT_POLL_INTERVAL_S: Final[float] = 15.0
DEFAULT_READY_URL: Final[str] = "http://localhost:41200/ready"
MAX_READY_BYTES: Final[int] = 1024
READY_REQUEST_TIMEOUT_S: Final[float] = 5.0

#: Gate-level failure categories beyond ``/ready``'s fixed check names.
GATE_CATEGORIES: Final[tuple[str, ...]] = (
    "readiness_unavailable",
    "evidence_unavailable",
    "observer_cycles",
    "qa_patrol_cycles",
    "sustained_window",
)
FAILURE_CATEGORIES: Final[tuple[str, ...]] = READINESS_CHECK_KEYS + GATE_CATEGORIES

#: ``not_required`` on success; ``recommended`` when the last readiness sample
#: was false or unavailable; ``operator_review`` when readiness held but the
#: cycle or sustained-window evidence never completed.
Rollback = Literal["not_required", "recommended", "operator_review"]


@dataclass(frozen=True)
class SustainedReadinessPolicy:
    """Validated cadence-derived bounds for one deployment wait."""

    patrol_interval_s: float
    longest_ttl_s: float
    timeout_s: float | None = None
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S

    def __post_init__(self) -> None:
        for name in ("patrol_interval_s", "longest_ttl_s", "poll_interval_s"):
            value = getattr(self, name)
            if not isinstance(value, int | float) or value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.timeout_s is not None and self.timeout_s < self.minimum_timeout_s:
            raise ValueError(
                f"readiness timeout {self.timeout_s:g}s is below the derived minimum "
                f"{self.minimum_timeout_s:g}s (two QA patrol cadences + longest liveness "
                f"TTL + {TIMEOUT_MARGIN_S:g}s margin); the deployment could never pass its gate"
            )

    @property
    def minimum_timeout_s(self) -> float:
        return (
            REQUIRED_PATROL_CADENCES * self.patrol_interval_s
            + self.longest_ttl_s
            + TIMEOUT_MARGIN_S
        )

    @property
    def effective_timeout_s(self) -> float:
        return self.minimum_timeout_s if self.timeout_s is None else self.timeout_s


@dataclass(frozen=True)
class SustainedReadinessTarget:
    """Everything one wait needs: policy, expected roster, and QA contract digest."""

    policy: SustainedReadinessPolicy
    expected: tuple[str, ...]
    qa_digest: str
    ready_url: str = DEFAULT_READY_URL

    def __post_init__(self) -> None:
        if not self.expected:
            raise ValueError("expected roster is empty")


def resolve_target(
    roster: Sequence[Any],
    *,
    timeout_s: float | None = None,
    ready_url: str = DEFAULT_READY_URL,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
) -> SustainedReadinessTarget:
    """Derive the wait from Git roster entries (``name``, ``liveness_ttl_seconds``,
    ``qa_patrol_contract``). Missing QA coverage or TTLs refuse rather than guess."""
    if not roster:
        raise ValueError("roster is empty; readiness cannot be derived")
    contracts = [
        entry.qa_patrol_contract
        for entry in roster
        if getattr(entry, "qa_patrol_contract", None) is not None
    ]
    if len(contracts) != 1:
        raise ValueError("exactly one enabled QA patrol contract is required")
    ttls = [getattr(entry, "liveness_ttl_seconds", None) for entry in roster]
    if not all(type(ttl) is int and ttl > 0 for ttl in ttls):
        raise ValueError("every roster butler needs a positive liveness_ttl_seconds")
    contract = contracts[0]
    policy = SustainedReadinessPolicy(
        patrol_interval_s=float(contract.patrol_interval_minutes * 60),
        longest_ttl_s=float(max(ttls)),  # type: ignore[type-var]
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
    )
    return SustainedReadinessTarget(
        policy=policy,
        expected=tuple(sorted(entry.name for entry in roster)),
        qa_digest=contract.digest,
        ready_url=ready_url,
    )


@dataclass(frozen=True)
class EvidenceSample:
    """One poll. ``ready=None`` means ``/ready`` was unavailable or malformed.

    ``observer_span`` is the (oldest, newest) healthy receiver observation over
    the expected non-paused daemons, or ``None`` when any is unobserved,
    ineligible, or the query failed. ``patrols`` lists qualifying
    ``(id, completed_at)`` rows after the window start, or ``None`` on failure.
    """

    ready: bool | None
    failing_checks: tuple[str, ...] = ()
    observer_span: tuple[datetime, datetime] | None = None
    patrols: tuple[tuple[str, datetime], ...] | None = None


@dataclass(frozen=True)
class ReadinessReceipt:
    """Content-blind outcome: counts, fixed categories, rollback classification."""

    passed: bool
    rollback: Rollback
    failing_categories: tuple[str, ...]
    observer_cycles: int
    qa_patrols: int
    sustained_s: float
    elapsed_s: float
    timeout_s: float

    def to_json(self) -> str:
        return json.dumps(
            {
                "result": "success" if self.passed else "failed",
                "rollback": self.rollback,
                "failing_categories": list(self.failing_categories),
                "observer_cycles": self.observer_cycles,
                "qa_patrols": self.qa_patrols,
                "sustained_s": round(self.sustained_s, 1),
                "elapsed_s": round(self.elapsed_s, 1),
                "timeout_s": round(self.timeout_s, 1),
            },
            separators=(",", ":"),
        )


@dataclass
class SustainedReadinessGate:
    """Pure state machine over :class:`EvidenceSample` polls."""

    policy: SustainedReadinessPolicy
    window_start: datetime
    started_at: float
    observer_cycles: int = 0
    _observer_boundary: datetime | None = None
    _patrol_ids: set[str] = field(default_factory=set)
    _last_patrol_at: datetime | None = None
    _sustained_since: float | None = None
    _last: EvidenceSample | None = None

    def __post_init__(self) -> None:
        self._observer_boundary = self.window_start

    @property
    def qa_patrols(self) -> int:
        return len(self._patrol_ids)

    def observe(self, sample: EvidenceSample, now: float) -> bool:
        """Fold one poll in; return True once deployment completion is proven."""
        self._last = sample
        if sample.ready is True:
            if self._sustained_since is None:
                self._sustained_since = now
        else:
            self._sustained_since = None
        span = sample.observer_span
        boundary = self._observer_boundary
        if span is not None and boundary is not None and span[0] > boundary:
            self.observer_cycles += 1
            self._observer_boundary = span[1]
        for patrol_id, completed_at in sorted(sample.patrols or (), key=lambda p: (p[1], p[0])):
            if completed_at <= self.window_start or patrol_id in self._patrol_ids:
                continue
            if self._last_patrol_at is None or completed_at > self._last_patrol_at:
                self._patrol_ids.add(patrol_id)
                self._last_patrol_at = completed_at
        return not self._missing(now)

    def _sustained_s(self, now: float) -> float:
        return 0.0 if self._sustained_since is None else now - self._sustained_since

    def _missing(self, now: float) -> list[str]:
        missing: list[str] = []
        last = self._last
        if last is None or last.ready is None:
            missing.append("readiness_unavailable")
        elif last.ready is False:
            missing.extend(c for c in last.failing_checks if c in READINESS_CHECK_KEYS)
        if self.observer_cycles < REQUIRED_OBSERVER_CYCLES:
            missing.append("observer_cycles")
        if self.qa_patrols < REQUIRED_QA_PATROLS:
            missing.append("qa_patrol_cycles")
        if self._sustained_s(now) <= self.policy.longest_ttl_s:
            missing.append("sustained_window")
        return missing

    def expired(self, now: float) -> bool:
        return now - self.started_at >= self.policy.effective_timeout_s

    def receipt(self, now: float) -> ReadinessReceipt:
        missing = self._missing(now)
        passed = not missing
        last = self._last
        if not passed and last is not None and (last.observer_span is None or last.patrols is None):
            missing.append("evidence_unavailable")
        rollback: Rollback
        if passed:
            rollback = "not_required"
        elif last is None or last.ready is not True:
            rollback = "recommended"
        else:
            rollback = "operator_review"
        ordered = tuple(c for c in FAILURE_CATEGORIES if c in missing)
        return ReadinessReceipt(
            passed=passed,
            rollback=rollback,
            failing_categories=ordered,
            observer_cycles=self.observer_cycles,
            qa_patrols=self.qa_patrols,
            sustained_s=self._sustained_s(now),
            elapsed_s=now - self.started_at,
            timeout_s=self.policy.effective_timeout_s,
        )


# -- evidence readers ---------------------------------------------------------


async def read_ready(client: httpx.AsyncClient, url: str) -> tuple[bool | None, tuple[str, ...]]:
    """Read ``/ready``: 200 ``{"ready": true}`` or 503 with fixed failing checks."""
    try:
        async with client.stream("GET", url, follow_redirects=False) as response:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_READY_BYTES:
                    return None, ()
            status = response.status_code
        payload = json.loads(body)
    except (httpx.HTTPError, ValueError):
        return None, ()
    if not isinstance(payload, dict):
        return None, ()
    if status == 200 and payload.get("ready") is True:
        return True, ()
    checks = payload.get("checks")
    if status == 503 and payload.get("ready") is False and isinstance(checks, Mapping):
        failing = tuple(k for k in READINESS_CHECK_KEYS if checks.get(k) is not True)
        return False, failing
    return None, ()


async def read_observer_span(
    pool: Any, expected: Sequence[str]
) -> tuple[datetime, datetime] | None:
    """Span of healthy receiver observations over expected non-paused daemons."""
    rows = await DashboardProbeRoleView(pool).fetch(FLEET_FACTS_SQL, list(expected))
    by_name = {row["name"]: row for row in rows}
    observed: list[datetime] = []
    for name in expected:
        row = by_name.get(name)
        if row is None:
            return None
        if row["policy_state"] == "paused":
            continue  # intentional owner exclusion
        at = row["healthy_observed_at"]
        if row["policy_state"] != "active" or at is None:
            return None
        observed.append(at)
    if not observed:
        return None
    return min(observed), max(observed)


async def read_patrols(pool: Any, digest: str, since: datetime) -> tuple[tuple[str, datetime], ...]:
    rows = await pool.fetch(
        QUALIFYING_PATROLS_SINCE_SQL, list(QUALIFYING_PATROL_STATUSES), digest, since
    )
    return tuple((str(row["id"]), row["completed_at"]) for row in rows)


async def await_sustained_readiness(
    target: SustainedReadinessTarget,
    pool: Any,
    *,
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> ReadinessReceipt:
    """Poll until completion is proven or the validated deadline passes.

    Read-only and idempotent: re-running it only re-reads evidence. The window
    opens at the database clock when the wait starts, so earlier patrols and
    observations never count.
    """
    policy = target.policy
    make_client = client_factory or (
        lambda: httpx.AsyncClient(trust_env=False, timeout=READY_REQUEST_TIMEOUT_S)
    )
    started = clock()
    gate: SustainedReadinessGate | None = None
    async with make_client() as client:
        while True:
            now = clock()
            if gate is None:
                try:
                    window_start = await pool.fetchval("SELECT clock_timestamp()")
                except Exception:  # noqa: BLE001 - evidence failure is a category
                    window_start = None
                if isinstance(window_start, datetime):
                    gate = SustainedReadinessGate(policy, window_start, started)
            if gate is not None:
                ready, failing = await read_ready(client, target.ready_url)
                try:
                    span = await read_observer_span(pool, target.expected)
                except Exception:  # noqa: BLE001
                    span = None
                try:
                    patrols = await read_patrols(pool, target.qa_digest, gate.window_start)
                except Exception:  # noqa: BLE001
                    patrols = None
                now = clock()
                if gate.observe(EvidenceSample(ready, failing, span, patrols), now):
                    return gate.receipt(now)
            if now - started >= policy.effective_timeout_s:
                if gate is None:
                    return ReadinessReceipt(
                        passed=False,
                        rollback="recommended",
                        failing_categories=("evidence_unavailable",),
                        observer_cycles=0,
                        qa_patrols=0,
                        sustained_s=0.0,
                        elapsed_s=now - started,
                        timeout_s=policy.effective_timeout_s,
                    )
                return gate.receipt(now)
            await sleep(policy.poll_interval_s)
