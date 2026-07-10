"""Proactive credential lifecycle notifications (bu-1lb5j).

The /secrets passport page is reactive and page-only: a credential can sit
expired or expiring for days before anyone happens to look at the dashboard
(the motivating incident: SPOTIFY_ACCESS_TOKEN sat expired for 25 days before
the owner noticed a red row). This module is a scheduled, deterministic (zero
LLM) scan that pushes a proactive owner notification the moment a credential
transitions into a state that needs attention, instead of waiting for a page
visit.

Design
------
- Reuses the exact same per-family fetch helpers as
  ``GET /api/secrets/inventory`` (``butlers.api.routers.secrets_v2``), so the
  notification path can never disagree with what the owner sees on the
  /secrets page — one state-derivation source of truth.
- "Needs attention" is ``state in {"expiring", "failing", "expired"}``
  (``_ATTENTION_STATES``). ``warn`` (never probed) and the FE-only synthetic
  states (``scope_mismatch``, ``revoked``, ``rotating``) are excluded — this
  job only pushes for credentials that are genuinely dying or dead.
- Debounce ("once per state transition, not per check") is persisted in
  ``public.audit_log`` — no new migration. Each successful delivery writes an
  ``action="lifecycle_state_notified"`` row with ``note=<state>``; the next
  scan reads the most recent such row per credential key and only re-notifies
  when the current state differs from what was last delivered. This
  read-then-write debounce assumes a single dashboard-api instance (the
  current deployment: one ``dashboard-api`` container, no replica scaling in
  ``docker-compose.yml``) — two concurrent replicas racing the same scan
  interval could both read "not yet notified" and double-send before either
  writes its marker. Revisit with a proper claim (e.g. an advisory lock or a
  unique constraint) before this job is ever run with more than one replica.
- Delivery reuses the same gating and dispatch primitives ``notify()`` uses
  for the owner-default page (quiet hours via
  ``butlers.core.approvals_policy``, context-bus dnd/sleeping via
  ``butlers.core.attention_ledger.get_suppressing_context_signal``,
  and the Switchboard ``deliver()`` dispatch path used by the insight
  delivery cycle in ``butlers.scheduled_jobs``), and every outcome is
  recorded via ``record_attention_event`` so it shows up honestly as
  delivered/suppressed/deferred in the attention ledger. Priority is
  "medium" — important, but not the priority>=90 tier that bypasses quiet
  hours — so quiet hours genuinely defer it, per the bead's guidance.
- Why this doesn't call the ``notify()`` MCP tool directly (bu-qvnce.8
  doctrine question): ``notify()`` is a closure defined inside
  ``register_notification_tools(ctx, mcp, _core_tool)``
  (``butlers.core_tools._notifications``), bound to a specific *butler
  daemon's* live runtime (``ctx.daemon``, its ``switchboard_client`` MCP
  connection, its DB pool). This job runs as an ``asyncio.Task`` inside the
  dashboard-api FastAPI process (see ``butlers.api.app.lifespan``), which
  never instantiates a butler ``Daemon``/``ToolContext`` and has no
  ``switchboard_client`` to call through — there is no live MCP tool to
  invoke from here, only a process-boundary away. What *is* importable from
  this process is the same set of underlying primitives ``notify()`` itself
  calls: the quiet-hours/context-bus gate functions above, the attention
  ledger writer, and ``butlers.tools.switchboard.notification.deliver``
  (the same plain-function dispatch path ``notify()`` uses for its own
  switchboard-self-delivery branch, i.e. no MCP hop). So this job composes
  the identical primitives rather than re-deriving their logic — a single
  source of truth is preserved for *what* the gate decides, even though the
  call site is a second, process-boundary-forced consumer alongside
  ``notify()`` and ``delivery_cycle()``. Known gap: this does not consult
  the *per-butler* delivery-preferences override
  (``butlers.core.temporal.delivery_db.get_delivery_preferences`` /
  ``should_defer_notification``, the first, older quiet-hours gate in
  ``notify()``) — that gate is keyed on a specific butler_name's own
  preferences row, and this job has no single natural butler identity to
  key it on. Tracked as a follow-up rather than blocking this change (see
  PR #2951 discussion).
- Natural future home: bu-a63hn (background verification loop) once it
  exists. This job does not block on it; it is a standalone scheduled check.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from butlers.api.db import DatabaseManager
from butlers.api.routers import audit as audit_router
from butlers.api.routers.secrets_v2 import (
    PROVIDER_CATALOG,
    _fetch_cli_secrets,
    _fetch_system_secrets,
    _fetch_user_secrets,
    _infer_provider_from_type,
)
from butlers.core.approvals_policy import (
    get_approvals_policy_quiet_hours,
    should_suppress_by_policy,
)
from butlers.core.attention_ledger import get_suppressing_context_signal, record_attention_event
from butlers.core.credential_keys import normalize_credential_key
from butlers.credential_store import resolve_owner_telegram_recipient

logger = logging.getLogger(__name__)

# States that warrant a proactive owner push. Deliberately excludes 'warn'
# (never-probed — not yet actionable) and the frontend-only synthetic states
# the backend never emits ('scope_mismatch', 'revoked', 'rotating').
_ATTENTION_STATES = frozenset({"expiring", "failing", "expired"})

_LIFECYCLE_NOTIFIED_ACTION = "lifecycle_state_notified"
_LIFECYCLE_ACTOR = "secrets_lifecycle_check"

_STATE_DESCRIPTIONS: dict[str, str] = {
    "expiring": "is expiring soon",
    "failing": "is failing its health probe",
    "expired": "has expired",
}

_DASHBOARD_PORT_DEFAULT = "41200"


def _dashboard_url() -> str:
    """Resolve the dashboard base URL from the environment.

    Mirrors the lookup in ``butlers.startup_guard._default_dashboard_url``
    (kept local rather than imported — that module is about a distinct
    concern, the Google-credential startup guard).
    """
    return os.environ.get(
        "DASHBOARD_URL",
        f"http://localhost:{os.environ.get('DASHBOARD_PORT', _DASHBOARD_PORT_DEFAULT)}",
    )


@dataclass(frozen=True)
class CredentialSnapshot:
    """One credential's current lifecycle-relevant state, for the scan below."""

    key: str  # canonical credential key, e.g. "s:BUTLER_TELEGRAM_TOKEN" / "u:google" / "c:claude"
    family: str  # "system" | "cli" | "user"
    label: str
    state: str
    provider: str | None = None  # user family only — display provider slug


async def _collect_snapshots(db: DatabaseManager) -> list[CredentialSnapshot]:
    """Collect current lifecycle-relevant state for every known credential.

    Reuses the same per-family fetch helpers as ``GET /api/secrets/inventory``
    so this scan can never see a different state than the /secrets page does.
    Mirrors get_inventory()'s scan shape (per-butler schemas + shared pool),
    minus the response-model assembly this job doesn't need.
    """
    snapshots: list[CredentialSnapshot] = []

    for butler_name in db.butler_names:
        try:
            pool = db.pool(butler_name)
        except KeyError:
            continue
        for row in await _fetch_system_secrets(pool, butler_name):
            snapshots.append(
                CredentialSnapshot(
                    key=normalize_credential_key("system", row.key),
                    family="system",
                    label=row.key,
                    state=row.state,
                )
            )

    try:
        shared_pool = db.credential_shared_pool()
    except KeyError:
        return snapshots

    # Shared application config (public.butler_secrets), excluding cli/cli-auth
    # rows — those are the CLI family, fetched separately below. Mirrors
    # get_inventory()'s exclusion so this job never double-counts a row.
    for row in await _fetch_system_secrets(shared_pool, "shared-public"):
        if row.category in ("cli", "cli-auth"):
            continue
        snapshots.append(
            CredentialSnapshot(
                key=normalize_credential_key("system", row.key),
                family="system",
                label=row.key,
                state=row.state,
            )
        )

    for row in await _fetch_cli_secrets(shared_pool):
        snapshots.append(
            CredentialSnapshot(
                key=normalize_credential_key("cli", row.key),
                family="cli",
                label=row.key,
                state=row.state,
            )
        )

    # Owner-default projection (identity=None): the owner's own credentials
    # plus the primary Google account's companion-entity credentials — the
    # same set the owner sees by default on /secrets.
    for row in await _fetch_user_secrets(shared_pool, identity=None):
        provider = _infer_provider_from_type(row.type)
        provider_meta = PROVIDER_CATALOG.get(provider)
        snapshots.append(
            CredentialSnapshot(
                key=normalize_credential_key("user", provider),
                family="user",
                label=provider_meta.label if provider_meta is not None else provider,
                state=row.state,
                provider=provider,
            )
        )

    return snapshots


async def _last_notified_state(pool: Any, key: str) -> str | None:
    """Return the state we last successfully delivered a notification for.

    Read from ``public.audit_log`` (no new migration): the most recent
    ``lifecycle_state_notified`` row's ``note`` column IS the state string
    that was current at delivery time. Returns None (never notified, or the
    table/lookup is unavailable) so the caller treats that as "not yet
    debounced" rather than silently skipping a real transition.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT note FROM public.audit_log
            WHERE target = $1 AND action = $2
            ORDER BY ts DESC
            LIMIT 1
            """,
            key,
            _LIFECYCLE_NOTIFIED_ACTION,
        )
    except Exception:
        logger.debug(
            "secrets_lifecycle_check: last-notified lookup failed for key=%s", key, exc_info=True
        )
        return None
    return row["note"] if row is not None else None


async def _check_suppression(pool: Any) -> str | None:
    """Decide whether a medium-priority owner notification should be suppressed.

    Mirrors notify()'s owner-default gate (quiet hours via
    ``public.approvals_policy``, then context-bus dnd/sleeping) — see
    ``core_tools/_notifications.py`` lines ~588-690 for the reference
    implementation this deliberately parallels. Returns a machine-readable
    reason string when suppressed, else None.
    """
    try:
        policy = await get_approvals_policy_quiet_hours(pool)
    except Exception:
        logger.debug("secrets_lifecycle_check: quiet-hours policy lookup failed", exc_info=True)
        policy = None

    if policy is not None:
        tz_name = policy.get("timezone", "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        now_local = datetime.now(UTC).astimezone(tz)
        if should_suppress_by_policy(policy, current_hour=now_local.hour):
            return "quiet_hours"

    context_signal = await get_suppressing_context_signal(pool)
    if context_signal is not None:
        return f"context_bus:{context_signal}"

    return None


def _compose_message(snapshot: CredentialSnapshot, dashboard_url: str) -> str:
    """Build the owner-facing notification body for one lifecycle transition.

    Always includes a deep link to the exact card on /secrets
    (``?focus=<key>``). For OAuth providers, also includes the re-authorize
    URL — ``GET /api/oauth/<provider>/start`` redirects straight to the
    provider's consent screen, so it's directly clickable from a Telegram
    message without a separate POST round-trip.
    """
    focus_url = f"{dashboard_url}/secrets?focus={quote(snapshot.key, safe='')}"
    description = _STATE_DESCRIPTIONS.get(snapshot.state, f"is now '{snapshot.state}'")
    lines = [f"Credential '{snapshot.label}' {description}.", focus_url]

    if snapshot.provider is not None:
        provider_meta = PROVIDER_CATALOG.get(snapshot.provider)
        if provider_meta is not None and provider_meta.kind == "oauth":
            lines.append(f"Re-authorize: {dashboard_url}/api/oauth/{snapshot.provider}/start")

    return "\n".join(lines)


async def run_secrets_lifecycle_check(db: DatabaseManager) -> dict[str, Any]:
    """Scan every credential and push a debounced owner notification for
    each NEW transition into an attention state.

    Returns a summary dict: ``{scanned, attention, delivered, suppressed,
    errors}``. Never raises — a failure in one credential's notify attempt is
    logged and counted in ``errors``, and the scan continues to the rest.
    """
    try:
        shared_pool = db.credential_shared_pool()
    except KeyError:
        logger.warning("secrets_lifecycle_check: no shared credential pool configured; skipping")
        return {"scanned": 0, "attention": 0, "delivered": 0, "suppressed": 0, "errors": 0}

    snapshots = await _collect_snapshots(db)
    attention = [s for s in snapshots if s.state in _ATTENTION_STATES]

    dashboard_url = _dashboard_url()
    delivered = suppressed = errors = 0

    for snapshot in attention:
        try:
            last_state = await _last_notified_state(shared_pool, snapshot.key)
            if last_state == snapshot.state:
                # Already notified for this exact state — debounce.
                continue

            suppress_reason = await _check_suppression(shared_pool)
            if suppress_reason is not None:
                suppressed += 1
                await record_attention_event(
                    shared_pool,
                    origin_butler=_LIFECYCLE_ACTOR,
                    source="notify",
                    outcome="suppressed",
                    channel="telegram",
                    intent="send",
                    priority="medium",
                    reason=suppress_reason,
                    dedup_key=snapshot.key,
                )
                continue

            recipient = await resolve_owner_telegram_recipient(shared_pool)
            if not recipient:
                errors += 1
                logger.warning(
                    "secrets_lifecycle_check: no telegram recipient configured for owner; "
                    "cannot deliver notification for key=%s",
                    snapshot.key,
                )
                # A genuine terminal failure — must be recorded, not silent,
                # or an unresolvable recipient reads identically to quiet-hours
                # discipline in the ledger.
                await record_attention_event(
                    shared_pool,
                    origin_butler=_LIFECYCLE_ACTOR,
                    source="notify",
                    outcome="deferred",
                    channel="telegram",
                    intent="send",
                    priority="medium",
                    reason="no_recipient_configured",
                    dedup_key=snapshot.key,
                )
                continue

            message = _compose_message(snapshot, dashboard_url)

            # Import locally: roster/ modules aren't always importable at
            # collection time (e.g. minimal test environments), and this is
            # the only place in this module that needs the live dispatch
            # path — mirrors the same local-import pattern used by
            # butlers.scheduled_jobs._build_switchboard_insight_notify_fn.
            from butlers.tools.switchboard.notification.deliver import deliver

            deliver_result = await deliver(
                shared_pool,
                channel="telegram",
                message=message,
                recipient=recipient,
                source_butler="switchboard",
                metadata={"origin": _LIFECYCLE_ACTOR, "credential_key": snapshot.key},
            )

            if deliver_result.get("status") == "failed":
                errors += 1
                await record_attention_event(
                    shared_pool,
                    origin_butler=_LIFECYCLE_ACTOR,
                    source="notify",
                    outcome="deferred",
                    channel="telegram",
                    intent="send",
                    priority="medium",
                    reason=f"delivery_error:{deliver_result.get('error', 'unknown')}",
                    dedup_key=snapshot.key,
                )
                continue

            delivered += 1
            await record_attention_event(
                shared_pool,
                origin_butler=_LIFECYCLE_ACTOR,
                source="notify",
                outcome="delivered",
                channel="telegram",
                intent="send",
                priority="medium",
                reason=f"state_transition:{snapshot.state}",
                dedup_key=snapshot.key,
                notification_ref=deliver_result.get("notification_id"),
            )
            # Debounce marker: only written on confirmed delivery, so a
            # suppressed/deferred attempt correctly retries on the next scan
            # instead of going silent for the rest of the credential's life.
            await audit_router.append(
                shared_pool,
                _LIFECYCLE_ACTOR,
                _LIFECYCLE_NOTIFIED_ACTION,
                target=snapshot.key,
                note=snapshot.state,
            )
        except Exception as exc:
            errors += 1
            logger.exception(
                "secrets_lifecycle_check: unexpected error notifying for key=%s", snapshot.key
            )
            # A genuine terminal failure — must be recorded, not silent, or an
            # exception deep in the dispatch path (e.g. a DB error) reads
            # identically to quiet-hours discipline in the ledger.
            await record_attention_event(
                shared_pool,
                origin_butler=_LIFECYCLE_ACTOR,
                source="notify",
                outcome="deferred",
                channel="telegram",
                intent="send",
                priority="medium",
                reason=f"unexpected_error:{type(exc).__name__}",
                dedup_key=snapshot.key,
            )

    return {
        "scanned": len(snapshots),
        "attention": len(attention),
        "delivered": delivered,
        "suppressed": suppressed,
        "errors": errors,
    }


# Default cadence for the background loop below. 30 minutes is frequent
# enough that a credential doesn't sit silently attention-needing for long,
# without hammering every butler schema's pool on a tight interval.
DEFAULT_SCAN_INTERVAL_S = 1800


async def run_secrets_lifecycle_loop(
    db: DatabaseManager,
    *,
    interval_s: float = DEFAULT_SCAN_INTERVAL_S,
) -> None:
    """Run ``run_secrets_lifecycle_check`` every ``interval_s`` until cancelled.

    Sleeps first rather than scanning immediately on startup — avoids a
    real-DB burst at every process boot (dev reloads, and any test that
    exercises the full API lifespan via ``with TestClient(app) as client:``)
    before the first useful check actually matters. A single scan's failure
    is logged and swallowed (mirrors ``run_secrets_lifecycle_check``'s own
    per-credential fault isolation) so one bad tick never kills the loop.

    Intended to be wrapped in ``asyncio.create_task()`` from the API lifespan
    and cancelled on shutdown — see ``butlers.api.app.lifespan``.

    Raises ``ValueError`` immediately for a non-positive ``interval_s`` rather
    than spinning a tight zero-sleep loop that would hammer every butler
    schema's pool — the caller (``butlers.api.app.lifespan``) already
    validates and falls back to ``DEFAULT_SCAN_INTERVAL_S`` before calling
    this, so this is a defense-in-depth guard for any other caller.
    """
    if interval_s <= 0:
        raise ValueError(f"interval_s must be a positive number, got {interval_s!r}")
    while True:
        await asyncio.sleep(interval_s)
        try:
            summary = await run_secrets_lifecycle_check(db)
            logger.info("secrets_lifecycle_check: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("secrets_lifecycle_check: scan failed")
