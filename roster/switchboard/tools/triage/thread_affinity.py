"""Thread-affinity lookup module for the Switchboard ingestion pipeline.

Implements the lookup algorithm from docs/switchboard/thread_affinity_routing.md §4.

Pipeline position (per spec §2):
  1. Unified ingestion policy evaluation (IngestionPolicyEvaluator)
  2. Thread-affinity global/thread override checks  <- this module
  3. Thread-affinity lookup in routing history       <- this module
  4. LLM classification fallback

Only applies when:
  - source_channel = 'email'
  - thread_id is present and non-empty

The caller (ingest pipeline) is responsible for integrating the lookup result
into the policy evaluation (via thread_affinity_target param).

Thread-affinity settings are fetched once per call. Callers that need high
throughput should maintain a short-lived cache (TTL << affinity TTL) at the
integration layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import asyncpg
from opentelemetry import metrics

logger = logging.getLogger(__name__)

_METER_NAME = "butlers.switchboard"

# Allowed miss reason values for thread affinity
_ALLOWED_AFFINITY_MISS_REASONS = frozenset(
    {"no_thread_id", "no_history", "conflict", "disabled", "error", "stale"}
)


class ThreadAffinityTelemetry:
    """OpenTelemetry metrics for thread-affinity routing lookups.

    Implements the metric contract from docs/switchboard/thread_affinity_routing.md S7.

    Metrics:
      - butlers.switchboard.thread_affinity.hit (counter)
      - butlers.switchboard.thread_affinity.miss (counter, with reason attribute)
      - butlers.switchboard.thread_affinity.stale (counter)
    """

    def __init__(self) -> None:
        meter = metrics.get_meter(_METER_NAME)

        self.hit = meter.create_counter(
            "butlers.switchboard.thread_affinity.hit",
            unit="1",
            description=(
                "Number of email thread affinity lookups that produced a routing decision "
                "without LLM classification."
            ),
        )

        self.miss = meter.create_counter(
            "butlers.switchboard.thread_affinity.miss",
            unit="1",
            description=(
                "Number of email thread affinity lookups that did not produce a route "
                "and fell through to LLM classification."
            ),
        )

        self.stale = meter.create_counter(
            "butlers.switchboard.thread_affinity.stale",
            unit="1",
            description=(
                "Number of email threads where historical routing exists but is "
                "outside the configured TTL window."
            ),
        )

    def record_hit(self, *, destination_butler: str) -> None:
        """Record a successful affinity hit."""
        self.hit.add(
            1,
            {
                "source": "email",
                "destination_butler": (
                    str(destination_butler)[:64] if destination_butler else "unknown"
                ),
                "policy_tier": "affinity",
                "schema_version": "thread_affinity.v1",
            },
        )

    def record_miss(self, *, reason: str) -> None:
        """Record an affinity miss."""
        safe_reason = reason if reason in _ALLOWED_AFFINITY_MISS_REASONS else "no_history"
        self.miss.add(
            1,
            {
                "source": "email",
                "reason": safe_reason,
                "schema_version": "thread_affinity.v1",
            },
        )

    def record_stale(self) -> None:
        """Record a stale affinity match (history exists but outside TTL)."""
        self.stale.add(
            1,
            {
                "source": "email",
                "schema_version": "thread_affinity.v1",
            },
        )


_THREAD_AFFINITY_TELEMETRY: ThreadAffinityTelemetry | None = None


def get_thread_affinity_telemetry() -> ThreadAffinityTelemetry:
    """Return the process-level thread affinity telemetry singleton."""
    global _THREAD_AFFINITY_TELEMETRY
    if _THREAD_AFFINITY_TELEMETRY is None:
        _THREAD_AFFINITY_TELEMETRY = ThreadAffinityTelemetry()
    return _THREAD_AFFINITY_TELEMETRY


def reset_thread_affinity_telemetry_for_tests() -> None:
    """Test helper to reset the thread affinity telemetry singleton."""
    global _THREAD_AFFINITY_TELEMETRY
    _THREAD_AFFINITY_TELEMETRY = None


# Override value prefix for forced butler routing
_FORCE_PREFIX = "force:"

# Special override value for disabled threads
_DISABLED_OVERRIDE = "disabled"

# Default TTL if settings row is missing
_DEFAULT_TTL_DAYS = 30


class AffinityOutcome(Enum):
    """Result classification for thread-affinity lookup."""

    HIT = "hit"
    """Found exactly one matching butler within TTL."""

    MISS_NO_THREAD_ID = "miss_no_thread_id"
    """thread_id was empty/missing — affinity not attempted."""

    MISS_NO_HISTORY = "miss_no_history"
    """No routing history found for this thread within TTL."""

    MISS_CONFLICT = "miss_conflict"
    """Multiple distinct butlers found in routing history — conflict."""

    MISS_STALE = "miss_stale"
    """Historical match exists but latest row is older than TTL."""

    MISS_DISABLED_GLOBAL = "miss_disabled_global"
    """Affinity globally disabled via settings."""

    MISS_DISABLED_THREAD = "miss_disabled_thread"
    """Affinity disabled for this specific thread via override."""

    FORCE_OVERRIDE = "force_override"
    """Thread-specific force override active."""

    MISS_ERROR = "miss_error"
    """Lookup or storage error; fell through (fail-open)."""

    @property
    def produces_route(self) -> bool:
        """True when this outcome resolves a routing target (no LLM needed)."""
        return self in (AffinityOutcome.HIT, AffinityOutcome.FORCE_OVERRIDE)

    @property
    def is_miss(self) -> bool:
        """True when this outcome falls through to LLM classification."""
        return not self.produces_route

    @property
    def telemetry_reason(self) -> str:
        """Low-cardinality reason tag for miss metrics.

        Only meaningful when is_miss=True.
        """
        _reason_map = {
            AffinityOutcome.MISS_NO_THREAD_ID: "no_thread_id",
            AffinityOutcome.MISS_NO_HISTORY: "no_history",
            AffinityOutcome.MISS_CONFLICT: "conflict",
            AffinityOutcome.MISS_STALE: "stale",
            AffinityOutcome.MISS_DISABLED_GLOBAL: "disabled",
            AffinityOutcome.MISS_DISABLED_THREAD: "disabled",
            AffinityOutcome.MISS_ERROR: "error",
        }
        return _reason_map.get(self, "no_history")


@dataclass(frozen=True)
class AffinityResult:
    """Result of a thread-affinity lookup.

    Attributes
    ----------
    outcome:
        Classification of this lookup result.
    target_butler:
        The resolved butler name when outcome is HIT or FORCE_OVERRIDE. None otherwise.
    """

    outcome: AffinityOutcome
    target_butler: str | None = None


@dataclass(frozen=True)
class ThreadAffinitySettings:
    """In-memory representation of thread_affinity_settings row."""

    enabled: bool
    ttl_days: int
    thread_overrides: dict[str, str]

    @classmethod
    def defaults(cls) -> ThreadAffinitySettings:
        """Return safe defaults when the settings row cannot be loaded."""
        return cls(enabled=True, ttl_days=_DEFAULT_TTL_DAYS, thread_overrides={})


async def load_settings(pool: asyncpg.Pool) -> ThreadAffinitySettings:
    """Load thread-affinity settings from the database.

    On error, returns safe defaults (fail-open: affinity enabled, TTL=30).

    Parameters
    ----------
    pool:
        Active asyncpg pool for the Switchboard DB.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT thread_affinity_enabled, thread_affinity_ttl_days, thread_overrides
            FROM thread_affinity_settings
            WHERE id = 1
            """
        )
    except Exception:
        logger.exception("Failed to load thread_affinity_settings; using defaults (fail-open)")
        return ThreadAffinitySettings.defaults()

    if row is None:
        logger.warning("thread_affinity_settings row not found; using defaults")
        return ThreadAffinitySettings.defaults()

    overrides: dict[str, str] = {}
    raw_overrides = row["thread_overrides"]
    if isinstance(raw_overrides, dict):
        overrides = {str(k): str(v) for k, v in raw_overrides.items()}

    return ThreadAffinitySettings(
        enabled=bool(row["thread_affinity_enabled"]),
        ttl_days=int(row["thread_affinity_ttl_days"]),
        thread_overrides=overrides,
    )


def _check_override(thread_id: str, settings: ThreadAffinitySettings) -> AffinityResult | None:
    """Check for a thread-specific override.

    Returns an AffinityResult if an override applies, None otherwise.

    Per spec §4 step 2:
    - "disabled" override → miss (disabled)
    - "force:<butler>" override → force_override with target_butler
    """
    override_value = settings.thread_overrides.get(thread_id)
    if override_value is None:
        return None

    if override_value == _DISABLED_OVERRIDE:
        return AffinityResult(outcome=AffinityOutcome.MISS_DISABLED_THREAD)

    if override_value.startswith(_FORCE_PREFIX):
        target = override_value[len(_FORCE_PREFIX) :]
        if target:
            return AffinityResult(
                outcome=AffinityOutcome.FORCE_OVERRIDE,
                target_butler=target,
            )
        # Malformed force: override — treat as no override
        logger.warning(
            "Malformed thread override for thread_id (value=%r); ignoring",
            override_value,
        )
    else:
        logger.warning(
            "Unknown thread override value (value=%r); ignoring",
            override_value,
        )

    return None


async def lookup_thread_affinity(
    pool: asyncpg.Pool,
    thread_id: str | None,
    source_channel: str,
    *,
    settings: ThreadAffinitySettings | None = None,
) -> AffinityResult:
    """Look up thread-affinity routing for an incoming email.

    Implements the algorithm from spec §4 in order:
      1. Skip if globally disabled.
      2. Check thread-specific override (disabled or force).
      3. Skip if thread_id is missing/empty.
      4. Query routing history within TTL window.
      5. Decide: hit (1 butler), conflict (>1 distinct butler), miss (0 rows).

    Telemetry is recorded for each outcome path (hit, miss, stale).

    Parameters
    ----------
    pool:
        Active asyncpg pool for the Switchboard DB.
    thread_id:
        External thread identity from ingest.v1 event.external_thread_id.
        None or empty string → miss (no_thread_id).
    source_channel:
        Source channel string. Must be 'email' for affinity to apply.
        Non-email channels always return miss (no_thread_id).
    settings:
        Pre-loaded settings; if None, will be loaded from DB.

    Returns
    -------
    AffinityResult
        Lookup outcome with optional target_butler.
    """
    telemetry = get_thread_affinity_telemetry()

    # Affinity only applies to email channel
    if source_channel != "email":
        return AffinityResult(outcome=AffinityOutcome.MISS_NO_THREAD_ID)

    # Load settings if not pre-loaded
    if settings is None:
        try:
            settings = await load_settings(pool)
        except Exception:
            logger.exception("Unexpected error loading thread affinity settings; failing open")
            telemetry.record_miss(reason="error")
            return AffinityResult(outcome=AffinityOutcome.MISS_ERROR)

    # Step 1: Global disable check
    if not settings.enabled:
        telemetry.record_miss(reason="disabled")
        return AffinityResult(outcome=AffinityOutcome.MISS_DISABLED_GLOBAL)

    # Step 2: Thread-specific override check (before thread_id validation)
    # Override can be applied even when thread_id might be valid.
    # But overrides require a thread_id to be keyed — check thread_id presence first
    # to determine whether to look up overrides.
    clean_thread_id: str | None = thread_id.strip() if thread_id else None

    if clean_thread_id:
        override_result = _check_override(clean_thread_id, settings)
        if override_result is not None:
            if override_result.outcome == AffinityOutcome.FORCE_OVERRIDE:
                telemetry.record_hit(destination_butler=override_result.target_butler or "unknown")
            else:
                telemetry.record_miss(reason=override_result.outcome.telemetry_reason)
            return override_result

    # Step 3: Missing thread_id → miss
    if not clean_thread_id:
        telemetry.record_miss(reason="no_thread_id")
        return AffinityResult(outcome=AffinityOutcome.MISS_NO_THREAD_ID)

    # Step 4: Query routing history within TTL
    try:
        rows = await pool.fetch(
            """
            SELECT
                target_butler,
                MAX(created_at) AS last_routed_at
            FROM routing_log
            WHERE source_channel = 'email'
              AND thread_id = $1
              AND created_at >= NOW() - ($2 || ' days')::INTERVAL
            GROUP BY target_butler
            ORDER BY last_routed_at DESC
            LIMIT 2
            """,
            clean_thread_id,
            str(settings.ttl_days),
        )
    except Exception:
        logger.exception(
            "Thread affinity lookup failed for thread_id (DB error); failing open (miss)"
        )
        telemetry.record_miss(reason="error")
        return AffinityResult(outcome=AffinityOutcome.MISS_ERROR)

    # Step 5: Decision
    if len(rows) == 0:
        # Check if there is stale history (outside TTL window)
        stale = await _has_stale_history(pool, clean_thread_id, settings.ttl_days)
        if stale:
            telemetry.record_stale()
            return AffinityResult(outcome=AffinityOutcome.MISS_STALE)
        telemetry.record_miss(reason="no_history")
        return AffinityResult(outcome=AffinityOutcome.MISS_NO_HISTORY)

    if len(rows) >= 2:
        # Conflict: multiple distinct butlers in this thread
        telemetry.record_miss(reason="conflict")
        return AffinityResult(outcome=AffinityOutcome.MISS_CONFLICT)

    # Exactly 1 row: hit
    target_butler = str(rows[0]["target_butler"])
    telemetry.record_hit(destination_butler=target_butler)
    return AffinityResult(
        outcome=AffinityOutcome.HIT,
        target_butler=target_butler,
    )


async def _has_stale_history(
    pool: asyncpg.Pool,
    thread_id: str,
    ttl_days: int,
) -> bool:
    """Check whether routing history exists for this thread but is outside the TTL window.

    Returns True if stale history found, False otherwise (or on error).
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT 1
            FROM routing_log
            WHERE source_channel = 'email'
              AND thread_id = $1
              AND created_at < NOW() - ($2 || ' days')::INTERVAL
            LIMIT 1
            """,
            thread_id,
            str(ttl_days),
        )
        return row is not None
    except Exception:
        logger.debug(
            "Failed to check stale history for thread; treating as no-history",
            exc_info=True,
        )
        return False


__all__ = [
    "AffinityOutcome",
    "AffinityResult",
    "ThreadAffinitySettings",
    "ThreadAffinityTelemetry",
    "get_thread_affinity_telemetry",
    "load_settings",
    "lookup_thread_affinity",
    "reset_thread_affinity_telemetry_for_tests",
]
