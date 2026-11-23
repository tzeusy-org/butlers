"""Bounded per-item retry for connector ingest failures (bu-dhloi1.149).

A connector that fails to ingest an item must not move its cursor, window or
checkpoint past that item: the next cycle reads it again. Retrying forever
would wedge the source behind one poison item, so each item gets a bounded
number of attempts. When they are spent, the connector parks the item as a
replayable ``connectors.filtered_events`` error row carrying the envelope it
tried (with its idempotency key) and only then moves on. Nothing is dropped
silently.

The count lives in memory. A restart starts the count again, which only adds
attempts; the cursor still holds the item.
"""

from __future__ import annotations

DEFAULT_MAX_ATTEMPTS = 3


class IngestRetryBudget:
    """Failed-attempt counts per item key, bounded at ``max_attempts``."""

    def __init__(self, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.max_attempts = max_attempts
        self._attempts: dict[str, int] = {}

    def failed(self, key: str) -> bool:
        """Record one failed attempt. True when the budget is spent: park the item.

        A spent key is forgotten, so the parked item does not pin memory.
        """
        attempts = self._attempts.get(key, 0) + 1
        if attempts >= self.max_attempts:
            self._attempts.pop(key, None)
            return True
        self._attempts[key] = attempts
        return False

    def succeeded(self, key: str) -> None:
        """Forget an item that went through."""
        self._attempts.pop(key, None)

    def attempts(self, key: str) -> int:
        """Failed attempts recorded so far for ``key`` (0 once spent or succeeded)."""
        return self._attempts.get(key, 0)

    def __len__(self) -> int:
        return len(self._attempts)
