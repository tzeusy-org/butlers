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

- A periodic outbound ping to an operator-configured URL
  (``EXTERNAL_DEADMAN_URL``). Any 2xx response counts as success — this is
  the de facto contract most "dead man's switch" monitoring services
  (Healthchecks.io, Cronitor, UptimeRobot heartbeat, ...) already implement,
  so this module stays provider-agnostic and requires no vendor SDK.
- A record of the last successful ping, persisted to ``public.audit_log``
  (no new migration — the exact pattern ``butlers.jobs.deploy_drift``
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
from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx

from butlers.api.db import DatabaseManager
from butlers.api.routers import audit as audit_router

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Ping + state
# ---------------------------------------------------------------------------


async def ping_external_deadman(url: str, *, timeout_s: float = DEFAULT_PING_TIMEOUT_S) -> bool:
    """GET *url* and return whether it succeeded (2xx). Never raises.

    Any 2xx response counts as success, matching the near-universal
    dead-man's-switch contract of "hitting this URL at all is the signal" —
    no response-body parsing, no provider-specific auth.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(url)
        return 200 <= resp.status_code < 300
    except Exception as exc:
        logger.warning("external deadman: ping to %s failed: %s", url, exc)
        return False


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


async def run_external_deadman_check(pool: asyncpg.Pool, url: str) -> dict[str, Any]:
    """Run one ping tick: ping *url*, record success. Never raises.

    A failed ping is logged at WARNING (by :func:`ping_external_deadman`) but
    intentionally does NOT write an audit row — the absence of a fresh
    success row IS the staleness signal ``InfraStateSource`` reads; no
    separate failure bookkeeping is needed, and it keeps ``audit_log`` growth
    to "one row per successful tick" instead of growing during an outage too.
    """
    ok = await ping_external_deadman(url)
    if not ok:
        return {"success": False}
    try:
        await audit_router.append(
            pool,
            _DEADMAN_ACTOR,
            _PING_SUCCESS_ACTION,
            target=url,
            result="success",
        )
    except Exception:
        logger.warning("external deadman: failed to record successful ping", exc_info=True)
        return {"success": True, "recorded": False}
    return {"success": True, "recorded": True}


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
    if interval_s <= 0:
        raise ValueError(f"interval_s must be a positive number, got {interval_s!r}")
    while True:
        await asyncio.sleep(interval_s)
        try:
            pool = db.pool("switchboard")
        except KeyError:
            logger.warning("external deadman: switchboard pool unavailable, skipping tick")
            continue
        try:
            summary = await run_external_deadman_check(pool, url)
            logger.info("external_deadman_check: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("external_deadman_check: tick failed")
