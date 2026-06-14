"""ToolCallFailuresSource — surfaces tool-call errors inside ANY session.

Queries the sanctioned ``public.v_qa_tool_call_failures`` read-only SQL view
(migration core_125), a UNION across all butler ``sessions`` tables that
explodes each ``tool_calls`` JSONB element with ``outcome = 'error'`` — for
**both** ``success = true`` and ``success = false`` sessions.

Why this source exists
----------------------
The ``session_records`` source only sees sessions whose *outcome* failed
(``v_qa_recent_failures`` filters ``success = false``).  When a butler's LLM
agent CATCHES a failing MCP tool call, recovers, and the session completes
``success = true``, that tool failure is invisible to ``session_records``.
PR #2285 closed the ``log_scanner`` half (tool exceptions emit a structured
``"MCP tool call failed (...)"`` error log).  This source closes the
per-session-record half so QA still has a DB-backed signal that does not
depend on log-file availability/rotation.

The source does NOT query butler schemas directly — only the view.

Dedup against log_scanner (fingerprint alignment)
-------------------------------------------------
The QA triage layer deduplicates findings **source-agnostically by
fingerprint** (``triage.triage_findings``: same fingerprint within a patrol
cycle → coalesced as ``active_investigation``).  So the conservative,
zero-new-machinery dedup strategy is: compute the **same fingerprint** the
``log_scanner`` source computes for the matching structured log line.

For a caught tool exception, ``mcp_wrappers._log_tool_call_failure`` emits a
log entry whose log-scanner fingerprint is::

    _compute_hash(
        exception_type = type(exc).__name__,          # e.g. "ValueError"
        call_site      = "butlers.mcp_wrappers",        # the emitting logger
        sanitized      = _sanitize_message(
            "MCP tool call failed (butler=B module=M tool=T): <ExcType>: <msg>"
        ),
    )

This source reconstructs that exact tuple from the view row:

  * ``error`` column holds ``"<ExcType>: <msg>"`` (written verbatim by
    ``capture_tool_call``), so the leading token gives ``exception_type`` and
    the full ``error`` reconstructs the log ``event`` string.
  * ``call_site`` is the fixed logger name ``"butlers.mcp_wrappers"``.

When both sources fire in the same patrol cycle they produce IDENTICAL
fingerprints and triage coalesces them — no double-reporting.  When the log
file is unavailable (rotation, missing log root), this source still surfaces
the failure on its own.

Dedup key chosen: the canonical QA fingerprint tuple
``(exception_type, "butlers.mcp_wrappers", sanitized_event)``.  Within this
source's own scan, rows are additionally aggregated on a stable key
``(session_id, tool_name, fingerprint)`` so repeated identical errors in one
session collapse to a single finding with ``occurrence_count``.

Spec reference
--------------
openspec/changes/qa-staffer/specs/staffer-qa/spec.md (V1 Discovery Sources)
Sibling of ``session_records`` (core_055 view) and ``log_scanner``.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

from butlers.core.healing.anonymizer import anonymize
from butlers.core.healing.fingerprint import (
    _compute_hash,
    _sanitize_message,
    _score_severity,
)
from butlers.core.qa.models import QaFinding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The sanctioned view for cross-schema tool-call-failure queries.
_VIEW_NAME = "public.v_qa_tool_call_failures"

#: Maximum length of event_summary stored in QaFinding.
_MAX_SUMMARY_LEN = 200

#: Maximum number of session IDs collected per fingerprint for structured evidence.
_MAX_EVIDENCE_SESSION_IDS = 5

#: The logger name that emits the "MCP tool call failed (...)" structured log
#: line (``butlers.mcp_wrappers._log_tool_call_failure``).  log_scanner uses
#: this as its ``call_site`` for those entries; we reuse it so fingerprints
#: align for cross-source dedup.
_TOOL_FAILURE_CALL_SITE = "butlers.mcp_wrappers"

#: The exact log-event template emitted by mcp_wrappers for a failed tool call.
#: Must stay byte-for-byte in sync with
#: ``mcp_wrappers._MCP_TOOL_CALL_FAILED_LOG_LINE``.
_TOOL_FAILURE_EVENT_TEMPLATE = (
    "MCP tool call failed (butler={butler} module={module} tool={tool}): {error}"
)

#: Health-check query — validates view accessibility before processing rows.
_HEALTH_CHECK_SQL = f"SELECT 1 FROM {_VIEW_NAME} LIMIT 0"

#: Query to fetch recent error tool-calls from the view.
_QUERY_SQL = f"""
    SELECT
        source_butler,
        session_id,
        session_success,
        tool_name,
        module_name,
        error,
        trigger_source,
        started_at,
        completed_at
    FROM {_VIEW_NAME}
    WHERE completed_at >= $1::timestamptz
    ORDER BY completed_at DESC
"""

#: Leading "ExcType:" parser, mirroring session_records' exception extraction.
_EXC_TYPE_RE = re.compile(
    r"^([A-Za-z][A-Za-z0-9_.]*(?:Error|Exception|Warning|Timeout|Interrupt))\b"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ToolCallFailuresSource:
    """Discovery source for tool-call failures inside otherwise-successful sessions.

    Queries ``public.v_qa_tool_call_failures`` for tool calls with
    ``outcome = 'error'`` and maps them to ``QaFinding`` objects whose
    fingerprints align with the ``log_scanner`` source for cross-source dedup.

    Parameters
    ----------
    pool:
        asyncpg connection pool.  The pool user must have SELECT access on
        ``public.v_qa_tool_call_failures`` (granted by migration core_125) and,
        transitively, on each ``<schema>.sessions`` table (granted to
        ``butler_qa_rw`` by core_055).
    repo_root:
        Repository root used by the anonymizer.  Defaults to CWD.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        repo_root: Path | None = None,
    ) -> None:
        self._pool = pool
        self._repo_root = (repo_root or Path.cwd()).resolve()

    @property
    def name(self) -> str:
        """Source identifier: ``"tool_call_failures"``."""
        return "tool_call_failures"

    async def discover(self, lookback_minutes: int) -> list[QaFinding]:
        """Query the view and return aggregated tool-call-failure findings.

        Parameters
        ----------
        lookback_minutes:
            Rows with ``completed_at >= now() - lookback_minutes`` are included.

        Returns
        -------
        list[QaFinding]
            One finding per unique fingerprint, with ``occurrence_count``
            reflecting how many error tool-calls share that fingerprint.
        """
        # Health-check: validate view accessibility (catches revoked grants early)
        try:
            await self._pool.execute(_HEALTH_CHECK_SQL)
        except asyncpg.PostgresError as exc:
            logger.error(
                "ToolCallFailuresSource: health check failed for %s: %s",
                _VIEW_NAME,
                exc,
            )
            raise

        cutoff = datetime.now(UTC) - timedelta(minutes=lookback_minutes)
        now = datetime.now(UTC)

        try:
            rows = await self._pool.fetch(_QUERY_SQL, cutoff)
        except asyncpg.PostgresError as exc:
            logger.error("ToolCallFailuresSource: query failed on %s: %s", _VIEW_NAME, exc)
            raise

        # fingerprint -> aggregation state
        aggregated: dict[str, _ToolCallFindingAccumulator] = {}
        # (session_id, tool_name, fingerprint) -> already counted in this scan
        seen_keys: set[tuple[str, str, str]] = set()
        # fingerprint -> seen session ids (for structured-evidence de-dup)
        seen_session_ids: dict[str, set[str]] = {}

        for row in rows:
            result = self._process_row(row, now)
            if result is None:
                continue

            finding, session_id_str = result
            fp = finding.fingerprint

            dedup_key = (session_id_str or "", finding.exception_type, fp)
            if fp not in aggregated:
                aggregated[fp] = _ToolCallFindingAccumulator(finding=finding)
                seen_session_ids[fp] = set()
                seen_keys.add(dedup_key)
            elif dedup_key not in seen_keys:
                acc = aggregated[fp]
                acc.occurrence_count += 1
                if finding.first_seen < acc.first_seen:
                    acc.first_seen = finding.first_seen
                if finding.last_seen > acc.last_seen:
                    acc.last_seen = finding.last_seen
                    acc.source_session_trigger_source = finding.source_session_trigger_source
                seen_keys.add(dedup_key)

            acc = aggregated[fp]
            if (
                session_id_str
                and session_id_str not in seen_session_ids[fp]
                and len(acc.session_ids) < _MAX_EVIDENCE_SESSION_IDS
            ):
                acc.session_ids.append(session_id_str)
                seen_session_ids[fp].add(session_id_str)

        return [acc.to_finding(now) for acc in aggregated.values()]

    def _process_row(
        self,
        row: asyncpg.Record,
        now: datetime,
    ) -> tuple[QaFinding, str | None] | None:
        """Convert one view row to a (QaFinding, session_id) tuple.

        Returns None if the row lacks the minimum data to build a useful finding
        (no tool name and no error text).
        """
        source_butler: str = row["source_butler"] or "unknown"
        tool_name: str | None = row["tool_name"]
        module_name: str | None = row["module_name"]
        error_text: str | None = row["error"]
        trigger_source: str | None = row["trigger_source"]
        completed_at: datetime | None = row["completed_at"]
        started_at: datetime | None = row["started_at"]
        raw_session_id = row["session_id"]
        session_id_str: str | None = str(raw_session_id) if raw_session_id is not None else None

        if not tool_name and not error_text:
            return None

        ts = completed_at or now
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        first_seen = started_at or ts
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=UTC)

        # exception_type: leading "ExcType" token from the captured error string
        # ("<ExcType>: <msg>"), mirroring session_records' extraction.  Falls
        # back to "ToolCallError" when the shape is unexpected.
        exception_type = _exception_type_from_error(error_text)

        # Reconstruct the SAME log event string log_scanner fingerprints, so the
        # two sources coalesce in triage's fingerprint dedup.
        log_event = _TOOL_FAILURE_EVENT_TEMPLATE.format(
            butler=source_butler,
            module=module_name or "unknown",
            tool=tool_name or "unknown",
            error=error_text or exception_type,
        )

        call_site = _TOOL_FAILURE_CALL_SITE
        sanitized_event_for_fp = _sanitize_message(log_event)
        fingerprint = _compute_hash(exception_type, call_site, sanitized_event_for_fp)

        # Display summary: the captured error text (anonymized), capped.
        raw_summary = (error_text or log_event)[:_MAX_SUMMARY_LEN]
        sanitized_summary = _sanitize_message(raw_summary)
        anon_summary = anonymize(sanitized_summary, self._repo_root)

        severity = _score_severity(exception_type, call_site)

        finding = QaFinding(
            fingerprint=fingerprint,
            source_type="tool_call_failures",
            source_butler=source_butler,
            severity=severity,
            exception_type=exception_type,
            event_summary=anon_summary,
            call_site=call_site,
            occurrence_count=1,
            first_seen=first_seen,
            last_seen=ts,
            timestamp=now,
            source_session_trigger_source=trigger_source,
        )
        return finding, session_id_str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _exception_type_from_error(error_text: str | None) -> str:
    """Extract the leading exception class name from a captured error string.

    ``capture_tool_call`` writes ``error`` as ``f"{type(exc).__name__}: {exc}"``
    so the class name is the leading token.  Returns ``"ToolCallError"`` when no
    recognizable class name is present.
    """
    if not error_text:
        return "ToolCallError"
    match = _EXC_TYPE_RE.match(error_text.strip())
    if match:
        return match.group(1)
    return "ToolCallError"


# ---------------------------------------------------------------------------
# Internal accumulator
# ---------------------------------------------------------------------------


class _ToolCallFindingAccumulator:
    """Aggregates view rows sharing a fingerprint into one QaFinding."""

    def __init__(self, finding: QaFinding) -> None:
        self.fingerprint = finding.fingerprint
        self.source_butler = finding.source_butler
        self.severity = finding.severity
        self.exception_type = finding.exception_type
        self.event_summary = finding.event_summary
        self.call_site = finding.call_site
        self.first_seen = finding.first_seen
        self.last_seen = finding.last_seen
        self.occurrence_count = 1
        self.source_session_trigger_source = finding.source_session_trigger_source
        self.session_ids: list[str] = []

    def to_finding(self, now: datetime) -> QaFinding:
        """Build an aggregated QaFinding with structured evidence."""
        structured_evidence: dict = {
            "source": "tool_call_failures",
            "session_ids": self.session_ids,
        }
        return QaFinding(
            fingerprint=self.fingerprint,
            source_type="tool_call_failures",
            source_butler=self.source_butler,
            severity=self.severity,
            exception_type=self.exception_type,
            event_summary=self.event_summary,
            call_site=self.call_site,
            occurrence_count=self.occurrence_count,
            first_seen=self.first_seen,
            last_seen=self.last_seen,
            timestamp=now,
            source_session_trigger_source=self.source_session_trigger_source,
            structured_evidence=structured_evidence,
        )
