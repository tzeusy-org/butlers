"""External deadman — an outside heartbeat that catches a silently broken host.

bu-9r3hd.4 (epic bu-9r3hd "Deploy spine", slice 4/5): a host reboot can leave
the Docker egress firewall unrestored (a known Docker+host-iptables quirk) —
every container comes back up healthy and every *internal* liveness signal
(connector heartbeats, butler heartbeats, the QA patrol itself) looks fine,
because none of them actually leave the host. Only a check that must cross
the (possibly broken) egress path to reach an outside party can detect this
failure mode; a purely internal check cannot, by construction, prove its own
network is intact.

This module implements the in-repo half of that external deadman:

- A finite committed database write/read and receiver-fleet health gate, followed
  by a durable at-most-once tick claim (never itself a success marker).
- A periodic outbound ping to an operator-configured URL
  (``EXTERNAL_DEADMAN_URL``). Any 2xx response counts as success — this is
  the de facto contract most "dead man's switch" monitoring services
  (Healthchecks.io, Cronitor, UptimeRobot heartbeat, ...) already implement,
  so this module stays provider-agnostic and requires no vendor SDK.
- A content-blind record of the last successful ping, persisted to
  ``public.audit_log`` (no new migration — the exact pattern ``butlers.jobs.deploy_drift``
  established for its own debounce/escalation state).
- A reader (:func:`get_last_deadman_success`) that
  ``butlers.core.qa.sources.infra_state.InfraStateSource`` uses to flag
  staleness as a QA discovery case once too many ping intervals have been
  missed.

What this module deliberately does NOT do: sign up for or configure any
specific third-party monitoring account. Acquiring an external monitor and
setting ``EXTERNAL_DEADMAN_URL`` in production is an operator action outside
this repo's scope (a new external account is a hard-gate item, not an
engineering decision) — see the bu-9r3hd.4 worker report's
Discovered-Follow-Ups for that remainder. Until configured, this loop is not
started at all (see ``butlers.api.app``'s lifespan wiring) — there is
genuinely nothing useful to do every tick with no target URL, and the
external-deadman-stale check in ``InfraStateSource`` treats "unconfigured" as
a legitimate absence, not a failure.

The TRUE external half — an outside party independently noticing "no ping
arrived in N minutes" and alerting even if this entire process (or host) is
dead — necessarily lives in whatever monitoring service ``EXTERNAL_DEADMAN_URL``
points at, not in this repo. :func:`get_last_deadman_success` /
``InfraStateSource`` is a same-host, best-effort early-warning companion to
that (it catches "our own outbound attempts have been failing/stale" even
before the external monitor's own alert fires), not a replacement for it.

Where this runs
----------------
Inside the dashboard-api process (see ``butlers.api.app.lifespan``), mirroring
``butlers.jobs.deploy_drift`` and ``butlers.jobs.secrets_lifecycle``: a
periodic, deterministic, zero-LLM check that doesn't belong to any single
butler daemon.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx

from butlers.api.db import DatabaseManager
from butlers.core import audit as audit_router

logger = logging.getLogger(__name__)

# HTTPX's ordinary INFO request record contains the entire monitor URL (often
# a bearer credential). Suppress only records from this task's deadman call,
# without changing global levels or muting unrelated clients.
_deadman_http = ContextVar("deadman_http", default=False)


class _MonitorURLFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _deadman_http.get()


logging.getLogger("httpx").addFilter(_MonitorURLFilter())


#: Env var naming the external deadman's ping target. Unset/empty = disabled.
EXTERNAL_DEADMAN_URL_ENV = "EXTERNAL_DEADMAN_URL"

#: Cadence for the background loop below. 10 minutes: tight enough that most
#: dead-man's-switch services' free tiers can alert within the hour even with
#: a generous multi-miss grace period (see InfraStateSource's
#: _DEADMAN_STALE_MULTIPLIER).
DEFAULT_DEADMAN_CHECK_INTERVAL_S = 600.0

#: HTTP timeout for a single ping attempt.
DEFAULT_PING_TIMEOUT_S = 10.0

_DEADMAN_ACTOR = "external_deadman"
_PING_SUCCESS_ACTION = "external_deadman_ping_success"
_TICK_ACTION = "external_deadman_tick"
DEFAULT_CHECK_TIMEOUT_S = 30.0
_DB_TIMEOUT_S = 5.0
# One serialization point for this fixed producer, not an endpoint-derived key.
_TICK_LOCK = 0x444541444D414E
_TICK_NAMESPACE = uuid.UUID("4838e994-20b8-4da6-b411-38870d495fa0")
_FLEET_SQL = """
    SELECT r.name, c.policy_state, c.observed_state, c.boot_epoch,
           c.observed_boot_epoch, c.route_compatible, c.accepting_routes,
           c.healthy_observed_at, r.liveness_ttl_seconds,
           clock_timestamp() AS checked_at
    FROM switchboard.butler_registry AS r
    LEFT JOIN switchboard.butler_registry_control_plane AS c USING (name)
    WHERE r.name = ANY($1::text[])
"""


def _positive_seconds(value: float) -> None:
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError("deadman interval/timeout must be positive and finite")


def _fleet_ready(rows: list[Any], expected: tuple[str, ...]) -> bool:
    """Use the receiver's current boot/TTL truth; absence is never health."""
    if not expected or len(rows) != len(set(expected)):
        return False
    if {row["name"] for row in rows} != set(expected):
        return False
    for row in rows:
        if row["policy_state"] == "paused":
            continue  # Explicit owner pause is the controller's intentional exclusion.
        ttl = row["liveness_ttl_seconds"]
        observed = row["healthy_observed_at"]
        checked = row["checked_at"]
        if not (
            row["policy_state"] == "active"
            and row["observed_state"] == "healthy"
            and type(row["boot_epoch"]) is int
            and row["boot_epoch"] > 0
            and type(row["observed_boot_epoch"]) is int
            and row["observed_boot_epoch"] == row["boot_epoch"]
            and row["route_compatible"] is True
            and row["accepting_routes"] is True
            and type(ttl) is int
            and ttl > 0
            and isinstance(observed, datetime)
            and isinstance(checked, datetime)
            and observed <= checked <= observed + timedelta(seconds=ttl)
        ):
            return False
    return True


# ---------------------------------------------------------------------------
# Ping + state
# ---------------------------------------------------------------------------


async def ping_external_deadman(url: str, *, timeout_s: float = DEFAULT_PING_TIMEOUT_S) -> bool:
    """Send one fixed, bodyless GET; never log the target or response content.

    Redirects and environment proxies are disabled. Streaming avoids buffering
    an arbitrary response body. Cancellation propagates to the owning loop.
    """
    _positive_seconds(timeout_s)
    token = _deadman_http.set(True)
    try:
        parsed = httpx.URL(url)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            return False
        async with asyncio.timeout(timeout_s):
            async with httpx.AsyncClient(
                timeout=timeout_s, follow_redirects=False, trust_env=False
            ) as client:
                async with client.stream("GET", url) as response:
                    return 200 <= response.status_code < 300
    except Exception:
        logger.warning("external deadman: ping failed")
        return False
    finally:
        _deadman_http.reset(token)


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def get_last_deadman_success(pool: asyncpg.Pool) -> datetime | None:
    """Return the timestamp of the most recent successful ping, or ``None``.

    Read-only — safe to call from both the background loop below and
    ``InfraStateSource``'s staleness check. A row per successful tick is a
    small, bounded write rate (one row per ``DEFAULT_DEADMAN_CHECK_INTERVAL_S``
    at most), so the "latest row wins" pattern needs no separate debounce
    table (unlike ``deploy_drift``'s first-detected/escalated markers, which
    track a *transition*, not a steady heartbeat).
    """
    row = await pool.fetchrow(
        """
        SELECT ts FROM public.audit_log
        WHERE action = $1
        ORDER BY ts DESC LIMIT 1
        """,
        _PING_SUCCESS_ACTION,
    )
    if row is None:
        return None
    return _as_aware_utc(row["ts"])


async def run_external_deadman_check(
    pool: asyncpg.Pool,
    url: str,
    *,
    expected_names: tuple[str, ...] = (),
    tick_id: uuid.UUID | None = None,
    timeout_s: float = DEFAULT_CHECK_TIMEOUT_S,
) -> dict[str, Any]:
    """Withhold the heartbeat unless a committed write/read and fleet check pass.

    A durable tick claim precedes HTTP. A cancelled/lost HTTP reply is NOT
    retried for that tick: the outside service may already have received it.
    The claim is not a success receipt. No endpoint or raw diagnostic is stored.
    """
    _positive_seconds(timeout_s)
    if not url:
        return {"success": False, "cause": "unconfigured"}
    tick_id = uuid.uuid4() if tick_id is None else tick_id
    if type(tick_id) is not uuid.UUID:
        raise ValueError("invalid deadman tick identity")
    phase = "database_unavailable"
    try:
        async with asyncio.timeout(timeout_s):
            async with pool.acquire(timeout=_DB_TIMEOUT_S) as connection:
                async with connection.transaction():
                    if not await connection.fetchval(
                        "SELECT pg_try_advisory_xact_lock($1::bigint)", _TICK_LOCK
                    ):
                        return {"success": False, "cause": "tick_busy"}
                    if (
                        await connection.fetchval(
                            "SELECT id FROM public.audit_log WHERE actor = $1 "
                            "AND action = $2 AND request_id = $3 LIMIT 1",
                            _DEADMAN_ACTOR,
                            _TICK_ACTION,
                            tick_id,
                        )
                        is not None
                    ):
                        return {"success": False, "cause": "duplicate_tick"}
                    phase = "fleet_unavailable"
                    rows = await connection.fetch(_FLEET_SQL, list(expected_names))
                    if not _fleet_ready(rows, expected_names):
                        return {"success": False, "cause": "fleet_unhealthy"}
                    phase = "database_unavailable"
                    claim = await audit_router.append(
                        connection,
                        _DEADMAN_ACTOR,
                        _TICK_ACTION,
                        target="external_deadman",
                        request_id=tick_id,
                        result="claimed",
                    )
            # A separate acquisition observes the committed claim, not our own
            # uncommitted write. A commit-loss ambiguity withholds this tick.
            if type(claim) is not int or claim <= 0:
                return {"success": False, "cause": "database_unavailable"}
            actual = await pool.fetchval(
                "SELECT id FROM public.audit_log WHERE id = $1 AND request_id = $2 "
                "AND actor = $3 AND action = $4",
                claim,
                tick_id,
                _DEADMAN_ACTOR,
                _TICK_ACTION,
                timeout=_DB_TIMEOUT_S,
            )
            if type(actual) is not int or actual != claim:
                return {"success": False, "cause": "database_unavailable"}
            phase = "ping_failed"
            if not await ping_external_deadman(url):
                return {"success": False, "cause": "ping_failed"}
            phase = "database_unavailable"
            receipt = await audit_router.append(
                pool,
                _DEADMAN_ACTOR,
                _PING_SUCCESS_ACTION,
                target="external_deadman",
                request_id=tick_id,
                result="success",
            )
            if type(receipt) is not int or receipt <= 0:
                return {"success": False, "cause": "database_unavailable"}
            return {"success": True, "recorded": True}
    except Exception:
        logger.warning("external deadman: tick withheld (%s)", phase)
        return {"success": False, "cause": phase}


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------


async def run_external_deadman_loop(
    db: DatabaseManager,
    *,
    url: str,
    interval_s: float = DEFAULT_DEADMAN_CHECK_INTERVAL_S,
) -> None:
    """Ping *url* every ``interval_s`` until cancelled.

    Sleeps first, mirroring ``run_migration_drift_loop`` — avoids a real
    outbound call at every process boot (dev reloads, full-lifespan tests)
    before the first tick actually matters. A single tick's failure is
    logged and swallowed so one bad tick never kills the loop.
    """
    _positive_seconds(interval_s)
    while True:
        await asyncio.sleep(interval_s)
        try:
            pool = db.pool("switchboard")
        except KeyError:
            logger.warning("external deadman: switchboard pool unavailable, skipping tick")
            continue
        try:
            # Bucket identity survives loop supervision/restarts and suppresses
            # duplicate submissions for the same configured cadence.
            bucket = int(datetime.now(UTC).timestamp() // interval_s)
            tick_id = uuid.uuid5(_TICK_NAMESPACE, f"{float(interval_s)}:{bucket}")
            summary = await run_external_deadman_check(
                pool, url, expected_names=tuple(db.configured_butler_names), tick_id=tick_id
            )
            logger.info("external_deadman_check: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("external_deadman_check: tick failed")
