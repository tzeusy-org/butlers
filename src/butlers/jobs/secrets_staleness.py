"""Background credential verification: staleness re-probe loop + probe-all sweep (bu-a63hn).

The /secrets passport page is otherwise only ever verified when a human clicks
a per-row "probe" button — the motivating incident: the owner hand-clicked
~20 probes on 2026-07-05 to clear the needs-hand bucket after credentials sat
un-probed for days. This module adds two things that both dispatch through the
EXACT same probe functions the dashboard endpoints use
(``probe_user_credential`` / ``probe_system_credential`` in
``butlers.api.routers.secrets_v2``, and ``test_api_key`` — the cli-auth test —
in ``butlers.api.routers.cli_auth``), called directly as plain async functions
(no HTTP round-trip) so persistence semantics never diverge: probe_log row +
test-state cache columns + audit stamp, same-transaction invariant, exactly as
a manual click would produce.

1. ``run_secrets_staleness_loop`` — a periodic background scan (wired into the
   dashboard-api lifespan, same shape as ``jobs.secrets_lifecycle``'s
   30-minute notifier loop from PR #2951) that re-probes any credential whose
   ``last_verified`` is stale: never verified, or older than
   ``staleness_s`` (default 24h, env-configurable — see
   ``butlers.api.app``).
2. ``run_secrets_probe_all`` — the engine behind ``POST
   /api/secrets/probe-all`` (the passport header's "probe all" button):
   sweeps every probeable credential regardless of staleness and returns a
   per-row outcome list.

Both share the same collection (``_collect_probe_targets``, mirroring
``jobs.secrets_lifecycle._collect_snapshots`` — same per-family fetch helpers
as ``GET /api/secrets/inventory``, so this scan can never disagree with what
the page or the lifecycle notifier already see) and the same serialized sweep
engine (``_sweep``): probes run ONE AT A TIME (no concurrency, no thundering
herd against provider APIs), and a per-provider-group circuit breaker skips
the rest of a group's targets for the remainder of the current sweep once
that group racks up ``_CIRCUIT_BREAK_THRESHOLD`` consecutive failures — a
breaker trip does not persist across sweeps, it only protects one pass from
hammering an already-down provider.

Scope notes
-----------
- ``never_set`` rows are skipped everywhere in this module — there is
  nothing to verify for a credential with no value.
- Plain ``cli`` category rows (the passport's rotate-endpoint tokens, e.g.
  arbitrary pasted API keys with no registered auth flow) have no live test
  path at all today — only ``cli-auth/<provider>`` rows (device_code/api_key
  flows persisted by ``butlers.cli_auth.persistence``) expose ``POST
  /api/cli-auth/<provider>/test``. Rows without a matching ``PROVIDERS``
  entry are skipped rather than guessed at.
- The user-credential collection reuses the identity=None owner-default
  projection (same one ``_collect_snapshots`` uses) — the owner's own
  credentials plus the primary Google account's companion-entity rows. Full
  multi-account enumeration is out of scope here (tracked separately); when
  bu-4v5es (capability-level probes) lands, this module does not block on it.
- The system-probe rate limit (``_check_system_probe_rate_limit``, 5s/key) is
  respected automatically — it's module state inside
  ``probe_system_credential`` itself, and this module calls that exact
  function. A 429 from that guard (or any other HTTPException) is caught and
  reported as a skipped outcome, never raised into the caller.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from butlers.api.db import DatabaseManager
from butlers.api.degraded import DegradedSources
from butlers.api.routers.secrets_v2 import (
    DEFAULT_STALENESS_S,
    _fetch_cli_secrets,
    _fetch_system_secrets,
    _fetch_user_secrets,
    _infer_provider_from_type,
    probe_system_credential,
    probe_user_credential,
)
from butlers.cli_auth.registry import PROVIDERS
from butlers.core.credential_keys import normalize_credential_key

logger = logging.getLogger(__name__)

# never_set rows have nothing to verify — skip them everywhere in this module.
_SKIP_STATES = frozenset({"never_set"})

#: Default scan cadence for the staleness loop. 30 minutes matches
#: jobs.secrets_lifecycle's notifier cadence (PR #2951) — frequent enough
#: that a stale credential doesn't sit un-reprobed for long, without hammering
#: every butler schema's pool on a tight interval.
DEFAULT_STALENESS_SCAN_INTERVAL_S: float = 1800

#: After this many consecutive failures for the same provider group within a
#: single sweep, remaining targets in that group are skipped for the rest of
#: THIS sweep — a provider outage should not retry-hammer every account under
#: it. Resets every sweep; not a lasting backoff.
_CIRCUIT_BREAK_THRESHOLD = 3


@dataclass(frozen=True)
class ProbeTarget:
    """One probeable credential, pre-resolved to whatever the underlying
    ``probe_*`` function needs so it can be dispatched with zero extra I/O."""

    canonical_key: str  # "u:google" / "s:BUTLER_TELEGRAM_TOKEN" / "c:cli-auth/codex"
    family: str  # "system" | "user" | "cli"
    label: str
    state: str
    last_verified: datetime | None
    circuit_group: str  # provider-ish grouping key for the breaker
    # Family-specific dispatch args (exactly one set populated per family):
    system_key: str | None = None
    user_provider: str | None = None
    user_identity: UUID | None = None
    cli_provider: str | None = None


@dataclass(frozen=True)
class ProbeOutcome:
    """Result of dispatching one ``ProbeTarget`` — either a real probe result
    or a skip (rate-limited, circuit-broken, or an unexpected error)."""

    key: str
    family: str
    label: str
    ok: bool | None  # None means skipped — never probed
    message: str | None = None
    skipped: bool = False
    skip_reason: str | None = None


# ---------------------------------------------------------------------------
# Collection — same per-family fetch helpers as GET /api/secrets/inventory
# ---------------------------------------------------------------------------


async def _collect_probe_targets(
    db: DatabaseManager,
    *,
    tracker: DegradedSources | None = None,
) -> list[ProbeTarget]:
    """Enumerate every probeable credential.

    Mirrors ``jobs.secrets_lifecycle._collect_snapshots``'s scan shape (same
    per-family fetch helpers, same owner-default user projection) so this
    module can never see a different set of credentials than the /secrets
    page or the lifecycle notifier do. A failed per-butler fetch is omitted
    while healthy butlers and the shared system/CLI/user families still run;
    when provided, ``tracker`` records that genuine partial failure.
    """
    targets: list[ProbeTarget] = []

    for butler_name in db.butler_names:
        try:
            pool = db.pool(butler_name)
        except KeyError:
            continue
        try:
            rows = await _fetch_system_secrets(pool, butler_name, tracker=tracker)
        except Exception:  # noqa: BLE001
            if tracker is not None:
                tracker.mark(
                    butler_name,
                    msg=f"Failed to collect staleness targets for butler {butler_name!r}",
                )
            else:
                logger.warning(
                    "secrets_staleness: failed to collect targets for butler %s",
                    butler_name,
                    exc_info=True,
                )
            continue
        for row in rows:
            if row.state in _SKIP_STATES:
                continue
            targets.append(
                ProbeTarget(
                    canonical_key=normalize_credential_key("system", row.key),
                    family="system",
                    label=row.key,
                    state=row.state,
                    last_verified=row.last_verified,
                    circuit_group=f"system:{row.key}",
                    system_key=row.key,
                )
            )

    try:
        shared_pool = db.credential_shared_pool()
    except KeyError:
        return targets

    # Shared application config (public.butler_secrets), excluding cli/cli-auth
    # rows — those are the CLI family, collected separately below. Mirrors
    # _collect_snapshots's exclusion so this scan never double-counts a row.
    for row in await _fetch_system_secrets(shared_pool, "shared-public", tracker=tracker):
        if row.category in ("cli", "cli-auth"):
            continue
        if row.state in _SKIP_STATES:
            continue
        targets.append(
            ProbeTarget(
                canonical_key=normalize_credential_key("system", row.key),
                family="system",
                label=row.key,
                state=row.state,
                last_verified=row.last_verified,
                circuit_group=f"system:{row.key}",
                system_key=row.key,
            )
        )

    for row in await _fetch_cli_secrets(shared_pool):
        if row.state in _SKIP_STATES:
            continue
        if not row.key.startswith("cli-auth/"):
            # Plain 'cli' rows (rotate-endpoint tokens) have no live test
            # path — only cli-auth/<provider> rows expose POST
            # /api/cli-auth/<provider>/test. Nothing to dispatch to.
            continue
        provider_name = row.key[len("cli-auth/") :]
        if provider_name not in PROVIDERS:
            continue
        targets.append(
            ProbeTarget(
                canonical_key=normalize_credential_key("cli", row.key),
                family="cli",
                label=row.key,
                state=row.state,
                last_verified=row.last_verified,
                circuit_group=f"cli:{provider_name}",
                cli_provider=provider_name,
            )
        )

    # Owner-default projection (identity=None): the owner's own credentials
    # plus the primary Google account's companion-entity credentials — the
    # same set the owner sees by default on /secrets and the same set
    # jobs.secrets_lifecycle already watches for lifecycle notifications.
    for row in await _fetch_user_secrets(shared_pool, identity=None):
        if row.state in _SKIP_STATES:
            continue
        provider = _infer_provider_from_type(row.type)
        targets.append(
            ProbeTarget(
                canonical_key=normalize_credential_key("user", provider),
                family="user",
                label=provider,
                state=row.state,
                last_verified=row.last_verified,
                circuit_group=f"user:{provider}",
                user_provider=provider,
                user_identity=UUID(row.entity_id),
            )
        )

    return targets


def _is_stale(target: ProbeTarget, *, staleness_s: float, now: datetime) -> bool:
    """A target is stale when it has never been verified, or its last probe
    is at least ``staleness_s`` seconds old."""
    if target.last_verified is None:
        return True
    last_verified = target.last_verified
    if last_verified.tzinfo is None:
        last_verified = last_verified.replace(tzinfo=UTC)
    return (now - last_verified) >= timedelta(seconds=staleness_s)


# ---------------------------------------------------------------------------
# Dispatch — call the EXACT dashboard probe functions directly (no HTTP hop)
# ---------------------------------------------------------------------------


async def _dispatch_probe(db: DatabaseManager, target: ProbeTarget) -> ProbeOutcome:
    """Call the same probe function the dashboard endpoints use, directly as
    a plain async function — identical persistence semantics (probe_log +
    cache columns + audit stamp, same-transaction invariant), zero duplicated
    probe logic.

    Never raises: an HTTPException (rate limit, 404, transaction failure) or
    any unexpected error is converted into a skipped ``ProbeOutcome`` so one
    bad credential never aborts the sweep.
    """
    try:
        if target.family == "system":
            assert target.system_key is not None
            response = await probe_system_credential(target.system_key, db=db)
            result = response.data
            return ProbeOutcome(
                key=target.canonical_key,
                family=target.family,
                label=target.label,
                ok=result.ok,
                message=result.message,
            )
        if target.family == "user":
            assert target.user_provider is not None
            response = await probe_user_credential(
                target.user_provider, identity=target.user_identity, db=db
            )
            result = response.data
            return ProbeOutcome(
                key=target.canonical_key,
                family=target.family,
                label=target.label,
                ok=result.ok,
                message=result.message,
            )
        if target.family == "cli":
            assert target.cli_provider is not None
            # Lazy import: cli_auth.py already lazily imports FROM secrets_v2
            # to avoid a cycle (see its _persist_test_outcome); this is the
            # same edge in reverse, kept lazy for symmetry and because this
            # module is imported by secrets_v2's own /probe-all endpoint.
            from butlers.api.routers.cli_auth import test_api_key

            cli_result = await test_api_key(target.cli_provider, db_manager=db)
            return ProbeOutcome(
                key=target.canonical_key,
                family=target.family,
                label=target.label,
                ok=cli_result.success,
                message=cli_result.detail,
            )
        raise ValueError(f"unknown probe target family {target.family!r}")
    except HTTPException as exc:
        skip_reason = "rate_limited" if exc.status_code == 429 else f"http_{exc.status_code}"
        logger.debug(
            "secrets_staleness: probe skipped for key=%s (%s)", target.canonical_key, skip_reason
        )
        return ProbeOutcome(
            key=target.canonical_key,
            family=target.family,
            label=target.label,
            ok=None,
            skipped=True,
            skip_reason=skip_reason,
        )
    except Exception:
        logger.exception(
            "secrets_staleness: probe dispatch failed for key=%s", target.canonical_key
        )
        return ProbeOutcome(
            key=target.canonical_key,
            family=target.family,
            label=target.label,
            ok=None,
            skipped=True,
            skip_reason="error",
        )


async def _sweep(db: DatabaseManager, targets: list[ProbeTarget]) -> list[ProbeOutcome]:
    """Serially probe every target — no concurrency, no thundering herd —
    with a per-provider-group circuit breaker (see module docstring)."""
    outcomes: list[ProbeOutcome] = []
    consecutive_failures: dict[str, int] = {}
    tripped: set[str] = set()

    for target in targets:
        if target.circuit_group in tripped:
            outcomes.append(
                ProbeOutcome(
                    key=target.canonical_key,
                    family=target.family,
                    label=target.label,
                    ok=None,
                    skipped=True,
                    skip_reason="circuit_open",
                )
            )
            continue

        outcome = await _dispatch_probe(db, target)
        outcomes.append(outcome)

        # An unexpected dispatch error (skip_reason="error") means the probe
        # never got a real answer from the provider — exactly the "provider
        # is down" case the breaker exists to catch (a hard-down provider
        # raises/times out rather than returning ok=False). Count it as a
        # failure too, or a full outage would never trip the breaker and
        # every remaining target in the group pays for a fresh timeout.
        # Rate-limited/circuit-open skips are not the provider's fault, so
        # they neither advance nor reset the streak.
        if outcome.ok is False or outcome.skip_reason == "error":
            count = consecutive_failures.get(target.circuit_group, 0) + 1
            consecutive_failures[target.circuit_group] = count
            if count >= _CIRCUIT_BREAK_THRESHOLD:
                tripped.add(target.circuit_group)
                logger.warning(
                    "secrets_staleness: circuit breaker tripped for group=%s "
                    "after %d consecutive failures",
                    target.circuit_group,
                    count,
                )
        elif outcome.ok is True:
            consecutive_failures[target.circuit_group] = 0

    return outcomes


def _summarize(outcomes: list[ProbeOutcome]) -> dict[str, int]:
    return {
        "probed": sum(1 for o in outcomes if not o.skipped),
        "ok": sum(1 for o in outcomes if o.ok is True),
        "failed": sum(1 for o in outcomes if o.ok is False),
        "skipped": sum(1 for o in outcomes if o.skipped),
    }


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def run_secrets_staleness_check(
    db: DatabaseManager, *, staleness_s: float = DEFAULT_STALENESS_S
) -> dict[str, Any]:
    """Re-probe every credential whose ``last_verified`` is stale (or never
    set). Returns a summary dict: ``{scanned, stale, probed, ok, failed,
    skipped}``, with ``sources_degraded`` added when a per-butler credential
    fetch failed.

    Never raises — collection failure (e.g. no shared pool configured)
    degrades to an empty scan; each individual probe is fault-isolated by
    ``_dispatch_probe``.
    """
    try:
        tracker = DegradedSources(logger)
        targets = await _collect_probe_targets(db, tracker=tracker)
    except Exception:
        logger.exception("secrets_staleness_check: target collection failed")
        return {"scanned": 0, "stale": 0, "probed": 0, "ok": 0, "failed": 0, "skipped": 0}

    now = datetime.now(UTC)
    stale = [t for t in targets if _is_stale(t, staleness_s=staleness_s, now=now)]
    outcomes = await _sweep(db, stale)
    summary = _summarize(outcomes)
    summary["scanned"] = len(targets)
    summary["stale"] = len(stale)
    if tracker.failed:
        summary["sources_degraded"] = tracker.names
    return summary


async def run_secrets_staleness_loop(
    db: DatabaseManager,
    *,
    interval_s: float = DEFAULT_STALENESS_SCAN_INTERVAL_S,
    staleness_s: float = DEFAULT_STALENESS_S,
) -> None:
    """Run ``run_secrets_staleness_check`` every ``interval_s`` until
    cancelled. Mirrors ``jobs.secrets_lifecycle.run_secrets_lifecycle_loop``'s
    shape: sleeps first (no real-DB burst at process boot / test client
    startup), swallows and logs a single tick's failure so it never kills the
    loop.

    Raises ``ValueError`` immediately for a non-positive ``interval_s`` — the
    caller (``butlers.api.app.lifespan``) already validates and falls back to
    the default before calling this; this is a defense-in-depth guard for any
    other caller.
    """
    if interval_s <= 0:
        raise ValueError(f"interval_s must be a positive number, got {interval_s!r}")
    while True:
        await asyncio.sleep(interval_s)
        try:
            summary = await run_secrets_staleness_check(db, staleness_s=staleness_s)
            logger.info("secrets_staleness_check: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("secrets_staleness_check: scan failed")


class ProbeAllAlreadyRunning(Exception):
    """Raised when a probe-all sweep is requested while one is already in flight."""


_PROBE_ALL_LOCK = asyncio.Lock()


async def run_secrets_probe_all(db: DatabaseManager) -> list[ProbeOutcome]:
    """Sweep EVERY probeable credential regardless of staleness — the engine
    behind ``POST /api/secrets/probe-all``.

    Same serialized dispatch + circuit breaker as the staleness loop; the
    only difference is no staleness filter. Guarded by a process-local lock
    so a double-click (or the staleness loop firing mid-sweep) cannot launch
    two overlapping full sweeps — raises ``ProbeAllAlreadyRunning`` instead of
    queueing behind the lock, so the caller can surface a prompt 429 rather
    than a caller silently waiting for someone else's sweep to finish.
    """
    if _PROBE_ALL_LOCK.locked():
        raise ProbeAllAlreadyRunning()
    async with _PROBE_ALL_LOCK:
        targets = await _collect_probe_targets(db)
        return await _sweep(db, targets)
