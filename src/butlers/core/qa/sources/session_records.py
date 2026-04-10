"""SessionRecordsSource — session-record-based QA discovery source.

Queries the sanctioned ``public.v_qa_recent_failures`` read-only SQL view
for failed sessions within the lookback window.  This view is a UNION across
all butler ``sessions`` tables (RFC 0010 sanctioned cross-schema exception).

The source does NOT query butler schemas directly — only the view.

Spec reference
--------------
openspec/changes/qa-staffer/specs/staffer-qa/spec.md (V1 Discovery Sources)
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
    _extract_call_site_from_str,
    _sanitize_message,
    _score_severity,
)
from butlers.core.qa.models import QaFinding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The sanctioned view for cross-schema session failure queries.
_VIEW_NAME = "public.v_qa_recent_failures"

#: Maximum length of event_summary stored in QaFinding.
_MAX_SUMMARY_LEN = 200

#: Health-check query — validates view accessibility before processing rows.
_HEALTH_CHECK_SQL = f"SELECT 1 FROM {_VIEW_NAME} LIMIT 0"

#: Query to fetch recent failures from the view.
_QUERY_SQL = f"""
    SELECT
        source_butler,
        session_id,
        error,
        healing_fingerprint,
        started_at,
        completed_at,
        status,
        trigger_source
    FROM {_VIEW_NAME}
    WHERE completed_at >= $1::timestamptz
    ORDER BY completed_at DESC
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class SessionRecordsSource:
    """Session-record-based QA discovery source.

    Queries ``public.v_qa_recent_failures`` for recent session failures and
    maps them to ``QaFinding`` objects.  Event summaries extracted from
    session error messages are passed through ``anonymize()`` before storage.

    Parameters
    ----------
    pool:
        asyncpg connection pool.  The pool user must have SELECT access on
        ``public.v_qa_recent_failures`` (granted by migration core_055 to
        the ``butler_qa_rw`` role).
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
        """Source identifier: ``"session_records"``."""
        return "session_records"

    async def discover(self, lookback_minutes: int) -> list[QaFinding]:
        """Query v_qa_recent_failures and return aggregated findings.

        Parameters
        ----------
        lookback_minutes:
            How far back to query: rows with ``completed_at >=
            now() - lookback_minutes`` are included.

        Returns
        -------
        list[QaFinding]
            One finding per unique fingerprint, with ``occurrence_count``
            reflecting how many sessions share that fingerprint.
        """
        # Health-check: validate view accessibility (catches revoked grants early)
        try:
            await self._pool.execute(_HEALTH_CHECK_SQL)
        except asyncpg.PostgresError as exc:
            logger.error(
                "SessionRecordsSource: health check failed for %s: %s",
                _VIEW_NAME,
                exc,
            )
            raise

        cutoff = datetime.now(UTC) - timedelta(minutes=lookback_minutes)
        now = datetime.now(UTC)

        try:
            rows = await self._pool.fetch(_QUERY_SQL, cutoff)
        except asyncpg.PostgresError as exc:
            logger.error("SessionRecordsSource: query failed on %s: %s", _VIEW_NAME, exc)
            raise

        # fingerprint -> aggregation state
        aggregated: dict[str, _SessionFindingAccumulator] = {}

        for row in rows:
            finding = self._process_row(row, now)
            if finding is None:
                continue

            fp = finding.fingerprint
            if fp not in aggregated:
                aggregated[fp] = _SessionFindingAccumulator(
                    fingerprint=fp,
                    source_butler=finding.source_butler,
                    severity=finding.severity,
                    exception_type=finding.exception_type,
                    event_summary=finding.event_summary,
                    call_site=finding.call_site,
                    first_seen=finding.first_seen,
                    last_seen=finding.last_seen,
                    source_session_trigger_source=finding.source_session_trigger_source,
                )
            else:
                acc = aggregated[fp]
                acc.occurrence_count += 1
                if finding.first_seen < acc.first_seen:
                    acc.first_seen = finding.first_seen
                if finding.last_seen > acc.last_seen:
                    acc.last_seen = finding.last_seen
                    # Always take trigger_source from the most recent session row,
                    # even when it is None, so recency semantics remain consistent.
                    acc.source_session_trigger_source = finding.source_session_trigger_source

        return [acc.to_finding(now) for acc in aggregated.values()]

    def _process_row(self, row: asyncpg.Record, now: datetime) -> QaFinding | None:
        """Convert one v_qa_recent_failures row to a QaFinding.

        Returns None if the row lacks enough data to build a useful finding.
        """
        source_butler: str = row["source_butler"] or "unknown"
        error_text: str | None = row["error"]
        healing_fingerprint: str | None = row["healing_fingerprint"]
        completed_at: datetime | None = row["completed_at"]
        started_at: datetime | None = row["started_at"]
        status: str = row["status"] or "error"
        trigger_source: str | None = row["trigger_source"]

        # Use completed_at as timestamp; fall back to now
        ts = completed_at or now
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        first_seen = started_at or ts
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=UTC)

        # Build exception type from status
        exception_type = _status_to_exception_type(status, error_text)

        # Extract call site from error traceback (if present in error text)
        call_site = _extract_call_site_from_str(error_text or "")

        # Fingerprint on the full sanitized error text (up to fingerprint.py's 500-char
        # internal cap) so this source stays compatible with canonical paths.
        # Store a shorter anonymized summary for display/storage only.
        full_error_text = error_text or f"session {status}"
        sanitized_event_for_fp = _sanitize_message(full_error_text)
        raw_summary = full_error_text[:_MAX_SUMMARY_LEN]
        sanitized_summary = _sanitize_message(raw_summary)
        anon_summary = anonymize(sanitized_summary, self._repo_root)

        # Prefer the pre-computed fingerprint from the session if available
        if healing_fingerprint and len(healing_fingerprint) == 64:
            fingerprint = healing_fingerprint
        else:
            fingerprint = _compute_hash(exception_type, call_site, sanitized_event_for_fp)

        severity = _score_severity(exception_type, call_site)

        return QaFinding(
            fingerprint=fingerprint,
            source_type="session_records",
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _status_to_exception_type(status: str, error_text: str | None) -> str:
    """Derive an exception type from session status and error text."""
    if status == "timeout":
        return "SessionTimeoutError"
    if status == "crash":
        return "SessionCrashError"
    # status == "error": try to extract exception class from the error text
    if error_text:
        # Look for "ExceptionName: ..." pattern at start of lines
        match = re.search(
            r"^([A-Za-z][A-Za-z0-9_.]+Error|[A-Za-z][A-Za-z0-9_.]+Exception)",
            error_text,
            re.MULTILINE,
        )
        if match:
            return match.group(1)
    return "SessionError"


# ---------------------------------------------------------------------------
# Internal accumulator
# ---------------------------------------------------------------------------


class _SessionFindingAccumulator:
    """Internal state for aggregating session rows with the same fingerprint."""

    def __init__(
        self,
        fingerprint: str,
        source_butler: str,
        severity: int,
        exception_type: str,
        event_summary: str,
        call_site: str,
        first_seen: datetime,
        last_seen: datetime,
        source_session_trigger_source: str | None = None,
    ) -> None:
        self.fingerprint = fingerprint
        self.source_butler = source_butler
        self.severity = severity
        self.exception_type = exception_type
        self.event_summary = event_summary
        self.call_site = call_site
        self.first_seen = first_seen
        self.last_seen = last_seen
        self.occurrence_count = 1
        self.source_session_trigger_source = source_session_trigger_source

    def to_finding(self, now: datetime) -> QaFinding:
        return QaFinding(
            fingerprint=self.fingerprint,
            source_type="session_records",
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
        )
