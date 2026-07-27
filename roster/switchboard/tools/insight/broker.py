"""Insight broker — core business logic for the proactive insight engine.

Implements:
- ``propose_insight_candidate()`` — validate and insert a candidate
- ``delivery_cycle()`` — orchestrate the full insight delivery pipeline
- Supporting helpers: expire, cooldown filter, dedup, adaptive budget, etc.

Database tables used (all in the ``public`` schema):
- ``public.insight_candidates`` — staging table for proposed insights
- ``public.insight_settings``   — user verbosity/budget settings
- ``public.insight_cooldowns``  — cooldown entries by dedup_key
- ``public.insight_engagement`` — engagement tracking per delivered insight
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg

from butlers.core.approvals_policy import (
    get_approvals_policy_quiet_hours,
    is_policy_quiet_now,
)
from butlers.core.attention_ledger import (
    URGENT_PRIORITY_THRESHOLD,
    record_attention_event,
)

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
    # dedup_key is the PRIMARY KEY here (not a synthetic id) to mirror the
    # production DDL in alembic/versions/core/core_010_insight_tables.py
    # exactly: one active cooldown per dedup_key. A prior divergence here
    # (a synthetic `id UUID PRIMARY KEY` with a non-unique dedup_key) let a
    # plain-INSERT pkey collision on redelivery ship to production undetected
    # by this test-backed fixture — see record_cooldowns()'s ON CONFLICT fix.
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS insight_cooldowns (
            dedup_key TEXT PRIMARY KEY,
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
    # bu-tdd4k.5: durable daily rollup — mirrors
    # alembic/versions/core/core_165_attention_daily_rollup.py exactly.
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS attention_daily_rollup (
            day                  DATE PRIMARY KEY,
            owner_ingress_count  INTEGER NOT NULL DEFAULT 0,
            insights_delivered   INTEGER NOT NULL DEFAULT 0,
            insights_engaged     INTEGER NOT NULL DEFAULT 0,
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
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
        metadata,
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


async def record_cooldowns(
    pool: asyncpg.Pool | asyncpg.Connection,
    candidates: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> None:
    """Record cooldown entries for delivered candidates.

    dedup_key is the table's PRIMARY KEY, so a redelivery of the same
    dedup_key after its prior cooldown expired (the expired row is still
    there — it's only reaped by ``cleanup_old_rows()``) hits that key again.
    ON CONFLICT upserts the existing row instead of erroring: this is
    ordinary redelivery, not a bug, and it crashed the daily delivery cycle
    (bu-tdd4k.1) before this fix.
    """
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
            ON CONFLICT (dedup_key) DO UPDATE
            SET cooldown_until = EXCLUDED.cooldown_until,
                reason = EXCLUDED.reason,
                created_at = now()
            """,
            cooldown_data,
        )


async def record_engagement_rows(
    pool: asyncpg.Pool | asyncpg.Connection,
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
    - insight_engagement: rows older than retention_days, after first rolling
      each affected day's delivered/engaged counts into
      ``attention_daily_rollup`` (bu-tdd4k.5) so the disengagement ratchet's
      history survives this purge
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

    # Roll up the day's delivered/engaged counts BEFORE the rows disappear —
    # this is the durable signal check_total_disengagement_auto_off falls
    # back to once a day is no longer present in insight_engagement.
    # Best-effort: a rollup failure (e.g. mid-deploy, migration not yet
    # applied) must not block the deletes below.
    try:
        await pool.execute(
            """
            INSERT INTO attention_daily_rollup (day, insights_delivered, insights_engaged)
            SELECT
                DATE_TRUNC('day', delivered_at)::date AS day,
                COUNT(*) AS insights_delivered,
                COUNT(*) FILTER (WHERE engaged = TRUE) AS insights_engaged
            FROM insight_engagement
            WHERE delivered_at < $1
            GROUP BY DATE_TRUNC('day', delivered_at)
            ON CONFLICT (day) DO UPDATE
            SET insights_delivered = EXCLUDED.insights_delivered,
                insights_engaged = EXCLUDED.insights_engaged,
                updated_at = now()
            """,
            cutoff,
        )
    except Exception:
        logger.warning(
            "cleanup_old_rows: failed to roll up insight_engagement into "
            "attention_daily_rollup before purge; continuing with delete",
            exc_info=True,
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

    # bu-tdd4k.5: fall back to attention_daily_rollup for any day in the
    # window that insight_engagement's 30-day purge has already reaped. The
    # 14-day window normally fits comfortably inside the 30-day raw retention,
    # but a shortened retention_days config or a rollup-first cleanup ordering
    # would otherwise silently truncate the ratchet's history.
    try:
        rows = await pool.fetch(
            """
            WITH raw_days AS (
                SELECT
                    DATE_TRUNC('day', delivered_at)::date AS day,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE engaged = TRUE) AS engaged_count
                FROM insight_engagement
                WHERE delivered_at >= $1 AND delivered_at < $2
                GROUP BY DATE_TRUNC('day', delivered_at)
            ),
            rollup_days AS (
                SELECT day, insights_delivered AS total, insights_engaged AS engaged_count
                FROM attention_daily_rollup
                WHERE day >= $1::date AND day < $2::date
                  AND day NOT IN (SELECT day FROM raw_days)
            )
            SELECT day, total, engaged_count FROM raw_days
            UNION ALL
            SELECT day, total, engaged_count FROM rollup_days
            ORDER BY day ASC
            """,
            window_start,
            window_end,
        )
    except asyncpg.UndefinedTableError:
        # attention_daily_rollup not migrated yet on this DB — degrade to the
        # original raw-only read rather than crashing the daily cycle.
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


# ---------------------------------------------------------------------------
# Correlated-candidate clustering (bu-ep4ks.9 slice 1 — zero-LLM, deterministic)
# ---------------------------------------------------------------------------


def _candidate_entity_key(candidate: dict[str, Any]) -> str | None:
    """Extract the correlation entity key from a candidate's metadata, if any."""
    metadata = candidate.get("metadata")
    if not isinstance(metadata, dict):
        return None
    entity_id = metadata.get("entity_id")
    return str(entity_id) if entity_id else None


def _candidate_time_window(candidate: dict[str, Any]) -> tuple[datetime, datetime] | None:
    """Extract the candidate's referenced event time window from metadata, if any.

    Supports two producer shapes: an explicit ``event_window: {start, end}``
    (ISO 8601 timestamps), or a coarser ``event_date`` (ISO date, normalized to
    a full UTC day). Both forms use half-open ``[start, end)`` semantics, so
    adjacent windows share no event time. An explicit ``event_window`` is
    authoritative: ``event_date`` is considered only when the window key is
    absent. Explicit windows must have positive duration; malformed, partial,
    or non-positive values fail open to "no correlation data" (returns None)
    rather than raising — a producer's metadata typo must not break digest
    formatting or silently choose a different correlation shape.
    """
    metadata = candidate.get("metadata")
    if not isinstance(metadata, dict):
        return None

    if "event_window" in metadata:
        window = metadata["event_window"]
        if not isinstance(window, dict):
            return None
        start_raw, end_raw = window.get("start"), window.get("end")
        if not start_raw or not end_raw:
            return None
        try:
            start = datetime.fromisoformat(str(start_raw))
            end = datetime.fromisoformat(str(end_raw))
        except ValueError:
            return None
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        if end <= start:
            return None
        return (start, end)

    event_date = metadata.get("event_date")
    if isinstance(event_date, str):
        try:
            day = date.fromisoformat(event_date)
        except ValueError:
            return None
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        return (start, start + timedelta(days=1))

    return None


def _cluster_candidates(candidates: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Deterministically group candidates sharing an entity or an overlapping
    event time window.

    Two candidates link when they share a non-null ``metadata.entity_id``, or
    when both resolve a time window (``metadata.event_window`` or
    ``metadata.event_date``) and those half-open windows overlap. Linkage is
    transitive (union-find), so a chain of pairwise links folds into one group.
    Candidates with no correlation data of their own remain singleton groups,
    preserving pre-clustering digest formatting for those entries.

    Group order follows each group's earliest-appearing member in
    ``candidates`` (already priority-ordered by the caller), so both
    within-cluster and across-group ordering stay deterministic.
    """
    n = len(candidates)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    entity_keys = [_candidate_entity_key(c) for c in candidates]
    windows = [_candidate_time_window(c) for c in candidates]

    for i in range(n):
        for j in range(i + 1, n):
            if entity_keys[i] is not None and entity_keys[i] == entity_keys[j]:
                union(i, j)
                continue
            if windows[i] is not None and windows[j] is not None:
                (s1, e1), (s2, e2) = windows[i], windows[j]
                if s1 < e2 and s2 < e1:
                    union(i, j)

    groups: dict[int, list[dict[str, Any]]] = {}
    for idx, candidate in enumerate(candidates):
        groups.setdefault(find(idx), []).append(candidate)

    return [groups[root] for root in sorted(groups)]


# ---------------------------------------------------------------------------
# LLM cluster synthesis (bu-ep4ks.9 slice 3 — one sentence per correlated
# cluster, best-effort, no new budget knob)
# ---------------------------------------------------------------------------

# Only the direct-Anthropic-Messages "api" runtime lane (``butlers.core.
# runtimes.api.ApiAdapter``) is fast/cheap enough to sit inline in a
# scheduled delivery cycle — see that module's docstring and
# ``roster.switchboard.tools.routing.structured_classify``'s identical gate
# for switchboard classification (migration ``core_157`` flips a butler's
# "cheap" catalog tier onto ``runtime_type="api"`` specifically for this
# reason). Any other resolved runtime means no such override is configured
# for this butler/tier — a CLI subprocess adapter (claude_code/codex/gemini/
# opencode) pays a multi-second cold start per invocation, which is not
# acceptable inline latency for a cron tick — so synthesis fails open
# instead of ever spawning one.
_SYNTHESIS_MAX_SENTENCE_CHARS = 280
_SYNTHESIS_TIMEOUT_SECONDS = 12


async def _synthesize_cluster_sentence(
    pool: asyncpg.Pool,
    cluster: list[dict[str, Any]],
    *,
    butler_name: str = "switchboard",
    credential_store: Any | None = None,
) -> str | None:
    """Best-effort one-sentence LLM synthesis for one correlated cluster.

    Deliberately reuses the EXISTING per-day candidate budget instead of
    adding a new LLM-call budget knob: this only ever fires once per
    multi-candidate cluster within a single cycle's already-budgeted
    selection (``effective_budget``, itself <= ``VERBOSITY_BUDGETS["verbose"]``
    == 5), so call volume is inherently bounded by the existing insight-count
    budget — no separate accounting is needed.

    Fails open to ``None`` on ANY error: no model resolved for the "cheap"
    tier, a non-"api" resolved runtime, over quota, a timeout, or a blank
    response. The caller falls back to the pre-slice-3 plain bullet list —
    synthesis is a cosmetic enhancement, never a correctness-relevant
    dependency of digest delivery.
    """
    try:
        from butlers.core.model_routing import (
            Complexity,
            check_token_quota,
            record_token_usage,
            resolve_model_with_effective_tier,
        )
        from butlers.core.runtimes.base import create_adapter

        catalog_result = await resolve_model_with_effective_tier(
            pool, butler_name, Complexity.CHEAP
        )
        if catalog_result is None:
            return None
        (
            runtime_type,
            model_id,
            _extra_args,
            catalog_entry_id,
            session_timeout_s,
            _effective_tier,
        ) = catalog_result
        if runtime_type != "api":
            return None

        quota = await check_token_quota(pool, catalog_entry_id)
        if not quota.allowed:
            return None

        bullets = "\n".join(f"- [{c.get('origin_butler', '')}] {c['message']}" for c in cluster)
        prompt = (
            "These notes were flagged as related. In ONE short sentence "
            "(under 20 words), state what connects them. Respond with just "
            "the sentence — no preamble, no quotes, no markdown.\n\n" + bullets
        )
        adapter = create_adapter("api", credential_store=credential_store, butler_name=butler_name)
        text, _tool_calls, usage = await asyncio.wait_for(
            adapter.invoke(
                prompt,
                "",
                {},
                {},
                model=model_id,
                timeout=min(session_timeout_s, _SYNTHESIS_TIMEOUT_SECONDS),
            ),
            timeout=_SYNTHESIS_TIMEOUT_SECONDS,
        )

        if usage and usage.get("input_tokens") is not None:
            await record_token_usage(
                pool,
                catalog_entry_id=catalog_entry_id,
                butler_name=butler_name,
                session_id=None,
                input_tokens=usage.get("input_tokens") or 0,
                output_tokens=usage.get("output_tokens") or 0,
                cached_input_tokens=usage.get("cache_read_input_tokens", 0) or 0,
                cache_creation_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
                purpose="insight_cluster_synthesis",
            )

        if not text or not text.strip():
            return None
        sentence = text.strip().splitlines()[0].strip()
        return sentence[:_SYNTHESIS_MAX_SENTENCE_CHARS] or None
    except Exception:
        logger.debug(
            "insight-delivery-cycle: cluster synthesis failed; falling back to bullet list",
            exc_info=True,
        )
        return None


async def _format_digest(
    candidates: list[dict[str, Any]],
    *,
    pool: asyncpg.Pool | None = None,
    credential_store: Any | None = None,
) -> str:
    """Format multiple candidates as a digest message.

    Candidates correlated by shared entity or overlapping event time window
    (see ``_cluster_candidates``) render as one labeled sub-group instead of
    unrelated flat bullets. Uncorrelated candidates render exactly as before
    this slice — a single numbered ``[Butler] message`` line.

    When ``pool`` is given, a multi-candidate cluster gets a best-effort
    one-sentence LLM synthesis (bu-ep4ks.9 slice 3) prepended to its
    ``Correlated (N):`` label. ``pool=None`` (the default, matching every
    call site before slice 3) skips synthesis entirely.
    """
    count = len(candidates)
    header = f"Daily Insights ({count}):"
    lines = [header]
    for i, cluster in enumerate(_cluster_candidates(candidates), start=1):
        if len(cluster) == 1:
            c = cluster[0]
            butler = c.get("origin_butler", "")
            msg = c["message"]
            label = f"[{butler.capitalize()}]" if butler else ""
            lines.append(f"{i}. {label} {msg}".strip())
        else:
            synthesis = (
                await _synthesize_cluster_sentence(pool, cluster, credential_store=credential_store)
                if pool is not None
                else None
            )
            if synthesis:
                lines.append(f"{i}. Correlated ({len(cluster)}): {synthesis}")
            else:
                lines.append(f"{i}. Correlated ({len(cluster)}):")
            for c in cluster:
                butler = c.get("origin_butler", "")
                msg = c["message"]
                label = f"[{butler.capitalize()}]" if butler else ""
                lines.append(f"   - {label} {msg}".strip())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Context-bus suppression (bu-ep4ks.9 slice 2 — presence-aware delivery)
# ---------------------------------------------------------------------------

# Precedence when more than one signal is active: dnd (owner's explicit hard
# stop) wins, then meeting, sleeping, traveling in that order. This is a
# broker-local extension of the dnd/sleeping-only suppression set in
# butlers.core.attention_ledger — that shared set is also consumed by
# decision digests, secrets-lifecycle notifications, and fleet-halt/model-
# breaker escalations, none of which should start holding on meeting/
# traveling just because the insight broker now does.
_CONTEXT_SUPPRESSING_SIGNALS = ("dnd", "meeting", "sleeping", "traveling")

# Max duration a given signal may hold routine insight delivery, independent
# of the signal's own (much longer) context-bus TTL — e.g. ``traveling`` can
# stay active for up to 30 days (butlers.context_bus._TTL_CONFIG), but routine
# insights must not silently queue for a month just because a trip is still
# technically ongoing. Once a signal has been continuously active longer than
# its cap, it stops suppressing delivery here (a still-active, shorter-capped
# signal, or quiet hours, may still apply).
_CONTEXT_MAX_HOLD: dict[str, timedelta] = {
    "dnd": timedelta(hours=4),
    "meeting": timedelta(hours=2),
    "sleeping": timedelta(hours=10),
    "traveling": timedelta(hours=6),
}


async def get_suppressing_context_signal(
    pool: asyncpg.Pool | None, *, now: datetime | None = None
) -> str | None:
    """Return the active context-bus signal type currently holding routine
    (sub-urgent) insight delivery, or None.

    Deterministic, zero-LLM read of ``public.user_context`` via the existing
    context-bus module. Fails open (returns None) on any error, matching
    every other context-bus reader in this codebase.
    """
    if pool is None:
        return None
    if now is None:
        now = datetime.now(UTC)
    try:
        from butlers.context_bus import get_active_context

        signals = await get_active_context(pool)
    except Exception:
        logger.debug("insight-delivery-cycle: context bus unavailable; failing open", exc_info=True)
        return None

    held = [
        s
        for s in signals
        if s.signal_type in _CONTEXT_SUPPRESSING_SIGNALS
        and (now - s.set_at) < _CONTEXT_MAX_HOLD[s.signal_type]
    ]
    if not held:
        return None

    for signal_type in _CONTEXT_SUPPRESSING_SIGNALS:
        if any(s.signal_type == signal_type for s in held):
            return signal_type
    return None  # pragma: no cover - `held` is filtered to these signals above.


# ---------------------------------------------------------------------------
# Hold-until-first-active daily cadence (bu-ep4ks.9 slice 5)
# ---------------------------------------------------------------------------

# Only consulted when delivery_cycle(daily_hold_mode=True) — the cron-side
# companion to this mode is a windowed cron (several ticks across the
# morning) replacing the old single fixed-clock 08:00 UTC slot: instead of
# firing once regardless of whether the owner is even reachable yet, each
# tick asks "is the owner not currently suppressed?" and delivers on the
# FIRST tick that says yes. Deliberately a UTC clock-hour comparison, not
# owner-local time: every other time comparison in this module (dnd/meeting/
# sleeping/traveling max-hold TTLs) is already relative-duration-based
# rather than local-clock-based, and the Owner Attention Policy quiet-hours
# check (which IS owner-local, via ``is_policy_quiet_now``) already ran
# above this — this constant only bounds how long "hold until active" is
# allowed to wait past whatever quiet-hours already permitted.
_DAILY_HOLD_FALLBACK_UTC_HOUR = 11


def _daily_hold_fallback_reached(now: datetime) -> bool:
    """True once the hard fallback deadline for a held daily digest has
    passed — past this point, dnd/meeting/sleeping/quiet_hours suppression
    is bypassed so the digest is not silently skipped for the whole day.

    Deliberately NOT bypassed for ``traveling`` — see the ``daily_hold_mode``
    branch in :func:`delivery_cycle` for the travel-day skip/defer rationale.
    """
    return now.hour >= _DAILY_HOLD_FALLBACK_UTC_HOUR


# ---------------------------------------------------------------------------
# Main delivery cycle
# ---------------------------------------------------------------------------


async def delivery_cycle(
    pool: asyncpg.Pool,
    *,
    notify_fn: Any | None = None,
    now: datetime | None = None,
    urgent_only: bool = False,
    daily_hold_mode: bool = False,
    credential_store: Any | None = None,
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
    urgent_only:
        bu-o8233 (JARVIS pursuit move 8 slice 4) — hourly urgent sub-cycle mode.
        Today's single daily cron slot means a priority>=90 candidate proposed
        minutes after the daily run can sit ``pending`` for nearly 24h before
        the existing priority-urgent bypass (which only affects quiet-hours/
        context-bus *suppression*, not cadence) ever gets a chance to consider
        it. When True, this cycle:
          - narrows candidate selection to ``priority >= URGENT_PRIORITY_THRESHOLD``
            from the start (routine candidates are never touched by this mode —
            they stay untouched ``pending`` for the next daily cycle);
          - skips the quiet-hours/context-bus consult entirely (urgent
            candidates always bypass both per RFC 0011 Amendment 1, so the
            reads would be pure overhead);
          - still honours an explicit ``verbosity=off`` opt-out (that is a
            hard user preference, not a time-based deferral the urgent bypass
            is meant to override);
          - has no daily budget cap — every eligible urgent candidate is
            delivered (or folded into one digest) this cycle, not just the
            top-B;
          - skips end-of-cycle maintenance (``cleanup_old_rows``,
            disengagement auto-off) — the daily cycle already covers those
            once a day; running them hourly too would be redundant.
        Idempotency across the two cycles needs no extra bookkeeping: both
        select ``WHERE status = 'pending'`` and this cycle's own delivery step
        flips delivered candidates to ``status = 'delivered'`` before
        returning, so a later daily cycle (or the next hourly tick) simply
        never sees them again — the same row-status guard the daily cycle
        already relies on.
    daily_hold_mode:
        bu-ep4ks.9 slice 5 — hold-until-first-active daily cadence. The
        production cron companion replaces the old single fixed 08:00 UTC
        slot with a windowed cron (several ticks across the morning); each
        tick calls this with ``daily_hold_mode=True``. No new persistent
        "already ran today" flag is needed: once a tick delivers, the
        delivered candidates flip to ``status = 'delivered'`` (the same
        idempotency ``urgent_only`` already relies on), so a later tick that
        same morning simply finds nothing pending and no-ops. Only changes
        behaviour when this cycle would otherwise be fully suppressed with
        no urgent candidate pending (see ``get_suppressing_context_signal``):
          - if the active suppressing signal is ``traveling``, the routine
            digest is deferred outright (never force-delivered by the hard
            fallback deadline below) — a home-life digest is not useful
            mid-trip, so a travel day skips/defers rather than eventually
            firing anyway;
          - otherwise (dnd/meeting/sleeping/quiet_hours), suppression is
            bypassed once :func:`_daily_hold_fallback_reached` returns True
            for ``now`` — the hard fallback deadline — so a held digest is
            never silently skipped for the entire day just because the
            owner stayed unreachable past the deadline.
        No effect when ``urgent_only=True`` (that mode skips the whole
        suppression consult) or when this cycle isn't suppressed at all.
    credential_store:
        Optional ``CredentialStore`` forwarded to slice 3's cluster
        synthesis (see ``_synthesize_cluster_sentence``) for resolving the
        Anthropic API key when the caller's environment doesn't already
        carry one. ``None`` falls back to the ``ANTHROPIC_API_KEY``
        environment variable.

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

    settings = await get_insight_settings(pool)

    # Step 1: Check Owner Attention Policy + context bus (bu-qvnce.8 slices 1-2;
    # extended to meeting/traveling with per-signal max-hold TTL by
    # bu-ep4ks.9 slice 2). Both are deterministic, non-LLM reads. Neither
    # suppresses a candidate at or above URGENT_PRIORITY_THRESHOLD (RFC 0011
    # Amendment 1: fail-open for urgent, budgeted for routine) — that check
    # happens below, once pending candidates are known, so a single urgent
    # candidate doesn't skip the whole cycle's suppression bookkeeping. In
    # urgent_only mode every candidate this cycle considers is already >= the
    # urgent threshold, so the suppression consult is skipped outright rather
    # than computed and then ignored.
    _suppression_reason: str | None = None
    # The signal that is "holding" this cycle, in isolation from the
    # human-readable reason string above — recorded as structured
    # attention-ledger telemetry (bu-ep4ks.9 slice 2) so "held by <signal>"
    # is queryable without parsing `reason`.
    _suppression_signal: str | None = None
    if not urgent_only:
        policy = await get_approvals_policy_quiet_hours(pool)
        _quiet_hours_active = is_policy_quiet_now(policy, now=now)
        _context_signal = await get_suppressing_context_signal(pool, now=now)
        if _quiet_hours_active:
            _suppression_reason = "quiet_hours"
            _suppression_signal = "quiet_hours"
        elif _context_signal is not None:
            _suppression_reason = f"context_bus:{_context_signal}"
            _suppression_signal = _context_signal

    # Check verbosity=off early. This is a hard user opt-out (distinct from
    # the time-based quiet-hours/context-bus deferral above), so it applies
    # even in urgent_only mode — the priority-urgent bypass fails open past
    # quiet hours and dnd/sleeping signals, not past the owner's own "insights
    # off" setting.
    configured_budget = _get_configured_budget(settings)
    if configured_budget == 0:
        if urgent_only:
            # Only touch the urgent working set this cycle actually considers.
            # Routine (sub-threshold) candidates must stay untouched 'pending'
            # for a later, non-suppressed cycle — the same invariant this mode
            # upholds everywhere else (see selection/budget/cleanup above).
            logger.info(
                "insight-delivery-cycle: verbosity=off, filtering pending urgent "
                "(priority>=%d) candidates only",
                URGENT_PRIORITY_THRESHOLD,
            )
            await pool.execute(
                """
                UPDATE insight_candidates SET status = 'filtered'
                WHERE status = 'pending' AND priority >= $1
                """,
                URGENT_PRIORITY_THRESHOLD,
            )
        else:
            logger.info("insight-delivery-cycle: verbosity=off, filtering all pending")
            await pool.execute(
                """
                UPDATE insight_candidates SET status = 'filtered'
                WHERE status = 'pending'
                """
            )
        result["skipped"] = True
        return result

    # Step 2: Expire candidates. Runs unconditionally — expiry bookkeeping
    # must not stall just because this cycle is (or may be) suppressed.
    expired = await expire_candidates(pool, now=now)
    result["expired"] = expired

    # Fetch pending candidates. urgent_only narrows this to priority>=90 from
    # the start — routine candidates are never selected, filtered, or
    # otherwise touched by this cycle.
    if urgent_only:
        rows = await pool.fetch(
            """
            SELECT id FROM insight_candidates
            WHERE status = 'pending' AND priority >= $1
            ORDER BY priority DESC, created_at ASC
            """,
            URGENT_PRIORITY_THRESHOLD,
        )
    else:
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

    if not urgent_only and _suppression_reason is not None:
        urgent_rows = await pool.fetch(
            """
            SELECT id FROM insight_candidates
            WHERE id = ANY($1::uuid[]) AND priority >= $2
            """,
            pending_ids,
            URGENT_PRIORITY_THRESHOLD,
        )
        if urgent_rows:
            # At least one urgent candidate is pending — narrow this cycle's
            # working set to urgent candidates only. Routine (sub-threshold)
            # candidates remain 'pending' untouched for a later, non-suppressed
            # cycle rather than being silently dropped.
            logger.info(
                "insight-delivery-cycle: suppressed (%s) but %d urgent (priority>=%d) "
                "candidate(s) pending — bypassing suppression for those only",
                _suppression_reason,
                len(urgent_rows),
                URGENT_PRIORITY_THRESHOLD,
            )
            pending_ids = [str(row["id"]) for row in urgent_rows]
        elif daily_hold_mode and _suppression_signal == "traveling":
            # Slice 5 travel-day skip/defer: a routine home-life digest is
            # not useful mid-trip, so a travel day fully defers the digest —
            # unlike dnd/meeting/sleeping/quiet_hours below, this is never
            # force-delivered by the hard fallback deadline. Each windowed
            # tick re-checks; once `traveling` clears (or its own slice-2
            # max-hold TTL lapses), a later tick the same day can still
            # succeed before the window closes.
            logger.info(
                "insight-delivery-cycle: travel day — deferring routine digest, "
                "no urgent (priority>=%d) candidates pending",
                URGENT_PRIORITY_THRESHOLD,
            )
            await record_attention_event(
                pool,
                origin_butler="switchboard",
                source="insight",
                outcome="suppressed",
                intent="insight",
                reason="travel_day_defer",
                metadata={"held_by": "traveling"},
            )
            result["skipped"] = True
            return result
        elif daily_hold_mode and _daily_hold_fallback_reached(now):
            # Hard fallback deadline reached: bypass suppression for the
            # FULL routine pending set (not just urgent) rather than holding
            # the digest hostage for the rest of the day. pending_ids stays
            # as the full routine set already computed above.
            logger.info(
                "insight-delivery-cycle: hard fallback deadline reached (now=%s); "
                "bypassing suppression (%s) for routine delivery",
                now.isoformat(),
                _suppression_reason,
            )
        else:
            logger.info(
                "insight-delivery-cycle: suppressed (%s), no urgent (priority>=%d) "
                "candidates pending — skipping",
                _suppression_reason,
                URGENT_PRIORITY_THRESHOLD,
            )
            await record_attention_event(
                pool,
                origin_butler="switchboard",
                source="insight",
                outcome="suppressed",
                intent="insight",
                reason=_suppression_reason,
                metadata={"held_by": _suppression_signal},
            )
            result["skipped"] = True
            return result

    # Step 3: Filter by cooldown
    eligible_ids = await filter_by_cooldown(pool, pending_ids, now=now)
    if not eligible_ids:
        return result

    # Step 4: Deduplicate
    eligible_ids = await deduplicate_candidates(pool, eligible_ids)
    if not eligible_ids:
        return result

    # Step 5: Compute effective budget. urgent_only has no daily cap — every
    # eligible urgent candidate is delivered this cycle (the budget exists to
    # ration routine insights across a day; it does not apply to the
    # always-deliver urgent bypass), so the "budget" here is simply the full
    # eligible set.
    if urgent_only:
        effective_budget = len(eligible_ids)
    else:
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
        # Still run cleanup so the cycle doesn't accumulate stale rows (daily
        # cycle only — see the urgent_only skip rationale on Step 10 below).
        if not urgent_only:
            await cleanup_old_rows(pool, now=now)
        return result

    deliver_count = len(selected)
    if deliver_count == 1:
        delivery_message = _format_standalone(selected[0])
    else:
        delivery_message = await _format_digest(
            selected, pool=pool, credential_store=credential_store
        )

    result["delivery_message"] = delivery_message

    # Compute delivery channel: majority vote from candidates that specify one.
    # NULL channel means "use the owner's primary channel" (resolved by notify_fn).
    # Per spec: for a digest, the most common channel wins; ties broken by the
    # first candidate's channel (highest-priority candidate, earliest created_at).
    from collections import Counter

    candidate_channels = [c.get("channel") for c in selected if c.get("channel")]
    if candidate_channels:
        delivery_channel: str | None = Counter(candidate_channels).most_common(1)[0][0]
    else:
        delivery_channel = None  # notify_fn resolves from owner's primary channel

    delivered_at = now
    notify_metadata: dict[str, Any] = {
        "insight_count": deliver_count,
        "insight_ids": selected_ids,
        "intent": "insight",
        "channel": delivery_channel,
    }

    # notify_fn is guaranteed non-None here (None case returns early above)
    deliver_success = True
    # Machine-readable failure class for the attention ledger (bu-wsm9m),
    # mirroring notify()'s reason vocabulary (bu-zcos8): a notify_fn error
    # return is a delivery_error, an exception mid-dispatch is an
    # unexpected_error. Stays None on the success path.
    failure_reason: str | None = None
    try:
        notify_result = await notify_fn(delivery_message, notify_metadata)
        if isinstance(notify_result, dict) and notify_result.get("status") == "error":
            deliver_success = False
            failure_reason = f"delivery_error:{notify_result.get('error', 'unknown')}"
            logger.error(
                "insight-delivery-cycle: notify failed: %s",
                notify_result.get("error"),
            )
    except Exception as exc:
        deliver_success = False
        failure_reason = f"unexpected_error:{type(exc).__name__}"
        logger.exception("insight-delivery-cycle: notify raised exception")

    if deliver_success:
        # Mark candidates delivered, record cooldowns, and record engagement
        # rows as one transaction. These three writes describe a single fact
        # (this candidate was delivered and its lifecycle is tracked) and
        # must commit together: before this, a crash partway through this
        # block (e.g. the insight_cooldowns pkey collision on redelivery —
        # bu-tdd4k.1) left candidates marked 'delivered' with no cooldown or
        # engagement row behind them. The insight then silently vanished
        # (never pending again, never actually tracked), and a later cycle's
        # benign "suppressed: quiet_hours" ledger write was the only trace
        # left — narrating a crash as ordinary quiet-hours discipline.
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE insight_candidates
                    SET status = 'delivered', delivered_at = $1, delivery_attempt_count = 0
                    WHERE id = ANY($2::uuid[])
                    """,
                    delivered_at,
                    selected_ids,
                )

                # Step 8: Record cooldowns
                await record_cooldowns(conn, selected, now=now)

                # Step 9: Record engagement
                await record_engagement_rows(conn, selected_ids, delivered_at=delivered_at)

        result["delivered"] = selected_ids

        # Attention ledger: one row per delivered candidate. Deliberately
        # outside the bookkeeping transaction above — record_attention_event
        # is a best-effort observability write (see its degraded-honesty
        # contract) and must never be able to abort or roll back the
        # delivery bookkeeping it is merely describing.
        # A single-candidate delivery is "delivered"; a batched digest
        # (deliver_count > 1) folds every candidate into one composed
        # message, so each is "coalesced".
        _ledger_outcome = "delivered" if deliver_count == 1 else "coalesced"
        for _c in selected:
            await record_attention_event(
                pool,
                origin_butler=_c.get("origin_butler") or "unknown",
                source="insight",
                outcome=_ledger_outcome,
                channel=delivery_channel or _c.get("channel"),
                intent="insight",
                priority=_c.get("priority"),
                dedup_key=_c.get("dedup_key"),
                notification_ref=str(_c["id"]),
            )
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
        # Filter candidates that have now failed 3 or more times; record the
        # delivery failure in metadata so callers can discriminate this path.
        await pool.execute(
            """
            UPDATE insight_candidates
            SET status = 'filtered',
                metadata = COALESCE(metadata, '{}'::jsonb)
                           || jsonb_build_object(
                               'delivery_failure', true,
                               'failed_attempts', delivery_attempt_count
                           )
            WHERE id = ANY($1::uuid[]) AND delivery_attempt_count >= 3
            """,
            selected_ids,
        )
        logger.warning(
            "insight-delivery-cycle: delivery failed for %d candidates; incremented attempt counts",
            len(selected_ids),
        )

        # Attention ledger: stamp one terminal outcome="failed" row per
        # candidate this cycle failed to deliver (bu-wsm9m). Before this, the
        # deliver_success=False branch bumped delivery_attempt_count and (after
        # 3 strikes) marked candidates 'filtered' with only a warn-log — the
        # insight choke point wrote NO ledger row, so a genuine delivery outage
        # read identically to a benign quiet-hours hold (which DOES write a
        # 'suppressed' row) on the exact surface built to prove silence is
        # chosen. This mirrors the notify() choke point's all-paths failed
        # accounting (bu-zcos8/bu-hmdqz.3, core-notify spec §"Attention Ledger
        # Recording at the notify() Boundary"): a real failure is "failed",
        # never "deferred"/"suppressed" (those are benign, chosen holds).
        #
        # [decision] The 3-strikes 'filtered' give-up does NOT get its own
        # distinct ledger row. The outcome vocabulary has no "abandoned"
        # outcome, and reusing "suppressed" would misrepresent a repeated-
        # failure give-up as a chosen quiet-hours/context-bus hold — the exact
        # conflation bu-hmdqz.3/bu-zcos8 warn against. Instead the terminal
        # give-up is encoded in the same per-candidate "failed" row's metadata
        # (terminally_filtered=true, retryable=false), keeping one row per
        # candidate per cycle (mirroring the delivered/coalesced branch's
        # cardinality) while still making every give-up provable via a metadata
        # filter. A pre-3-strikes failure stays 'pending' and the next cycle
        # retries it, so its row carries retryable=true.
        #
        # Deliberately OUTSIDE any transaction and best-effort/fail-open (see
        # record_attention_event's degraded-honesty contract): a ledger-write
        # hiccup must never abort the attempt-count/filter bookkeeping it
        # merely describes.
        reason = failure_reason or "delivery_error:unknown"
        post_rows = await pool.fetch(
            """
            SELECT id, origin_butler, priority, dedup_key, channel,
                   status, delivery_attempt_count
            FROM insight_candidates
            WHERE id = ANY($1::uuid[])
            """,
            selected_ids,
        )
        for _r in post_rows:
            _terminally_filtered = _r["status"] == "filtered"
            await record_attention_event(
                pool,
                # insight_candidates.origin_butler is NOT NULL (see the schema
                # in create_insight_tables / the core migration), and this is a
                # bare asyncpg Record access — a missing key would KeyError, not
                # yield None — so no None-guard is warranted here.
                origin_butler=_r["origin_butler"],
                source="insight",
                outcome="failed",
                channel=delivery_channel or _r["channel"],
                intent="insight",
                priority=_r["priority"],
                dedup_key=_r["dedup_key"],
                notification_ref=str(_r["id"]),
                reason=reason,
                metadata={
                    "failed_attempts": _r["delivery_attempt_count"],
                    "terminally_filtered": _terminally_filtered,
                    "retryable": not _terminally_filtered,
                },
            )

    # Step 10: Cleanup + disengagement auto-off. Skipped in urgent_only mode —
    # these are daily-cadence maintenance concerns the regular cycle already
    # covers once a day; running them on every hourly urgent tick too would
    # just be redundant work (cleanup_old_rows' retention window is in days,
    # and the auto-off check requires 14 consecutive *days* of history).
    if not urgent_only:
        await cleanup_old_rows(pool, now=now)
        await check_total_disengagement_auto_off(pool, now=now, notify_fn=notify_fn)

    return result
