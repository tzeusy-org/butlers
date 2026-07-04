"""Shared primitive for the degraded-envelope convention.

See CLAUDE.md "API Conventions -- Degraded-Mode Response Envelope": a
fan-out/aggregation endpoint must never let one source's failure silently
zero-fill, empty-fill, or vanish from the result. ``DegradedSources``
accumulates which named sources failed during a request so that state can be
threaded into the response's ``ApiMeta``/``PaginationMeta`` bag (or a
bespoke boolean field, mirroring the existing ``aggregates_available``
convention) instead of being swallowed by a bare ``except: continue``.
"""

from __future__ import annotations

import logging


class DegradedSources:
    """Accumulates which fan-out sources failed during one request.

    Use inside a ``for source in sources: try: ... except Exception:
    tracker.mark(source)`` loop, then surface ``tracker.failed`` (bool) and
    ``tracker.names`` (list[str]) in the response envelope so a partial or
    failed source is never mistaken for a truthful empty/zero result.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self.names: list[str] = []

    def mark(self, source: str, *, msg: str = "Fan-out source failed") -> None:
        self.names.append(source)
        self._logger.warning("%s: source=%s", msg, source, exc_info=True)

    @property
    def failed(self) -> bool:
        return bool(self.names)
