"""Insight broker — core business logic for the proactive insight engine.

Implements:
- ``propose_insight_candidate()`` — validate and insert a candidate
- ``delivery_cycle()`` — orchestrate the full insight delivery pipeline
- Supporting helpers: expire, cooldown filter, dedup, adaptive budget, etc.

Database tables used (all in the ``public`` schema):
- ``public.insight_candidates`` — staging table for proposed insights
- ``public.insight_settings``   — user verbosity/quiet-hours settings
- ``public.insight_cooldowns``  — cooldown entries by dedup_key
- ``public.insight_engagement`` — engagement tracking per delivered insight
"""

from __future__ import annotations

import json
import logging
import re
import zoneinfo
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_STATUSES = frozenset({"pending", "delivered", "expired", "filtered"})
TERMINAL_STATUSES = frozenset({"delivered", "expired", "filtered"})

# Default cooldown periods by priority range (days)
_DEFAULT_COOLDOWN_BY_PRIORITY: list[tuple[range, int]] = [
    (range(90, 101), 1),
    (range(70, 90), 7),
    (range(50, 70), 14),
    (range(1, 50), 30),
]

# Verbosity presets: name -> daily budget
VERBOSITY_BUDGETS: dict[str, int] = {
    "off": 0,
    "minimal": 1,
    "normal": 3,
    "verbose": 5,
}

# Compiled dedup_key pattern
_DEDUP_KEY_PATTERN = re.compile(r"^[^:]+:[^:]+:[^:]+(?::[^:]+)?$")


# ---------------------------------------------------------------------------
# DDL helpers (for tests)
# ---------------------------------------------------------------------------


async def create_insight_tables(pool: asyncpg.Pool) -> None:
    """Create all insight-related tables in the public schema.

    Intended for use in tests. In production these tables are created
    via Alembic migrations.
    """
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS insight_settings (
            id INTEGER PRIMARY KEY DEFAULT 1,
            verbosity TEXT NOT NULL DEFAULT 'minimal',
            custom_budget INTEGER,
            quiet_start INTEGER,
            quiet_end INTEGER,
            quiet_timezone TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS insight_candidates (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            origin_butler TEXT NOT NULL,
            priority INTEGER NOT NULL CHECK (priority >= 1 AND priority <= 100),
            category TEXT NOT NULL,
            dedup_key TEXT NOT NULL,
            cooldown_days INTEGER,
            expires_at TIMESTAMPTZ NOT NULL,
            message TEXT NOT NULL,
            channel TEXT,
            metadata JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            status TEXT NOT NULL DEFAULT 'pending',
            delivered_at TIMESTAMPTZ,
            delivery_attempt_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS insight_cooldowns (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            dedup_key TEXT NOT NULL,
            cooldown_until TIMESTAMPTZ NOT NULL,
            reason TEXT NOT NULL DEFAULT 'delivered',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS insight_engagement (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            insight_id UUID NOT NULL,
            delivered_at TIMESTAMPTZ NOT NULL,
            engaged BOOLEAN NOT NULL DEFAULT FALSE
        )
    """)
    await pool.execute("""
        CREATE INDEX IF NOT EXISTS idx_insight_engagement_delivered_engaged
        ON insight_engagement (delivered_at, engaged)
    """)


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------


async def get_insight_settings(pool: asyncpg.Pool) -> dict[str, Any]:
    """Return the current insight settings row, creating defaults if missing."""
    row = await pool.fetchrow("SELECT * FROM insight_settings WHERE id = 1")
    if row is None:
        await pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'minimal')
            ON CONFLICT (id) DO NOTHING
        """)
        row = await pool.fetchrow("SELECT * FROM insight_settings WHERE id = 1")
    return dict(row)


def _get_configured_budget(settings: dict[str, Any]) -> int:
    """Return the raw configured budget from settings (before adaptive reduction)."""
    verbosity = settings.get("verbosity", "minimal")
    custom_budget = settings.get("custom_budget")
    if custom_budget is not None:
        return int(custom_budget)
    return VERBOSITY_BUDGETS.get(verbosity, 1)


def _get_default_cooldown(priority: int) -> int:
    """Return the default cooldown days for a given priority."""
    for priority_range, days in _DEFAULT_COOLDOWN_BY_PRIORITY:
        if priority in priority_range:
            return days
    return 30


# ---------------------------------------------------------------------------
# propose_insight_candidate
# ---------------------------------------------------------------------------


async def propose_insight_candidate(
    pool: asyncpg.Pool,
    *,
    origin_butler: str,
    priority: int,
    category: str,
    dedup_key: str,
    message: str,
    expires_at: str | datetime,
    cooldown_days: int | None = None,
    channel: str | None = None,
    metadata: dict | None = None,
) -> dict[str, str]:
    """Validate and insert an insight candidate into the staging table.

    Returns
    -------
    dict with ``status`` and ``reason``:
    - ``{"status": "accepted", "reason": "candidate queued for delivery cycle"}``
    - ``{"status": "filtered", "reason": "verbosity is off"}``
    - ``{"status": "error", "reason": "<description>"}``
    """
    # --- Priority validation ---
    if not isinstance(priority, int) or not (1 <= priority <= 100):
        return {"status": "error", "reason": "priority must be between 1 and 100"}

    # --- Dedup key validation ---
    if not dedup_key:
        return {"status": "error", "reason": "dedup_key is required and must be non-empty"}
    if not _DEDUP_KEY_PATTERN.match(dedup_key):
        return {
            "status": "error",
            "reason": (
                "dedup_key must match format {category}:{entity}:{time-scope} "
                "or {butler}:{category}:{entity}:{time-scope}"
            ),
        }

    # --- Message validation ---
    if not message or not message.strip():
        return {"status": "error", "reason": "message must be non-empty"}

    # --- expires_at validation ---
    if expires_at is None:
        return {"status": "error", "reason": "expires_at is required"}
    if isinstance(expires_at, str):
        try:
            expires_dt = datetime.fromisoformat(expires_at)
        except ValueError:
            return {"status": "error", "reason": "expires_at must be a valid ISO 8601 datetime"}
    else:
        expires_dt = expires_at

    # Normalise to UTC-aware
    if expires_dt.tzinfo is None:
        expires_dt = expires_dt.replace(tzinfo=UTC)

    if expires_dt <= datetime.now(UTC):
        return {"status": "error", "reason": "expires_at must be in the future"}

    # --- Verbosity gate ---
    settings = await get_insight_settings(pool)
    verbosity = settings.get("verbosity", "minimal")
    if verbosity == "off" and settings.get("custom_budget") is None:
        return {"status": "filtered", "reason": "verbosity is off"}
    configured_budget = _get_configured_budget(settings)
    if configured_budget == 0:
        return {"status": "filtered", "reason": "verbosity is off"}

    # --- Insert candidate ---
    await pool.execute(
        """
        INSERT INTO insight_candidates
            (origin_butler, priority, category, dedup_key, cooldown_days,
             expires_at, message, channel, metadata, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, 'pending')
        """,
        origin_butler,
        priority,
        category,
        dedup_key,
        cooldown_days,
        expires_dt,
        message,
        channel,
        json.dumps(metadata) if metadata is not None else None,
    )
    return {"status": "accepted", "reason": "candidate queued for delivery cycle"}


# ---------------------------------------------------------------------------
# Delivery cycle steps
# ---------------------------------------------------------------------------


async def expire_candidates(pool: asyncpg.Pool, *, now: datetime | None = None) -> int:
    """Mark candidates past their expires_at as 'expired'.

    Returns the number of candidates expired.
    """
    if now is None:
        now = datetime.now(UTC)
    result = await pool.execute(
        """
        UPDATE insight_candidates
        SET status = 'expired'
        WHERE status = 'pending' AND expires_at <= $1
        """,
        now,
    )
    # asyncpg returns "UPDATE N" string
    count_str = result.split()[-1] if result else "0"
    return int(count_str)


async def filter_by_cooldown(
    pool: asyncpg.Pool,
    candidate_ids: list[str],
    *,
    now: datetime | None = None,
) -> list[str]:
    """Return the subset of candidate_ids NOT currently under cooldown.

    Candidates with active cooldowns are marked 'filtered'.
    Returns the list of IDs that remain eligible.
    """
    if not candidate_ids:
        return []
    if now is None:
        now = datetime.now(UTC)

    # Fetch dedup_keys of active cooldowns
    active_cooldown_keys: set[str] = set()
    rows = await pool.fetch(
        "SELECT DISTINCT dedup_key FROM insight_cooldowns WHERE cooldown_until > $1",
        now,
    )
    for row in rows:
        active_cooldown_keys.add(row["dedup_key"])

    if not active_cooldown_keys:
        return candidate_ids

    # Fetch candidates with their dedup_keys
    rows = await pool.fetch(
        "SELECT id, dedup_key FROM insight_candidates WHERE id = ANY($1::uuid[])",
        candidate_ids,
    )
    eligible_ids: list[str] = []
    filtered_ids: list[str] = []
    for row in rows:
        if row["dedup_key"] in active_cooldown_keys:
            filtered_ids.append(str(row["id"]))
        else:
            eligible_ids.append(str(row["id"]))

    if filtered_ids:
        await pool.execute(
            """
            UPDATE insight_candidates SET status = 'filtered'
            WHERE id = ANY($1::uuid[])
            """,
            filtered_ids,
        )

    return eligible_ids


async def deduplicate_candidates(
    pool: asyncpg.Pool,
    candidate_ids: list[str],
) -> list[str]:
    """Deduplicate candidates by dedup_key, keeping the highest-priority one.

    Ties broken by created_at ascending (earliest wins).
    Losers are marked 'filtered'. Returns the winning IDs.
    """
    if not candidate_ids:
        return []

    rows = await pool.fetch(
        """
        SELECT id, dedup_key, priority, created_at
        FROM insight_candidates
        WHERE id = ANY($1::uuid[]) AND status = 'pending'
        ORDER BY dedup_key, priority DESC, created_at ASC
        """,
        candidate_ids,
    )

    winners: dict[str, str] = {}  # dedup_key -> winning id
    for row in rows:
        key = row["dedup_key"]
        if key not in winners:
            winners[key] = str(row["id"])

    winner_ids = list(winners.values())
    loser_ids = [cid for cid in candidate_ids if cid not in winner_ids]

    if loser_ids:
        await pool.execute(
            """
            UPDATE insight_candidates SET status = 'filtered'
            WHERE id = ANY($1::uuid[])
            """,
            loser_ids,
        )

    return winner_ids


async def compute_effective_budget(
    pool: asyncpg.Pool,
    settings: dict[str, Any],
    *,
    window_days: int = 14,
    now: datetime | None = None,
) -> int:
    """Compute the effective delivery budget after adaptive reduction.

    Rules:
    - engagement_rate >= 0.5  → full configured budget
    - 0.25 <= rate < 0.5      → max(1, budget - 1)
    - rate < 0.25             → 1
    - No deliveries in window → rate = 1.0 (no penalty)
    """
    configured = _get_configured_budget(settings)
    if configured == 0:
        return 0

    if now is None:
        now = datetime.now(UTC)
    window_start = now - timedelta(days=window_days)

    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE engaged = TRUE) AS engaged_count
        FROM insight_engagement
        WHERE delivered_at >= $1 AND delivered_at <= $2
        """,
        window_start,
        now,
    )

    total = int(row["total"]) if row else 0
    engaged_count = int(row["engaged_count"]) if row else 0

    if total == 0:
        # No history → no penalty
        return configured

    rate = engaged_count / total

    if rate >= 0.5:
        return configured
    elif rate >= 0.25:
        return max(1, configured - 1)
    else:
        return 1


def _is_quiet_hours(settings: dict[str, Any], *, now: datetime | None = None) -> bool:
    """Return True if the current time falls within configured quiet hours."""
    quiet_start = settings.get("quiet_start")
    quiet_end = settings.get("quiet_end")
    quiet_timezone = settings.get("quiet_timezone")

    if quiet_start is None or quiet_end is None:
        return False

    if now is None:
        now = datetime.now(UTC)

    # Convert to user's timezone
    if quiet_timezone:
        try:
            tz = zoneinfo.ZoneInfo(quiet_timezone)
            local_now = now.astimezone(tz)
        except zoneinfo.ZoneInfoNotFoundError:
            logger.warning(
                "Timezone %r not found for quiet hours, falling back to UTC.", quiet_timezone
            )
            local_now = now
    else:
        local_now = now

    current_hour = local_now.hour

    if quiet_start <= quiet_end:
        # Same-day range, e.g. 22-23 or 9-17
        return quiet_start <= current_hour < quiet_end
    else:
        # Wraps midnight, e.g. 22-6
        return current_hour >= quiet_start or current_hour < quiet_end


async def record_cooldowns(
    pool: asyncpg.Pool,
    candidates: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> None:
    """Record cooldown entries for delivered candidates."""
    if now is None:
        now = datetime.now(UTC)

    cooldown_data = []
    for candidate in candidates:
        cooldown_days = candidate.get("cooldown_days") or _get_default_cooldown(
            candidate["priority"]
        )
        cooldown_until = now + timedelta(days=cooldown_days)
        cooldown_data.append((candidate["dedup_key"], cooldown_until))

    if cooldown_data:
        await pool.executemany(
            """
            INSERT INTO insight_cooldowns (dedup_key, cooldown_until, reason)
            VALUES ($1, $2, 'delivered')
            """,
            cooldown_data,
        )


async def record_engagement_rows(
    pool: asyncpg.Pool,
    candidate_ids: list[str],
    *,
    delivered_at: datetime | None = None,
) -> None:
    """Create engagement tracking rows (engaged=FALSE) for delivered candidates."""
    if not candidate_ids:
        return
    if delivered_at is None:
        delivered_at = datetime.now(UTC)

    engagement_data = [(cid, delivered_at) for cid in candidate_ids]
    await pool.executemany(
        """
        INSERT INTO insight_engagement (insight_id, delivered_at, engaged)
        VALUES ($1::uuid, $2, FALSE)
        """,
        engagement_data,
    )


async def check_and_update_engagement(
    pool: asyncpg.Pool,
    *,
    window_minutes: int = 60,
    now: datetime | None = None,
) -> int:
    """Mark engagement rows as engaged=TRUE for insights delivered within the window.

    Called on each Switchboard ingress request: if the user sends any message
    to any butler within 60 minutes of an insight's delivered_at, the insight
    is considered engaged.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    window_minutes:
        Engagement detection window in minutes (default: 60).
    now:
        Reference time (defaults to UTC now). Used in tests to control time.

    Returns
    -------
    int
        Number of engagement rows updated to engaged=TRUE.
    """
    if now is None:
        now = datetime.now(UTC)
    window_start = now - timedelta(minutes=window_minutes)

    result = await pool.execute(
        """
        UPDATE insight_engagement
        SET engaged = TRUE
        WHERE engaged = FALSE
          AND delivered_at >= $1
          AND delivered_at <= $2
        """,
        window_start,
        now,
    )
    # asyncpg returns "UPDATE N" string
    count_str = result.split()[-1] if result else "0"
    updated = int(count_str)
    if updated > 0:
        logger.debug(
            "insight engagement: marked %d row(s) as engaged (window=%dmin)",
            updated,
            window_minutes,
        )
    return updated


async def cleanup_old_rows(
    pool: asyncpg.Pool,
    *,
    now: datetime | None = None,
    retention_days: int = 30,
) -> None:
    """Delete old insight data to prevent unbounded table growth.

    - insight_candidates: non-pending rows older than retention_days
    - insight_cooldowns: rows where cooldown_until is older than retention_days
    - insight_engagement: rows older than retention_days
    """
    if now is None:
        now = datetime.now(UTC)
    cutoff = now - timedelta(days=retention_days)

    await pool.execute(
        """
        DELETE FROM insight_candidates
        WHERE status != 'pending' AND created_at < $1
        """,
        cutoff,
    )
    await pool.execute(
        """
        DELETE FROM insight_cooldowns
        WHERE cooldown_until < $1
        """,
        cutoff,
    )
    await pool.execute(
        """
        DELETE FROM insight_engagement
        WHERE delivered_at < $1
        """,
        cutoff,
    )


_AUTO_OFF_MESSAGE = (
    "I've paused proactive insights since you haven't found them useful. "
    "You can re-enable them anytime."
)


async def check_total_disengagement_auto_off(
    pool: asyncpg.Pool,
    *,
    now: datetime | None = None,
    notify_fn: Any | None = None,
) -> bool:
    """Check for total disengagement (0% engagement for 14 consecutive days).

    Per spec: if engagement_rate == 0.0 for 14 consecutive days with at least
    1 insight delivered per day, auto-downgrade verbosity to 'off' and deliver
    a final notification.

    Returns True if auto-off was triggered, False otherwise.
    """
    if now is None:
        now = datetime.now(UTC)

    # Query daily engagement data for the last 14 complete days.
    # Anchor the window to midnight boundaries so DATE_TRUNC grouping yields
    # exactly 14 day buckets (days D-14 through D-1 relative to today).
    # Use an exclusive upper bound (<) to exclude today's partial day, which
    # would otherwise inflate bucket count to 15 with an inclusive (<=) end.
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_start = today_midnight - timedelta(days=14)
    window_end = today_midnight  # exclusive: don't include today's partial day
    rows = await pool.fetch(
        """
        SELECT
            DATE_TRUNC('day', delivered_at) AS day,
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE engaged = TRUE) AS engaged_count
        FROM insight_engagement
        WHERE delivered_at >= $1 AND delivered_at < $2
        GROUP BY DATE_TRUNC('day', delivered_at)
        ORDER BY day ASC
        """,
        window_start,
        window_end,
    )

    if not rows:
        return False

    # Must have at least 14 days of data with deliveries
    if len(rows) < 14:
        return False

    # All 14 days must have zero engagement
    for row in rows:
        if int(row["total"]) == 0:
            return False
        if int(row["engaged_count"]) > 0:
            return False

    # Total disengagement detected — auto-downgrade to off
    logger.warning(
        "insight-delivery-cycle: total disengagement detected over 14 days, "
        "auto-downgrading verbosity to off"
    )
    # Ensure the settings row exists (created lazily) before updating
    await pool.execute("""
        INSERT INTO insight_settings (id, verbosity)
        VALUES (1, 'minimal')
        ON CONFLICT (id) DO NOTHING
    """)
    await pool.execute(
        """
        UPDATE insight_settings SET verbosity = 'off', updated_at = $1
        WHERE id = 1
        """,
        now,
    )

    # Deliver final notification via direct notify (not through the pipeline)
    if notify_fn is not None:
        try:
            await notify_fn(_AUTO_OFF_MESSAGE, {"intent": "insight", "auto_off": True})
        except Exception:
            logger.exception("insight-delivery-cycle: failed to deliver auto-off notification")

    return True


def _format_standalone(candidate: dict[str, Any]) -> str:
    """Format a single candidate as a standalone delivery message."""
    butler = candidate.get("origin_butler", "")
    message = candidate["message"]
    prefix = f"[{butler.capitalize()}] " if butler else ""
    return f"{prefix}{message}"


def _format_digest(candidates: list[dict[str, Any]]) -> str:
    """Format multiple candidates as a digest message."""
    count = len(candidates)
    header = f"Daily Insights ({count}):"
    lines = [header]
    for i, c in enumerate(candidates, start=1):
        butler = c.get("origin_butler", "")
        msg = c["message"]
        label = f"[{butler.capitalize()}]" if butler else ""
        lines.append(f"{i}. {label} {msg}".strip())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main delivery cycle
# ---------------------------------------------------------------------------


async def delivery_cycle(
    pool: asyncpg.Pool,
    *,
    notify_fn: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Orchestrate the full insight delivery pipeline.

    Steps (per spec §Delivery cycle execution order):
    1. Check quiet hours — if active, skip and return
    2. Expire candidates past expires_at
    3. Filter candidates with active cooldowns
    4. Deduplicate by dedup_key (keep highest priority)
    5. Compute effective budget (apply adaptive reduction)
    6. Select top-B candidates by priority
    7. Deliver via notify (digest for B>1, standalone for B=1)
    8. Record cooldowns for delivered candidates
    9. Record engagement tracking rows
    10. Clean up old rows

    Parameters
    ----------
    pool:
        Database connection pool.
    notify_fn:
        Async callable ``notify_fn(message, metadata) -> dict``.
        If None, delivery is skipped (useful for testing cycle logic).
    now:
        Reference time (defaults to UTC now). Used in tests to control time.

    Returns
    -------
    dict with keys:
        - ``skipped``: True if quiet hours or budget=0 caused early exit
        - ``expired``: number of candidates expired
        - ``delivered``: list of delivered candidate IDs
        - ``delivery_message``: the formatted message sent (or None)
        - ``effective_budget``: the computed budget
    """
    if now is None:
        now = datetime.now(UTC)

    result: dict[str, Any] = {
        "skipped": False,
        "expired": 0,
        "delivered": [],
        "delivery_message": None,
        "effective_budget": 0,
    }

    # Step 1: Check quiet hours
    settings = await get_insight_settings(pool)
    if _is_quiet_hours(settings, now=now):
        logger.info("insight-delivery-cycle: quiet hours active, skipping")
        result["skipped"] = True
        return result

    # Check verbosity=off early
    configured_budget = _get_configured_budget(settings)
    if configured_budget == 0:
        logger.info("insight-delivery-cycle: verbosity=off, filtering all pending")
        await pool.execute(
            """
            UPDATE insight_candidates SET status = 'filtered'
            WHERE status = 'pending'
            """
        )
        result["skipped"] = True
        return result

    # Step 2: Expire candidates
    expired = await expire_candidates(pool, now=now)
    result["expired"] = expired

    # Fetch pending candidates
    rows = await pool.fetch(
        """
        SELECT id FROM insight_candidates
        WHERE status = 'pending'
        ORDER BY priority DESC, created_at ASC
        """
    )
    pending_ids = [str(row["id"]) for row in rows]

    if not pending_ids:
        return result

    # Step 3: Filter by cooldown
    eligible_ids = await filter_by_cooldown(pool, pending_ids, now=now)
    if not eligible_ids:
        return result

    # Step 4: Deduplicate
    eligible_ids = await deduplicate_candidates(pool, eligible_ids)
    if not eligible_ids:
        return result

    # Step 5: Compute effective budget
    effective_budget = await compute_effective_budget(pool, settings, now=now)
    result["effective_budget"] = effective_budget

    if effective_budget == 0:
        return result

    # Step 6: Select top-B by priority (created_at tiebreak)
    rows = await pool.fetch(
        """
        SELECT id, origin_butler, priority, category, dedup_key,
               cooldown_days, message, channel, metadata
        FROM insight_candidates
        WHERE id = ANY($1::uuid[]) AND status = 'pending'
        ORDER BY priority DESC, created_at ASC
        LIMIT $2
        """,
        eligible_ids,
        effective_budget,
    )
    selected = [dict(row) for row in rows]
    selected_ids = [str(c["id"]) for c in selected]

    if not selected:
        return result

    # Step 7: Deliver
    # Guard: if no notify function is wired, skip delivery entirely rather than
    # silently marking candidates as delivered without sending anything.
    if notify_fn is None:
        logger.warning(
            "insight-delivery-cycle: notify_fn not wired — skipping delivery of %d candidates; "
            "no candidates will be marked delivered or consumed",
            len(selected),
        )
        result["skipped"] = True
        # Still run cleanup so the cycle doesn't accumulate stale rows
        await cleanup_old_rows(pool, now=now)
        return result

    deliver_count = len(selected)
    if deliver_count == 1:
        delivery_message = _format_standalone(selected[0])
    else:
        delivery_message = _format_digest(selected)

    result["delivery_message"] = delivery_message

    delivered_at = now
    notify_metadata: dict[str, Any] = {
        "insight_count": deliver_count,
        "insight_ids": selected_ids,
        "intent": "insight",
    }

    # notify_fn is guaranteed non-None here (None case returns early above)
    deliver_success = True
    try:
        notify_result = await notify_fn(delivery_message, notify_metadata)
        if isinstance(notify_result, dict) and notify_result.get("status") == "error":
            deliver_success = False
            logger.error(
                "insight-delivery-cycle: notify failed: %s",
                notify_result.get("error"),
            )
    except Exception:
        deliver_success = False
        logger.exception("insight-delivery-cycle: notify raised exception")

    if deliver_success:
        # Mark candidates as delivered and reset consecutive-failure counter
        await pool.execute(
            """
            UPDATE insight_candidates
            SET status = 'delivered', delivered_at = $1, delivery_attempt_count = 0
            WHERE id = ANY($2::uuid[])
            """,
            delivered_at,
            selected_ids,
        )
        result["delivered"] = selected_ids

        # Step 8: Record cooldowns
        await record_cooldowns(pool, selected, now=now)

        # Step 9: Record engagement
        await record_engagement_rows(pool, selected_ids, delivered_at=delivered_at)
    else:
        # Delivery failed — increment attempt counter; filter candidates that have
        # reached the 3-consecutive-failure threshold
        await pool.execute(
            """
            UPDATE insight_candidates
            SET delivery_attempt_count = delivery_attempt_count + 1
            WHERE id = ANY($1::uuid[])
            """,
            selected_ids,
        )
        # Filter candidates that have now failed 3 or more times
        await pool.execute(
            """
            UPDATE insight_candidates
            SET status = 'filtered'
            WHERE id = ANY($1::uuid[]) AND delivery_attempt_count >= 3
            """,
            selected_ids,
        )
        logger.warning(
            "insight-delivery-cycle: delivery failed for %d candidates; incremented attempt counts",
            len(selected_ids),
        )

    # Step 10: Cleanup
    await cleanup_old_rows(pool, now=now)

    # Auto-off check: total disengagement over 14 consecutive days
    await check_total_disengagement_auto_off(pool, now=now, notify_fn=notify_fn)

    return result
