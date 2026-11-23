"""The owner-watch tick's one health record, and its reporter (bu-4c7nvp.1.5.2).

The scheduled tick reports how its last run went into one Switchboard ``state`` row. The
capability-health collector (``butlers.capability_health.collectors.probe_owner_watches``) reads
that row and publishes the ``owner_watches`` dependency, because the tick has no perception
writer role of its own.

The row holds only ``{state, reason, at}``: closed words and a time. It never carries a watch id,
a source, a predicate or any owner data.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Final

KEY: Final = "owner_watch:tick_health"
#: A tick that has not reported within two tick periods (5 minutes each) is no longer a claim.
MAX_AGE_SECONDS: Final = 600

UP: Final = "up"
DEGRADED: Final = "degraded"
TICK_OK: Final = "tick_ok"
NO_SNAPSHOT_PROVIDER: Final = "no_snapshot_provider"
SOURCES_UNREADABLE: Final = "sources_unreadable"
#: The closed vocabulary the collector accepts; anything else reads as unmeasured.
KNOWN: Final = frozenset(
    {(UP, TICK_OK), (DEGRADED, NO_SNAPSHOT_PROVIDER), (DEGRADED, SOURCES_UNREADABLE)}
)


class StateReporter:
    """Writes the tick's outcome to the Switchboard ``state`` table (one row, overwritten)."""

    def __init__(self, pool: Any, *, now: Any = lambda: datetime.now(UTC)) -> None:
        self._pool = pool
        self._now = now

    async def report(self, dependency: str, state: str, reason: str) -> None:
        value = {"state": state, "reason": reason, "at": self._now().isoformat()}
        await self._pool.execute(
            """
            INSERT INTO state (key, value, updated_at, version)
            VALUES ($1, $2::jsonb, now(), 1)
            ON CONFLICT (key) DO UPDATE
               SET value = EXCLUDED.value, updated_at = now(), version = state.version + 1
            """,
            KEY,
            json.dumps(value, separators=(",", ":")),
        )
