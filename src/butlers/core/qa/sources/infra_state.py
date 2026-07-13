"""InfraStateSource — connector, butler-heartbeat, backup, and external-deadman staleness.

Real incident (2026-07-10 JARVIS pursuit dossier, eco:reliability): every
infra health signal was pull-only (dashboard tiles only) — connectors have
been silently dead for 7+ weeks with nothing noticing. bu-9r3hd.1's migration
drift sentinel closed the "merged != deployed" gap for schema drift, but
explicitly deferred every OTHER infra-health signal to this bead (see
``openspec/changes/deploy-drift-sentinel/tasks.md``, "Deferred" section):
connector-offline, backup-stale, heartbeat-stale, plus the external deadman
(``butlers.jobs.external_deadman``).

Unlike ``deploy_drift.py`` (which escalates directly to
``public.healing_attempts`` because it runs in the dashboard-api process, a
process boundary away from the QA staffer's patrol loop), this module is a
genuine ``DiscoverySource`` registered with the QA staffer — it participates
in the normal patrol -> triage -> dispatch pipeline like ``log_scanner`` /
``session_records`` / ``tool_call_failures``. An investigation agent (or the
existing no-commits-produced safety net in ``core.qa.dispatch``) is trusted
to recognize these as ops/infra issues rather than code bugs, exactly as it
already does for any other discovery source's findings — no bespoke
direct-to-``unfixable`` shortcut is reimplemented here.

Four checks, one discovery source
----------------------------------
1. **connector-offline** — reads ``public.v_qa_connector_state`` (core
   migration ``sw_024``, a sanctioned RFC 0010 view over
   ``switchboard.connector_registry``). Reuses
   ``butlers.api.models.connector.derive_liveness`` — the SAME
   online/stale/offline definition the dashboard's own connector list already
   uses — rather than inventing a second threshold that could quietly
   disagree with it. A ``state='paused'`` connector (a deliberate operator
   action via the pause endpoint), an archived/soft-deleted identity, and a
   cursor-only storage row that never represented a heartbeat identity
   (already excluded by the view itself) are never flagged. A connector with
   no heartbeat yet gets a 15-minute grace window from ``first_seen_at`` so a
   registration moments ago never fires on the very next patrol tick.
2. **heartbeat-stale** — reads ``public.v_qa_butler_heartbeat`` (same
   migration, over ``switchboard.butler_registry``). Recomputes staleness
   independently from ``last_seen_at`` + the per-butler
   ``liveness_ttl_seconds`` (mirroring the formula in
   ``roster/switchboard/tools/registry/registry.py::_derive_eligibility_state``)
   rather than trusting the stored ``eligibility_state`` column, which is
   only reconciled lazily on routing calls (``_reconcile_eligibility_state``)
   and can sit stale forever for a butler nobody routes to anymore — exactly
   the "dead and nobody noticed" failure mode this bead exists to close.
3. **backup-stale** — reuses
   ``butlers.api.routers.system.read_backup_facts_from_dir`` (the same
   recency/reachability facts ``GET /api/system/backups`` surfaces) against
   the ``BUTLERS_BACKUP_DIR`` env var. An unset env var is a legitimate
   absence (not every deployment enables backups) and is silently skipped,
   matching that endpoint's own documented contract. A *configured* but
   unreachable directory, or one with no dump ever recorded, or a most-recent
   dump the endpoint itself already flagged ``backup_stale`` (against
   :data:`butlers.api.routers.system.BACKUP_STALE_THRESHOLD_HOURS`), is a
   genuine failure.
4. **external-deadman-stale** — reads the last successful ping recorded by
   ``butlers.jobs.external_deadman`` (``EXTERNAL_DEADMAN_URL`` env var). Also
   a legitimate absence when unconfigured.

``lookback_minutes`` is accepted (protocol conformance) but ignored: every
check here is a point-in-time liveness/staleness comparison against a fixed
cadence, not a scan over a rolling log/session window (mirrors
``ButlerReportsSource``, which ignores it for the same reason).

Spec reference
--------------
openspec/changes/deploy-drift-sentinel/tasks.md (Deferred: bu-9r3hd.4)
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

from butlers.core.healing.fingerprint import _compute_hash, _sanitize_message
from butlers.core.qa.models import QaFinding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONNECTOR_VIEW = "public.v_qa_connector_state"
_HEARTBEAT_VIEW = "public.v_qa_butler_heartbeat"

#: Health-check query -- validates view accessibility before processing rows
#: (catches revoked grants/dropped views early), mirroring
#: ToolCallFailuresSource's ``_HEALTH_CHECK_SQL`` pattern.
_HEALTH_CHECK_SQL = (
    f"SELECT 1 FROM {_CONNECTOR_VIEW} LIMIT 0; SELECT 1 FROM {_HEARTBEAT_VIEW} LIMIT 0"
)

#: Env var read by butlers.api.routers.system (kept in sync here rather than
#: imported, to avoid a hard import-time dependency on the api.routers module
#: for a single string constant).
_BACKUP_DIR_ENV = "BUTLERS_BACKUP_DIR"

#: A connector/butler with no liveness signal yet gets this much grace from
#: its registration time before being flagged -- avoids firing on the very
#: first patrol tick after a fresh registration.
_NEVER_SEEN_GRACE = timedelta(minutes=15)

#: How many missed external-deadman ping intervals to tolerate before
#: flagging staleness -- absorbs a transient network blip without noise.
_DEADMAN_STALE_MULTIPLIER = 3

# Severity: 0=critical, 1=high, 2=medium, 3=low, 4=info (core.healing.fingerprint).
# Connector/butler process death and a stalled external deadman are all
# "something stopped running" -- high. Backup staleness is comparatively
# lower urgency (no imminent user-visible harm) -- medium.
_SEVERITY_CONNECTOR_OFFLINE = 1
_SEVERITY_BUTLER_HEARTBEAT_STALE = 1
_SEVERITY_BACKUP_STALE = 2
_SEVERITY_DEADMAN_STALE = 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class InfraStateSource:
    """Discovery source for connector, butler, backup, and deadman staleness.

    Parameters
    ----------
    pool:
        asyncpg connection pool. Must be able to SELECT
        ``public.v_qa_connector_state`` / ``public.v_qa_butler_heartbeat``
        (granted to ``butler_qa_rw`` by migration ``sw_024``) and
        ``public.audit_log`` (already granted to every butler role by core
        migrations).
    backup_dir_env:
        Env var naming the backup directory. Defaults to
        ``BUTLERS_BACKUP_DIR`` (matches ``GET /api/system/backups``).
    deadman_ping_interval_s:
        Expected external-deadman ping cadence, used to size the staleness
        threshold. Defaults to
        ``butlers.jobs.external_deadman.DEFAULT_DEADMAN_CHECK_INTERVAL_S``.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        backup_dir_env: str = _BACKUP_DIR_ENV,
        deadman_ping_interval_s: float | None = None,
    ) -> None:
        self._pool = pool
        self._backup_dir_env = backup_dir_env
        self._deadman_ping_interval_s = deadman_ping_interval_s

    @property
    def name(self) -> str:
        """Source identifier: ``"infra_state"``."""
        return "infra_state"

    async def discover(self, lookback_minutes: int) -> list[QaFinding]:
        """Check connector/butler/backup/deadman staleness and return findings.

        ``lookback_minutes`` is accepted for protocol conformance but ignored
        (see module docstring).
        """
        # Health-check first: validates both views are queryable before any
        # row processing, so a revoked grant or dropped view surfaces as a
        # clear, patrol-logged error rather than a silently empty result.
        try:
            await self._pool.execute(_HEALTH_CHECK_SQL)
        except asyncpg.PostgresError as exc:
            logger.error("InfraStateSource: health check failed: %s", exc)
            raise

        now = datetime.now(UTC)
        findings: list[QaFinding] = []
        findings.extend(await self._check_connectors(now))
        findings.extend(await self._check_butler_heartbeats(now))
        findings.extend(self._check_backup(now))
        findings.extend(await self._check_external_deadman(now))
        return findings

    # ------------------------------------------------------------------
    # connector-offline
    # ------------------------------------------------------------------

    async def _check_connectors(self, now: datetime) -> list[QaFinding]:
        from butlers.api.models.connector import derive_liveness

        rows = await self._pool.fetch(f"SELECT * FROM {_CONNECTOR_VIEW}")

        findings: list[QaFinding] = []
        for row in rows:
            # A paused connector is a deliberate operator action (dashboard
            # pause endpoint), not a failure.
            if row["state"] == "paused":
                continue

            last_heartbeat_at = _as_aware(row["last_heartbeat_at"])
            if derive_liveness(last_heartbeat_at) != "offline":
                continue

            anchor = last_heartbeat_at or _as_aware(row["first_seen_at"])
            if anchor is not None and (now - anchor) < _NEVER_SEEN_GRACE:
                continue  # freshly registered, hasn't had a chance to check in yet

            identity = f"{row['connector_type']}/{row['endpoint_identity']}"
            raw_summary = (
                f"Connector {identity} is offline "
                f"(last heartbeat {anchor.isoformat() if anchor else 'never'}, "
                f"{_format_age(now, anchor)})"
            )
            findings.append(
                self._build_finding(
                    exception_type="ConnectorOffline",
                    call_site=f"connector:{identity}",
                    raw_summary=raw_summary,
                    severity=_SEVERITY_CONNECTOR_OFFLINE,
                    source_butler="switchboard",
                    first_seen=anchor or now,
                    now=now,
                )
            )
        return findings

    # ------------------------------------------------------------------
    # heartbeat-stale
    # ------------------------------------------------------------------

    async def _check_butler_heartbeats(self, now: datetime) -> list[QaFinding]:
        rows = await self._pool.fetch(f"SELECT * FROM {_HEARTBEAT_VIEW}")

        findings: list[QaFinding] = []
        for row in rows:
            name = row["name"]
            quarantined_at = _as_aware(row["quarantined_at"])
            last_seen_at = _as_aware(row["last_seen_at"])
            registered_at = _as_aware(row["registered_at"])
            ttl_seconds = row["liveness_ttl_seconds"] or 300

            # The grace window only applies to a butler that has genuinely
            # never checked in yet (last_seen_at is None) -- a butler that
            # HAS checked in before (even recently) already has a real
            # liveness signal, and a recent last_seen_at plus an active
            # quarantine must still trip below, not be graced away.
            if (
                last_seen_at is None
                and registered_at is not None
                and (now - registered_at) < _NEVER_SEEN_GRACE
            ):
                continue  # freshly registered, hasn't had a chance to check in yet

            anchor = last_seen_at or registered_at

            # Quarantine is itself a terminal "this butler is broken" state
            # -- always stale, regardless of the ttl math below.
            if quarantined_at is not None:
                stale = True
            else:
                # Mirrors registry.py's _derive_eligibility_state formula
                # (last_seen_at + ttl >= now => active), recomputed
                # independently rather than trusting the stored
                # eligibility_state column: that column is only reconciled
                # lazily on routing calls and can freeze stale forever for a
                # butler nobody routes to anymore.
                stale = anchor is None or (anchor + timedelta(seconds=ttl_seconds)) < now

            if not stale:
                continue

            raw_summary = f"Butler '{name}' heartbeat is stale"
            if quarantined_at is not None:
                raw_summary += f" (quarantined at {quarantined_at.isoformat()})"
            raw_summary += (
                f", last seen {anchor.isoformat()}" if anchor is not None else ", never seen"
            )

            findings.append(
                self._build_finding(
                    exception_type="ButlerHeartbeatStale",
                    call_site=f"butler_heartbeat:{name}",
                    raw_summary=raw_summary,
                    severity=_SEVERITY_BUTLER_HEARTBEAT_STALE,
                    source_butler=name,
                    first_seen=anchor or now,
                    now=now,
                )
            )
        return findings

    # ------------------------------------------------------------------
    # backup-stale
    # ------------------------------------------------------------------

    def _check_backup(self, now: datetime) -> list[QaFinding]:
        backup_dir_raw = os.environ.get(self._backup_dir_env, "").strip()
        if not backup_dir_raw:
            # Not configured -- matches GET /api/system/backups' own
            # documented contract ("expected state for unconfigured
            # deployments") -- a legitimate absence, not a finding.
            return []

        from butlers.api.routers.system import (
            BACKUP_STALE_THRESHOLD_HOURS,
            read_backup_facts_from_dir,
        )

        facts = read_backup_facts_from_dir(Path(backup_dir_raw))

        if not facts.backup_source_reachable:
            return [
                self._build_finding(
                    exception_type="BackupSourceUnreachable",
                    call_site="backup:pg_dump",
                    raw_summary=(
                        f"Backup directory '{backup_dir_raw}' is configured but unreachable"
                    ),
                    severity=_SEVERITY_BACKUP_STALE,
                    source_butler="switchboard",
                    first_seen=now,
                    now=now,
                )
            ]

        if facts.last_backup_at is None:
            return [
                self._build_finding(
                    exception_type="BackupStale",
                    call_site="backup:pg_dump",
                    raw_summary=f"Backup directory '{backup_dir_raw}' has no backup on record yet",
                    severity=_SEVERITY_BACKUP_STALE,
                    source_butler="switchboard",
                    first_seen=now,
                    now=now,
                )
            ]

        if not facts.backup_stale:
            return []

        last_backup_at = datetime.fromisoformat(facts.last_backup_at)
        return [
            self._build_finding(
                exception_type="BackupStale",
                call_site="backup:pg_dump",
                raw_summary=(
                    f"Last successful backup was {last_backup_at.isoformat()} "
                    f"({_format_age(now, last_backup_at)} ago, threshold "
                    f"{BACKUP_STALE_THRESHOLD_HOURS}h)"
                ),
                severity=_SEVERITY_BACKUP_STALE,
                source_butler="switchboard",
                first_seen=last_backup_at,
                now=now,
            )
        ]

    # ------------------------------------------------------------------
    # external-deadman-stale
    # ------------------------------------------------------------------

    async def _check_external_deadman(self, now: datetime) -> list[QaFinding]:
        from butlers.jobs.external_deadman import (
            DEFAULT_DEADMAN_CHECK_INTERVAL_S,
            EXTERNAL_DEADMAN_URL_ENV,
            get_last_deadman_success,
        )

        if not os.environ.get(EXTERNAL_DEADMAN_URL_ENV, "").strip():
            return []  # not configured -- legitimate absence

        last_success = await get_last_deadman_success(self._pool)

        interval_s = self._deadman_ping_interval_s or DEFAULT_DEADMAN_CHECK_INTERVAL_S
        threshold = timedelta(seconds=interval_s * _DEADMAN_STALE_MULTIPLIER)

        if last_success is not None and (now - last_success) <= threshold:
            return []

        raw_summary = "External deadman ping has not succeeded " + (
            f"since {last_success.isoformat()}" if last_success else "since it was configured"
        )
        return [
            self._build_finding(
                exception_type="ExternalDeadmanStale",
                call_site="external_deadman:ping",
                raw_summary=raw_summary,
                severity=_SEVERITY_DEADMAN_STALE,
                source_butler="switchboard",
                first_seen=last_success or now,
                now=now,
            )
        ]

    # ------------------------------------------------------------------
    # Shared finding construction
    # ------------------------------------------------------------------

    def _build_finding(
        self,
        *,
        exception_type: str,
        call_site: str,
        raw_summary: str,
        severity: int,
        source_butler: str,
        first_seen: datetime,
        now: datetime,
    ) -> QaFinding:
        # _sanitize_message collapses the embedded timestamp/age into
        # placeholders, so the fingerprint stays stable across patrol ticks
        # even though the human-readable event_summary keeps the real values
        # (same two-step pattern as ToolCallFailuresSource).
        fingerprint = _compute_hash(exception_type, call_site, _sanitize_message(raw_summary))
        return QaFinding(
            fingerprint=fingerprint,
            source_type=self.name,
            source_butler=source_butler,
            severity=severity,
            exception_type=exception_type,
            event_summary=raw_summary[:200],
            call_site=call_site,
            occurrence_count=1,
            first_seen=first_seen,
            last_seen=now,
            timestamp=now,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_aware(value: datetime | None) -> datetime | None:
    """Normalize a possibly-naive timestamp to UTC-aware, passing None through."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _format_age(now: datetime, anchor: datetime | None) -> str:
    """Render a human-readable age string for an event_summary."""
    if anchor is None:
        return "never"
    seconds = max((now - anchor).total_seconds(), 0.0)
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"
