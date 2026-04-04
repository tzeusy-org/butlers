"""DiscoverySource protocol — the pluggable interface for QA error detection.

Any class implementing ``DiscoverySource`` can be registered with the QA
staffer module and polled during each patrol cycle.  The protocol makes no
assumptions about how errors are found: sources may read log files, query
SQL views, drain in-memory buffers, or probe external systems.

No LLM invocation is permitted inside ``discover()`` — all filtering must
use tool-based approaches (regex, SQL, file parsing) to avoid context
wastage.

Usage
-----
To implement a new discovery source::

    class MySource:
        @property
        def name(self) -> str:
            return "my_source"

        async def discover(self, lookback_minutes: int) -> list[QaFinding]:
            ...

    # Register at QA staffer startup:
    staffer.register_source(MySource())
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from butlers.core.qa.models import QaFinding


@runtime_checkable
class DiscoverySource(Protocol):
    """Protocol for pluggable QA error detection sources.

    Attributes
    ----------
    name:
        Short identifier for this source.  Used in patrol records
        (``sources_polled``), metrics labels, and log messages.
        Example values: ``"log_scanner"``, ``"session_records"``,
        ``"butler_reports"``.

    Methods
    -------
    discover(lookback_minutes):
        Perform a scan and return normalized findings.  Must be async.
        All filtering is tool-based (zero LLM calls).
    """

    @property
    def name(self) -> str:
        """Short source identifier string."""
        ...

    async def discover(self, lookback_minutes: int) -> list[QaFinding]:
        """Scan for error findings within the lookback window.

        Parameters
        ----------
        lookback_minutes:
            How far back to scan, relative to the current time.
            E.g. ``15`` means "only include errors from the last 15 minutes".

        Returns
        -------
        list[QaFinding]
            Zero or more normalized findings.  Multiple log entries with the
            same fingerprint are aggregated into a single finding with
            ``occurrence_count > 1``.
        """
        ...
