"""LogScannerSource — cross-butler log scanning discovery source.

Reads structured JSON log files from all deployed butlers, staffers, and
connectors.  Uses tool-based filtering (JSON parsing, regex, severity checks)
to extract error/warning events.  No LLM invocation.

Log root structure (relative to BUTLERS_LOG_ROOT or ``logs/``)::

    logs/
      butlers/*.log   — per-butler application logs (qa.log excluded)
      connectors/*.log — standalone connector logs
      uvicorn/*.log   — HTTP server / MCP transport logs

Findings are aggregated by fingerprint within a single scan cycle.

Structured evidence
-------------------
Each aggregated finding carries a ``structured_evidence`` dict populated from
the raw JSON log entry without embedding sensitive payloads:

  ``source``: always ``"log_scanner"``.
  ``log_file``: the log filename (stem) where the fingerprint was first seen.
  ``level``: the log level of the first occurrence (e.g. ``"error"``).
  ``trigger_source``: the ``trigger_source`` field from the log entry's JSON
                      if present; ``None`` if absent.

The prompt builder emits a ``## Structured Evidence`` section rendering the
identifiers inline for the investigation agent (Phase 1).  Out-of-band artifact
persistence for large evidence bundles is deferred to Phase 2.

Spec reference
--------------
openspec/changes/qa-staffer/specs/qa-log-scanner/spec.md
openspec/changes/qa-staffer/specs/qa-investigation-dispatch/spec.md
  §Requirement: Structured Evidence Payloads
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

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

#: Default maximum number of log entries processed per scan.
DEFAULT_MAX_ENTRIES_PER_SCAN = 10_000

#: Default maximum number of unique findings per scan.
DEFAULT_MAX_FINDINGS_PER_SCAN = 100

#: Default hard cap on total lines parsed (including benign INFO/DEBUG lines) per scan.
#: Prevents unbounded CPU/latency under extremely noisy-but-benign log traffic.
DEFAULT_MAX_TOTAL_LINES = 200_000

#: Default wall-clock cap in seconds per ``discover()`` call.
DEFAULT_MAX_SCAN_SECONDS = 30.0

#: Maximum length of event_summary stored in QaFinding.
_MAX_SUMMARY_LEN = 200

#: Log level strings that are always included.
_ERROR_LEVELS = frozenset({"error", "critical"})

#: Log level strings that are included only with crash sentinel patterns.
_WARNING_LEVELS = frozenset({"warning", "warn"})

#: Crash sentinel patterns that cause WARNING entries to be included.
_CRASH_SENTINEL_PATTERNS = re.compile(
    r"OOM|SIGKILL|ConnectionRefused|TimeoutError|deadlock|out of memory",
    re.IGNORECASE,
)

#: Log subdirectories to scan, relative to the log root.
_LOG_SUBDIRS = ("butlers", "connectors", "uvicorn")

#: The QA staffer's own log file — excluded from scanning to prevent
#: self-investigation (QA errors are monitored via Prometheus/OTel).
_QA_LOG_EXCLUDE = "qa.log"

#: Environment variable for log root override.
_LOG_ROOT_ENV = "BUTLERS_LOG_ROOT"

#: Default log root (relative to CWD if not absolute).
_DEFAULT_LOG_ROOT = "logs"

#: Bytes to read from the end of a log file per chunk when scanning backwards.
_TAIL_CHUNK_SIZE = 64 * 1024  # 64 KiB


# ---------------------------------------------------------------------------
# Internal data types
# ---------------------------------------------------------------------------


@dataclass
class LogEntry:
    """Parsed log line from a structlog JSON log file."""

    level: str
    event: str
    timestamp: datetime
    butler_name: str
    logger: str = ""
    exception: str | None = None
    traceback: str | None = None
    trigger_source: str | None = None
    raw: dict = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_log_root(log_root_override: Path | None = None) -> Path:
    """Return the resolved log root directory."""
    if log_root_override is not None:
        return log_root_override.resolve()
    env_val = os.environ.get(_LOG_ROOT_ENV, "").strip()
    if env_val:
        return Path(env_val).resolve()
    return Path(_DEFAULT_LOG_ROOT).resolve()


def _butler_name_from_filename(log_file: Path) -> str:
    """Derive a butler name from a log filename (stem without extension)."""
    return log_file.stem


def _parse_timestamp(ts_str: str) -> datetime | None:
    """Parse an ISO 8601 timestamp string to an aware datetime (UTC)."""
    if not ts_str:
        return None
    # Try common formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z",
    ):
        try:
            dt = datetime.strptime(ts_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            continue
    # Fallback: try fromisoformat (Python 3.11+)
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


def _parse_log_line(line: str, butler_name: str) -> LogEntry | None:
    """Parse a single JSON-lines log line into a LogEntry.

    Returns None if the line is malformed or missing required fields.
    """
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    level = (data.get("level") or data.get("log_level") or "").lower()
    event = data.get("event") or data.get("message") or data.get("msg") or ""
    ts_str = data.get("timestamp") or data.get("ts") or data.get("time") or ""

    if not level or not event:
        return None

    ts = _parse_timestamp(ts_str)
    if ts is None:
        # Can't filter by time without a valid timestamp — skip
        return None

    # butler_name: prefer value from log context, fall back to filename
    bn = data.get("butler_name") or data.get("butler") or butler_name
    logger_name = data.get("logger") or data.get("module") or ""
    exception = data.get("exception") or data.get("exc_type") or None
    traceback = data.get("traceback") or data.get("exc_info") or None
    trigger_source = data.get("trigger_source") or None

    return LogEntry(
        level=level,
        event=str(event),
        timestamp=ts,
        butler_name=str(bn),
        logger=str(logger_name),
        exception=str(exception) if exception else None,
        traceback=str(traceback) if traceback else None,
        trigger_source=str(trigger_source) if trigger_source else None,
        raw=data,
    )


def _should_include_entry(entry: LogEntry) -> bool:
    """Return True if this log entry qualifies for finding extraction."""
    # Codex MCP-discovery exhaustion is better sourced from session_records:
    # the runtime/session tables tell us whether the session actually failed,
    # while the raw adapter log can be emitted on a path that later recovers.
    if (
        entry.logger == "butlers.core.runtimes.codex"
        and "MCP discovery failed after" in entry.event
    ):
        return False
    # Codex refresh-lock contention can contain words like "deadlock" while
    # describing the adapter's non-fatal fallback path. It is operational
    # contention, not a crash sentinel.
    if entry.logger == "butlers.core.runtimes.codex" and (
        "codex_refresh_lock: lock held" in entry.event
        or "codex_refresh_lock: waiting" in entry.event
    ):
        return False

    if entry.level in _ERROR_LEVELS:
        return True
    if entry.level in _WARNING_LEVELS:
        # Only include warnings with crash sentinel patterns
        text = (entry.event or "") + " " + (entry.exception or "")
        return bool(_CRASH_SENTINEL_PATTERNS.search(text))
    return False


def _level_to_severity(level: str, exception_type: str, call_site: str) -> int:
    """Map log level + exception info to a QA severity integer."""
    if level == "critical":
        return 0  # SEVERITY_CRITICAL
    if level == "error":
        return _score_severity(exception_type, call_site)
    # warning with crash sentinel — treat as high
    return 1  # SEVERITY_HIGH


def _read_file_tail(log_file: Path, cutoff: datetime) -> list[str]:
    """Read lines from *log_file* starting from lines within the cutoff window.

    Reads the file from the end in chunks, stopping once we encounter entries
    older than *cutoff*.  Only scans the active ``.log`` file (not rotated
    files like ``.log.1``).

    Returns a list of raw line strings (newest-first order is reversed to
    oldest-first before returning).
    """
    lines: list[str] = []
    try:
        file_size = log_file.stat().st_size
    except OSError:
        return lines

    if file_size == 0:
        return lines

    # Read in chunks from the end
    offset = file_size
    leftover = b""
    found_old = False

    try:
        with log_file.open("rb") as fh:
            while offset > 0 and not found_old:
                chunk_size = min(_TAIL_CHUNK_SIZE, offset)
                offset -= chunk_size
                fh.seek(offset)
                chunk = fh.read(chunk_size)
                # Prepend any leftover bytes from the previous chunk boundary
                data = chunk + leftover
                raw_lines = data.split(b"\n")
                # The first element may be an incomplete line at the chunk boundary
                leftover = raw_lines[0]
                complete_lines = raw_lines[1:]

                for raw_line in reversed(complete_lines):
                    line_str = raw_line.decode("utf-8", errors="replace").strip()
                    if not line_str:
                        continue
                    # Quick timestamp check to stop reading if we've gone past the window
                    # (only parse JSON when necessary to estimate boundary)
                    try:
                        data_peek = json.loads(line_str)
                        ts_str = (
                            data_peek.get("timestamp")
                            or data_peek.get("ts")
                            or data_peek.get("time")
                            or ""
                        )
                        ts = _parse_timestamp(ts_str)
                        if ts is not None and ts < cutoff:
                            found_old = True
                            break
                    except (json.JSONDecodeError, ValueError):
                        pass
                    lines.append(line_str)

            # Handle any remaining leftover bytes (beginning of file)
            if leftover:
                line_str = leftover.decode("utf-8", errors="replace").strip()
                if line_str:
                    lines.append(line_str)

    except OSError as exc:
        logger.debug("LogScannerSource: could not read %s: %s", log_file, exc)
        return []

    # Lines were collected newest-first; reverse to oldest-first
    lines.reverse()
    return lines


def _extract_call_site(entry: LogEntry) -> str:
    """Extract a call site string from a LogEntry.

    Prefers the logger module path; falls back to ``<unknown>:<unknown>``.
    """
    if entry.traceback:
        # Try to extract from traceback string
        cs = _extract_call_site_from_str(entry.traceback)
        if cs != "<unknown>:<unknown>":
            return cs
    if entry.logger:
        return entry.logger
    return "<unknown>:<unknown>"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class LogScannerSource:
    """Cross-butler log scanning discovery source.

    Reads structured JSON log files from ``logs/butlers/``,
    ``logs/connectors/``, and ``logs/uvicorn/`` within the configured
    lookback window.  Produces aggregated ``QaFinding`` objects for each
    unique fingerprint seen.

    Parameters
    ----------
    log_root:
        Override the log root directory.  If ``None``, falls back to the
        ``BUTLERS_LOG_ROOT`` environment variable, then ``logs/`` relative
        to CWD.
    repo_root:
        Repository root used by the anonymizer for path normalization.
        Defaults to CWD if not provided.
    max_entries_per_scan:
        Hard cap on candidate log entries (error/critical/qualifying-warning)
        processed per ``discover()`` call.  Protects against starvation of
        real errors under noisy benign traffic.
    max_findings_per_scan:
        Hard cap on unique findings produced per ``discover()`` call.
    max_total_lines:
        Hard cap on total lines parsed (including benign INFO/DEBUG lines) per
        ``discover()`` call.  Bounds CPU and latency under extremely
        noisy-but-benign log traffic.  Default ``200_000``.
    max_scan_seconds:
        Wall-clock cap in seconds for a single ``discover()`` call.  Scan
        stops gracefully and returns findings collected so far.  Default 30.

    Attributes
    ----------
    last_truncated:
        ``datetime`` of the most recent scan that was cut short by any cap,
        or ``None`` if no truncation has occurred.
    last_truncated_reason:
        Short reason string for the most recent truncation, e.g.
        ``"max_total_lines"``, ``"max_scan_seconds"``,
        ``"max_entries_per_scan"``, or ``"max_findings_per_scan"``.
        ``None`` if no truncation has occurred.
    """

    def __init__(
        self,
        log_root: Path | None = None,
        repo_root: Path | None = None,
        max_entries_per_scan: int = DEFAULT_MAX_ENTRIES_PER_SCAN,
        max_findings_per_scan: int = DEFAULT_MAX_FINDINGS_PER_SCAN,
        max_total_lines: int = DEFAULT_MAX_TOTAL_LINES,
        max_scan_seconds: float = DEFAULT_MAX_SCAN_SECONDS,
    ) -> None:
        self._log_root = log_root
        self._repo_root = (repo_root or Path.cwd()).resolve()
        self._max_entries = max_entries_per_scan
        self._max_findings = max_findings_per_scan
        self._max_total_lines = max_total_lines
        self._max_scan_seconds = max_scan_seconds

        # Truncation telemetry — updated each time a cap is hit during discover()
        self.last_truncated: datetime | None = None
        self.last_truncated_reason: str | None = None

    @property
    def name(self) -> str:
        """Source identifier: ``"log_scanner"``."""
        return "log_scanner"

    async def discover(self, lookback_minutes: int) -> list[QaFinding]:
        """Scan log files and return aggregated findings.

        Parameters
        ----------
        lookback_minutes:
            Only include log entries at or after ``now() - lookback_minutes``.

        Returns
        -------
        list[QaFinding]
            Deduplicated, aggregated findings.
        """
        now = datetime.now(UTC)
        cutoff = now - timedelta(minutes=lookback_minutes)
        log_root = _get_log_root(self._log_root)
        scan_start = time.monotonic()

        # fingerprint -> aggregation state
        aggregated: dict[str, _FindingAccumulator] = {}

        total_lines_parsed = 0
        entries_processed = 0
        malformed_count = 0
        truncated_entries = False
        truncated_findings = False
        truncated_total_lines = False
        truncated_time = False

        for subdir_name in _LOG_SUBDIRS:
            subdir = log_root / subdir_name
            if not subdir.exists():
                logger.debug("LogScannerSource: skipping missing directory %s", subdir)
                continue

            # Shuffle file order to avoid deterministic starvation of later
            # files/subdirectories under sustained benign load.
            log_files = list(subdir.glob("*.log"))
            random.shuffle(log_files)

            for log_file in log_files:
                # Exclude QA staffer's own log
                if log_file.name == _QA_LOG_EXCLUDE:
                    continue

                butler_name = _butler_name_from_filename(log_file)
                raw_lines = _read_file_tail(log_file, cutoff)

                for line in raw_lines:
                    # Wall-clock cap — checked on every line to bound latency.
                    if time.monotonic() - scan_start >= self._max_scan_seconds:
                        truncated_time = True
                        break

                    # Total-lines cap — counts every parsed line regardless of level.
                    if total_lines_parsed >= self._max_total_lines:
                        truncated_total_lines = True
                        break

                    # Candidate-entries cap — counts only temporally-valid entries.
                    if entries_processed >= self._max_entries:
                        truncated_entries = True
                        break

                    if len(aggregated) >= self._max_findings:
                        truncated_findings = True
                        break

                    total_lines_parsed += 1

                    entry = _parse_log_line(line, butler_name)
                    if entry is None:
                        malformed_count += 1
                        continue

                    # Temporal filter
                    if entry.timestamp < cutoff:
                        continue

                    # Budget only covers candidate error/warning entries;
                    # benign entries are skipped without consuming quota.
                    if not _should_include_entry(entry):
                        continue

                    entries_processed += 1

                    # Build finding fields
                    exception_type = entry.exception or "unknown"
                    call_site = _extract_call_site(entry)

                    # Fingerprint on the full sanitized event (up to fingerprint.py's 500-char
                    # internal cap) so this source stays compatible with canonical paths.
                    # Store a shorter anonymized summary for display/storage only.
                    sanitized_event_for_fp = _sanitize_message(entry.event)
                    raw_summary = entry.event[:_MAX_SUMMARY_LEN]
                    sanitized_summary = _sanitize_message(raw_summary)
                    anon_summary = anonymize(sanitized_summary, self._repo_root)

                    fingerprint = _compute_hash(exception_type, call_site, sanitized_event_for_fp)

                    if fingerprint not in aggregated:
                        if len(aggregated) >= self._max_findings:
                            truncated_findings = True
                            break
                        severity = _level_to_severity(entry.level, exception_type, call_site)
                        # Extract trigger_source from raw log JSON for structured evidence
                        entry_trigger_source = entry.raw.get("trigger_source") or None
                        aggregated[fingerprint] = _FindingAccumulator(
                            fingerprint=fingerprint,
                            source_butler=entry.butler_name,
                            severity=severity,
                            exception_type=exception_type,
                            event_summary=anon_summary,
                            call_site=call_site,
                            source_file=log_file.name,
                            first_seen=entry.timestamp,
                            last_seen=entry.timestamp,
                            log_level=entry.level,
                            trigger_source=entry_trigger_source,
                        )
                    else:
                        acc = aggregated[fingerprint]
                        acc.occurrence_count += 1
                        if entry.timestamp < acc.first_seen:
                            acc.first_seen = entry.timestamp
                        if entry.timestamp > acc.last_seen:
                            acc.last_seen = entry.timestamp
                            # Always take trigger_source from the most recent log entry,
                            # even when it is None, so recency semantics remain consistent.
                            acc.trigger_source = entry.trigger_source

                hit_cap = (
                    truncated_entries
                    or truncated_findings
                    or truncated_total_lines
                    or truncated_time
                )
                if hit_cap:
                    break
            if truncated_entries or truncated_findings or truncated_total_lines or truncated_time:
                break

        if malformed_count > 0:
            logger.debug("LogScannerSource: skipped %d malformed JSON lines", malformed_count)
        if truncated_time:
            logger.warning(
                "LogScannerSource: scan wall-clock limit reached after %.1fs"
                " (max_scan_seconds=%.1f); %d lines parsed, %d candidate entries,"
                " returning partial results",
                time.monotonic() - scan_start,
                self._max_scan_seconds,
                total_lines_parsed,
                entries_processed,
            )
            self.last_truncated = now
            self.last_truncated_reason = "max_scan_seconds"
        elif truncated_total_lines:
            logger.warning(
                "LogScannerSource: total-lines cap reached (%d lines;"
                " max_total_lines=%d); returning partial results",
                total_lines_parsed,
                self._max_total_lines,
            )
            self.last_truncated = now
            self.last_truncated_reason = "max_total_lines"
        elif truncated_entries:
            logger.warning(
                "LogScannerSource: truncated at %d candidate entries (max_entries_per_scan=%d)",
                entries_processed,
                self._max_entries,
            )
            self.last_truncated = now
            self.last_truncated_reason = "max_entries_per_scan"
        elif truncated_findings:
            logger.warning(
                "LogScannerSource: finding cap reached (%d); some findings may be missed",
                self._max_findings,
            )
            self.last_truncated = now
            self.last_truncated_reason = "max_findings_per_scan"

        findings = [acc.to_finding(now) for acc in aggregated.values()]
        return findings


# ---------------------------------------------------------------------------
# Internal accumulator
# ---------------------------------------------------------------------------


@dataclass
class _FindingAccumulator:
    """Internal state for aggregating log entries with the same fingerprint.

    Retains the log level and trigger_source from the first occurrence so that
    the aggregated ``QaFinding`` carries structured evidence for investigation
    agents.
    """

    fingerprint: str
    source_butler: str
    severity: int
    exception_type: str
    event_summary: str
    call_site: str
    source_file: str
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int = 1
    # Structured evidence fields — set from the first matching log entry
    log_level: str = field(default="")
    trigger_source: str | None = field(default=None)

    def to_finding(self, now: datetime) -> QaFinding:
        """Build an aggregated QaFinding with structured evidence."""
        structured_evidence: dict = {
            "source": "log_scanner",
            "log_file": Path(self.source_file).stem,
            "level": self.log_level,
        }
        if self.trigger_source is not None:
            structured_evidence["trigger_source"] = self.trigger_source
        return QaFinding(
            fingerprint=self.fingerprint,
            source_type="log_scanner",
            source_butler=self.source_butler,
            severity=self.severity,
            exception_type=self.exception_type,
            event_summary=self.event_summary,
            call_site=self.call_site,
            occurrence_count=self.occurrence_count,
            first_seen=self.first_seen,
            last_seen=self.last_seen,
            timestamp=now,
            source_file=self.source_file,
            source_session_trigger_source=self.trigger_source,
            structured_evidence=structured_evidence,
        )
