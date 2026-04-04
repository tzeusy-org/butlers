"""QA data models — shared across discovery sources, triage, and dispatch layers.

The central type is ``QaFinding``: a normalized, deduplicated error signal
produced by any discovery source.  Each finding carries a computed SHA-256
fingerprint so that findings from different sources (log scanner, session
records, butler reports) can be deduplicated by the triage layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class QaFinding:
    """A normalized error finding produced by a discovery source.

    Instances are produced by ``DiscoverySource.discover()`` implementations
    and consumed by the triage layer.  The ``fingerprint`` field provides a
    stable deduplication key across sources.

    Fields
    ------
    fingerprint:
        64-character lowercase SHA-256 hex string.  Computed from
        ``exception_type + call_site + normalized_event_summary``.
    source_type:
        Name of the discovery source that produced this finding
        (e.g. ``"log_scanner"``, ``"session_records"``, ``"butler_reports"``).
    source_butler:
        Name of the butler whose logs/sessions/reports contained the error.
    severity:
        Integer severity score.  0=critical, 1=high, 2=medium, 3=low.
    exception_type:
        Fully qualified exception class name, or ``"unknown"`` if unavailable.
    event_summary:
        First 200 chars of the error event/message, sanitized via
        ``anonymize()`` to strip PII before storage.
    call_site:
        ``<file>:<function>`` from the innermost application frame, or the
        logger module path for log-scanner findings.
    occurrence_count:
        Number of log/session entries with this fingerprint seen in the
        current scan window.
    first_seen:
        Timestamp of the earliest occurrence in this scan window.
    last_seen:
        Timestamp of the most recent occurrence in this scan window.
    timestamp:
        When the finding was produced (typically equals ``last_seen``).
    context:
        Optional free-form context string.  May contain sensitive data
        (declared sensitive in ``tool_metadata()``).
    source_file:
        For log-scanner findings: the log filename where the entry was found.
        Empty string for other source types.
    """

    fingerprint: str
    source_type: str
    source_butler: str
    severity: int
    exception_type: str
    event_summary: str
    call_site: str
    occurrence_count: int
    first_seen: datetime
    last_seen: datetime
    timestamp: datetime
    context: str | None = None
    source_file: str = field(default="")
