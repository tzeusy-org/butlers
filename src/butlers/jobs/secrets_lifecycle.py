"""Proactive credential lifecycle notifications (bu-1lb5j).

The /secrets passport page is reactive and page-only: a credential can sit
expired or expiring for days before anyone happens to look at the dashboard.
This module is a scheduled, deterministic (zero LLM) scan that pushes a
proactive owner notification the moment a credential transitions into a state
that needs attention, instead of waiting for a page visit. Provider-managed
Spotify OAuth artifacts are excluded because their short-lived access tokens
rotate routinely; actionable Spotify health comes from the dedicated connector
status and refresh-failure path.

Design
------
- Reuses the exact same per-family fetch helpers as
  ``GET /api/secrets/inventory`` (``butlers.api.routers.secrets_v2``), then
  applies the same provider-managed Spotify exclusion as the frontend before
  producing notifications.
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
  for the owner-default page, applying the THREE gates in notify()'s own order
  (bu-178v1): (1) the per-butler ``delivery_preferences`` quiet-hours gate
  (``get_delivery_preferences`` → ``should_defer_notification``, keyed on the
  ``_DELIVERY_IDENTITY`` this job delivers under — see that constant's
  ``[decision]``), which DEFERS by enqueuing a ``notify.v1`` envelope the
  switchboard flusher redelivers at the next batch window; (2) the
  approvals-policy quiet-hours gate (``butlers.core.approvals_policy``); and
  (3) the context-bus dnd/sleeping gate
  (``butlers.core.attention_ledger.get_suppressing_context_signal``). Gates
  (2)/(3) SUPPRESS (drop, retry next scan). Every outcome is recorded via
  ``record_attention_event`` so it shows up honestly as
  delivered/deferred/suppressed/failed in the attention ledger (bu-hmdqz.3: a
  genuine delivery failure — no recipient, a transport error, an unexpected
  exception — is recorded as ``outcome="failed"``, never ``"deferred"``, which
  is reserved for a benign hold that resolves on its own). Priority is "medium"
  — important, but not the priority>=90 tier that bypasses quiet hours — so
  quiet hours genuinely defer it, per the bead's guidance.
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
  ``notify()`` and ``delivery_cycle()``. The *per-butler* delivery-preferences
  override (``get_delivery_preferences`` / ``should_defer_notification``,
  notify()'s first quiet-hours gate) IS now consulted (bu-178v1, closing the
  PR #2951 follow-up): this job has no butler identity of its own, so it keys
  the lookup on ``_DELIVERY_IDENTITY`` ("switchboard") — the same identity it
  already delivers and enqueues retries under (see that constant's
  ``[decision]``).
- Cross-container transport (bu-hmdqz.3): ``deliver()``'s underlying
  ``switchboard.route()`` connects directly to the target butler's MCP
  endpoint, resolved from ``switchboard.butler_registry`` — every daemon
  self-registers that endpoint as ``http://localhost:<port>`` from
  butlers-up's own point of view. That "localhost" is wrong from THIS
  process (dashboard-api, a separate container), so every delivery attempt
  from here used to fail to connect regardless of gating/dispatch logic
  being otherwise identical to ``notify()``'s — see
  ``butlers.core.mcp_urls.resolve_cross_container_mcp_url``, which
  ``route()`` now applies via the ``BUTLERS_HOST`` env var Docker Compose
  already sets on this container. A transport failure that slips through
  anyway is queued for retry via ``_enqueue_delivery_retry`` (switchboard's
  own ``deferred_notifications`` table, flushed by switchboard's in-container
  scheduler tick — never subject to this same cross-container problem).
- Retry-envelope dedup (bu-id0fh): the debounce marker only advances on a
  *confirmed* delivery, and the scan interval equals the retry backoff, so a
  persistent multi-tick outage would otherwise enqueue a fresh retry envelope
  every cycle — on recovery every accumulated envelope plus the next direct
  attempt each fires, giving the owner N+1 duplicate pings for one state
  transition (bounded only by the 24h expiry, so N can reach ~48).
  ``_enqueue_delivery_retry`` therefore *supersedes*: it cancels prior pending
  envelopes for the same credential (matched on the state-independent
  ``_focus_fragment`` deep-link, since the strict ``notify.v1`` envelope cannot
  carry a dedup field) before enqueueing the latest state, bounding the queue
  to one pending envelope per credential; and a subsequent direct delivery
  cancels that leftover too. Net: exactly one delivery per transition on the
  common recovery path, a bounded ≤2 in the rare drain-races-the-scan case —
  never N+1. Drain-side dedup is deliberately NOT added: enqueue-time supersede
  already guarantees ≤1 pending per credential under the
  single-dashboard-api-replica assumption this job's audit_log debounce already
  relies on. The debounce marker is still written only on genuine delivery, so
  the fix never trades a duplicate for a *silent* credential — the failure mode
  the original design guards against, and the strictly worse one.
- Natural future home: bu-a63hn (background verification loop) once it
  exists. This job does not block on it; it is a standalone scheduled check.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from butlers.api.db import DatabaseManager
from butlers.api.degraded import DegradedSources
from butlers.api.routers import audit as audit_router
from butlers.api.routers.secrets_v2 import (
    PROVIDER_CATALOG,
    _fetch_cli_secrets,
    _fetch_system_secrets,
    _fetch_user_secrets,
    _infer_provider_from_type,
    _secrets_schema_absent_at_start,
    _secrets_source_schema,
)
from butlers.core.attention_ledger import (
    check_owner_notify_suppression,
    record_attention_event,
)
from butlers.core.credential_keys import normalize_credential_key
from butlers.core.temporal.delivery import compute_deliver_at, should_defer_notification
from butlers.core.temporal.delivery_db import (
    cancel_pending_notifications_matching_line,
    get_delivery_preferences,
    insert_deferred_notification,
)
from butlers.credential_store import resolve_owner_telegram_recipient

logger = logging.getLogger(__name__)

# States that warrant a proactive owner push. Deliberately excludes 'warn'
# (never-probed — not yet actionable) and the frontend-only synthetic states
# the backend never emits ('scope_mismatch', 'revoked', 'rotating').
_ATTENTION_STATES = frozenset({"expiring", "failing", "expired"})

# Spotify OAuth rows are runtime artifacts managed by the dedicated provider
# flow. A one-hour access-token expiry is routine, not an actionable credential
# failure; refresh rejection is surfaced by the Spotify connector status.
_LIFECYCLE_EXCLUDED_SYSTEM_CATEGORIES = frozenset({"spotify"})

_LIFECYCLE_NOTIFIED_ACTION = "lifecycle_state_notified"
_LIFECYCLE_ACTOR = "secrets_lifecycle_check"

# [decision] (bu-178v1) The butler identity this job assumes for the per-butler
# delivery_preferences quiet-hours lookup (notify()'s FIRST gate). This job runs
# in the dashboard-api process with no butler identity; it already attributes
# every delivery to source_butler/origin_butler="switchboard", delivers to the
# OWNER (not a per-butler recipient), and enqueues its retry envelopes on
# switchboard's own deferred_notifications table. So "switchboard" is the one
# consistent, least-surprising delivery identity — mirroring how notify() keys
# delivery_preferences on the *calling* butler. Option (a) "per-credential
# owning butler" was rejected: the cli/user/shared credential families have no
# owning butler, and gating an owner-level credential-expiry ping on some
# unrelated butler's quiet hours is surprising. delivery_preferences rows are
# per-schema (the query is unqualified), so this is read via db.pool(identity).
_DELIVERY_IDENTITY = "switchboard"

_STATE_DESCRIPTIONS: dict[str, str] = {
    "expiring": "is expiring soon",
    "failing": "is failing its health probe",
    "expired": "has expired",
}

_DASHBOARD_PORT_DEFAULT = "41200"

# bu-hmdqz.3 slice 2: how long to wait before retrying a transport-failed
# delivery via the deferred_notifications flusher. The retry envelope is
# flushed by switchboard's OWN scheduler tick (running inside butlers-up,
# same container as every other butler daemon) -- so unlike this job's own
# first attempt (dashboard-api, a separate container, see
# resolve_cross_container_mcp_url()), the retry is never subject to the
# cross-container transport bug in the first place.
_RETRY_BACKOFF = timedelta(minutes=30)


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


async def _collect_snapshots(
    db: DatabaseManager,
    *,
    tracker: DegradedSources | None = None,
) -> list[CredentialSnapshot]:
    """Collect current lifecycle-relevant state for every known credential.

    Reuses the same per-family fetch helpers as ``GET /api/secrets/inventory``
    and mirrors its scan shape (per-butler schemas + shared pool), minus the
    response-model assembly this job doesn't need. A failed per-butler fetch
    is omitted while the scan continues to healthy butlers and the shared
    system/CLI/user families; when provided, ``tracker`` records that genuine
    partial failure so callers do not report a clean scan. Provider-managed
    Spotify rows are omitted because dedicated provider status owns their
    health.
    """
    snapshots: list[CredentialSnapshot] = []

    for butler_name in db.butler_names:
        try:
            pool = db.pool(butler_name)
        except KeyError:
            continue
        try:
            rows = await _fetch_system_secrets(
                pool,
                butler_name,
                source_schema=_secrets_source_schema(db, butler_name),
                schema_absent_at_start=_secrets_schema_absent_at_start(db, butler_name),
                tracker=tracker,
            )
        except Exception:  # noqa: BLE001
            if tracker is not None:
                tracker.mark(
                    butler_name,
                    msg=f"Failed to collect lifecycle secrets for butler {butler_name!r}",
                )
            else:
                logger.warning(
                    "secrets_lifecycle: failed to collect secrets for butler %s",
                    butler_name,
                    exc_info=True,
                )
            continue
        for row in rows:
            if row.category in _LIFECYCLE_EXCLUDED_SYSTEM_CATEGORIES:
                continue
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
    for row in await _fetch_system_secrets(
        shared_pool,
        "shared-public",
        schema_absent_at_start=_secrets_schema_absent_at_start(db, "shared-public"),
        tracker=tracker,
    ):
        if row.category in ("cli", "cli-auth") or (
            row.category in _LIFECYCLE_EXCLUDED_SYSTEM_CATEGORIES
        ):
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
    """Apply legacy destructive suppression to a secrets-lifecycle job push.

    The shared helper returns a terminal reason for this out-of-process caller to
    record as ``suppressed`` and drop; it does not mirror direct ``notify()``
    owner-default parking. Kept as a module-local name because
    ``run_secrets_lifecycle_check`` and its tests reference and monkeypatch it
    (bu-gts7r).
    """
    return await check_owner_notify_suppression(pool, log_context="secrets_lifecycle_check")


def _focus_fragment(key: str) -> str:
    """State-independent deep-link fragment embedded in every lifecycle message
    for a credential.

    Serves double duty: it is the ``/secrets`` deep link ``_compose_message``
    builds, and — because it is identical across every state a credential passes
    through (``expiring`` → ``expired`` …) yet unique per credential — it is the
    dedup token used to supersede prior pending retry envelopes for the same
    credential (see ``_enqueue_delivery_retry`` and the direct-delivery cleanup
    in ``run_secrets_lifecycle_check``). Keeping the two uses on one helper means
    the deep link and the dedup discriminator can never silently drift apart.

    The fragment appears on its own line in the composed message (``_compose_message``
    joins lines with ``\n``), so it is always either followed by a newline or at
    end-of-message — the line boundary ``cancel_pending_notifications_matching_line``
    anchors on to avoid a shorter key colliding with a longer sibling.
    """
    return f"/secrets?focus={quote(key, safe='')}"


def _compose_message(snapshot: CredentialSnapshot, dashboard_url: str) -> str:
    """Build the owner-facing notification body for one lifecycle transition.

    Always includes a deep link to the exact card on /secrets
    (``?focus=<key>``). For OAuth providers, also includes the re-authorize
    URL — ``GET /api/oauth/<provider>/start`` redirects straight to the
    provider's consent screen, so it's directly clickable from a Telegram
    message without a separate POST round-trip.
    """
    focus_url = f"{dashboard_url}{_focus_fragment(snapshot.key)}"
    description = _STATE_DESCRIPTIONS.get(snapshot.state, f"is now '{snapshot.state}'")
    lines = [f"Credential '{snapshot.label}' {description}.", focus_url]

    if snapshot.provider is not None:
        provider_meta = PROVIDER_CATALOG.get(snapshot.provider)
        if provider_meta is not None and provider_meta.kind == "oauth":
            lines.append(f"Re-authorize: {dashboard_url}/api/oauth/{snapshot.provider}/start")

    return "\n".join(lines)


async def _supersede_pending_retries(pool: Any, dedup_marker: str) -> int:
    """Cancel prior PENDING retry envelopes sharing ``dedup_marker``, best-effort.

    ``dedup_marker`` is a credential's state-independent ``_focus_fragment``.
    Cancelling before enqueueing a fresh envelope (and again once a direct
    delivery finally succeeds) is what bounds a persistent multi-tick outage to
    a single pending envelope per credential — the core of bu-id0fh's N+1 fix.
    A failure here is logged and swallowed: superseding is an optimisation on
    top of the (already-recorded) delivery attempt, never a reason to abort it.
    """
    try:
        return await cancel_pending_notifications_matching_line(
            pool, butler_name="switchboard", line_token=dedup_marker
        )
    except Exception:
        logger.warning(
            "secrets_lifecycle_check: failed to supersede prior pending retry "
            "envelopes for marker=%s",
            dedup_marker,
            exc_info=True,
        )
        return 0


async def _enqueue_deferred_envelope(
    db: DatabaseManager,
    *,
    channel: str,
    message: str,
    recipient: str,
    dedup_marker: str,
    deliver_at: datetime,
) -> str | None:
    """Supersede prior pending envelopes for this credential, then enqueue a
    fresh ``notify.v1`` envelope on switchboard's ``deferred_notifications``.

    This is the SINGLE deferral path (bu-178v1) shared by both callers:
    ``_enqueue_delivery_retry`` (a transport failure — short retry backoff) and
    the ``delivery_preferences`` quiet-hours gate in
    ``run_secrets_lifecycle_check`` (a benign owner-quiet-hours hold — the next
    batch window). They differ ONLY in ``deliver_at``; the supersede, envelope
    shape, target pool, and best-effort contract are identical, so there is no
    second deferral mechanism to keep in sync.

    bu-hmdqz.3 slice 2: a ``delivery_error`` does not mean the message
    itself is undeliverable — it means THIS process (dashboard-api) could not
    reach a butler MCP endpoint. Rather than depending on the next 30-minute
    scan tick (which would re-run through the exact same cross-container
    transport, and — before slice 3's ``resolve_cross_container_mcp_url`` fix
    — always failed the same way), this writes a ``notify.v1`` envelope
    directly into ``switchboard.deferred_notifications``. Switchboard's own
    scheduler tick (``butlers.core.scheduler._tick_deferred_notification_pass``)
    runs inside butlers-up — the SAME container every butler daemon lives in
    — and flushes it in-process via ``switchboard.notification.deliver.deliver``
    (see ``butlers.background``'s ``_scheduler_notify_fn``), so the retry is
    never subject to this job's own cross-container transport problem.

    ``origin_butler`` in the envelope must be ``"switchboard"`` to satisfy
    ``deliver()``'s ``notify_request.origin_butler == source_butler`` check
    for the flusher's in-process switchboard-self-delivery path.

    Supersede (bu-id0fh): before inserting, prior PENDING envelopes for the
    same credential (matched by ``dedup_marker``, a state-independent
    ``_focus_fragment``) are cancelled. Without this, a persistent outage —
    where every 30-minute scan re-fails and re-enqueues because the debounce
    marker only advances on a *confirmed* delivery — would leave one pending
    envelope per tick (up to ~48 before the 24h expiry), and every one of them
    plus the next direct attempt would fire on recovery: N+1 duplicate pings
    for a single state transition. Cancel-then-insert bounds the queue to
    exactly one pending envelope carrying the *latest* state (latest-state-wins
    supersede — a credential that changed state mid-outage supersedes its own
    stale envelope rather than delivering both). The dedup key is NOT stored in
    the envelope itself: the ``notify.v1`` envelope is strictly re-validated
    (``extra="forbid"``) when the flusher redelivers it, so it is matched on the
    already-present deep-link fragment in ``message`` instead.

    Best-effort: a failure to enqueue is logged and returns ``None``. A caller
    MUST record that as an honest ``failed`` attention outcome rather than
    claiming a deferred hold without an envelope reference. For a transport
    retry, the original delivery has already failed; for a quiet-hours hold,
    the next lifecycle scan remains eligible to retry. Returns the deferred
    notification's id (for the ledger row's ``notification_ref``) or ``None``
    if enqueuing was skipped/failed.
    """
    try:
        switchboard_pool = db.pool("switchboard")
    except KeyError:
        logger.warning(
            "secrets_lifecycle_check: switchboard pool unavailable; "
            "cannot enqueue deferred envelope for recipient=%s",
            recipient,
        )
        return None

    # Supersede prior pending envelopes for this credential BEFORE enqueueing
    # the fresh one, so the queue never holds more than a single pending retry
    # per credential regardless of how many ticks the outage spans.
    superseded = await _supersede_pending_retries(switchboard_pool, dedup_marker)
    if superseded:
        logger.info(
            "secrets_lifecycle_check: superseded %d prior pending retry "
            "envelope(s) before enqueueing latest state (marker=%s)",
            superseded,
            dedup_marker,
        )

    envelope = {
        "schema_version": "notify.v1",
        "origin_butler": "switchboard",
        "delivery": {
            "intent": "send",
            "channel": channel,
            "message": message,
            "recipient": recipient,
        },
    }
    try:
        return await insert_deferred_notification(
            switchboard_pool,
            butler_name="switchboard",
            channel=channel,
            message=message,
            priority="medium",
            envelope=envelope,
            deliver_at=deliver_at,
        )
    except Exception:
        logger.warning(
            "secrets_lifecycle_check: failed to enqueue deferred envelope",
            exc_info=True,
        )
        return None


async def _enqueue_delivery_retry(
    db: DatabaseManager,
    *,
    channel: str,
    message: str,
    recipient: str,
    dedup_marker: str,
) -> str | None:
    """Enqueue a retry envelope for a transport-failed delivery, best-effort.

    Thin wrapper over ``_enqueue_deferred_envelope`` with a short
    ``_RETRY_BACKOFF`` ``deliver_at``: switchboard's own scheduler tick (running
    inside butlers-up, not this dashboard-api process) redelivers it, so the
    retry is never subject to this job's cross-container transport problem.
    """
    return await _enqueue_deferred_envelope(
        db,
        channel=channel,
        message=message,
        recipient=recipient,
        dedup_marker=dedup_marker,
        deliver_at=datetime.now(UTC) + _RETRY_BACKOFF,
    )


async def _delivery_preferences_deferral(
    db: DatabaseManager,
    *,
    channel: str,
    priority: str,
) -> datetime | None:
    """Return the batch ``deliver_at`` if switchboard's ``delivery_preferences``
    quiet hours defer this notification, else ``None``.

    Mirrors notify()'s FIRST quiet-hours gate
    (``core_tools/_notifications.py``: ``get_delivery_preferences`` →
    ``should_defer_notification`` → ``compute_deliver_at``), keyed on the
    ``_DELIVERY_IDENTITY`` this job delivers under (see the ``[decision]`` note
    on that constant). ``delivery_preferences`` is a per-schema table (the query
    is unqualified), so the lookup uses that identity's own pool.

    Best-effort and fail-open: a missing switchboard pool, a missing table, or
    any lookup error returns ``None`` (deliver now), never blocking the
    notification — identical to notify()'s own "deliver immediately on lookup
    failure" contract.
    """
    try:
        identity_pool = db.pool(_DELIVERY_IDENTITY)
    except KeyError:
        return None

    try:
        prefs = await get_delivery_preferences(identity_pool, _DELIVERY_IDENTITY)
    except Exception:
        logger.debug(
            "secrets_lifecycle_check: delivery_preferences lookup failed; delivering now",
            exc_info=True,
        )
        return None

    if prefs is None:
        return None

    # `or "UTC"` (not a .get default) so an explicit null timezone value also
    # falls back rather than reaching ZoneInfo(None); the try/except still
    # guards an invalid tz *string*.
    tz_name = prefs.get("timezone") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone(tz).time()

    if should_defer_notification(
        priority=priority, current_time=now_local, prefs=prefs, channel=channel
    ):
        return compute_deliver_at(prefs=prefs, now=now_utc)
    return None


async def run_secrets_lifecycle_check(db: DatabaseManager) -> dict[str, Any]:
    """Scan every credential and push a debounced owner notification for
    each NEW transition into an attention state.

    Returns a summary dict: ``{scanned, attention, delivered, deferred,
    suppressed, errors}``, with ``sources_degraded`` added when a per-butler
    credential fetch failed. ``deferred`` counts owner-quiet-hours holds
    enqueued to the deferred_notifications table (a benign hold the flusher
    redelivers) — distinct from ``suppressed`` (approvals-policy/context-bus
    drops) and ``errors`` (genuine failures). Never raises — a failure in one
    credential's notify attempt is logged and counted in ``errors``, and the
    scan continues.
    """
    try:
        shared_pool = db.credential_shared_pool()
    except KeyError:
        logger.warning("secrets_lifecycle_check: no shared credential pool configured; skipping")
        return {
            "scanned": 0,
            "attention": 0,
            "delivered": 0,
            "deferred": 0,
            "suppressed": 0,
            "errors": 0,
        }

    tracker = DegradedSources(logger)
    snapshots = await _collect_snapshots(db, tracker=tracker)
    attention = [s for s in snapshots if s.state in _ATTENTION_STATES]

    dashboard_url = _dashboard_url()
    delivered = deferred = suppressed = errors = 0

    for snapshot in attention:
        # Per-iteration resolved state (bu-ziuye). The per-credential try below can
        # raise at many points — inside _last_notified_state, _delivery_preferences_deferral,
        # _check_suppression, the deliver import/call, or the ledger/audit writes. Track
        # message / recipient / dedup_marker / deferred_at as each becomes resolvable so
        # the except handler can enqueue a retry envelope ONLY when enough state exists for
        # a correct redelivery. These are reset every iteration precisely because Python
        # names are function-scoped, not block-scoped: without the reset, a raise-before-
        # resolution would read a PRIOR credential's leftover message/recipient and enqueue
        # a mis-addressed envelope.
        message: str | None = None
        recipient: str | None = None
        dedup_marker: str | None = None
        deferred_at: datetime | None = None
        try:
            last_state = await _last_notified_state(shared_pool, snapshot.key)
            if last_state == snapshot.state:
                # Already notified for this exact state — debounce.
                continue

            message = _compose_message(snapshot, dashboard_url)
            # State-independent dedup token for this credential's deferred/retry
            # envelopes (see _focus_fragment / _supersede_pending_retries).
            dedup_marker = _focus_fragment(snapshot.key)

            # Gate 1 (mirrors notify()'s FIRST gate): per-butler
            # delivery_preferences quiet hours, keyed on this job's switchboard
            # delivery identity. Unlike the approvals-policy/context-bus gates
            # below (which SUPPRESS — drop, retry next scan), a delivery_prefs
            # hold is a benign DEFERRAL: enqueue a notify.v1 envelope that
            # switchboard's flusher redelivers when the window ends. Reuses the
            # single _enqueue_deferred_envelope path (supersede-at-enqueue,
            # bu-id0fh) so a persistent multi-scan window never accumulates more
            # than one pending envelope per credential. The debounce marker is
            # deliberately NOT advanced here (only a confirmed direct delivery
            # advances it), and the post-delivery supersede cancels the leftover
            # envelope on the recovery scan — the same ≤1-residual guarantee the
            # transport-retry path already relies on.
            deferred_at = await _delivery_preferences_deferral(
                db, channel="telegram", priority="medium"
            )
            if deferred_at is not None:
                recipient = await resolve_owner_telegram_recipient(shared_pool)
                if not recipient:
                    # No deliverable recipient — the deferred envelope would be
                    # undeliverable at flush time, so this is a genuine failure,
                    # recorded honestly rather than enqueued to silently expire.
                    errors += 1
                    logger.warning(
                        "secrets_lifecycle_check: no telegram recipient configured for owner; "
                        "cannot defer notification for key=%s",
                        snapshot.key,
                    )
                    await record_attention_event(
                        shared_pool,
                        origin_butler=_LIFECYCLE_ACTOR,
                        source="notify",
                        outcome="failed",
                        channel="telegram",
                        intent="send",
                        priority="medium",
                        reason="no_recipient_configured",
                        dedup_key=snapshot.key,
                    )
                    continue
                envelope_ref = await _enqueue_deferred_envelope(
                    db,
                    channel="telegram",
                    message=message,
                    recipient=recipient,
                    dedup_marker=dedup_marker,
                    deliver_at=deferred_at,
                )
                if envelope_ref is None:
                    # A quiet-hours defer is truthful only when the envelope
                    # was actually persisted. Do not deliver inside the quiet
                    # window or advance the debounce marker; a later scan can
                    # retry this transition after the queue recovers.
                    errors += 1
                    await record_attention_event(
                        shared_pool,
                        origin_butler=_LIFECYCLE_ACTOR,
                        source="notify",
                        outcome="failed",
                        channel="telegram",
                        intent="send",
                        priority="medium",
                        reason="delivery_preferences_queue_failure_retryable",
                        dedup_key=snapshot.key,
                        notification_ref=None,
                    )
                    continue
                deferred += 1
                await record_attention_event(
                    shared_pool,
                    origin_butler=_LIFECYCLE_ACTOR,
                    source="notify",
                    outcome="deferred",
                    channel="telegram",
                    intent="send",
                    priority="medium",
                    reason="delivery_preferences_quiet_hours",
                    dedup_key=snapshot.key,
                    notification_ref=envelope_ref,
                )
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
                # discipline in the ledger. Not retried via the deferred queue:
                # unlike a transport failure, retrying without a configured
                # recipient can never succeed.
                await record_attention_event(
                    shared_pool,
                    origin_butler=_LIFECYCLE_ACTOR,
                    source="notify",
                    outcome="failed",
                    channel="telegram",
                    intent="send",
                    priority="medium",
                    reason="no_recipient_configured",
                    dedup_key=snapshot.key,
                )
                continue

            # message / dedup_marker were computed above (before the gates).
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
                # Retryable: recipient + message are already resolved above,
                # so (unlike no_recipient_configured) a later retry can
                # genuinely succeed — e.g. once the transport itself recovers
                # (resolve_cross_container_mcp_url) or Messenger comes back.
                # Enqueue a retry envelope on switchboard's OWN
                # deferred_notifications table so switchboard's next
                # scheduler tick (running inside butlers-up, not this
                # dashboard-api process) actually redelivers it — see
                # _enqueue_delivery_retry's docstring.
                retry_ref = await _enqueue_delivery_retry(
                    db,
                    channel="telegram",
                    message=message,
                    recipient=recipient,
                    dedup_marker=dedup_marker,
                )
                await record_attention_event(
                    shared_pool,
                    origin_butler=_LIFECYCLE_ACTOR,
                    source="notify",
                    outcome="failed",
                    channel="telegram",
                    intent="send",
                    priority="medium",
                    reason=f"delivery_error:{deliver_result.get('error', 'unknown')}",
                    dedup_key=snapshot.key,
                    notification_ref=retry_ref,
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
                result="delivered",
            )
            # This direct delivery satisfied the state transition. Cancel any
            # pending retry envelope left over from a prior failed tick so
            # switchboard's flusher does not ALSO redeliver it (the drain path
            # is credential-agnostic and cannot advance the debounce marker
            # itself) — the common recovery path where transport is healthy
            # again by the time the next scan runs, giving exactly one delivery
            # rather than one direct + one drained (bu-id0fh). A drain that
            # happens to fire just before this scan is the bounded residual:
            # one extra ping, never the old N+1.
            # Best-effort: this cleanup runs AFTER the delivery + marker have
            # already succeeded, so any failure resolving the switchboard pool
            # must not propagate to the outer handler (which would increment
            # `errors` and record a duplicate `failed` event for a delivery
            # that actually succeeded). Swallow broadly, not just KeyError.
            try:
                switchboard_pool = db.pool("switchboard")
            except Exception:
                switchboard_pool = None
            if switchboard_pool is not None:
                cancelled = await _supersede_pending_retries(switchboard_pool, dedup_marker)
                if cancelled:
                    logger.info(
                        "secrets_lifecycle_check: direct delivery superseded %d pending "
                        "retry envelope(s) (marker=%s)",
                        cancelled,
                        dedup_marker,
                    )
        except Exception as exc:
            errors += 1
            logger.exception(
                "secrets_lifecycle_check: unexpected error notifying for key=%s", snapshot.key
            )
            # A genuine terminal failure — must be recorded, not silent, or an
            # exception deep in the dispatch path (e.g. a DB error) reads
            # identically to quiet-hours discipline in the ledger.
            #
            # bu-ziuye: retry when enough state is resolved. If the raise landed
            # AFTER message + recipient (+ dedup_marker) were resolved — e.g. the
            # deliver() import or call itself raised instead of returning a
            # failed result, or a ledger write faulted — a later retry can
            # genuinely succeed, exactly like the delivery_error path. Enqueue on
            # the SAME single deferral path (_enqueue_deferred_envelope, bu-id0fh
            # supersede-at-enqueue) — no second mechanism. If the raise fired
            # BEFORE the message/recipient were resolvable (e.g. inside
            # _last_notified_state or _check_suppression), there is nothing safe
            # to enqueue, so we stamp a plain failed row honestly rather than
            # queueing a half-built or mis-addressed envelope.
            #
            # deliver_at: honor a resolved quiet-hours deferral (deferred_at) so
            # the retry is never redelivered inside quiet hours — the flusher
            # gates purely on deliver_at (deliver() does not re-check quiet
            # hours), so respecting the window means carrying its timestamp
            # through, not reimplementing the gate. In practice message +
            # recipient are only both resolved on the non-deferred main path
            # (the defer branch resolves recipient but continues without a
            # raisable call after it), so this normally uses the short transport
            # backoff; the deferred_at guard keeps the retry correct regardless.
            # The debounce marker is deliberately NOT advanced here — only a
            # confirmed direct delivery advances it, so the retry (or the next
            # scan) still fires.
            retry_ref: str | None = None
            if message is not None and recipient is not None and dedup_marker is not None:
                retry_deliver_at = (
                    deferred_at if deferred_at is not None else datetime.now(UTC) + _RETRY_BACKOFF
                )
                retry_ref = await _enqueue_deferred_envelope(
                    db,
                    channel="telegram",
                    message=message,
                    recipient=recipient,
                    dedup_marker=dedup_marker,
                    deliver_at=retry_deliver_at,
                )
            reason = (
                f"unexpected_error_retry:{type(exc).__name__}"
                if retry_ref is not None
                else f"unexpected_error:{type(exc).__name__}"
            )
            await record_attention_event(
                shared_pool,
                origin_butler=_LIFECYCLE_ACTOR,
                source="notify",
                outcome="failed",
                channel="telegram",
                intent="send",
                priority="medium",
                reason=reason,
                dedup_key=snapshot.key,
                notification_ref=retry_ref,
            )

    summary: dict[str, Any] = {
        "scanned": len(snapshots),
        "attention": len(attention),
        "delivered": delivered,
        "deferred": deferred,
        "suppressed": suppressed,
        "errors": errors,
    }
    if tracker.failed:
        summary["sources_degraded"] = tracker.names
    return summary


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
