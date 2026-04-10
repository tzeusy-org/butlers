"""ButlerReportsSource — reactive relay discovery source.

Receives findings from butlers via the QA staffer's ``report_finding`` MCP
tool (routed through Switchboard's ``route()`` tool).  Buffers findings
in-memory and drains the buffer on each patrol tick.

The buffer is volatile — findings that were not yet processed by a patrol
cycle are lost on daemon restart.  This is acceptable because:

  1. The ``session_records`` source will rediscover failures from the DB.
  2. The ``log_scanner`` source will find them in logs.
  3. The triage layer deduplicates by fingerprint.

The ``report_finding`` MCP tool handler on the QA staffer module calls
``ButlerReportsSource.accept()`` to enqueue incoming findings.

Spec reference
--------------
openspec/changes/qa-staffer/specs/staffer-qa/spec.md (V1 Discovery Sources)
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import UTC, datetime

from butlers.core.qa.models import QaFinding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default maximum number of buffered reactive findings.
#: When the buffer is full, the oldest entries are dropped with a WARNING.
DEFAULT_MAX_REACTIVE_BUFFER = 50


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ButlerReportsSource:
    """In-memory buffer for reactive butler-reported findings.

    Thread-safety: ``accept()`` may be called from MCP tool handlers
    (which run in asyncio tasks).  All mutations use an asyncio Lock.

    Parameters
    ----------
    max_buffer:
        Maximum number of findings to buffer.  When exceeded, the oldest
        entries are dropped with a WARNING.
    """

    def __init__(self, max_buffer: int = DEFAULT_MAX_REACTIVE_BUFFER) -> None:
        self._max_buffer = max_buffer
        self._buffer: deque[QaFinding] = deque()
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Source identifier: ``"butler_reports"``."""
        return "butler_reports"

    async def accept(
        self,
        fingerprint: str,
        exception_type: str,
        call_site: str,
        severity: int,
        event_summary: str,
        source_butler: str,
        context: str | None = None,
        trigger_source: str | None = None,
    ) -> None:
        """Enqueue a finding relayed from a butler via the report_finding tool.

        Called by the QA staffer's ``report_finding`` MCP tool handler.
        Returns immediately after enqueueing (``report_finding`` is
        synchronous from the caller's perspective).

        If the buffer is at capacity, the oldest entry is dropped to make
        room for the new one.

        Parameters
        ----------
        fingerprint:
            Canonical fingerprint computed by the QA module handler via
            ``compute_fingerprint_from_report``.  Always authoritative; the
            caller-supplied hint has already been discarded before this point.
        exception_type:
            Fully qualified exception class name.
        call_site:
            ``<file>:<function>`` call site.
        severity:
            Canonical integer severity score (0=critical, 1=high, 2=medium,
            3=low, 4=info).  Already validated and clamped to 0–4 by the
            QA module handler before this call.
        event_summary:
            Sanitized error event summary (already passed through
            ``anonymize()`` at the module tool layer — the ``context``
            field is sensitive and must not be stored directly).
        source_butler:
            Name of the reporting butler.
        context:
            Optional context string (declared sensitive; not stored in
            ``event_summary``).
        trigger_source:
            Optional ``trigger_source`` value from the calling session (e.g.
            ``"healing"`` or ``"qa"``).  Stored as
            ``source_session_trigger_source`` on the finding for QA
            self-recursion suppression.
        """
        now = datetime.now(UTC)
        finding = QaFinding(
            fingerprint=fingerprint,
            source_type="butler_reports",
            source_butler=source_butler,
            severity=severity,
            exception_type=exception_type,
            event_summary=event_summary,
            call_site=call_site,
            occurrence_count=1,
            first_seen=now,
            last_seen=now,
            timestamp=now,
            context=context,
            source_session_trigger_source=trigger_source,
        )

        async with self._lock:
            if len(self._buffer) >= self._max_buffer:
                dropped = self._buffer.popleft()
                logger.warning(
                    "ButlerReportsSource: buffer full (%d); dropped oldest finding "
                    "fingerprint=%s from butler=%s",
                    self._max_buffer,
                    dropped.fingerprint,
                    dropped.source_butler,
                )
            self._buffer.append(finding)

    async def discover(self, lookback_minutes: int) -> list[QaFinding]:
        """Drain the buffer and return all buffered findings.

        The ``lookback_minutes`` parameter is ignored for this source —
        reactive findings are already in the right time window by definition
        (they were reported since the last patrol tick).

        Returns
        -------
        list[QaFinding]
            All buffered findings (drained — buffer is empty after this call).
        """
        async with self._lock:
            findings = list(self._buffer)
            self._buffer.clear()
        return findings

    @property
    def buffer_size(self) -> int:
        """Current number of buffered findings (for monitoring)."""
        return len(self._buffer)

    def accept_sync(
        self,
        fingerprint: str,
        exception_type: str,
        call_site: str,
        severity: int,
        event_summary: str,
        source_butler: str,
        context: str | None = None,
        trigger_source: str | None = None,
    ) -> None:
        """Synchronous variant of ``accept()`` for use in non-async contexts.

        Uses a best-effort append without the asyncio lock.  Only suitable
        for single-threaded startup or testing contexts.
        """
        now = datetime.now(UTC)
        finding = QaFinding(
            fingerprint=fingerprint,
            source_type="butler_reports",
            source_butler=source_butler,
            severity=severity,
            exception_type=exception_type,
            event_summary=event_summary,
            call_site=call_site,
            occurrence_count=1,
            first_seen=now,
            last_seen=now,
            timestamp=now,
            context=context,
            source_session_trigger_source=trigger_source,
        )
        if len(self._buffer) >= self._max_buffer:
            self._buffer.popleft()
        self._buffer.append(finding)
