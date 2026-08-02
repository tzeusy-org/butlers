"""Connector endpoints for the ingestion console.

Provides:

- ``router`` — endpoints under ``/api/ingestion/connectors``

Endpoints
---------
GET  /api/ingestion/connectors/summaries        — connector list (all fields DB-sourced)
GET  /api/ingestion/connectors/cross-summary    — cross-connector aggregate + aggregates_available
POST /api/ingestion/connectors/{type}/{identity}/pause       — pause a connector (audit-only)
POST /api/ingestion/connectors/{type}/{identity}/run-now    — resume a paused connector (audit-only)
POST /api/ingestion/connectors/{type}/{identity}/archive    — soft-archive an id (audit-only)
POST /api/ingestion/connectors/{type}/{identity}/unarchive  — restore an archived id (audit-only)
POST /api/ingestion/connectors/{type}/{identity}/disconnect — Approvals-gated; soft-delete (§4.4)
POST /api/ingestion/connectors/{type}/{identity}/rotate-token — rejects if unreplayable (§4.5)
POST /api/ingestion/connectors/{type}/{identity}/reauth      — BLOCKED HTTP 503 (§4.6)
GET  /api/ingestion/connectors/available                     — enumerable connector profiles
GET  /api/ingestion/connectors/{type}/{identity}/events      — recent events [bu-5ywn2]
GET  /api/ingestion/connectors/{type}/{identity}/incidents   — incident events [bu-5ywn2]
GET  /api/ingestion/connectors/{type}/{identity}/routing-rules — scoped rules [bu-5ywn2]

The ``summaries`` and ``cross-summary`` endpoints proxy the existing
``/api/switchboard/connectors`` and ``/api/switchboard/connectors/summary``
endpoints. ``cross-summary`` adds an ``aggregates_available`` flag derived from
whether the Prometheus backend is reachable (via the pipeline stats cache);
``summaries`` does not — every field it returns is DB-sourced, so it carries
its own genuine-failure-only flags (``hourly_events_available``,
``device_liveness_available``, ``owntracks_cadence_available``) instead.

Spec: openspec/changes/redesign-ingestion-dispatch-console/specs/
      connector-lifecycle-ceremony/spec.md
      connector-state-aggregates/spec.md (aggregates_available threading)
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse
from butlers.api.models.connector import derive_liveness as _liveness
from butlers.api.routers.audit import append as _audit_append
from butlers.modules.approvals.command_contracts import (
    CONNECTOR_DISCONNECT_COMMAND,
    CONNECTOR_ROTATE_TOKEN_PRODUCER,
)
from butlers.modules.approvals.park import park_pending_action

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/ingestion/connectors", tags=["ingestion"])

_SWITCHBOARD_BUTLER = "switchboard"

# Per-device liveness staleness threshold (bu-e16to). A multi-device connector
# (e.g. OwnTracks, where several physical devices post through one shared
# connector_type) surfaces a per-sender-identity `devices` list; a device with
# no event in this long is flagged `stale` so a silently-dead device is never
# invisible behind a healthy connector-level heartbeat from a sibling device.
_DEVICE_STALE_THRESHOLD = _dt.timedelta(hours=48)

# How far back the per-device liveness query looks for a `MAX(received_at)` per
# sender identity. Without a bound this is an unindexed full scan of
# public.ingestion_events (no index covers source_sender_identity); bounding to
# received_at's existing DESC index keeps it a bounded index scan. 90 days safely
# covers the motivating bu-e16to regression (devices silent ~70 days) while
# keeping the query cheap. A device with zero events in this window drops out of
# the `devices` list entirely rather than showing as maximally stale.
_DEVICE_LOOKBACK_WINDOW = _dt.timedelta(days=90)

# Minimum offline age for an active identity to become an archive REVIEW
# CANDIDATE (bu-u19yv). An identity whose last heartbeat is strictly older than
# this AND that has a newer, currently-online sibling of the same
# connector_type is surfaced as a flag-only suggestion in the dashboard's
# review queue — NEVER auto-archived (auto-archiving risks silencing a merely
# quiet live connector). The comparison is strict (`> 30d`), so an identity that
# last heartbeated exactly 30 days ago is not yet a candidate.
_ARCHIVE_CANDIDATE_MIN_OFFLINE = _dt.timedelta(days=30)

# OwnTracks movement inference consumes durable points from
# connectors.owntracks_points, not generic ingestion counters. Fewer than one
# point per hour over the trailing day is an operationally sparse signal: it is
# enough to prove the evidence stream is below the supported baseline, but it
# is deliberately NOT folded into connector health because the webhook
# transport can be healthy while the phone reports too infrequently.
_OWNTRACKS_CADENCE_WINDOW = _dt.timedelta(hours=24)
_OWNTRACKS_MIN_POINTS_PER_WINDOW = 24


def _owntracks_cadence_warning(point_count: int) -> str:
    """Describe a sparse OwnTracks evidence stream without changing health."""
    return (
        f"Only {point_count} OwnTracks location points were recorded in the last 24 hours. "
        f"The operational baseline is {_OWNTRACKS_MIN_POINTS_PER_WINDOW}; "
        "use Move mode during waking hours."
    )


def _online_identities_by_type(
    rows: list[Any] | Any,
) -> dict[str, set[str]]:
    """Map each connector_type to the set of its currently-online, non-archived identities.

    Used as the "newer online sibling" test for the archive review queue
    (bu-u19yv). An archived identity never counts as a live sibling — archiving
    it must not, in turn, make its offline peers look archivable.

    ``rows`` are registry rows (mappings) with ``connector_type``,
    ``endpoint_identity``, ``archived_at`` and ``last_heartbeat_at`` keys.
    """
    by_type: dict[str, set[str]] = {}
    for r in rows:
        if r["archived_at"] is None and _liveness(r["last_heartbeat_at"]) == "online":
            by_type.setdefault(r["connector_type"], set()).add(r["endpoint_identity"])
    return by_type


def _is_archive_candidate(
    *,
    connector_type: str,
    endpoint_identity: str,
    archived_at: _dt.datetime | None,
    last_heartbeat_at: _dt.datetime | None,
    online_identities_by_type: dict[str, set[str]],
    now: _dt.datetime,
) -> bool:
    """Return whether an identity is a flag-only archive REVIEW candidate (bu-u19yv).

    A candidate is an ACTIVE (non-archived) identity that BOTH:

    - last heartbeated strictly more than ``_ARCHIVE_CANDIDATE_MIN_OFFLINE``
      (30 days) ago, AND
    - has at least one *other* identity of the same ``connector_type`` that is
      currently ``online`` and not archived (a "newer online sibling").

    This is a SUGGESTION only. It never affects health rollups or alerting, and
    it can never mask a genuinely-failing live connector: a failing live
    connector is not offline for 30+ days, and a merely-quiet identity with no
    online sibling is not flagged. An identity that has never heartbeated
    (``last_heartbeat_at is None``) is not a candidate — there is no heartbeat
    age to compare, so the >30d test cannot be met deterministically.
    """
    if archived_at is not None:
        return False
    if last_heartbeat_at is None:
        return False
    if (now - last_heartbeat_at) <= _ARCHIVE_CANDIDATE_MIN_OFFLINE:
        return False
    siblings = online_identities_by_type.get(connector_type, set())
    return any(identity != endpoint_identity for identity in siblings)


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _pool(db: DatabaseManager):
    """Retrieve the switchboard butler's connection pool.

    Raises HTTPException 503 if the pool is not available.
    Connector lifecycle state is stored in connector_registry, which lives
    in the switchboard schema.
    """
    try:
        return db.pool(_SWITCHBOARD_BUTLER)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Connector registry database is not available",
        )


def _build_dashboard_approval_push_runtime(db: DatabaseManager) -> Any | None:
    """Build the park -> Switchboard delivery boundary for a dashboard-API park.

    The dashboard-API process is not a butler daemon: it has no
    ``switchboard_client`` MCP connection, so it dispatches exactly the way
    ``daemon.py``'s ``_build_approval_push_runtime`` does when the daemon
    process itself IS switchboard -- import ``deliver()`` and call it
    in-process against the switchboard pool.  Returns ``None`` when the
    switchboard pool or shared credential pool is unavailable (mirrors the
    daemon-side helper's degrade-gracefully behavior; bu-mda0r).
    """
    from butlers.credential_store import CredentialStore, resolve_owner_entity_info
    from butlers.modules.approvals.notifications import ApprovalPushRuntime
    from butlers.tools.switchboard.notification.deliver import deliver as _switchboard_deliver

    # DatabaseManager.pool()/.credential_shared_pool() only ever raise
    # KeyError (unregistered pool) in real code -- see api/db.py. This helper
    # is inherently best-effort and must degrade rather than break the
    # primary pending_actions INSERT it accompanies, but only for that one
    # documented failure mode; anything else should surface (bu-1j5q6).
    try:
        pool = db.pool(_SWITCHBOARD_BUTLER)
    except KeyError:
        logger.warning(
            "Approval push runtime unavailable for dashboard connector actions "
            "(switchboard pool not registered)"
        )
        return None

    try:
        shared_pool = db.credential_shared_pool()
    except KeyError:
        logger.warning(
            "Approval push runtime unavailable for dashboard connector actions "
            "(shared credential pool not registered)"
        )
        return None

    credential_store = CredentialStore(pool=pool, fallback_pools=[shared_pool])

    async def _resolve_owner_recipient() -> str | None:
        return await resolve_owner_entity_info(pool, "telegram_chat_id")

    async def _dispatch(envelope: dict[str, Any]) -> None:
        result = await _switchboard_deliver(
            pool,
            source_butler=_SWITCHBOARD_BUTLER,
            notify_request=envelope,
        )
        if result.get("status") == "failed":
            raise RuntimeError(
                f"Approval request delivery failed: {result.get('error') or 'unknown error'}"
            )

    return ApprovalPushRuntime(
        dispatch=_dispatch,
        resolve_owner_recipient=_resolve_owner_recipient,
        credential_store=credential_store,
    )


def _get_prometheus_url() -> str | None:
    """Return Prometheus base URL from env, or None if not configured or empty."""
    return os.environ.get("PROMETHEUS_URL") or None


# ---------------------------------------------------------------------------
# GET /api/ingestion/connectors/summaries
# ---------------------------------------------------------------------------


@router.get("/summaries", response_model=ApiResponse[dict])
async def list_connector_summaries_with_aggregates(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Return the connector list. Every field is DB-sourced.

    Fetches connector registry rows from the switchboard database. This
    endpoint has **no** Prometheus dependency — it never surfaced any
    Prometheus-backed field (no spark24h/rate1h, etc.), so it carries no
    ``aggregates_available`` flag. Its only degraded-mode flags gate the two
    DB queries that can independently fail (``hourly_events_available``,
    ``device_liveness_available`` below).

    Each connector entry includes ``hourly_events`` — a 24-element array of
    per-hour event counts for the last 24 hours (oldest bucket first, newest
    last).  This is sourced from ``public.ingestion_events`` (not Prometheus)
    so it is always populated (subject to ``hourly_events_available``).

    Each connector entry also includes ``hourly_filtered_events`` (bu-scyro) — a
    DISTINCT 24-element array counting ``connectors.filtered_events`` rows per
    hour (any status — filtered/error/replay_*). This is never folded into
    ``hourly_events``/``today.messages_ingested``: doing so would fabricate
    ingestion volume that never happened. It exists because every
    self-persisting connector's skip volume (gmail, telegram, home_assistant
    post-bu-416vk, google_calendar post-bu-iq2qr) is otherwise invisible on
    this chart — pre-bu-416vk home_assistant was the anomaly whose skip
    decisions inflated the ``ingested`` series instead of living in
    filtered_events like every other connector. ``hourly_events_available``
    (top-level, mirrors ``device_liveness_available``)
    is ``false`` only if the combined ingested+filtered query itself failed.

    ``today.messages_ingested`` is the **true last-24h count** derived by
    summing ``hourly_events``.  The raw ``counter_messages_ingested`` column
    is a cumulative lifetime counter (since process start) and is intentionally
    not exposed here to avoid mislabeling lifetime volumes as "today".

    Each connector entry also includes ``devices`` (nullable) — a fallback signal
    for connector_types where ``connector_registry`` still shares ONE row across
    several distinct sender identities (bu-e16to). ``devices`` is derived from
    ``public.ingestion_events`` UNIONed with ``connectors.filtered_events``
    (bu-scyro — so a sender/device that is 100% skip-routed and never reaches
    ingestion_events is not invisible here either; filtered_events rows with an
    empty ``sender_identity`` are excluded, since a handful of connectors write
    that placeholder when no real identity was resolvable), within the last
    ``_DEVICE_LOOKBACK_WINDOW`` (90 days), and is a list of
    ``{sender_identity, last_seen_at, stale}`` entries, one per device, sorted most
    recent first. ``stale`` is true when a device's last event is older than
    ``_DEVICE_STALE_THRESHOLD`` (48h).

    ``devices`` is ``null`` when: (a) the connector_type has only one distinct
    sender identity (nothing to disambiguate), or (b) the connector_type's
    ``connector_registry`` rows have caught up to every device this fallback
    already knows about (``registry_row_counts`` >= the known device count —
    e.g. OwnTracks post-bu-86zll, once fully migrated), in which case each
    row's own ``state``/``last_heartbeat_at`` is already device-accurate and
    attaching the same ``devices`` list to every one of those rows would
    double up / could disagree with each row's own liveness. A connector_type
    only *partway* migrated (some devices already have their own row, a
    sibling still doesn't) keeps the badge — gating on row count alone would
    otherwise hide that still-unregistered sibling behind neither its own row
    nor the badge (bu-e16to). A device with no event at all in the lookback
    window drops out of ``devices`` entirely rather than appearing maximally
    stale. ``device_liveness_available`` (top-level, sibling of
    ``hourly_events_available``) is ``false`` only if the per-device query itself
    failed — it is unrelated to whether any given connector_type has devices data.

    Each connector entry also includes ``archived`` (bool) and ``archived_at``
    (nullable ISO-8601) — the soft-archive state (bu-33dm2). Archived rows are
    still returned here (so the dashboard can group them into a collapsed
    "archived" section that stays reachable for history), but the frontend
    separates them from the active roster so they never contribute to
    attention/KPIs, and the fleet-health rollups (``cross-summary`` and the
    switchboard ``/connectors/summary``) exclude them entirely so a superseded,
    permanently-offline identity stops dragging fleet health down. Archived
    ``!=`` degraded: archiving never masks a genuinely-failing *live* connector,
    which stays in the active roster.

    Each connector entry also includes ``archive_candidate`` (bool, bu-u19yv) —
    a flag-only review-queue SUGGESTION, true when an active (non-archived)
    identity last heartbeated >30d ago AND a newer online sibling of the same
    ``connector_type`` exists. It is computed read-only from the same rows (no
    extra query, no storage). It is a suggestion ONLY: it never feeds the
    fleet-health rollups or alerting (those exclude ``archived`` only), never
    removes the row from the active roster, and can never mask a
    genuinely-failing live connector (which is not offline for 30+ days). The
    dashboard surfaces candidates as a review queue with a one-click archive
    that reuses the existing audit-logged archive endpoint — never auto-archived.

    Always returns HTTP 200. Connector registry errors fall back to an empty list
    with ``connector_registry_available: false`` so callers do not mistake the
    fallback for a true empty roster.
    Hourly timeseries errors fall back to all-zero ``hourly_events`` arrays per connector.
    Per-device liveness query errors fall back to ``devices: null`` for every connector
    and ``device_liveness_available: false``.
    OwnTracks cadence query errors keep operational warnings empty and set
    ``owntracks_cadence_available: false``.
    """
    pool = _pool(db)

    try:
        rows = await pool.fetch(
            """
            SELECT
                connector_type,
                endpoint_identity,
                state,
                error_message,
                version,
                uptime_s,
                last_heartbeat_at,
                first_seen_at,
                counter_messages_ingested,
                counter_messages_failed,
                archived_at
            FROM connector_registry
            WHERE deleted_at IS NULL
            ORDER BY first_seen_at DESC
            """,
        )
    except Exception:
        logger.warning("connector summaries: failed to fetch from registry", exc_info=True)
        return ApiResponse[dict](data={"connectors": [], "connector_registry_available": False})

    # Count registry rows per connector_type. The `devices` badge list (below)
    # exists specifically for connector_types where connector_registry cannot
    # yet distinguish devices -- one shared row for several senders (bu-e16to).
    # Once a connector_type is fixed to register one row per device (bu-86zll,
    # e.g. OwnTracks), each row's own state/last_heartbeat_at is device-accurate,
    # and attaching the same ingestion_events-derived `devices` list to every
    # one of those rows would double up and could disagree with each row's own
    # (now-authoritative) liveness.
    #
    # The gate below (registry_row_counts vs. len(device_map[...])) intentionally
    # requires the registry to have caught up to *every* device the fallback
    # already knows about, not merely ">1". A connector_type mid-migration --
    # some devices already registering their own row, one sibling device still
    # dead/not-yet-posted since the fix deployed and so still without ANY
    # registry row -- would otherwise have its badge suppressed by an early
    # partial row count while that dead sibling has no row of its own to show
    # its staleness either, making it invisible via *both* signals (exactly the
    # bu-e16to failure mode this badge exists to prevent). Suppressing only
    # once registry_row_counts >= known device count avoids that window.
    registry_row_counts: dict[str, int] = {}
    for r in rows:
        registry_row_counts[r["connector_type"]] = (
            registry_row_counts.get(r["connector_type"], 0) + 1
        )

    # Build per-connector hourly timeseries from ingestion_events AND
    # filtered_events in one combined query (bu-scyro). Returns two 24-element
    # lists per connector (oldest hour first, newest last) — zero-filled for
    # missing buckets:
    #   - hourly_map          — 'ingested' series, sourced from
    #     public.ingestion_events WHERE status='ingested' (unchanged semantics)
    #   - hourly_filtered_map — 'filtered' series, sourced from
    #     connectors.filtered_events (every row regardless of status —
    #     filtered/error/replay_* — since all of them represent traffic that
    #     never reached ingestion_events)
    #
    # The two series are kept DISTINCT, never summed together: folding filtered
    # volume into "ingested" would fabricate ingestion volume that never
    # happened. This closes the bu-416vk/bu-scyro gap where every
    # self-persisting connector's skip volume (gmail, telegram, HA post-#2986,
    # calendar post-#2994) was invisible on this chart — pre-#2986 HA was the
    # anomaly whose skip decisions inflated the 'ingested' series instead.
    #
    # Sourced from the DB (not Prometheus) so both series are always populated
    # (subject only to hourly_events_available on a genuine query failure).
    hourly_map: dict[tuple[str, str], list[int]] = {}
    hourly_filtered_map: dict[tuple[str, str], list[int]] = {}
    hourly_events_available = True
    if rows:
        try:
            now_utc = _dt.datetime.now(_dt.UTC)
            # Truncate to the start of the current hour so bucket 23 is the most recent
            # complete-or-in-progress hour.
            window_start = now_utc.replace(minute=0, second=0, microsecond=0) - _dt.timedelta(
                hours=23
            )
            # Single UNION ALL query, bounded to the same 24h window on both
            # sides. filtered_events is monthly-partitioned (core_007); a 24h
            # window touches at most 2 partitions and is covered by
            # ix_filtered_events_timeline (received_at DESC) — no new index
            # required.
            hourly_rows = await pool.fetch(
                """
                SELECT connector_type, endpoint_identity, hour_bucket, source, event_count
                FROM (
                    SELECT
                        source_channel           AS connector_type,
                        source_endpoint_identity AS endpoint_identity,
                        date_trunc('hour', received_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
                            AS hour_bucket,
                        'ingested'               AS source,
                        count(*)                 AS event_count
                    FROM public.ingestion_events
                    WHERE received_at >= $1
                      AND status = 'ingested'
                    GROUP BY source_channel, source_endpoint_identity,
                             date_trunc('hour', received_at AT TIME ZONE 'UTC')
                    UNION ALL
                    SELECT
                        connector_type,
                        endpoint_identity,
                        date_trunc('hour', received_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
                            AS hour_bucket,
                        'filtered'               AS source,
                        count(*)                 AS event_count
                    FROM connectors.filtered_events
                    WHERE received_at >= $1
                    GROUP BY connector_type, endpoint_identity,
                             date_trunc('hour', received_at AT TIME ZONE 'UTC')
                ) combined
                """,
                window_start,
            )
            # Populate bucket arrays for each connector key seen in hourly_rows,
            # routing to the ingested or filtered map by the `source` discriminator.
            for hr in hourly_rows:
                key = (hr["connector_type"], hr["endpoint_identity"])
                target_map = hourly_map if hr["source"] == "ingested" else hourly_filtered_map
                if key not in target_map:
                    target_map[key] = [0] * 24
                # Determine which bucket index this hour falls into (0 = oldest, 23 = newest)
                bucket_offset = int((hr["hour_bucket"] - window_start).total_seconds() // 3600)
                if 0 <= bucket_offset < 24:
                    target_map[key][bucket_offset] = int(hr["event_count"])
        except Exception:
            logger.warning("connector summaries: failed to fetch hourly timeseries", exc_info=True)
            # hourly_map/hourly_filtered_map stay empty — connectors fall back to
            # all-zeros below, and hourly_events_available flips false so a
            # genuine query failure is never rendered as an honest all-quiet chart.
            hourly_events_available = False

    # Per-device liveness fallback for connector_types whose connector_registry
    # rows can't (yet) disambiguate devices on their own (bu-e16to). Some
    # connector_types have several distinct physical devices posting through
    # them (e.g. OwnTracks household phones) sharing one connector_registry row
    # -- historically ONE shared heartbeat identity, thrashing between whichever
    # device most recently resolved it, so a silently-dead sibling device was
    # otherwise invisible (fixed at the connector level for OwnTracks by
    # bu-86zll, which gives each device its own registry row; the fallback below
    # stays in place for any connector_type not yet fixed, gated by
    # registry_row_counts below). source_sender_identity on public.ingestion_events
    # is set per-event from the payload's own device id, independent of
    # connector_registry's identity churn, so it is the source of truth here.
    #
    # bu-scyro: also union connectors.filtered_events' sender_identity so a
    # device/sender that is now (post-#2986) 100% skip-routed into
    # filtered_events — never touching ingestion_events at all — is not
    # invisible to this liveness signal. filtered_events.sender_identity is
    # NOT NULL at the schema level (core_007) but a handful of connectors write
    # an empty string when no real identity was resolvable (google_health,
    # spotify, google_calendar's non-organizer branch) — those rows are
    # excluded (sender_identity <> '') so an empty placeholder never becomes a
    # fake "device". "unknown" placeholders (discord/gmail/telegram parse
    # failures) are kept, matching how the existing ingestion_events branch
    # already tolerates them.
    device_liveness_available = True
    device_map: dict[str, list[dict[str, Any]]] = {}
    if rows:
        try:
            device_lookback_start = _dt.datetime.now(_dt.UTC) - _DEVICE_LOOKBACK_WINDOW
            device_rows = await pool.fetch(
                """
                SELECT connector_type, sender_identity, MAX(last_seen_at) AS last_seen_at
                FROM (
                    SELECT
                        source_channel AS connector_type,
                        source_sender_identity AS sender_identity,
                        received_at AS last_seen_at
                    FROM public.ingestion_events
                    WHERE source_sender_identity IS NOT NULL
                      AND status = 'ingested'
                      AND received_at >= $1
                    UNION ALL
                    SELECT
                        connector_type,
                        sender_identity,
                        received_at AS last_seen_at
                    FROM connectors.filtered_events
                    WHERE sender_identity <> ''
                      AND received_at >= $1
                ) combined
                GROUP BY connector_type, sender_identity
                """,
                device_lookback_start,
            )
            now_for_devices = _dt.datetime.now(_dt.UTC)
            by_type: dict[str, list[dict[str, Any]]] = {}
            for dr in device_rows:
                by_type.setdefault(dr["connector_type"], []).append(
                    {"sender_identity": dr["sender_identity"], "last_seen_at": dr["last_seen_at"]}
                )
            # Only surface a `devices` list for genuinely multi-device connector_types
            # (>1 distinct sender identity ever seen) -- a single-sender connector's
            # device would just duplicate the roster row's own liveness verdict.
            for ctype, devices in by_type.items():
                if len(devices) < 2:
                    continue
                device_map[ctype] = [
                    {
                        "sender_identity": d["sender_identity"],
                        "last_seen_at": d["last_seen_at"].isoformat(),
                        "stale": (now_for_devices - d["last_seen_at"]) > _DEVICE_STALE_THRESHOLD,
                    }
                    for d in sorted(devices, key=lambda d: d["last_seen_at"], reverse=True)
                ]
        except Exception:
            logger.warning(
                "connector summaries: failed to fetch per-device liveness", exc_info=True
            )
            device_liveness_available = False
            device_map = {}

    # OwnTracks cadence diagnostic. Chronicler's movement inference reads the
    # durable point evidence table directly, so generic ingestion_events counts
    # are not a trustworthy proxy. Query only active OwnTracks identities;
    # archived endpoints remain reachable for history but must not create new
    # operational attention. A query failure has its own explicit availability
    # flag and produces no warnings, avoiding both a fabricated all-clear and a
    # false transport-health failure.
    owntracks_cadence_available = True
    owntracks_point_counts: dict[str, int] = {}
    active_owntracks_identities = [
        r["endpoint_identity"]
        for r in rows
        if r["connector_type"] == "owntracks" and r["archived_at"] is None
    ]
    if active_owntracks_identities:
        try:
            cadence_window_start = _dt.datetime.now(_dt.UTC) - _OWNTRACKS_CADENCE_WINDOW
            cadence_rows = await pool.fetch(
                """
                SELECT endpoint_identity, count(*) AS point_count
                FROM connectors.owntracks_points
                WHERE endpoint_identity = ANY($1::text[])
                  AND ts >= $2
                GROUP BY endpoint_identity
                """,
                active_owntracks_identities,
                cadence_window_start,
            )
            owntracks_point_counts = {
                cadence_row["endpoint_identity"]: int(cadence_row["point_count"])
                for cadence_row in cadence_rows
            }
        except Exception:
            logger.warning(
                "connector summaries: failed to fetch OwnTracks point cadence",
                exc_info=True,
            )
            owntracks_cadence_available = False

    # Precompute the "newer online sibling" lookup once for the archive review
    # queue (bu-u19yv), then evaluate each row against it below. Read-only and
    # derived entirely from the rows already fetched — no extra query, no
    # storage, and deliberately kept out of the fleet-health rollups.
    now_for_candidates = _dt.datetime.now(_dt.UTC)
    online_by_type = _online_identities_by_type(rows)

    connectors = []
    for r in rows:
        liveness = _liveness(r["last_heartbeat_at"])
        key = (r["connector_type"], r["endpoint_identity"])
        hourly = hourly_map.get(key, [0] * 24)
        hourly_filtered = hourly_filtered_map.get(key, [0] * 24)
        # Sum the hourly timeseries (already a real 24h window from public.ingestion_events)
        # to produce a true last-24h ingestion count.  The raw counter_messages_ingested is
        # cumulative since process start and must NOT be used as a "today" figure.
        messages_ingested_24h = sum(hourly)
        operational_warnings: list[str] = []
        if (
            owntracks_cadence_available
            and r["connector_type"] == "owntracks"
            and r["archived_at"] is None
        ):
            point_count = owntracks_point_counts.get(r["endpoint_identity"], 0)
            if point_count < _OWNTRACKS_MIN_POINTS_PER_WINDOW:
                operational_warnings.append(_owntracks_cadence_warning(point_count))
        connectors.append(
            {
                "connector_type": r["connector_type"],
                "endpoint_identity": r["endpoint_identity"],
                "liveness": liveness,
                "state": r["state"],
                "error_message": r["error_message"],
                "version": r["version"],
                "uptime_s": r["uptime_s"],
                "last_heartbeat_at": (
                    r["last_heartbeat_at"].isoformat() if r["last_heartbeat_at"] else None
                ),
                "first_seen_at": r["first_seen_at"].isoformat(),
                # Soft-archive state (bu-33dm2). Archived rows are still returned
                # here so the dashboard can group them into a collapsed "archived"
                # section (reachable for history), but the FE separates them out
                # so they never count toward attention/KPIs, and the fleet-health
                # rollups (cross-summary, /connectors/summary) exclude them
                # entirely. `archived` is a convenience boolean mirroring whether
                # `archived_at` is set.
                "archived": r["archived_at"] is not None,
                "archived_at": (r["archived_at"].isoformat() if r["archived_at"] else None),
                # Flag-only archive REVIEW-QUEUE suggestion (bu-u19yv): true when
                # this active identity last heartbeated >30d ago AND a newer
                # online sibling of the same connector_type exists. A pure
                # SUGGESTION — it never feeds the health rollups/alerting (those
                # only exclude `archived`), never removes the row from the active
                # roster, and can never mask a failing live connector (which is
                # not offline 30+ days). The dashboard surfaces these as a review
                # queue with a one-click archive reusing the archive endpoint.
                "archive_candidate": _is_archive_candidate(
                    connector_type=r["connector_type"],
                    endpoint_identity=r["endpoint_identity"],
                    archived_at=r["archived_at"],
                    last_heartbeat_at=r["last_heartbeat_at"],
                    online_identities_by_type=online_by_type,
                    now=now_for_candidates,
                ),
                # Additive, read-only operational diagnostics. These warnings
                # surface evidence quality without mutating the connector's
                # transport state/liveness or fleet-health rollups.
                "operational_warnings": operational_warnings,
                "today": {
                    "messages_ingested": messages_ingested_24h,
                    "messages_failed": r["counter_messages_failed"] or 0,
                },
                "hourly_events": hourly,
                # Distinct 'filtered' series — connectors.filtered_events volume for this
                # connector, NEVER folded into hourly_events/messages_ingested above (that
                # would fabricate ingestion volume that never happened). Render as a
                # visually-quiet second series alongside hourly_events.
                "hourly_filtered_events": hourly_filtered,
                # Only suppress the ingestion_events-derived `devices` badge
                # list once connector_registry has a row for *every* device the
                # fallback knows about for this connector_type (registry_row_counts
                # >= known device count) -- not merely more than one row. A
                # partially-migrated connector_type (some devices already have
                # their own row; a sibling still doesn't) must keep the badge so
                # that still-unregistered/dead sibling stays visible somewhere.
                "devices": (
                    device_map.get(r["connector_type"])
                    if registry_row_counts.get(r["connector_type"], 0)
                    < len(device_map.get(r["connector_type"], []))
                    else None
                ),
            }
        )

    return ApiResponse[dict](
        data={
            "connectors": connectors,
            # The registry is the primary roster source. This explicit true
            # distinguishes an actually empty registry from the HTTP-200
            # fallback above when its query failed.
            "connector_registry_available": True,
            "device_liveness_available": device_liveness_available,
            # False only if the combined ingested+filtered hourly query itself
            # raised — mirrors device_liveness_available
            # (genuine-failure-only degraded flag; never fabricated zeros).
            "hourly_events_available": hourly_events_available,
            # False only if the OwnTracks durable-point cadence query itself
            # raised. The connector warnings stay empty in that case and the
            # frontend names the degraded source explicitly.
            "owntracks_cadence_available": owntracks_cadence_available,
        }
    )


# ---------------------------------------------------------------------------
# GET /api/ingestion/connectors/cross-summary
# ---------------------------------------------------------------------------


@router.get("/cross-summary", response_model=ApiResponse[dict])
async def get_cross_connector_summary_with_aggregates(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Return cross-connector aggregate summary with ``aggregates_available`` flag.

    Aggregates health and volume counts across all active connectors and
    includes ``aggregates_available`` indicating whether Prometheus-backed
    per-connector time-series metrics are expected to be valid.

    Always returns HTTP 200 — database errors fall back to zero-value summary.
    """
    pool = _pool(db)
    aggregates_available = _get_prometheus_url() is not None

    # Check the pipeline cache for a recent successful fetch
    try:
        import time

        from butlers.api.routers.ingestion_pipeline import _CACHE_TTL_SECONDS, _pipeline_cache

        cached = _pipeline_cache.get("24h")
        if cached is not None:
            ts, data = cached
            if time.monotonic() - ts < _CACHE_TTL_SECONDS:
                aggregates_available = data.get("aggregates_available", False)
    except Exception:
        pass

    _zero_summary = {
        "total_connectors": 0,
        "connectors_online": 0,
        "connectors_stale": 0,
        "connectors_offline": 0,
        "total_messages_ingested": 0,
        "total_messages_failed": 0,
        "overall_error_rate_pct": 0.0,
        "aggregates_available": aggregates_available,
    }

    try:
        # Fetch per-connector heartbeat + message counters.
        # Liveness (online/stale/offline) is computed in Python from
        # last_heartbeat_at using the same thresholds as /summaries, so
        # both endpoints always agree on per-connector liveness counts.
        rows = await pool.fetch(
            """
            SELECT
                last_heartbeat_at,
                coalesce(counter_messages_ingested, 0) AS messages_ingested,
                coalesce(counter_messages_failed, 0)   AS messages_failed
            FROM connector_registry
            WHERE deleted_at IS NULL
              -- Archived (superseded) identities are excluded from the
              -- fleet-health rollup so a permanently-offline dead endpoint
              -- stops dragging the online/stale/offline counts down (bu-33dm2).
              AND archived_at IS NULL
            """,
        )
    except Exception:
        logger.warning("cross-summary: failed to query connector_registry", exc_info=True)
        return ApiResponse[dict](data=_zero_summary)

    if rows is None:
        return ApiResponse[dict](data=_zero_summary)

    online = stale = offline = 0
    total_ingested = total_failed = 0
    for r in rows:
        lv = _liveness(r["last_heartbeat_at"])
        if lv == "online":
            online += 1
        elif lv == "stale":
            stale += 1
        else:
            offline += 1
        total_ingested += int(r["messages_ingested"] or 0)
        total_failed += int(r["messages_failed"] or 0)

    total_attempts = total_ingested + total_failed
    error_rate_pct = (total_failed / total_attempts * 100.0) if total_attempts > 0 else 0.0

    return ApiResponse[dict](
        data={
            "total_connectors": len(rows),
            "connectors_online": online,
            "connectors_stale": stale,
            "connectors_offline": offline,
            "total_messages_ingested": total_ingested,
            "total_messages_failed": total_failed,
            "overall_error_rate_pct": round(error_rate_pct, 2),
            "aggregates_available": aggregates_available,
        }
    )


# ---------------------------------------------------------------------------
# POST /api/ingestion/connectors/{type}/{identity}/pause
# ---------------------------------------------------------------------------


@router.post(
    "/{connector_type}/{endpoint_identity}/pause",
    response_model=ApiResponse[dict],
    status_code=200,
)
async def pause_connector(
    connector_type: str,
    endpoint_identity: str,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Pause a connector — audit-only, no Approvals gate.

    Sets ``connector_registry.state`` to ``'paused'`` and emits an audit
    entry with ``action='connector.pause'``.

    No Approvals module call is made (this action is audit-log-only per the
    lifecycle gate matrix spec).

    Returns HTTP 200 with the connector identity on success.
    Returns HTTP 404 if the connector is not found in the registry.
    Returns HTTP 503 if the connector registry is unavailable.
    """
    pool = _pool(db)

    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                row = await conn.fetchrow(
                    "UPDATE connector_registry"
                    " SET state = 'paused'"
                    " WHERE connector_type = $1 AND endpoint_identity = $2 AND deleted_at IS NULL"
                    " RETURNING connector_type, endpoint_identity, state",
                    connector_type,
                    endpoint_identity,
                )
            except Exception:
                logger.warning(
                    "Failed to pause connector %s/%s",
                    connector_type,
                    endpoint_identity,
                    exc_info=True,
                )
                raise HTTPException(status_code=503, detail="Connector registry is not available")

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Connector '{connector_type}/{endpoint_identity}' not found",
                )

        # Emit the audit entry AFTER the state change has committed (outside the
        # transaction block, matching the disconnect/rotate-token/archive audit
        # pattern). If _audit_append were inside conn.transaction() and raised,
        # asyncpg would abort the transaction; the swallowed exception then lets
        # the context manager issue COMMIT, which Postgres turns into a ROLLBACK
        # of the aborted tx — silently discarding the pause while still returning
        # 200. Auditing post-commit keeps the audit write best-effort (logged on
        # failure) without ever rolling back the persisted state.
        try:
            client_host = getattr(request.client, "host", None) if request.client else None
            await _audit_append(
                conn,
                actor="dashboard",
                action="connector.pause",
                target=f"{connector_type}/{endpoint_identity}",
                note=f"Connector '{connector_type}/{endpoint_identity}' paused via dashboard",
                ip=client_host,
            )
        except Exception:
            logger.warning(
                "ingestion_connectors: failed to append audit_log entry for pause %s/%s",
                connector_type,
                endpoint_identity,
                exc_info=True,
            )

    logger.info("Paused connector %s/%s", connector_type, endpoint_identity)

    return ApiResponse[dict](
        data={
            "connector_type": str(row["connector_type"]),
            "endpoint_identity": str(row["endpoint_identity"]),
            "state": str(row["state"]),
        }
    )


# ---------------------------------------------------------------------------
# POST /api/ingestion/connectors/{type}/{identity}/run-now
# ---------------------------------------------------------------------------


@router.post(
    "/{connector_type}/{endpoint_identity}/run-now",
    response_model=ApiResponse[dict],
    status_code=200,
)
async def run_now_connector(
    connector_type: str,
    endpoint_identity: str,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Resume a paused connector and trigger the next poll cycle — audit-only, no Approvals gate.

    Validates that the connector is currently in the ``'paused'`` state.
    Returns HTTP 409 if the connector is not paused (spec: "Run-now semantics").

    On a paused connector:
    - Clears the pause by setting ``state`` back to ``'unknown'`` (the connector
      will self-report its true state on the next heartbeat)
    - Emits an audit entry with ``action='connector.run_now'``

    The connector picks up the state change on its next poll cycle.

    Returns HTTP 200 with the connector identity on success.
    Returns HTTP 404 if the connector is not found in the registry.
    Returns HTTP 409 if the connector is not currently paused.
    Returns HTTP 503 if the connector registry is unavailable.
    """
    pool = _pool(db)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # SELECT FOR UPDATE: lock the row to prevent a concurrent pause/run-now race
            try:
                current_row = await conn.fetchrow(
                    "SELECT connector_type, endpoint_identity, state"
                    " FROM connector_registry"
                    " WHERE connector_type = $1 AND endpoint_identity = $2 AND deleted_at IS NULL"
                    " FOR UPDATE",
                    connector_type,
                    endpoint_identity,
                )
            except Exception:
                logger.warning(
                    "Failed to fetch connector state for run-now %s/%s",
                    connector_type,
                    endpoint_identity,
                    exc_info=True,
                )
                raise HTTPException(status_code=503, detail="Connector registry is not available")

            if current_row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Connector '{connector_type}/{endpoint_identity}' not found",
                )

            current_state = str(current_row["state"])
            if current_state != "paused":
                # Spec: "Run-now on non-paused connector rejected"
                # The response body identifies the connector's actual state.
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Connector '{connector_type}/{endpoint_identity}' is not paused "
                        f"(current state: '{current_state}'). "
                        "run-now is only valid on a paused connector."
                    ),
                )

            # Clear the pause — set state to 'unknown'; connector self-reports on next heartbeat
            try:
                row = await conn.fetchrow(
                    "UPDATE connector_registry"
                    " SET state = 'unknown'"
                    " WHERE connector_type = $1 AND endpoint_identity = $2"
                    " RETURNING connector_type, endpoint_identity, state",
                    connector_type,
                    endpoint_identity,
                )
            except Exception:
                logger.warning(
                    "Failed to clear pause for connector %s/%s",
                    connector_type,
                    endpoint_identity,
                    exc_info=True,
                )
                raise HTTPException(status_code=503, detail="Connector registry is not available")

        # Emit the audit entry AFTER the state change has committed (outside the
        # transaction block, matching the disconnect/rotate-token/archive audit
        # pattern). If _audit_append were inside conn.transaction() and raised,
        # asyncpg would abort the transaction; the swallowed exception then lets
        # the context manager issue COMMIT, which Postgres turns into a ROLLBACK
        # of the aborted tx — silently discarding the resume while still returning
        # 200. Auditing post-commit keeps the audit write best-effort (logged on
        # failure) without ever rolling back the persisted state.
        try:
            client_host = getattr(request.client, "host", None) if request.client else None
            await _audit_append(
                conn,
                actor="dashboard",
                action="connector.run_now",
                target=f"{connector_type}/{endpoint_identity}",
                note=f"Connector '{connector_type}/{endpoint_identity}' resumed via run-now",
                ip=client_host,
            )
        except Exception:
            logger.warning(
                "ingestion_connectors: failed to append audit_log entry for run-now %s/%s",
                connector_type,
                endpoint_identity,
                exc_info=True,
            )

    logger.info("run-now: cleared pause for connector %s/%s", connector_type, endpoint_identity)

    return ApiResponse[dict](
        data={
            "connector_type": str(row["connector_type"]),
            "endpoint_identity": str(row["endpoint_identity"]),
            "state": str(row["state"]),
        }
    )


# ---------------------------------------------------------------------------
# POST /api/ingestion/connectors/{type}/{identity}/archive
# POST /api/ingestion/connectors/{type}/{identity}/unarchive
# ---------------------------------------------------------------------------


@router.post(
    "/{connector_type}/{endpoint_identity}/archive",
    response_model=ApiResponse[dict],
    status_code=200,
)
async def archive_connector(
    connector_type: str,
    endpoint_identity: str,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Archive a superseded connector identity — audit-only, no Approvals gate (bu-33dm2).

    Sets ``connector_registry.archived_at`` to NOW() (idempotent — a re-archive
    of an already-archived row leaves the original timestamp untouched) and emits
    an audit entry with ``action='connector.archive'``.

    Archiving is a soft, reversible state distinct from ``disconnect``'s
    ``deleted_at`` soft-delete: an archived row is still listed (grouped into the
    dashboard's collapsed "archived" section and reachable for history) but is
    EXCLUDED from the fleet-health rollups, so a permanently-offline superseded
    identity stops dragging fleet health down. History references
    (ingestion_events, filtered_events, audit_log) are preserved — nothing is
    deleted.

    No Approvals-module call is made (archival is a low-risk, reversible,
    audit-log-only action, matching the ``pause`` gate).

    Returns HTTP 200 with the connector identity + ``archived_at`` on success.
    Returns HTTP 404 if the connector is not found (or already soft-deleted).
    Returns HTTP 503 if the connector registry is unavailable.
    """
    return await _set_archived(
        connector_type,
        endpoint_identity,
        request,
        db,
        archive=True,
    )


@router.post(
    "/{connector_type}/{endpoint_identity}/unarchive",
    response_model=ApiResponse[dict],
    status_code=200,
)
async def unarchive_connector(
    connector_type: str,
    endpoint_identity: str,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Restore an archived connector identity to the active roster (bu-33dm2).

    Clears ``connector_registry.archived_at`` (sets it back to NULL) and emits an
    audit entry with ``action='connector.unarchive'``. Idempotent — unarchiving a
    row that is not archived is a no-op that still returns 200.

    Audit-only, no Approvals gate (mirrors ``archive`` / ``pause``).

    Returns HTTP 200 with the connector identity on success.
    Returns HTTP 404 if the connector is not found (or soft-deleted).
    Returns HTTP 503 if the connector registry is unavailable.
    """
    return await _set_archived(
        connector_type,
        endpoint_identity,
        request,
        db,
        archive=False,
    )


async def _set_archived(
    connector_type: str,
    endpoint_identity: str,
    request: Request,
    db: DatabaseManager,
    *,
    archive: bool,
) -> ApiResponse[dict]:
    """Shared implementation for archive / unarchive (bu-33dm2).

    ``archive=True`` sets ``archived_at`` to NOW() only when it is currently NULL
    (so a re-archive preserves the original timestamp); ``archive=False`` clears
    it. Both emit an audit entry and are gated only by the connector existing and
    not being soft-deleted.
    """
    pool = _pool(db)
    action = "connector.archive" if archive else "connector.unarchive"

    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                if archive:
                    # COALESCE preserves an existing archived_at (idempotent re-archive).
                    row = await conn.fetchrow(
                        "UPDATE connector_registry"
                        " SET archived_at = COALESCE(archived_at, now())"
                        " WHERE connector_type = $1 AND endpoint_identity = $2"
                        " AND deleted_at IS NULL"
                        " RETURNING connector_type, endpoint_identity, archived_at",
                        connector_type,
                        endpoint_identity,
                    )
                else:
                    row = await conn.fetchrow(
                        "UPDATE connector_registry"
                        " SET archived_at = NULL"
                        " WHERE connector_type = $1 AND endpoint_identity = $2"
                        " AND deleted_at IS NULL"
                        " RETURNING connector_type, endpoint_identity, archived_at",
                        connector_type,
                        endpoint_identity,
                    )
            except Exception:
                logger.warning(
                    "Failed to %s connector %s/%s",
                    "archive" if archive else "unarchive",
                    connector_type,
                    endpoint_identity,
                    exc_info=True,
                )
                raise HTTPException(status_code=503, detail="Connector registry is not available")

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Connector '{connector_type}/{endpoint_identity}' not found",
                )

        # Emit the audit entry AFTER the state change has committed (outside the
        # transaction block, matching the disconnect/rotate-token audit pattern).
        # If _audit_append were inside conn.transaction() and raised, asyncpg
        # would abort the transaction; the swallowed exception then lets the
        # context manager issue COMMIT, which Postgres turns into a ROLLBACK of
        # the aborted tx — silently discarding the archive/unarchive while still
        # returning 200. Auditing post-commit keeps the audit write best-effort
        # (logged on failure) without ever rolling back the persisted state.
        try:
            client_host = getattr(request.client, "host", None) if request.client else None
            verb = "archived" if archive else "unarchived"
            await _audit_append(
                conn,
                actor="dashboard",
                action=action,
                target=f"{connector_type}/{endpoint_identity}",
                note=(f"Connector '{connector_type}/{endpoint_identity}' {verb} via dashboard"),
                ip=client_host,
            )
        except Exception:
            logger.warning(
                "ingestion_connectors: failed to append audit_log entry for %s %s/%s",
                action,
                connector_type,
                endpoint_identity,
                exc_info=True,
            )

    logger.info(
        "%s connector %s/%s",
        "Archived" if archive else "Unarchived",
        connector_type,
        endpoint_identity,
    )

    archived_at = row["archived_at"]
    return ApiResponse[dict](
        data={
            "connector_type": str(row["connector_type"]),
            "endpoint_identity": str(row["endpoint_identity"]),
            "archived": archived_at is not None,
            "archived_at": archived_at.isoformat() if archived_at else None,
        }
    )


# ---------------------------------------------------------------------------
# POST /api/ingestion/connectors/{type}/{identity}/disconnect
# ---------------------------------------------------------------------------

#: Path to the connector-oauth-scope-surface spec (blocking reauth)
_OAUTH_SCOPE_SURFACE_SPEC = "connector-oauth-scope-surface"


@router.post(
    "/{connector_type}/{endpoint_identity}/disconnect",
    response_model=ApiResponse[dict],
    status_code=202,
)
async def disconnect_connector(
    connector_type: str,
    endpoint_identity: str,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Disconnect a connector — Approvals-gated; soft-deletes via ``deleted_at`` (§4.4).

    Submits a pending approval action for the disconnect operation.  The
    connector row is NOT immediately modified; the approval gate keeps the
    connector in its current state until the action is resolved.

    When the approval resolves:
    - Approved: ``connector_registry.deleted_at`` is set to NOW() (soft-delete)
    - Denied: no state change occurs

    The Approvals module runs at the MCP server level — the dashboard API
    submits the intent via ``pending_actions``; the MCP layer resolves it.

    Returns HTTP 202 with ``{status: "pending_approval", action_id: ...}`` on success.
    Returns HTTP 404 if the connector is not found in the registry.
    Returns HTTP 503 if the connector registry or approvals subsystem is unavailable.

    An audit entry with ``action='connector.disconnect'`` is emitted on submission.
    """
    pool = _pool(db)

    # Verify connector exists before creating a pending action
    try:
        existing = await pool.fetchrow(
            "SELECT connector_type, endpoint_identity FROM connector_registry"
            " WHERE connector_type = $1 AND endpoint_identity = $2 AND deleted_at IS NULL",
            connector_type,
            endpoint_identity,
        )
    except Exception:
        logger.warning(
            "disconnect: failed to fetch connector %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Connector registry is not available")

    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Connector '{connector_type}/{endpoint_identity}' not found",
        )

    # Create a pending_actions row for the Approvals gate
    action_id = uuid.uuid4()
    now = datetime.now(UTC)
    target = f"{connector_type}/{endpoint_identity}"
    tool_args = CONNECTOR_DISCONNECT_COMMAND.materialize(
        {
            "connector_type": connector_type,
            "endpoint_identity": endpoint_identity,
        }
    )
    # Bind the sanitized dict directly (no json.dumps, no ::jsonb cast) —
    # asyncpg's registered jsonb codec already serializes once; pre-serializing
    # double-encodes into a jsonb-typed STRING (bu-cymc4/bu-bstqu).
    safe_tool_args = json.loads(json.dumps(tool_args, default=str))

    # 72-hour expiry for lifecycle approval actions
    expires_at = now + timedelta(hours=72)

    try:
        # park_pending_action is the single choke point for PENDING inserts:
        # it writes the row AND attempts the owner-facing push in one call
        # (bu-mda0r). This dashboard-API context has no daemon-cached
        # runtime, so a fresh one is built per request.
        await park_pending_action(
            pool,
            action_id=action_id,
            tool_name=CONNECTOR_DISCONNECT_COMMAND.name,
            tool_args=safe_tool_args,
            agent_summary=f"Disconnect connector '{target}' (soft-delete)",
            requested_at=now,
            expires_at=expires_at,
            origin_butler=_SWITCHBOARD_BUTLER,
            approval_push_runtime=_build_dashboard_approval_push_runtime(db),
        )
    except Exception:
        logger.warning(
            "disconnect: failed to insert pending_action for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Approvals subsystem is not available")

    # Emit audit entry for the disconnect submission
    try:
        client_host = getattr(request.client, "host", None) if request.client else None
        await _audit_append(
            pool,
            actor="dashboard",
            action="connector.disconnect",
            target=target,
            note=(
                f"Connector '{target}' disconnect submitted for approval (action_id={action_id})"
            ),
            ip=client_host,
        )
    except Exception:
        logger.warning(
            "disconnect: failed to append audit_log entry for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )

    logger.info(
        "Disconnect submitted for connector %s/%s (action_id=%s)",
        connector_type,
        endpoint_identity,
        action_id,
    )

    return ApiResponse[dict](
        data={
            "status": "pending_approval",
            "action_id": str(action_id),
            "connector_type": connector_type,
            "endpoint_identity": endpoint_identity,
            "message": (
                f"Connector '{target}' disconnect queued for approval. "
                "The connector will be soft-deleted when the action is approved."
            ),
        }
    )


# ---------------------------------------------------------------------------
# POST /api/ingestion/connectors/{type}/{identity}/rotate-token
# ---------------------------------------------------------------------------


@router.post(
    "/{connector_type}/{endpoint_identity}/rotate-token",
    response_model=None,
    status_code=409,
)
async def rotate_connector_token(
    connector_type: str,
    endpoint_identity: str,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> NoReturn:
    """Reject token rotation until it has a safe replayable command.

    The endpoint intentionally does not persist credential material in a
    pending action. It also has no durable credential reference or deterministic
    provider operation to invoke later, so parking the request would let an
    owner approve a command that cannot execute. Record a safe audit failure
    and reject before the pending-actions boundary instead.
    """
    pool = _pool(db)

    # Verify connector exists before creating a pending action
    try:
        existing = await pool.fetchrow(
            "SELECT connector_type, endpoint_identity FROM connector_registry"
            " WHERE connector_type = $1 AND endpoint_identity = $2 AND deleted_at IS NULL",
            connector_type,
            endpoint_identity,
        )
    except Exception:
        logger.warning(
            "rotate-token: failed to fetch connector %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Connector registry is not available")

    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Connector '{connector_type}/{endpoint_identity}' not found",
        )

    target = f"{connector_type}/{endpoint_identity}"

    # Audit the refusal itself. If this cannot be persisted, do not turn the
    # missing audit signal into a deceptively normal client failure.
    try:
        client_host = getattr(request.client, "host", None) if request.client else None
        await _audit_append(
            pool,
            actor="dashboard",
            action="connector.rotate_token.unreplayable",
            target=target,
            note=(
                f"Token rotation rejected for connector '{target}': "
                "no safe replayable command is available; no pending action was created"
            ),
            ip=client_host,
            result="error",
            error="safe replayable command unavailable",
        )
    except Exception:
        logger.error(
            "rotate-token: failed to append unreplayable-command audit for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail="Token rotation is unavailable because its refusal audit could not be recorded",
        )

    logger.warning(
        "rotate-token: rejected unreplayable request for connector %s/%s",
        connector_type,
        endpoint_identity,
    )
    raise HTTPException(
        status_code=409,
        detail=CONNECTOR_ROTATE_TOKEN_PRODUCER.rejection_reason,
    )


# ---------------------------------------------------------------------------
# POST /api/ingestion/connectors/{type}/{identity}/reauth
# ---------------------------------------------------------------------------


@router.post(
    "/{connector_type}/{endpoint_identity}/reauth",
    status_code=503,
)
async def reauth_connector(
    connector_type: str,
    endpoint_identity: str,
) -> dict:
    """Reauth connector — BLOCKED until ``connector-oauth-scope-surface`` spec exists (§4.6).

    This endpoint is permanently blocked at the handler level with HTTP 503
    until the ``connector-oauth-scope-surface`` spec is ratified and implemented.

    The response body identifies the blocking spec dependency by name.
    No ``Retry-After`` header is set — recovery requires spec creation, not time.

    No Approvals-module call is made; the request is rejected before any
    approval entry is created.
    """
    raise HTTPException(
        status_code=503,
        detail={
            "blocked_by_spec": _OAUTH_SCOPE_SURFACE_SPEC,
            "message": (
                f"The reauth action is blocked until the '{_OAUTH_SCOPE_SURFACE_SPEC}' "
                "spec is ratified. This endpoint will return HTTP 503 until that spec "
                "exists in openspec/specs/. No Retry-After applies — recovery requires "
                "spec creation, not time."
            ),
            "connector_type": connector_type,
            "endpoint_identity": endpoint_identity,
        },
    )


# ---------------------------------------------------------------------------
# Static connector profile catalog
#
# These are the connector types the framework can deploy, independent of
# whether any instance is currently registered in connector_registry.
# The response is safe to cache on the client for at least 60 seconds.
#
# Fields: connector_type, channel, provider, display_name, supports_backfill
# ---------------------------------------------------------------------------

_CONNECTOR_CATALOG: list[dict[str, Any]] = [
    {
        "connector_type": "gmail",
        "channel": "email",
        "provider": "google",
        "display_name": "Gmail",
        "supports_backfill": True,
    },
    {
        "connector_type": "telegram_bot",
        "channel": "telegram",
        "provider": "telegram",
        "display_name": "Telegram Bot",
        "supports_backfill": False,
    },
    {
        "connector_type": "telegram_user_client",
        "channel": "telegram",
        "provider": "telegram",
        "display_name": "Telegram User Client",
        "supports_backfill": True,
    },
    {
        "connector_type": "home_assistant",
        "channel": "home-assistant",
        "provider": "home_assistant",
        "display_name": "Home Assistant",
        "supports_backfill": False,
    },
    {
        "connector_type": "discord_user",
        "channel": "discord",
        "provider": "discord",
        "display_name": "Discord User Client",
        "supports_backfill": True,
    },
    {
        "connector_type": "spotify",
        "channel": "spotify",
        "provider": "spotify",
        "display_name": "Spotify",
        "supports_backfill": False,
    },
    {
        "connector_type": "owntracks",
        "channel": "owntracks",
        "provider": "owntracks",
        "display_name": "OwnTracks",
        "supports_backfill": False,
    },
    {
        "connector_type": "whatsapp_user_client",
        "channel": "whatsapp",
        "provider": "whatsapp",
        "display_name": "WhatsApp User Client",
        "supports_backfill": False,
    },
    {
        "connector_type": "steam",
        "channel": "steam",
        "provider": "steam",
        "display_name": "Steam",
        "supports_backfill": False,
    },
    {
        "connector_type": "google_calendar",
        "channel": "google_calendar",
        "provider": "google",
        "display_name": "Google Calendar",
        "supports_backfill": True,
    },
    {
        "connector_type": "google_drive",
        "channel": "google_drive",
        "provider": "google",
        "display_name": "Google Drive",
        "supports_backfill": True,
    },
    {
        "connector_type": "google_health",
        "channel": "google_health",
        "provider": "google",
        "display_name": "Google Health",
        "supports_backfill": True,
    },
    {
        "connector_type": "activitywatch",
        "channel": "activitywatch",
        "provider": "activitywatch",
        "display_name": "ActivityWatch",
        "supports_backfill": False,
    },
]


class ConnectorProfile(BaseModel):
    """A single connector profile entry from the discovery catalog."""

    connector_type: str
    channel: str
    provider: str
    display_name: str
    supports_backfill: bool


class ConnectorAvailableResponse(BaseModel):
    """Response body for GET /api/ingestion/connectors/available."""

    data: list[ConnectorProfile]


# ---------------------------------------------------------------------------
# GET /api/ingestion/connectors/available
# ---------------------------------------------------------------------------


@router.get("/available", response_model=ConnectorAvailableResponse)
async def list_available_connectors() -> ConnectorAvailableResponse:
    """Return the list of connector profiles the framework can deploy.

    The response is independent of whether any instance is currently
    registered in connector_registry.  Suitable for client-side caching
    for at least 60 seconds.

    Used by the dashboard "add connector" affordance and the
    ConnectorsListPage dormant/available section (§3.5).
    """
    profiles = [ConnectorProfile(**p) for p in _CONNECTOR_CATALOG]
    return ConnectorAvailableResponse(data=profiles)


# ---------------------------------------------------------------------------
# Connector-scoped event and rule endpoints [bu-5ywn2]
# ---------------------------------------------------------------------------

_MAX_CONNECTOR_EVENTS = 100
_MAX_CONNECTOR_INCIDENTS = 50
_DEFAULT_CONNECTOR_EVENTS = 20
_DEFAULT_CONNECTOR_INCIDENTS = 10

#: Status values that classify an ingestion event as an incident
_INCIDENT_STATUSES: frozenset[str] = frozenset({"failed", "error", "replay_failed"})


class ConnectorEventSummary(BaseModel):
    """A single event row returned from the connector-scoped events endpoint."""

    id: str
    received_at: str | None
    source_channel: str | None
    source_sender_identity: str | None
    status: str
    filter_reason: str | None
    error_detail: str | None


class ConnectorEventsResponse(BaseModel):
    """Response envelope for GET …/{type}/{identity}/events."""

    events: list[ConnectorEventSummary]
    connector_type: str
    endpoint_identity: str
    total_returned: int


class ConnectorIncidentSummary(BaseModel):
    """A single incident row returned from the connector-scoped incidents endpoint."""

    id: str
    received_at: str | None
    source_channel: str | None
    status: str
    error_detail: str | None
    filter_reason: str | None


class ConnectorIncidentsResponse(BaseModel):
    """Response envelope for GET …/{type}/{identity}/incidents."""

    incidents: list[ConnectorIncidentSummary]
    connector_type: str
    endpoint_identity: str
    total_returned: int


class ConnectorRoutingRule(BaseModel):
    """A single ingestion rule referencing this connector."""

    id: str
    scope: str
    rule_type: str
    condition: dict[str, Any]
    action: str
    priority: int
    enabled: bool
    name: str | None
    description: str | None
    created_by: str
    created_at: str
    updated_at: str


class ConnectorRoutingRulesResponse(BaseModel):
    """Response envelope for GET …/{type}/{identity}/routing-rules."""

    rules: list[ConnectorRoutingRule]
    connector_type: str
    endpoint_identity: str
    total_returned: int
    filter_note: str | None = None


# ---------------------------------------------------------------------------
# GET /api/ingestion/connectors/{type}/{identity}/events
# ---------------------------------------------------------------------------


@router.get(
    "/{connector_type}/{endpoint_identity}/events",
    response_model=ConnectorEventsResponse,
)
async def list_connector_events(
    connector_type: str,
    endpoint_identity: str,
    limit: int = Query(
        _DEFAULT_CONNECTOR_EVENTS,
        ge=1,
        le=_MAX_CONNECTOR_EVENTS,
        description="Max events to return (default 20, max 100)",
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ConnectorEventsResponse:
    """Return the most recent events for a specific connector.

    Queries the unified ingestion timeline (public.ingestion_events UNION ALL
    connectors.filtered_events) scoped to this connector's (connector_type,
    endpoint_identity) pair.  Results are ordered newest first.

    public.ingestion_events is matched via source_channel (connector_type) AND
    source_endpoint_identity (endpoint_identity).
    connectors.filtered_events is matched via connector_type AND endpoint_identity.

    Returns HTTP 404 if the connector is not in the registry.
    Returns HTTP 503 if the connector registry is unavailable.
    """
    pool = _pool(db)

    # Verify connector exists in registry before querying events
    try:
        existing = await pool.fetchrow(
            "SELECT connector_type, endpoint_identity FROM connector_registry"
            " WHERE connector_type = $1 AND endpoint_identity = $2 AND deleted_at IS NULL",
            connector_type,
            endpoint_identity,
        )
    except Exception:
        logger.warning(
            "connector-events: registry lookup failed for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Connector registry is not available")

    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Connector '{connector_type}/{endpoint_identity}' not found",
        )

    # Query unified event timeline scoped to this connector.
    # Two sources:
    #   1. public.ingestion_events — matched via source_endpoint_identity
    #   2. connectors.filtered_events — matched via connector_type + endpoint_identity
    try:
        rows = await pool.fetch(
            """
            SELECT
                id::text AS id,
                received_at,
                source_channel,
                source_sender_identity,
                status,
                NULL::text AS filter_reason,
                error_detail
            FROM public.ingestion_events
            WHERE source_endpoint_identity = $2
              AND source_channel = $1
            UNION ALL
            SELECT
                id::text AS id,
                received_at,
                source_channel,
                sender_identity AS source_sender_identity,
                status,
                filter_reason,
                error_detail
            FROM connectors.filtered_events
            WHERE connector_type = $1
              AND endpoint_identity = $2
            ORDER BY received_at DESC NULLS LAST
            LIMIT $3
            """,
            connector_type,
            endpoint_identity,
            limit,
        )
    except Exception:
        logger.warning(
            "connector-events: failed to query events for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Event query failed")

    events = [
        ConnectorEventSummary(
            id=str(r["id"]),
            received_at=r["received_at"].isoformat() if r["received_at"] else None,
            source_channel=r["source_channel"],
            source_sender_identity=r["source_sender_identity"],
            status=r["status"],
            filter_reason=r["filter_reason"],
            error_detail=r["error_detail"],
        )
        for r in rows
    ]

    return ConnectorEventsResponse(
        events=events,
        connector_type=connector_type,
        endpoint_identity=endpoint_identity,
        total_returned=len(events),
    )


# ---------------------------------------------------------------------------
# GET /api/ingestion/connectors/{type}/{identity}/incidents
# ---------------------------------------------------------------------------


@router.get(
    "/{connector_type}/{endpoint_identity}/incidents",
    response_model=ConnectorIncidentsResponse,
)
async def list_connector_incidents(
    connector_type: str,
    endpoint_identity: str,
    limit: int = Query(
        _DEFAULT_CONNECTOR_INCIDENTS,
        ge=1,
        le=_MAX_CONNECTOR_INCIDENTS,
        description="Max incidents to return (default 10, max 50)",
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ConnectorIncidentsResponse:
    """Return recent incident events (failures and errors) for a specific connector.

    An incident is any event with status in ('failed', 'error', 'replay_failed').
    This represents degraded or failed processing — distinct from the full event
    stream which includes successfully ingested events.

    Queries both public.ingestion_events and connectors.filtered_events, each
    filtered by the incident status set and scoped to this connector.  Results
    are ordered newest first.

    Returns HTTP 404 if the connector is not in the registry.
    Returns HTTP 503 if the connector registry is unavailable.
    """
    pool = _pool(db)

    # Verify connector exists in registry before querying incidents
    try:
        existing = await pool.fetchrow(
            "SELECT connector_type, endpoint_identity FROM connector_registry"
            " WHERE connector_type = $1 AND endpoint_identity = $2 AND deleted_at IS NULL",
            connector_type,
            endpoint_identity,
        )
    except Exception:
        logger.warning(
            "connector-incidents: registry lookup failed for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Connector registry is not available")

    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Connector '{connector_type}/{endpoint_identity}' not found",
        )

    # Build ordered incident status tuple for parameterised ANY() check
    incident_statuses = list(_INCIDENT_STATUSES)

    try:
        rows = await pool.fetch(
            """
            SELECT
                id::text AS id,
                received_at,
                source_channel,
                status,
                error_detail,
                NULL::text AS filter_reason
            FROM public.ingestion_events
            WHERE source_endpoint_identity = $2
              AND source_channel = $1
              AND status = ANY($3::text[])
            UNION ALL
            SELECT
                id::text AS id,
                received_at,
                source_channel,
                status,
                error_detail,
                filter_reason
            FROM connectors.filtered_events
            WHERE connector_type = $1
              AND endpoint_identity = $2
              AND status = ANY($3::text[])
            ORDER BY received_at DESC NULLS LAST
            LIMIT $4
            """,
            connector_type,
            endpoint_identity,
            incident_statuses,
            limit,
        )
    except Exception:
        logger.warning(
            "connector-incidents: failed to query incidents for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Incident query failed")

    incidents = [
        ConnectorIncidentSummary(
            id=str(r["id"]),
            received_at=r["received_at"].isoformat() if r["received_at"] else None,
            source_channel=r["source_channel"],
            status=r["status"],
            error_detail=r["error_detail"],
            filter_reason=r["filter_reason"],
        )
        for r in rows
    ]

    return ConnectorIncidentsResponse(
        incidents=incidents,
        connector_type=connector_type,
        endpoint_identity=endpoint_identity,
        total_returned=len(incidents),
    )


# ---------------------------------------------------------------------------
# GET /api/ingestion/connectors/{type}/{identity}/routing-rules
# ---------------------------------------------------------------------------


@router.get(
    "/{connector_type}/{endpoint_identity}/routing-rules",
    response_model=ConnectorRoutingRulesResponse,
)
async def list_connector_routing_rules(
    connector_type: str,
    endpoint_identity: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ConnectorRoutingRulesResponse:
    """Return ingestion rules referencing this connector.

    Queries the ``ingestion_rules`` table for rules whose ``scope`` matches
    the structured connector scope: ``'connector:<connector_type>:<endpoint_identity>'``.

    This is a precise structured match — no text search is needed because the
    scope column encodes connector identity explicitly in the format defined
    by design.md D2.

    Results are ordered by priority ASC, created_at ASC, id ASC (same as
    the global rule list endpoint).

    Returns HTTP 404 if the connector is not in the registry.
    Returns HTTP 503 if the connector registry or rules table is unavailable.
    """
    pool = _pool(db)

    # Verify connector exists in registry
    try:
        existing = await pool.fetchrow(
            "SELECT connector_type, endpoint_identity FROM connector_registry"
            " WHERE connector_type = $1 AND endpoint_identity = $2 AND deleted_at IS NULL",
            connector_type,
            endpoint_identity,
        )
    except Exception:
        logger.warning(
            "connector-routing-rules: registry lookup failed for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Connector registry is not available")

    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Connector '{connector_type}/{endpoint_identity}' not found",
        )

    # Structured scope match: design.md D2 format is 'connector:<type>:<identity>'
    connector_scope = f"connector:{connector_type}:{endpoint_identity}"

    try:
        rows = await pool.fetch(
            """
            SELECT
                id::text AS id,
                scope,
                rule_type,
                condition,
                action,
                priority,
                enabled,
                name,
                description,
                created_by,
                created_at,
                updated_at
            FROM ingestion_rules
            WHERE scope = $1
              AND deleted_at IS NULL
            ORDER BY priority ASC, created_at ASC, id ASC
            """,
            connector_scope,
        )
    except Exception:
        logger.warning(
            "connector-routing-rules: failed to query rules for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Routing rules query failed")

    rules = []
    for r in rows:
        condition = r["condition"]
        if isinstance(condition, str):
            condition = json.loads(condition)
        rules.append(
            ConnectorRoutingRule(
                id=str(r["id"]),
                scope=r["scope"],
                rule_type=r["rule_type"],
                condition=condition,
                action=r["action"],
                priority=r["priority"],
                enabled=r["enabled"],
                name=r["name"],
                description=r["description"],
                created_by=r["created_by"],
                created_at=r["created_at"].isoformat(),
                updated_at=r["updated_at"].isoformat(),
            )
        )

    return ConnectorRoutingRulesResponse(
        rules=rules,
        connector_type=connector_type,
        endpoint_identity=endpoint_identity,
        total_returned=len(rules),
    )
