"""QA Staffer module — patrol loop, discovery, triage, and investigation dispatch.

This module is the runtime heart of the QA Staffer. It:
  - Registers three MCP tools: report_finding, force_patrol, get_qa_status
  - Manages the patrol loop (scheduler-driven, asyncio.Lock for overlap prevention)
  - Registers discovery sources (log_scanner, session_records, butler_reports)
  - Delegates triage and dispatch to core.qa.*
  - Handles severity-0 reactive mini-patrols
  - Recovers stale patrol rows and stale investigating attempts on startup

The QA module's tables (qa_patrols, qa_findings, qa_dismissals) are in the
public schema and are managed by core migrations (core_051–core_055).
The module returns None from migration_revisions().

Spec reference
--------------
openspec/changes/qa-staffer/specs/staffer-qa/spec.md
openspec/changes/qa-staffer/tasks.md (6.5–6.8)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from butlers.core.healing import reap_stale_worktrees, recover_stale_attempts
from butlers.core.healing.fingerprint import compute_fingerprint_from_report
from butlers.core.qa.dispatch import (
    QA_GH_TOKEN_KEY,
    QA_GIT_AUTHOR_EMAIL_KEY,
    QA_GIT_AUTHOR_NAME_KEY,
    QaDispatchConfig,
    check_open_pr_statuses,
    dispatch_novel_findings,
)
from butlers.core.qa.findings import get_dispatch_queued_findings
from butlers.core.qa.journal import record_patrol_tick_events
from butlers.core.qa.models import QaFinding
from butlers.core.qa.repo_clone import ManagedRepoClone
from butlers.core.qa.repo_whitelist import RepoWhitelist
from butlers.core.qa.sources.butler_reports import ButlerReportsSource
from butlers.core.qa.sources.log_scanner import (
    DEFAULT_MAX_SCAN_SECONDS,
    DEFAULT_MAX_TOTAL_LINES,
    LogScannerSource,
)
from butlers.core.qa.sources.session_records import SessionRecordsSource
from butlers.core.qa.sources.tool_call_failures import ToolCallFailuresSource
from butlers.core.qa.triage import triage_findings
from butlers.core.spawn_hooks import get_spawner
from butlers.modules.base import Module, ToolMeta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenTelemetry — optional, graceful no-op when not configured
# ---------------------------------------------------------------------------

try:
    from opentelemetry import context as otel_context
    from opentelemetry import trace

    from butlers.core.telemetry import tag_butler_span

    _tracer = trace.get_tracer("butlers.qa")
    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
#
# All metrics follow RFC 0005 low-cardinality discipline: no UUIDs, fingerprints,
# or butler names appear as label values.  source_type is bounded to the three
# known discovery sources; dedup_reason is bounded to the four triage outcomes.


def _get_qa_patrol_total():
    """Return the qa_patrol_total Prometheus Counter."""
    try:
        from prometheus_client import Counter

        return Counter(
            "qa_patrol_total",
            "Total QA patrol cycle completions by outcome status",
            labelnames=["status"],
        )
    except (ImportError, ValueError):
        logger.debug(
            "Failed to initialize Prometheus counter 'qa_patrol_total';"
            " metric will not be exported",
            exc_info=True,
        )
        return None


def _get_qa_findings_total():
    """Return the qa_findings_total Prometheus Counter."""
    try:
        from prometheus_client import Counter

        return Counter(
            "qa_findings_total",
            "Total QA findings processed during triage, by source type and dedup reason",
            labelnames=["source_type", "dedup_reason"],
        )
    except (ImportError, ValueError):
        logger.debug(
            "Failed to initialize Prometheus counter 'qa_findings_total';"
            " metric will not be exported",
            exc_info=True,
        )
        return None


def _get_qa_findings_retention_purged_total():
    """Return the qa_findings_retention_purged_total Prometheus Counter."""
    try:
        from prometheus_client import Counter

        return Counter(
            "qa_findings_retention_purged_total",
            "Total QA finding rows whose retained raw evidence lines were purged",
        )
    except (ImportError, ValueError):
        logger.debug(
            "Failed to initialize Prometheus counter 'qa_findings_retention_purged_total';"
            " metric will not be exported",
            exc_info=True,
        )
        return None


def _get_qa_investigations_active():
    """Return the qa_investigations_active Prometheus Gauge."""
    try:
        from prometheus_client import Gauge

        return Gauge(
            "qa_investigations_active",
            "Current number of QA healing_attempts rows with status=investigating",
        )
    except (ImportError, ValueError):
        logger.debug(
            "Failed to initialize Prometheus gauge 'qa_investigations_active';"
            " metric will not be exported",
            exc_info=True,
        )
        return None


def _get_qa_patrol_duration_seconds():
    """Return the qa_patrol_duration_seconds Prometheus Histogram."""
    try:
        from prometheus_client import Histogram

        return Histogram(
            "qa_patrol_duration_seconds",
            "QA patrol cycle wall-clock duration in seconds",
        )
    except (ImportError, ValueError):
        logger.debug(
            "Failed to initialize Prometheus histogram 'qa_patrol_duration_seconds';"
            " metric will not be exported",
            exc_info=True,
        )
        return None


def _get_qa_investigation_duration_seconds():
    """Return the qa_investigation_duration_seconds Prometheus Histogram."""
    try:
        from prometheus_client import Histogram

        return Histogram(
            "qa_investigation_duration_seconds",
            "QA investigation duration in seconds from creation to terminal status",
            labelnames=["status"],
        )
    except (ImportError, ValueError):
        logger.debug(
            "Failed to initialize Prometheus histogram 'qa_investigation_duration_seconds';"
            " metric will not be exported",
            exc_info=True,
        )
        return None


_qa_patrol_total = _get_qa_patrol_total()
_qa_findings_total = _get_qa_findings_total()
_qa_findings_retention_purged_total = _get_qa_findings_retention_purged_total()
_qa_investigations_active = _get_qa_investigations_active()
_qa_patrol_duration_seconds = _get_qa_patrol_duration_seconds()
_qa_investigation_duration_seconds = _get_qa_investigation_duration_seconds()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default patrol interval in minutes.
_DEFAULT_PATROL_INTERVAL = 10

#: Default log lookback window in minutes.
_DEFAULT_LOG_LOOKBACK = 15

#: Default max concurrent investigations.
_DEFAULT_MAX_CONCURRENT = 2

#: Default severity threshold (medium = 2, so 0–2 trigger investigation).
_DEFAULT_SEVERITY_THRESHOLD = 2

#: Default reactive buffer size.
_DEFAULT_MAX_REACTIVE_BUFFER = 50

#: Default UTC hour for the daily evidence retention cleanup.
_DEFAULT_RETENTION_CLEANUP_HOUR = 4

#: Known source names (for config validation).
_KNOWN_SOURCES = frozenset(
    {"log_scanner", "session_records", "butler_reports", "tool_call_failures"}
)

#: Maps caller-supplied integer severity (0–4) to the hint string accepted by
#: ``compute_fingerprint_from_report``.  Used when canonicalizing report_finding
#: caller payloads so that caller severity acts only as a *hint* to the canonical
#: auto-scorer, not as the authoritative severity.
_SEVERITY_INT_TO_HINT: dict[int, str] = {
    0: "critical",
    1: "high",
    2: "medium",
    3: "low",
    4: "info",
}

#: Valid severity range enforced before passing to canonical computation.
_SEVERITY_MIN = 0
_SEVERITY_MAX = 4

#: Valid patrol status values that should be recovered on startup.
_STALE_PATROL_STATUSES = frozenset({"running"})


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------


class QaConfig(BaseModel):
    """Configuration for the QA staffer module.

    All fields have safe defaults so that ``[modules.qa]`` with no sub-keys
    is a valid (enabled) configuration.

    Parameters
    ----------
    enabled:
        Master on/off switch.  When ``False``, patrol ticks are skipped.
    patrol_interval_minutes:
        Patrol interval in minutes.  Default 10.
    log_lookback_minutes:
        How far back (in minutes) the log scanner and session records source
        should look for errors.  Default 15.
    max_concurrent_investigations:
        Max simultaneous ``investigating`` rows.  Default 2.
    severity_threshold:
        Maximum severity score that triggers investigation.  Lower is more
        severe.  Default 2 (medium).
    enabled_sources:
        List of source names to enable.  Default all three v1 sources.
    max_reactive_buffer:
        Max buffered reactive findings in the butler_reports source.
        Default 50.
    log_scanner_max_entries:
        Hard cap on candidate error/warning entries processed per log-scanner
        scan.  Only entries that pass the severity filter (error/critical/
        crash-warning) count against this budget; benign INFO/DEBUG lines do
        not.  Default 10 000.
    log_scanner_max_findings:
        Hard cap on unique fingerprinted findings produced per log-scanner
        scan.  Default 100.
    dashboard_base_url:
        Optional URL for inclusion in investigation prompts.
    log_scanner_max_total_lines:
        Hard cap on total lines parsed (including benign lines) per log scanner
        ``discover()`` call.  Default 200_000.
    log_scanner_max_scan_seconds:
        Wall-clock cap in seconds per log scanner ``discover()`` call.  Default 30.
    retention_cleanup_hour:
        UTC hour when the daily raw evidence cleanup should run.  Default 4.
    """

    enabled: bool = True
    patrol_interval_minutes: int = _DEFAULT_PATROL_INTERVAL
    log_lookback_minutes: int = _DEFAULT_LOG_LOOKBACK
    max_concurrent_investigations: int = _DEFAULT_MAX_CONCURRENT
    severity_threshold: int = _DEFAULT_SEVERITY_THRESHOLD
    enabled_sources: list[str] = [
        "log_scanner",
        "session_records",
        "butler_reports",
        "tool_call_failures",
    ]
    max_reactive_buffer: int = _DEFAULT_MAX_REACTIVE_BUFFER
    log_scanner_max_entries: int = 10_000
    log_scanner_max_findings: int = 100
    dashboard_base_url: str | None = None
    log_scanner_max_total_lines: int = DEFAULT_MAX_TOTAL_LINES
    log_scanner_max_scan_seconds: float = DEFAULT_MAX_SCAN_SECONDS
    retention_cleanup_hour: int = _DEFAULT_RETENTION_CLEANUP_HOUR
    model_config = ConfigDict(extra="forbid")

    @field_validator("patrol_interval_minutes", "log_lookback_minutes")
    @classmethod
    def _must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Must be a positive integer")
        return v

    @field_validator("max_concurrent_investigations")
    @classmethod
    def _min_one(cls, v: int) -> int:
        if v < 1:
            raise ValueError("Must be at least 1")
        return v

    @field_validator("log_scanner_max_entries", "log_scanner_max_findings")
    @classmethod
    def _scanner_caps_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("Must be at least 1")
        return v

    @field_validator("retention_cleanup_hour")
    @classmethod
    def _valid_utc_hour(cls, v: int) -> int:
        if v < 0 or v > 23:
            raise ValueError("Must be an hour from 0 to 23")
        return v

    @field_validator("enabled_sources")
    @classmethod
    def _known_sources(cls, v: list[str]) -> list[str]:
        unknown = set(v) - _KNOWN_SOURCES
        if unknown:
            raise ValueError(f"Unknown source(s): {sorted(unknown)}")
        return v


# ---------------------------------------------------------------------------
# Module implementation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Active instance singleton — deterministic job handlers in daemon.py use this
# to reach the QaModule instance (which holds config, sources, locks, etc.).
# Set during on_startup(), cleared during on_shutdown().
# ---------------------------------------------------------------------------

_active_instance: QaModule | None = None


def get_active_instance() -> QaModule | None:
    """Return the active QaModule instance, or None if not started."""
    return _active_instance


def _ensure_aware_datetime(value: Any) -> datetime | None:
    """Normalize DB timestamps for cutoff comparisons."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _coerce_jsonb(value: Any) -> Any:
    """Return decoded JSONB values, accepting string fixtures in unit tests."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


class QaModule(Module):
    """QA Staffer module — patrol loop, triage, and investigation dispatch.

    MCP tools registered:
    - ``report_finding``: Accept a finding relayed from a butler via Switchboard.
    - ``force_patrol``: Trigger an immediate patrol cycle.
    - ``get_qa_status``: Return QA operational summary.

    The patrol loop is scheduler-driven: the QA staffer daemon fires a tick
    every ``patrol_interval_minutes`` minutes.  The module uses an asyncio.Lock
    to prevent overlapping patrol cycles.
    """

    def __init__(self) -> None:
        self._config = QaConfig()
        self._butler_name: str = "qa"
        self._pool: Any = None
        self._repo_root: Path = Path(".")
        self._credential_store: Any = None

        # Managed repo clone — initialized on startup
        self._managed_clone: ManagedRepoClone | None = None

        # Repository whitelist — initialized on startup with the DB pool
        self._repo_whitelist: RepoWhitelist | None = None

        # Discovery sources — registered at startup
        self._butler_reports_source: ButlerReportsSource | None = None
        self._log_scanner_source: LogScannerSource | None = None
        self._sources: list[Any] = []

        # Patrol state
        self._patrol_lock = asyncio.Lock()
        self._current_patrol_id: uuid.UUID | None = None
        self._last_patrol_at: datetime | None = None
        self._last_patrol_status: str | None = None
        self._last_patrol_findings: int = 0
        self._last_patrol_novel: int = 0
        self._last_patrol_dispatched: int = 0

        # Background watchdog tasks
        self._watchdog_tasks: list[asyncio.Task[Any]] = []

        # Mini-patrol task (triggered by severity-0 reactive findings)
        self._mini_patrol_task: asyncio.Task[Any] | None = None

        # Notify function — injected via wire_runtime() from the daemon.
        # Signature: async (channel, message, priority) -> dict
        self._notify_fn: Callable[..., Coroutine[Any, Any, Any]] | None = None

        # Switchboard client — injected via wire_runtime() from the daemon.
        # Enables inter-butler communication via Switchboard route() calls.
        self._switchboard_client: Any | None = None

        # Rate-limit missing-token notifications to once per patrol cycle.
        # Stores the patrol_id of the last patrol where we sent the alert.
        self._last_missing_token_notified_patrol_id: uuid.UUID | None = None

    # ------------------------------------------------------------------
    # Module ABC
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "qa"

    @property
    def config_schema(self) -> type[BaseModel]:
        return QaConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        # QA tables (qa_patrols, qa_findings, qa_dismissals) are in the public
        # schema and managed by the core migration chain (core_051–core_055).
        return None

    # ------------------------------------------------------------------
    # Sensitivity metadata
    # ------------------------------------------------------------------

    def tool_metadata(self) -> dict[str, ToolMeta]:
        """Mark context as sensitive on report_finding.

        The context field may contain agent reasoning about user-related errors.
        """
        return {
            "report_finding": ToolMeta(
                arg_sensitivities={
                    "context": True,
                    "event_summary": True,
                }
            ),
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        """Register sources, recover stale state, reap worktrees.

        Called after dependency resolution and migrations.  Stale ``running``
        patrol rows are recovered (marked as ``error`` with daemon restart
        reason).  Stale ``investigating`` healing attempts are recovered using
        deadline-aware logic (core_066) — ``dispatch_pending`` was removed.

        Parameters
        ----------
        config:
            QaConfig instance or dict (dict will be coerced to QaConfig).
        db:
            Butler database instance (``db.pool`` for asyncpg).
        credential_store:
            Optional CredentialStore for DB-first credential resolution.
        blob_store:
            Unused by this module.
        """
        global _active_instance

        self._config = config if isinstance(config, QaConfig) else QaConfig(**(config or {}))

        pool = getattr(db, "pool", None) if db is not None else None
        self._pool = pool
        self._credential_store = credential_store

        _active_instance = self

        # Initialize repository whitelist (loaded lazily from DB via TTL cache)
        self._repo_whitelist = RepoWhitelist(db_pool=pool)
        if pool is not None:
            try:
                await self._repo_whitelist.ensure_loaded()
            except Exception:
                logger.warning(
                    "QaModule startup: failed to load repo whitelist; "
                    "PR creation will be blocked until load succeeds",
                    exc_info=True,
                )

        # Initialize managed repo clone
        self._managed_clone = ManagedRepoClone(pool=pool)
        if pool is not None:
            try:
                clone_path = await self._managed_clone.ensure_cloned()
                self._repo_root = clone_path
                logger.info("QaModule startup: managed clone ready at %s", clone_path)
            except Exception:
                logger.warning(
                    "QaModule startup: failed to initialize managed repo clone; "
                    "will fall back to daemon-provided repo_root",
                    exc_info=True,
                )

        # Register discovery sources
        self._sources = []
        self._butler_reports_source = None
        self._log_scanner_source = None

        enabled = set(self._config.enabled_sources)
        session_records_available = "session_records" in enabled and pool is not None

        if "butler_reports" in enabled:
            self._butler_reports_source = ButlerReportsSource(
                max_buffer=self._config.max_reactive_buffer
            )
            self._sources.append(self._butler_reports_source)
            logger.info("QaModule: registered butler_reports source")

        if "log_scanner" in enabled:
            self._log_scanner_source = LogScannerSource(
                repo_root=self._repo_root,
                max_entries_per_scan=self._config.log_scanner_max_entries,
                max_findings_per_scan=self._config.log_scanner_max_findings,
                max_total_lines=self._config.log_scanner_max_total_lines,
                max_scan_seconds=self._config.log_scanner_max_scan_seconds,
                suppress_session_duplicate_timeouts=session_records_available,
            )
            self._sources.append(self._log_scanner_source)
            logger.info("QaModule: registered log_scanner source")

        if "session_records" in enabled:
            if pool is not None:
                self._sources.append(SessionRecordsSource(pool))
                logger.info("QaModule: registered session_records source")
            else:
                logger.info("QaModule: session_records source skipped (no DB pool at startup)")

        if "tool_call_failures" in enabled:
            if pool is not None:
                self._sources.append(ToolCallFailuresSource(pool, repo_root=self._repo_root))
                logger.info("QaModule: registered tool_call_failures source")
            else:
                logger.info("QaModule: tool_call_failures source skipped (no DB pool at startup)")

        disabled = _KNOWN_SOURCES - enabled
        for src_name in sorted(disabled):
            logger.info("QaModule: source %s disabled (not in enabled_sources)", src_name)

        if pool is None:
            logger.debug("QaModule.on_startup: no DB pool — skipping recovery")
            return

        # Recover stale patrol rows
        await self._recover_stale_patrols(pool)

        # Recover stale healing attempts (stale investigating rows)
        try:
            recovered = await recover_stale_attempts(
                pool, timeout_minutes=self._config.log_lookback_minutes * 4
            )
            if recovered:
                logger.info("QaModule startup: recovered %d stale attempt(s)", recovered)
        except Exception:
            logger.warning("QaModule startup: recover_stale_attempts failed", exc_info=True)

        # Reap orphaned worktrees
        try:
            await reap_stale_worktrees(self._repo_root, pool)
        except Exception:
            logger.warning("QaModule startup: reap_stale_worktrees failed", exc_info=True)

    async def _recover_stale_patrols(self, pool: Any) -> None:
        """Mark any stale 'running' patrol rows as 'error' after daemon restart.

        This prevents stale rows from blocking patrol overlap detection on the
        next patrol cycle.
        """
        try:
            rows = await pool.fetch(
                """
                SELECT id FROM public.qa_patrols
                WHERE status = 'running' AND completed_at IS NULL
                """
            )
            if not rows:
                return
            for row in rows:
                await pool.execute(
                    """
                    UPDATE public.qa_patrols
                    SET status = 'error',
                        completed_at = now(),
                        error_detail = 'daemon restart during patrol'
                    WHERE id = $1
                    """,
                    row["id"],
                )
            logger.info("QaModule startup: recovered %d stale patrol row(s)", len(rows))
        except Exception:
            logger.warning("QaModule startup: _recover_stale_patrols failed", exc_info=True)

    async def on_shutdown(self) -> None:
        """Cancel watchdog tasks (best-effort).

        Active investigation sessions are NOT terminated — they may complete
        independently after the daemon shuts down (daemon phase 3 drain).
        """
        # Cancel mini-patrol task
        if self._mini_patrol_task and not self._mini_patrol_task.done():
            self._mini_patrol_task.cancel()
            try:
                await self._mini_patrol_task
            except (asyncio.CancelledError, Exception):
                pass
        self._mini_patrol_task = None

        # Cancel watchdog tasks
        for task in list(self._watchdog_tasks):
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._watchdog_tasks.clear()

        global _active_instance
        _active_instance = None

        logger.debug("QaModule shut down; watchdog tasks cancelled")

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        """Register report_finding, force_patrol, and get_qa_status tools."""
        self._config = config if isinstance(config, QaConfig) else QaConfig(**(config or {}))
        if butler_name:
            self._butler_name = butler_name
        self._pool = getattr(db, "pool", None) if db is not None else None

        module = self

        @mcp.tool()
        async def report_finding(
            fingerprint: str,
            exception_type: str,
            call_site: str,
            severity: int,
            event_summary: str,
            source_butler: str,
            context: str | None = None,
            trigger_source: str | None = None,
        ) -> dict:
            """Accept an error finding relayed from a butler via Switchboard.

            Called by butler self-healing modules via Switchboard's route()
            tool: ``call_tool("route", {"target_butler": "qa",
            "tool_name": "report_finding", "args": {...}})``.

            This is a synchronous buffer enqueue — no investigation is
            dispatched immediately.  The finding is picked up on the next
            patrol cycle.  For severity 0 (critical), an immediate mini-patrol
            is triggered.

            Parameters
            ----------
            fingerprint:
                Caller-supplied fingerprint hint.  The handler ignores this
                value as the authoritative dedup key and recomputes a canonical
                fingerprint from the structured report fields instead.  Pass a
                best-effort value; any string is accepted.
            exception_type:
                Fully qualified exception class name.
            call_site:
                ``<file>:<function>`` call site string.
            severity:
                Caller-supplied severity hint (0=critical, 1=high, 2=medium,
                3=low, 4=info).  Out-of-range values are clamped.  The handler
                passes this as a hint to canonical scoring; authoritative rules
                may override the caller value.
            event_summary:
                Sanitized error event summary.
            source_butler:
                Name of the reporting butler.
            context:
                Optional context string (declared sensitive; may contain agent
                reasoning about user-related errors).
            trigger_source:
                Optional ``trigger_source`` value from the calling session
                (e.g. ``"healing"`` or ``"qa"``).  Propagated as
                ``source_session_trigger_source`` for QA self-recursion
                suppression.
            """
            return await module._handle_report_finding(
                fingerprint=fingerprint,
                exception_type=exception_type,
                call_site=call_site,
                severity=severity,
                event_summary=event_summary,
                source_butler=source_butler,
                context=context,
                trigger_source=trigger_source,
            )

        @mcp.tool()
        async def force_patrol() -> dict:
            """Trigger an immediate QA patrol cycle.

            Useful for operators who want to run a patrol outside the normal
            schedule (e.g., after deploying a fix to verify no regressions).

            Returns the patrol result summary.
            """
            return await module._handle_force_patrol()

        @mcp.tool()
        async def get_qa_status() -> dict:
            """Return a QA staffer operational summary.

            Includes: last patrol timestamp, last patrol status, finding and
            investigation counts, enabled sources, current config, and
            watchdog task count.
            """
            return module._handle_get_qa_status()

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    async def _handle_report_finding(
        self,
        fingerprint: str,
        exception_type: str,
        call_site: str,
        severity: int,
        event_summary: str,
        source_butler: str,
        context: str | None,
        trigger_source: str | None = None,
    ) -> dict:
        """Handle the report_finding MCP tool call.

        Canonicalizes the caller-supplied fingerprint and severity before
        enqueueing, so that dedup keys are stable and severity constraints
        cannot be violated by malformed caller payloads.

        Fingerprint:
            The caller-supplied ``fingerprint`` is ignored as the authoritative
            dedup key.  A canonical fingerprint is recomputed from
            ``exception_type``, ``call_site``, and ``event_summary`` via
            ``compute_fingerprint_from_report``, ensuring that semantically
            identical reports from different callers or sessions always produce
            the same dedup key regardless of what fingerprint the caller sent.

        Severity:
            The caller-supplied ``severity`` integer is validated against the
            allowed range (0–4).  Out-of-range values are clamped and logged
            as a warning.  The clamped value is passed as a *hint* to
            ``compute_fingerprint_from_report``, which uses it only as a
            tiebreaker when the auto-scorer returns medium — ensuring that
            authoritative severity rules (critical DB errors, high runtime
            adapter errors, etc.) always take precedence.

        Enqueues the finding in the butler_reports source buffer and returns
        immediately.  For canonical severity == 0 (critical), schedules an
        immediate mini-patrol.
        """
        if self._butler_reports_source is None:
            logger.warning(
                "QaModule.report_finding: butler_reports source not registered "
                "(fingerprint=%s butler=%s)",
                fingerprint[:12] if len(fingerprint) >= 12 else fingerprint,
                source_butler,
            )
            return {"accepted": False, "reason": "butler_reports_disabled"}

        # ------------------------------------------------------------------
        # Severity validation: clamp to valid range 0–4
        # ------------------------------------------------------------------
        if not isinstance(severity, int) or severity < _SEVERITY_MIN or severity > _SEVERITY_MAX:
            raw_int = int(severity) if isinstance(severity, int) else _SEVERITY_MAX
            clamped = max(_SEVERITY_MIN, min(_SEVERITY_MAX, raw_int))
            logger.warning(
                "QaModule.report_finding: out-of-range severity=%r from butler=%s; clamping to %d",
                severity,
                source_butler,
                clamped,
            )
            severity = clamped

        # ------------------------------------------------------------------
        # Fingerprint canonicalization: recompute from structured payload fields.
        # Caller-supplied fingerprint is used only for the pre-source-check log
        # message above; the canonical fingerprint drives dedup and dispatch.
        # ------------------------------------------------------------------
        severity_hint = _SEVERITY_INT_TO_HINT.get(severity)
        fp_result = compute_fingerprint_from_report(
            error_type=exception_type,
            error_message=event_summary,
            call_site=call_site,
            traceback_str=None,
            severity_hint=severity_hint,
        )
        canonical_fingerprint = fp_result.fingerprint
        canonical_severity = fp_result.severity

        if canonical_fingerprint != fingerprint:
            logger.debug(
                "QaModule.report_finding: fingerprint mismatch — "
                "caller=%s canonical=%s butler=%s; using canonical",
                fingerprint[:12] if len(fingerprint) >= 12 else fingerprint,
                canonical_fingerprint[:12],
                source_butler,
            )

        await self._butler_reports_source.accept(
            fingerprint=canonical_fingerprint,
            exception_type=exception_type,
            call_site=call_site,
            severity=canonical_severity,
            event_summary=event_summary,
            source_butler=source_butler,
            context=context,
            trigger_source=trigger_source,
        )
        logger.debug(
            "QaModule.report_finding: accepted fingerprint=%s butler=%s severity=%d",
            canonical_fingerprint[:12],
            source_butler,
            canonical_severity,
        )

        # Schedule immediate mini-patrol for critical findings (severity == 0)
        if canonical_severity == 0:
            self._schedule_mini_patrol(canonical_fingerprint)

        return {"accepted": True}

    async def _handle_force_patrol(self) -> dict:
        """Handle the force_patrol MCP tool call.

        Runs a full patrol cycle synchronously and returns the result.
        If a patrol is already running, returns a skip response.
        """
        if not self._config.enabled:
            return {"status": "skipped", "reason": "qa_module_disabled"}

        if self._pool is None:
            return {"status": "skipped", "reason": "no_db_pool"}

        # Non-blocking acquire.  asyncio is single-threaded and cooperative, so
        # there is no await point between ``locked()`` returning False and
        # ``acquire()`` completing on a free lock — the check-then-acquire is
        # race-free.  (The previous ``wait_for(acquire(), timeout=0)`` was
        # broken: with timeout=0, wait_for creates the acquire task but cancels
        # it before it can run, so it ALWAYS raised TimeoutError even for a free
        # lock, falsely reporting ``patrol_already_running``.)
        if self._patrol_lock.locked():
            return {"status": "skipped", "reason": "patrol_already_running"}
        await self._patrol_lock.acquire()

        # We now hold the lock — run the patrol body directly (not via
        # _run_patrol_cycle which would try to re-acquire), then release.
        try:
            result = await self._run_patrol_body()
        finally:
            self._patrol_lock.release()
        return {
            "status": result["status"],
            "patrol_id": result.get("patrol_id"),
            "findings_count": result.get("findings_count", 0),
            "novel_count": result.get("novel_count", 0),
            "dispatched_count": result.get("dispatched_count", 0),
            "sources_polled": result.get("sources_polled", []),
        }

    def _handle_get_qa_status(self) -> dict:
        """Handle the get_qa_status MCP tool call."""
        # Prune completed watchdog tasks
        self._watchdog_tasks = [t for t in self._watchdog_tasks if not t.done()]
        enabled_sources = [s.name for s in self._sources]
        return {
            "enabled": self._config.enabled,
            "last_patrol_at": (self._last_patrol_at.isoformat() if self._last_patrol_at else None),
            "last_patrol_status": self._last_patrol_status,
            "last_patrol_findings": self._last_patrol_findings,
            "last_patrol_novel": self._last_patrol_novel,
            "last_patrol_dispatched": self._last_patrol_dispatched,
            "active_watchdog_tasks": len(self._watchdog_tasks),
            "enabled_sources": enabled_sources,
            "patrol_interval_minutes": self._config.patrol_interval_minutes,
            "log_lookback_minutes": self._config.log_lookback_minutes,
            "max_concurrent_investigations": self._config.max_concurrent_investigations,
            "severity_threshold": self._config.severity_threshold,
            "butler_reports_buffer_size": (
                self._butler_reports_source.buffer_size
                if self._butler_reports_source is not None
                else 0
            ),
            "log_scanner_max_entries": self._config.log_scanner_max_entries,
            "log_scanner_max_findings": self._config.log_scanner_max_findings,
            "log_scanner_last_truncated": (
                self._log_scanner_source.last_truncated
                if self._log_scanner_source is not None
                else False
            ),
            "log_scanner_last_truncated_reason": (
                self._log_scanner_source.last_truncated_reason
                if self._log_scanner_source is not None
                else None
            ),
        }

    # ------------------------------------------------------------------
    # Patrol loop
    # ------------------------------------------------------------------

    async def run_patrol_tick(self) -> None:
        """Execute a scheduled patrol tick.

        Called by the scheduler at the configured interval.  Uses an asyncio.Lock
        to prevent overlapping patrol cycles: if the previous patrol is still
        running, this tick is recorded as ``skipped_overlap``.

        This method does not raise — all errors are caught and logged.
        """
        if not self._config.enabled:
            logger.debug("QaModule.run_patrol_tick: module disabled — skipping")
            return

        if self._pool is None:
            logger.debug("QaModule.run_patrol_tick: no DB pool — skipping")
            return

        if self._patrol_lock.locked():
            # Record a skipped_overlap patrol row
            logger.warning("QaModule.run_patrol_tick: patrol already running — skipping (overlap)")
            await self._record_patrol_skip(self._pool)
            return

        try:
            await self._run_patrol_cycle()
        except Exception:
            logger.error("QaModule.run_patrol_tick: unexpected error", exc_info=True)

    async def daily_evidence_cleanup(self, *, now: datetime | None = None) -> dict[str, int | str]:
        """Strip retained raw evidence lines from aged QA finding rows.

        Raw ``investigation_notes.evidence_lines`` are retained for a bounded
        window only: terminal attempts older than 14 days, or findings older
        than 30 days, should keep their narrative notes but lose the raw lines.
        Malformed notes payloads are skipped with a warning so one bad row does
        not stop the daily job.
        """
        if self._pool is None:
            logger.debug("QaModule.daily_evidence_cleanup: no DB pool — skipping")
            return {"status": "skipped", "cleaned_rows": 0, "malformed_rows": 0}

        run_at = now or datetime.now(UTC)
        terminal_cutoff = run_at - timedelta(days=14)
        finding_cutoff = run_at - timedelta(days=30)

        rows = await self._pool.fetch(
            """
            SELECT f.id,
                   f.created_at,
                   f.healing_attempt_id,
                   f.structured_evidence,
                   h.closed_at
            FROM public.qa_findings f
            LEFT JOIN public.healing_attempts h ON h.id = f.healing_attempt_id
            WHERE f.structured_evidence IS NOT NULL
              AND f.structured_evidence ? 'investigation_notes'
              AND (
                    (h.closed_at IS NOT NULL AND h.closed_at < $1)
                 OR (f.healing_attempt_id IS NULL AND f.created_at < $2)
              )
            """,
            terminal_cutoff,
            finding_cutoff,
        )

        cleaned_rows = 0
        malformed_rows = 0
        for row in rows:
            finding_id = row["id"]
            created_at = _ensure_aware_datetime(row["created_at"])
            healing_attempt_id = row["healing_attempt_id"]
            closed_at = _ensure_aware_datetime(row["closed_at"])
            is_old_terminal = closed_at is not None and closed_at < terminal_cutoff
            is_old_unlinked_finding = (
                healing_attempt_id is None
                and created_at is not None
                and created_at < finding_cutoff
            )
            if not is_old_terminal and not is_old_unlinked_finding:
                continue

            structured_evidence = _coerce_jsonb(row["structured_evidence"])
            if not isinstance(structured_evidence, dict):
                malformed_rows += 1
                logger.warning(
                    "QaModule.daily_evidence_cleanup: malformed structured_evidence "
                    "shape; skipping finding_id=%s",
                    finding_id,
                )
                continue

            investigation_notes = structured_evidence.get("investigation_notes")
            if not isinstance(investigation_notes, dict):
                malformed_rows += 1
                logger.warning(
                    "QaModule.daily_evidence_cleanup: malformed investigation_notes "
                    "shape; skipping finding_id=%s",
                    finding_id,
                )
                continue

            if "evidence_lines" not in investigation_notes:
                continue

            cleaned_notes = dict(investigation_notes)
            cleaned_notes.pop("evidence_lines", None)
            cleaned_evidence = dict(structured_evidence)
            cleaned_evidence["investigation_notes"] = cleaned_notes

            await self._pool.execute(
                """
                UPDATE public.qa_findings
                SET structured_evidence = $2
                WHERE id = $1
                """,
                finding_id,
                cleaned_evidence,
            )
            cleaned_rows += 1

        if cleaned_rows and _qa_findings_retention_purged_total is not None:
            try:
                _qa_findings_retention_purged_total.inc(cleaned_rows)
            except Exception:
                logger.debug(
                    "QaModule: failed to record qa_findings_retention_purged_total metric",
                    exc_info=True,
                )

        logger.info(
            "QaModule daily evidence cleanup complete: cleaned_rows=%d malformed_rows=%d",
            cleaned_rows,
            malformed_rows,
        )
        return {
            "status": "completed",
            "cleaned_rows": cleaned_rows,
            "malformed_rows": malformed_rows,
        }

    async def run_scheduled_evidence_cleanup(
        self, *, now: datetime | None = None
    ) -> dict[str, int | str]:
        """Run the daily evidence cleanup only at the configured UTC hour."""
        run_at = now or datetime.now(UTC)
        if run_at.hour != self._config.retention_cleanup_hour:
            logger.debug(
                "QaModule.run_scheduled_evidence_cleanup: skipping outside configured hour "
                "(current_hour=%d configured_hour=%d)",
                run_at.hour,
                self._config.retention_cleanup_hour,
            )
            return {"status": "skipped", "cleaned_rows": 0, "malformed_rows": 0}
        return await self.daily_evidence_cleanup(now=run_at)

    async def _run_patrol_cycle(self) -> dict:
        """Execute one complete patrol cycle under the asyncio.Lock.

        Acquires ``_patrol_lock`` and delegates to ``_run_patrol_body``.

        Returns
        -------
        dict
            Summary of the patrol cycle outcome.
        """
        async with self._patrol_lock:
            return await self._run_patrol_body()

    async def _run_patrol_body(self) -> dict:
        """Execute the patrol cycle body.

        Must be called with ``_patrol_lock`` already held.  Creates a patrol
        record, polls all configured sources, runs triage, dispatches novel
        findings, and updates the patrol record on completion.

        Returns
        -------
        dict
            Summary of the patrol cycle outcome.
        """
        pool = self._pool
        if pool is None:
            return {"status": "error", "reason": "no_db_pool"}

        patrol_start = time.monotonic()
        patrol_started_at = datetime.now(UTC)
        patrol_id = await self._create_patrol_record(pool)
        self._current_patrol_id = patrol_id
        sources_polled: list[str] = []
        all_findings = []
        error_detail: str | None = None

        # Start the qa.patrol parent span (root — not child of any calling context)
        _patrol_span = None
        _patrol_span_token = None
        if _HAS_OTEL:
            _patrol_span = _tracer.start_span(
                "qa.patrol",
                context=otel_context.Context(),  # fresh context — root span
                attributes={
                    "qa.patrol_id": str(patrol_id),
                },
            )
            tag_butler_span(_patrol_span, "qa")
            _patrol_span_token = otel_context.attach(trace.set_span_in_context(_patrol_span))

        try:
            # Phase 1: Discover
            for source in self._sources:
                _discover_span = None
                _discover_span_token = None
                if _HAS_OTEL:
                    _discover_span = _tracer.start_span(f"qa.discover.{source.name}")
                    _discover_span_token = otel_context.attach(
                        trace.set_span_in_context(_discover_span)
                    )
                try:
                    findings = await source.discover(self._config.log_lookback_minutes)
                    sources_polled.append(source.name)
                    all_findings.extend(findings)
                    logger.debug(
                        "QaModule patrol %s: source=%s returned %d finding(s)",
                        patrol_id,
                        source.name,
                        len(findings),
                    )
                except Exception as src_exc:
                    logger.error(
                        "QaModule patrol %s: source=%s failed: %s",
                        patrol_id,
                        source.name,
                        src_exc,
                        exc_info=True,
                    )
                    detail = f"source {source.name} failed: {src_exc!r}"
                    error_detail = f"{error_detail}; {detail}" if error_detail else detail
                    if _HAS_OTEL and _discover_span is not None:
                        _discover_span.record_exception(src_exc)
                        _discover_span.set_status(trace.StatusCode.ERROR, str(src_exc))
                finally:
                    if _HAS_OTEL and _discover_span is not None:
                        _discover_span.end()
                        if _discover_span_token is not None:
                            otel_context.detach(_discover_span_token)

            # Update patrol span with sources_polled count now that discovery is done
            if _HAS_OTEL and _patrol_span is not None:
                _patrol_span.set_attribute("qa.sources_polled", len(sources_polled))

            # Phase 1b: Inject queued findings from previous patrol cycles.
            # Findings skipped due to concurrency pressure in a prior patrol are
            # stored with dispatch_queued=TRUE.  Load them now, clear the flag
            # atomically, and reconstitute them as QaFinding objects so that
            # triage applies fresh novelty/cooldown/dismissal checks before any
            # dispatch attempt.  A bounded limit prevents a large backlog from
            # overwhelming one patrol cycle.
            try:
                queued_rows = await get_dispatch_queued_findings(pool)
                if queued_rows:
                    logger.info(
                        "QaModule patrol %s: loading %d queued finding(s) from previous cycles",
                        patrol_id,
                        len(queued_rows),
                    )
                    queued_findings = [_qa_finding_from_row(row) for row in queued_rows]
                    # Prepend so queued findings are processed before new discoveries
                    # (they have already been waiting at least one cycle).
                    all_findings = queued_findings + all_findings
            except Exception as _q_exc:
                logger.warning(
                    "QaModule patrol %s: failed to load queued findings (non-fatal): %s",
                    patrol_id,
                    _q_exc,
                    exc_info=True,
                )

            # Phase 2: Triage
            _triage_span = None
            _triage_span_token = None
            if _HAS_OTEL:
                _triage_span = _tracer.start_span("qa.triage")
                _triage_span_token = otel_context.attach(trace.set_span_in_context(_triage_span))
            try:
                triage_result = await triage_findings(
                    pool=pool,
                    patrol_id=patrol_id,
                    findings=all_findings,
                    cooldown_minutes=60,  # default cooldown
                )
            except Exception as triage_exc:
                if _HAS_OTEL and _triage_span is not None:
                    _triage_span.record_exception(triage_exc)
                    _triage_span.set_status(trace.StatusCode.ERROR, str(triage_exc))
                raise
            finally:
                if _HAS_OTEL and _triage_span is not None:
                    _triage_span.end()
                    if _triage_span_token is not None:
                        otel_context.detach(_triage_span_token)

            findings_count = len(triage_result.all_findings)
            novel_count = len(triage_result.novel_findings)

            # Increment qa_findings_total per triaged finding (RFC 0005 low-cardinality labels)
            if _qa_findings_total is not None:
                try:
                    for tf in triage_result.all_findings:
                        dedup_label = tf.dedup_reason if tf.dedup_reason is not None else "novel"
                        _qa_findings_total.labels(
                            source_type=tf.finding.source_type,
                            dedup_reason=dedup_label,
                        ).inc()
                except Exception:
                    logger.debug(
                        "QaModule: failed to record qa_findings_total metric", exc_info=True
                    )

            # Phase 3: Dispatch
            gh_token = await self._resolve_gh_token()
            git_author_name, git_author_email = await self._resolve_git_identity()
            if not gh_token:  # None or empty string
                await self._notify_missing_gh_token(patrol_id)
            dispatch_config = QaDispatchConfig(
                severity_threshold=self._config.severity_threshold,
                max_concurrent=self._config.max_concurrent_investigations,
                dashboard_base_url=self._config.dashboard_base_url,
                repo_whitelist=self._repo_whitelist,
            )

            # Prune completed watchdog tasks before dispatching
            self._watchdog_tasks = [t for t in self._watchdog_tasks if not t.done()]

            # Refresh managed repo clone before dispatch
            if self._managed_clone is not None:
                try:
                    refreshed_root = await self._managed_clone.refresh()
                    self._repo_root = refreshed_root
                except Exception:
                    logger.warning(
                        "QaModule patrol %s: managed repo refresh failed (non-fatal); "
                        "using existing clone state",
                        patrol_id,
                        exc_info=True,
                    )

            _dispatch_span = None
            _dispatch_span_token = None
            if _HAS_OTEL:
                _dispatch_span = _tracer.start_span(
                    "qa.dispatch",
                    attributes={"qa.novel_findings": novel_count},
                )
                _dispatch_span_token = otel_context.attach(
                    trace.set_span_in_context(_dispatch_span)
                )
            _spawner = get_spawner()
            try:
                dispatch_results = await dispatch_novel_findings(
                    pool=pool,
                    novel_findings=triage_result.novel_findings,
                    patrol_id=patrol_id,
                    config=dispatch_config,
                    repo_root=self._repo_root,
                    spawner=_spawner,
                    gh_token=gh_token,
                    git_author_name=git_author_name,
                    git_author_email=git_author_email,
                    task_registry=self._watchdog_tasks,
                    metrics=getattr(_spawner, "_metrics", None),
                )
            except Exception as dispatch_exc:
                if _HAS_OTEL and _dispatch_span is not None:
                    _dispatch_span.record_exception(dispatch_exc)
                    _dispatch_span.set_status(trace.StatusCode.ERROR, str(dispatch_exc))
                raise
            finally:
                if _HAS_OTEL and _dispatch_span is not None:
                    _dispatch_span.end()
                    if _dispatch_span_token is not None:
                        otel_context.detach(_dispatch_span_token)

            dispatched_count = sum(1 for r in dispatch_results if r.accepted)

            # Phase 4: PR status check
            await self._check_pr_statuses(
                pool,
                gh_token,
                git_author_name=git_author_name,
                git_author_email=git_author_email,
                patrol_id=patrol_id,
                patrol_started_at=patrol_started_at,
            )

            # Phase 5: Journal ticks for open cases unchanged during this cycle.
            tick_event_ids = await record_patrol_tick_events(
                pool,
                patrol_id=patrol_id,
                patrol_started_at=patrol_started_at,
            )
            if _HAS_OTEL and _patrol_span is not None:
                _patrol_span.set_attribute("qa.tick_events", len(tick_event_ids))

            # Phase 6: Metric snapshots (non-fatal)
            await self._record_investigation_metrics(pool)

            # Determine final patrol status.
            # "suppressed" means findings were found but all were filtered out
            # by cooldown or severity threshold (novel_count > 0 but nothing
            # dispatched and no dispatch error).
            if error_detail:
                patrol_status = "error"
            elif findings_count == 0:
                patrol_status = "clean"
            elif dispatched_count > 0:
                patrol_status = "findings_dispatched"
            elif novel_count > 0:
                # Novel findings exist but none were dispatched (suppressed by
                # cooldown or severity threshold).
                patrol_status = "suppressed"
            else:
                # Findings found but all were duplicates/dismissed — no novel work.
                patrol_status = "clean"

            # Update patrol record
            await self._complete_patrol_record(
                pool=pool,
                patrol_id=patrol_id,
                status=patrol_status,
                findings_count=findings_count,
                novel_count=novel_count,
                dispatched_count=dispatched_count,
                sources_polled=sources_polled,
                error_detail=error_detail,
            )

            # Update module state
            self._last_patrol_at = datetime.now(UTC)
            self._last_patrol_status = patrol_status
            self._last_patrol_findings = findings_count
            self._last_patrol_novel = novel_count
            self._last_patrol_dispatched = dispatched_count
            self._current_patrol_id = None

            # Record patrol completion metrics
            patrol_duration = time.monotonic() - patrol_start
            if _qa_patrol_total is not None:
                try:
                    _qa_patrol_total.labels(status=patrol_status).inc()
                except Exception:
                    logger.debug("QaModule: failed to record qa_patrol_total metric", exc_info=True)
            if _qa_patrol_duration_seconds is not None:
                try:
                    _qa_patrol_duration_seconds.observe(patrol_duration)
                except Exception:
                    logger.debug(
                        "QaModule: failed to record qa_patrol_duration_seconds metric",
                        exc_info=True,
                    )

            logger.info(
                "QaModule patrol complete: status=%s findings=%d novel=%d "
                "dispatched=%d sources=%s patrol_id=%s",
                patrol_status,
                findings_count,
                novel_count,
                dispatched_count,
                sources_polled,
                patrol_id,
            )

            return {
                "status": patrol_status,
                "patrol_id": str(patrol_id),
                "findings_count": findings_count,
                "novel_count": novel_count,
                "dispatched_count": dispatched_count,
                "sources_polled": sources_polled,
            }

        except Exception as exc:
            error_msg = repr(exc)
            logger.error(
                "QaModule patrol error: patrol_id=%s error=%s",
                patrol_id,
                error_msg,
                exc_info=True,
            )
            if _HAS_OTEL and _patrol_span is not None:
                _patrol_span.record_exception(exc)
                _patrol_span.set_status(trace.StatusCode.ERROR, error_msg)
            await self._complete_patrol_record(
                pool=pool,
                patrol_id=patrol_id,
                status="error",
                findings_count=0,
                novel_count=0,
                dispatched_count=0,
                sources_polled=sources_polled,
                error_detail=error_msg[:500],
            )
            self._last_patrol_at = datetime.now(UTC)
            self._last_patrol_status = "error"
            self._current_patrol_id = None

            # Record error patrol metrics
            patrol_duration = time.monotonic() - patrol_start
            if _qa_patrol_total is not None:
                try:
                    _qa_patrol_total.labels(status="error").inc()
                except Exception:
                    logger.debug(
                        "QaModule: failed to record qa_patrol_total metric (error path)",
                        exc_info=True,
                    )
            if _qa_patrol_duration_seconds is not None:
                try:
                    _qa_patrol_duration_seconds.observe(patrol_duration)
                except Exception:
                    logger.debug(
                        "QaModule: failed to record qa_patrol_duration_seconds metric (error path)",
                        exc_info=True,
                    )

            return {"status": "error", "reason": error_msg}
        finally:
            if _HAS_OTEL and _patrol_span is not None:
                _patrol_span.end()
                if _patrol_span_token is not None:
                    otel_context.detach(_patrol_span_token)

    def _schedule_mini_patrol(self, fingerprint: str) -> None:
        """Schedule an immediate mini-patrol for a severity-0 finding.

        Creates a background task to run a full patrol cycle immediately
        (all configured sources).  This is a best-effort trigger; if a
        patrol is already running, the finding will be picked up by the
        normal scheduled cycle.

        Parameters
        ----------
        fingerprint:
            64-character hex fingerprint of the critical finding.  Used
            only for log context.
        """
        if self._mini_patrol_task and not self._mini_patrol_task.done():
            logger.debug(
                "QaModule: mini-patrol already pending for severity-0 "
                "finding (fingerprint=%s), skipping duplicate",
                fingerprint[:12],
            )
            return

        logger.info(
            "QaModule: triggering immediate mini-patrol for severity-0 finding fingerprint=%s",
            fingerprint[:12],
        )

        async def _run_mini_patrol() -> None:
            try:
                await asyncio.sleep(0)  # yield to allow the tool handler to return first
                await self.run_patrol_tick()
            except Exception:
                logger.warning("QaModule mini-patrol failed", exc_info=True)

        try:
            loop = asyncio.get_running_loop()
            self._mini_patrol_task = loop.create_task(_run_mini_patrol())
        except RuntimeError:
            # No running event loop — mini-patrol not possible in this context
            logger.debug("QaModule: could not schedule mini-patrol (no running event loop)")

    # ------------------------------------------------------------------
    # Patrol DB helpers
    # ------------------------------------------------------------------

    async def _create_patrol_record(self, pool: Any) -> uuid.UUID:
        """Insert a new 'running' patrol record and return its UUID."""
        patrol_id = await pool.fetchval(
            """
            INSERT INTO public.qa_patrols (
                status, log_lookback_minutes, sources_polled
            )
            VALUES ('running', $1, $2)
            RETURNING id
            """,
            self._config.log_lookback_minutes,
            [],
        )
        return patrol_id

    async def _complete_patrol_record(
        self,
        pool: Any,
        patrol_id: uuid.UUID,
        status: str,
        findings_count: int,
        novel_count: int,
        dispatched_count: int,
        sources_polled: list[str],
        error_detail: str | None,
    ) -> None:
        """Update the patrol record with final outcome."""
        await pool.execute(
            """
            UPDATE public.qa_patrols
            SET completed_at = now(),
                status = $2,
                findings_count = $3,
                novel_count = $4,
                dispatched_count = $5,
                sources_polled = $6,
                error_detail = $7
            WHERE id = $1
            """,
            patrol_id,
            status,
            findings_count,
            novel_count,
            dispatched_count,
            sources_polled,
            error_detail,
        )

    async def _record_patrol_skip(self, pool: Any) -> None:
        """Insert a skipped_overlap patrol record (no findings, immediate close)."""
        try:
            await pool.execute(
                """
                INSERT INTO public.qa_patrols (
                    status, completed_at, log_lookback_minutes, sources_polled
                )
                VALUES ('skipped_overlap', now(), $1, '{}')
                """,
                self._config.log_lookback_minutes,
            )
        except Exception:
            logger.debug("QaModule: failed to record skipped_overlap patrol", exc_info=True)

        if _qa_patrol_total is not None:
            try:
                _qa_patrol_total.labels(status="skipped_overlap").inc()
            except Exception:
                logger.debug(
                    "QaModule: failed to record qa_patrol_total metric (skipped_overlap)",
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # Investigation metric snapshots
    # ------------------------------------------------------------------

    async def _record_investigation_metrics(self, pool: Any) -> None:
        """Query DB to update qa_investigations_active gauge and record durations.

        Queries the public.healing_attempts table for QA-originated investigations
        (qa_patrol_id IS NOT NULL).  Updates the qa_investigations_active gauge with
        the current count of rows with status='investigating'.  Records
        qa_investigation_duration_seconds for any QA investigations that closed since
        the last patrol, anchored to self._last_patrol_at to avoid double-counting
        across overlapping lookback windows.

        This method is called once per patrol cycle and does not raise — any DB or
        metric errors are caught and logged at DEBUG level so they cannot abort a patrol.
        Asyncio CancelledError is always re-raised so task cancellation propagates.
        """
        try:
            # Update active investigation gauge
            active_count = await pool.fetchval(
                """
                SELECT COUNT(*)
                FROM public.healing_attempts
                WHERE status = 'investigating'
                  AND qa_patrol_id IS NOT NULL
                """
            )
            if _qa_investigations_active is not None:
                try:
                    _qa_investigations_active.set(active_count or 0)
                except Exception:
                    logger.debug(
                        "QaModule: failed to set qa_investigations_active metric", exc_info=True
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("QaModule: failed to query active investigation count", exc_info=True)

        # Record durations for investigations that closed since the last patrol.
        # Using self._last_patrol_at as a high-water mark avoids double-counting rows
        # that would otherwise reappear in a rolling lookback window across multiple patrols.
        try:
            if self._last_patrol_at is not None:
                recently_closed = await pool.fetch(
                    """
                    SELECT status,
                           EXTRACT(EPOCH FROM (closed_at - created_at)) AS duration_seconds
                    FROM public.healing_attempts
                    WHERE qa_patrol_id IS NOT NULL
                      AND closed_at IS NOT NULL
                      AND closed_at > $1
                    """,
                    self._last_patrol_at,
                )
            else:
                # First patrol run: look back one interval to catch any investigations
                # that closed before this patrol but after daemon startup.
                lookback_interval = self._config.patrol_interval_minutes
                recently_closed = await pool.fetch(
                    """
                    SELECT status,
                           EXTRACT(EPOCH FROM (closed_at - created_at)) AS duration_seconds
                    FROM public.healing_attempts
                    WHERE qa_patrol_id IS NOT NULL
                      AND closed_at IS NOT NULL
                      AND closed_at >= now() - ($1 * INTERVAL '1 minute')
                    """,
                    lookback_interval,
                )
            if _qa_investigation_duration_seconds is not None:
                for row in recently_closed:
                    try:
                        duration = float(row["duration_seconds"] or 0)
                        status = str(row["status"])
                        _qa_investigation_duration_seconds.labels(status=status).observe(duration)
                    except Exception:
                        logger.debug(
                            "QaModule: failed to record qa_investigation_duration_seconds metric",
                            exc_info=True,
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("QaModule: failed to query recently closed investigations", exc_info=True)

    # ------------------------------------------------------------------
    # Credential resolution
    # ------------------------------------------------------------------

    async def _resolve_gh_token(self) -> str | None:
        """Resolve BUTLERS_QA_GH_TOKEN from the credential store."""
        if self._credential_store is None:
            return None
        try:
            token = await self._credential_store.resolve(QA_GH_TOKEN_KEY)
            return token
        except Exception:
            logger.debug("QaModule: failed to resolve GH token", exc_info=True)
            return None

    async def _resolve_git_identity(self) -> tuple[str | None, str | None]:
        """Resolve optional git author identity for QA-generated commits."""
        if self._credential_store is None:
            return None, None

        try:
            author_name = await self._credential_store.resolve(QA_GIT_AUTHOR_NAME_KEY)
        except Exception:
            logger.debug("QaModule: failed to resolve QA git author name", exc_info=True)
            author_name = None

        try:
            author_email = await self._credential_store.resolve(QA_GIT_AUTHOR_EMAIL_KEY)
        except Exception:
            logger.debug("QaModule: failed to resolve QA git author email", exc_info=True)
            author_email = None

        cleaned_name = (author_name or "").strip() or None
        cleaned_email = (author_email or "").strip() or None
        return cleaned_name, cleaned_email

    # ------------------------------------------------------------------
    # PR status check
    # ------------------------------------------------------------------

    async def _check_pr_statuses(
        self,
        pool: Any,
        gh_token: str | None,
        git_author_name: str | None = None,
        git_author_email: str | None = None,
        patrol_id: uuid.UUID | None = None,
        patrol_started_at: datetime | None = None,
    ) -> None:
        """Check GitHub status of open PR investigations.

        Wraps check_open_pr_statuses from core.qa.dispatch with error
        isolation so PR check failures don't abort the patrol cycle.

        When the module's spawner is wired (``wire_runtime`` was called), also
        enables PR review conversation tracking: detects "changes requested"
        or unresolved review threads and dispatches follow-up agents.

        ``patrol_id`` is threaded through to enable per-cycle follow-up
        budgeting (``follow_up_cycle_count`` resets when the cycle changes).
        """
        from butlers.core.qa.dispatch import QaDispatchConfig

        # Build dispatch config from module config for follow-up dispatch
        _spawner = get_spawner()
        dispatch_config: QaDispatchConfig | None = None
        if _spawner is not None:
            dispatch_config = QaDispatchConfig(
                severity_threshold=self._config.severity_threshold,
                max_concurrent=self._config.max_concurrent_investigations,
                dashboard_base_url=self._config.dashboard_base_url,
            )

        try:
            await check_open_pr_statuses(
                pool,
                self._repo_root,
                gh_token,
                git_author_name=git_author_name,
                git_author_email=git_author_email,
                spawner=_spawner,
                config=dispatch_config,
                task_registry=self._watchdog_tasks,
                patrol_id=patrol_id,
                patrol_started_at=patrol_started_at,
            )
        except Exception:
            logger.warning("QaModule: check_open_pr_statuses failed (non-fatal)", exc_info=True)

    # ------------------------------------------------------------------
    # Missing-token notification
    # ------------------------------------------------------------------

    async def _notify_missing_gh_token(self, patrol_id: uuid.UUID) -> None:
        """Send a one-per-patrol-cycle alert when the GH token is missing.

        Rate-limited: only one notification is sent per patrol cycle
        (identified by ``patrol_id``).  This method always logs a
        ``WARNING`` when first called for a given patrol cycle; the external
        notification step (via ``notify_fn``) is skipped only when
        ``notify_fn`` is ``None``.

        Parameters
        ----------
        patrol_id:
            The UUID of the current patrol cycle.  Used to deduplicate
            notifications so we do not send more than one alert per patrol
            cycle.
        """
        if self._last_missing_token_notified_patrol_id == patrol_id:
            logger.debug(
                "QaModule: missing GH token already notified for patrol %s — skipping", patrol_id
            )
            return

        self._last_missing_token_notified_patrol_id = patrol_id

        logger.warning(
            "QaModule: BUTLERS_QA_GH_TOKEN is missing — investigations cannot open PRs. "
            "Provision the credential via: butler secrets set BUTLERS_QA_GH_TOKEN <token>"
        )

        if self._notify_fn is None:
            return

        message = (
            "QA Staffer alert: GitHub token is missing.\n\n"
            "Investigations cannot open pull requests until the credential is provisioned.\n\n"
            "To fix, provision the secret via the butler CLI or dashboard:\n"
            "  butler secrets set BUTLERS_QA_GH_TOKEN <your-github-token>\n\n"
            "The token requires at least 'repo' scope to create pull requests."
        )
        try:
            await self._notify_fn(
                channel="telegram",
                message=message,
                priority="high",
            )
        except Exception:
            logger.warning(
                "QaModule: failed to send missing-token notification (non-fatal)", exc_info=True
            )

    # ------------------------------------------------------------------
    # Runtime wiring (called by daemon after register_tools)
    # ------------------------------------------------------------------

    def wire_runtime(
        self,
        spawner: Any,
        repo_root: Path | str,
        notify_fn: Callable[..., Coroutine[Any, Any, Any]] | None = None,
        switchboard_client: Any = None,
    ) -> None:
        """Wire the module to the spawner and runtime dependencies.

        Called by the QA staffer daemon after ``register_tools()`` to give the
        module access to the spawner (for investigation dispatch) and the
        repo root (for worktree creation).  Butler identity is provided
        exclusively through ``register_tools()`` and is not repeated here.

        Parameters
        ----------
        spawner:
            Spawner instance for dispatching investigations.
        repo_root:
            Absolute path to the repository root.
        notify_fn:
            Optional async callable used to send operator notifications.
            Signature matches the daemon's ``notify()`` MCP tool:
            ``notify_fn(channel, message, priority) -> dict``.
            When ``None``, missing-token alerts are only logged.
        switchboard_client:
            Optional Switchboard MCP client for inter-butler communication.
            When provided, enables future QA-to-butler routing via Switchboard.
            When ``None``, the module operates without Switchboard connectivity.
        """
        # spawner is registered in the core spawn_hooks singleton by the daemon
        # before wire_runtime() is called; do NOT store it here (Vision Rule 2).
        # Only use daemon's repo_root as fallback if managed clone is not active
        if self._managed_clone is None or self._managed_clone.clone_path is None:
            self._repo_root = Path(repo_root)
        self._notify_fn = notify_fn
        self._switchboard_client = switchboard_client

    @property
    def managed_clone(self) -> ManagedRepoClone | None:
        """Return the managed repo clone instance, if initialized."""
        return self._managed_clone


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _qa_finding_from_row(row: dict) -> QaFinding:
    """Reconstitute a QaFinding from a qa_findings DB row dict.

    Used to rebuild QaFinding objects from rows loaded via
    :func:`get_dispatch_queued_findings` so that queued findings can be
    injected back into the triage batch in a subsequent patrol cycle.

    Parameters
    ----------
    row:
        A ``qa_findings`` row dict (as returned by asyncpg ``fetch`` + ``dict()``).

    Returns
    -------
    QaFinding
        Reconstituted finding.  The ``timestamp`` field is set to ``last_seen``
        since the original value is not stored separately.
    """
    structured_evidence: dict | None = None
    if row.get("structured_evidence") is not None:
        raw = row["structured_evidence"]
        if isinstance(raw, dict):
            structured_evidence = raw
        elif isinstance(raw, str):
            try:
                structured_evidence = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                logger.warning(
                    "_qa_finding_from_row: structured_evidence is a non-JSON string for "
                    "fingerprint=%s; discarding",
                    row.get("fingerprint"),
                )
        # else: unexpected type — leave as None

    last_seen = row.get("last_seen")
    if last_seen is None:
        logger.warning(
            "_qa_finding_from_row: last_seen is None for fingerprint=%s; timestamp will be None",
            row.get("fingerprint"),
        )

    return QaFinding(
        fingerprint=row["fingerprint"],
        source_type=row["source_type"],
        source_butler=row["source_butler"],
        severity=row["severity"],
        exception_type=row["exception_type"],
        event_summary=row["event_summary"],
        call_site=row["call_site"],
        occurrence_count=row["occurrence_count"],
        first_seen=row["first_seen"],
        last_seen=last_seen,
        timestamp=last_seen,
        source_session_trigger_source=row.get("source_session_trigger_source"),
        structured_evidence=structured_evidence,
    )
