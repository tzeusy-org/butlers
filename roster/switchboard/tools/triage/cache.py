"""Triage rule cache with atomic refresh and fail-open semantics.

Implements the runtime cache contract from spec §5.5:
- Atomic rule set swap on refresh.
- Fail-open (pass_through) when cache reload fails.
- Invalid rows skipped and logged.
- Supports event-driven invalidation and periodic reload.

The cache is process-level. One cache instance per switchboard process is expected.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Default periodic refresh interval (seconds) per spec §5.5
_DEFAULT_REFRESH_INTERVAL_S = 60

# Required fields for a valid rule row
_REQUIRED_RULE_FIELDS = frozenset({"id", "rule_type", "condition", "action", "priority"})
_VALID_RULE_TYPES = frozenset({"sender_domain", "sender_address", "header_condition", "mime_type"})
_VALID_SIMPLE_ACTIONS = frozenset({"skip", "metadata_only", "low_priority_queue", "pass_through"})


def _validate_rule_row(row: dict[str, Any]) -> str | None:
    """Validate a triage rule row from the database.

    Returns None if valid, or a human-readable error string if invalid.
    Invalid rows are skipped during cache load — they do not abort the load.
    """
    for field in _REQUIRED_RULE_FIELDS:
        if row.get(field) is None:
            return f"missing required field: {field!r}"

    rule_type = str(row["rule_type"])
    if rule_type not in _VALID_RULE_TYPES:
        return f"invalid rule_type: {rule_type!r}"

    action = str(row["action"])
    if action not in _VALID_SIMPLE_ACTIONS and not action.startswith("route_to:"):
        return f"invalid action: {action!r}"

    condition = row.get("condition")
    if not isinstance(condition, dict):
        return f"condition must be a dict, got {type(condition).__name__}"

    return None


class TriageRuleCache:
    """In-memory cache for active triage rules.

    Usage pattern:
        cache = TriageRuleCache(pool)
        await cache.load()
        rules = cache.get_rules()

    Refresh contract (spec §5.5):
    - load() is atomic: either replaces full rule set or leaves stale cache.
    - On load failure: stale rules are preserved (fail-open semantics).
    - Invalid rows are skipped, not propagated.
    - available property: True if cache has ever successfully loaded.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        refresh_interval_s: int = _DEFAULT_REFRESH_INTERVAL_S,
    ) -> None:
        self._pool = pool
        self._refresh_interval_s = refresh_interval_s

        # Active rule set — sorted by (priority ASC, created_at ASC, id ASC)
        self._rules: list[dict[str, Any]] = []

        # Whether at least one successful load has occurred
        self._loaded: bool = False

        # Timestamp of last successful load (monotonic)
        self._last_load_time: float = 0.0

        # Background refresh task (optional; started by start_background_refresh)
        self._refresh_task: asyncio.Task[None] | None = None

        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        """True if the cache has at least one successful load."""
        return self._loaded

    @property
    def last_load_time(self) -> float:
        """Monotonic timestamp of the last successful load, or 0.0 if never loaded."""
        return self._last_load_time

    def get_rules(self) -> list[dict[str, Any]]:
        """Return the current active rule set.

        Returns the cached rule list (possibly empty if loaded but no active rules).
        Thread-safe: this is a simple list read; Python GIL protects the reference swap.
        """
        return self._rules

    async def load(self, pool: asyncpg.Pool | None = None) -> bool:
        """Load (or reload) active triage rules from the database.

        Fetches active (enabled=true, deleted_at IS NULL) rules ordered by
        (priority ASC, created_at ASC, id ASC) per spec §5.4.

        On success: atomically replaces internal rule set, returns True.
        On failure: leaves existing rule set unchanged (fail-open), returns False.
        Invalid rows are skipped with a warning log.

        Parameters
        ----------
        pool:
            Optional alternative pool; uses instance pool if None.

        Returns
        -------
        bool
            True if rules were loaded successfully, False on error.
        """
        active_pool = pool or self._pool
        try:
            rows = await active_pool.fetch(
                """
                SELECT
                    id::text AS id,
                    rule_type,
                    condition,
                    action,
                    priority,
                    enabled,
                    created_by,
                    created_at::text AS created_at,
                    updated_at::text AS updated_at
                FROM triage_rules
                WHERE enabled = TRUE
                  AND deleted_at IS NULL
                ORDER BY priority ASC, created_at ASC, id ASC
                """
            )
        except Exception as exc:
            logger.error(
                "Triage rule cache reload failed (database error): %s; "
                "preserving stale rule set (fail-open)",
                exc,
                exc_info=True,
            )
            return False

        valid_rules: list[dict[str, Any]] = []
        skipped = 0

        for row in rows:
            # asyncpg returns Record objects; convert to dict for evaluator
            raw: dict[str, Any] = dict(row)

            # condition may come back as a dict from asyncpg JSONB parsing
            if not isinstance(raw.get("condition"), dict):
                logger.warning(
                    "Triage rule id=%s: condition is not a dict (%r); skipping",
                    raw.get("id"),
                    type(raw.get("condition")).__name__,
                )
                skipped += 1
                continue

            error = _validate_rule_row(raw)
            if error:
                logger.warning(
                    "Triage rule id=%s failed validation: %s; skipping",
                    raw.get("id"),
                    error,
                )
                skipped += 1
                continue

            valid_rules.append(raw)

        # Atomic swap: replace rule set only on success
        async with self._lock:
            self._rules = valid_rules
            self._loaded = True
            self._last_load_time = time.monotonic()

        logger.info(
            "Triage rule cache loaded: %d active rules (%d skipped invalid)",
            len(valid_rules),
            skipped,
        )
        return True

    def invalidate(self) -> None:
        """Mark cache as stale to force next get to reload.

        This does NOT immediately reload — call load() explicitly or wait for
        the background refresh to pick up the invalidation.

        Designed for event-driven invalidation triggered by rule mutations.
        """
        self._last_load_time = 0.0
        logger.debug("Triage rule cache invalidated (next load will refresh)")

    def needs_refresh(self) -> bool:
        """True if cache needs a periodic refresh."""
        if not self._loaded:
            return True
        age = time.monotonic() - self._last_load_time
        return age >= self._refresh_interval_s

    async def start_background_refresh(self) -> None:
        """Start a background coroutine that periodically refreshes the rule cache.

        Does nothing if already running. The task refreshes only when
        needs_refresh() is True. On failure, the stale cache is preserved.
        """
        if self._refresh_task is not None and not self._refresh_task.done():
            return

        self._refresh_task = asyncio.create_task(
            self._background_refresh_loop(),
            name="triage-rule-cache-refresh",
        )

    async def stop_background_refresh(self) -> None:
        """Stop the background refresh task."""
        task = self._refresh_task
        if task is None or task.done():
            self._refresh_task = None
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._refresh_task = None

    async def _background_refresh_loop(self) -> None:
        """Periodic refresh loop. Runs until cancelled."""
        # Initial load if not yet available
        if not self._loaded:
            await self.load()

        while True:
            try:
                await asyncio.sleep(self._refresh_interval_s)
                if self.needs_refresh():
                    await self.load()
            except asyncio.CancelledError:
                logger.debug("Triage rule cache background refresh loop cancelled")
                return
            except Exception:
                logger.exception("Unexpected error in triage rule cache refresh loop; continuing")


__all__ = ["TriageRuleCache"]
