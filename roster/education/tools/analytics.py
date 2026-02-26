"""Education butler — learning analytics: snapshot computation, trends, and cross-topic stats."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg

from butlers.tools.education._helpers import _row_to_dict

# ---------------------------------------------------------------------------
# Type alias for the optional curriculum_replan callback
# ---------------------------------------------------------------------------

CurriculumReplanCallback = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _bucket_hour(hour: int) -> str:
    """Map an hour (0-23) to a time-of-day bucket name.

    morning   : 06:00–11:59 (hours 6-11)
    afternoon : 12:00–17:59 (hours 12-17)
    evening   : 18:00–05:59 (hours 18-23, 0-5)
    """
    if 6 <= hour <= 11:
        return "morning"
    if 12 <= hour <= 17:
        return "afternoon"
    return "evening"


# ---------------------------------------------------------------------------
# analytics_compute_snapshot
# ---------------------------------------------------------------------------


async def analytics_compute_snapshot(
    pool: asyncpg.Pool,
    mind_map_id: str,
    snapshot_date: date | None = None,
) -> dict[str, Any]:
    """Compute all 14 metrics for one mind map and upsert into analytics_snapshots.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map to snapshot.
    snapshot_date:
        Date for the snapshot. Defaults to today (UTC).

    Returns
    -------
    dict
        The computed metrics dict (same as stored in the JSONB column).
    """
    if snapshot_date is None:
        snapshot_date = datetime.now(tz=UTC).date()

    async with pool.acquire() as conn:
        # ------------------------------------------------------------------
        # 1. Node counts
        # ------------------------------------------------------------------
        node_rows = await conn.fetch(
            """
            SELECT id, mastery_status, ease_factor, updated_at
            FROM education.mind_map_nodes
            WHERE mind_map_id = $1
            """,
            mind_map_id,
        )
        total_nodes = len(node_rows)
        mastered_nodes = sum(1 for r in node_rows if r["mastery_status"] == "mastered")
        mastery_pct = round(mastered_nodes / total_nodes, 2) if total_nodes > 0 else 0.0

        # ------------------------------------------------------------------
        # 2. avg_ease_factor
        # ------------------------------------------------------------------
        if total_nodes > 0:
            avg_ease_factor = round(
                sum(float(r["ease_factor"]) for r in node_rows) / total_nodes, 2
            )
        else:
            avg_ease_factor = 0.0

        # ------------------------------------------------------------------
        # 3. Retention rates (review-type only, anchored to snapshot_date)
        # ------------------------------------------------------------------
        retention_rate_7d = await _compute_retention_rate(
            conn, mind_map_id, snapshot_date=snapshot_date, days=7
        )
        retention_rate_30d = await _compute_retention_rate(
            conn, mind_map_id, snapshot_date=snapshot_date, days=30
        )

        # ------------------------------------------------------------------
        # 4. Velocity: avg nodes mastered per week over last 4 weeks
        #    Computed in-memory from node_rows already fetched in step 1,
        #    anchored to snapshot_date for reproducibility.
        # ------------------------------------------------------------------
        velocity = _compute_velocity(node_rows, snapshot_date=snapshot_date)

        # ------------------------------------------------------------------
        # 5. Estimated completion days
        # ------------------------------------------------------------------
        unmastered = total_nodes - mastered_nodes
        if velocity > 0 and unmastered > 0:
            estimated_completion_days = math.ceil(unmastered / velocity * 7)
        else:
            estimated_completion_days = None

        # ------------------------------------------------------------------
        # 6. Struggling nodes (list of UUIDs)
        #    Nodes with 5+ review responses whose last-5 review avg quality < 2.5
        # ------------------------------------------------------------------
        struggling_nodes = await _compute_struggling_nodes(conn, mind_map_id)

        # ------------------------------------------------------------------
        # 7. Strongest subtree
        # ------------------------------------------------------------------
        strongest_subtree = await _compute_strongest_subtree(conn, mind_map_id)

        # ------------------------------------------------------------------
        # 8. Total quiz responses
        # ------------------------------------------------------------------
        total_quiz_responses = await conn.fetchval(
            """
            SELECT COUNT(*) FROM education.quiz_responses
            WHERE mind_map_id = $1
            """,
            mind_map_id,
        )
        total_quiz_responses = int(total_quiz_responses or 0)

        # ------------------------------------------------------------------
        # 9. avg_quality_score (all response types)
        # ------------------------------------------------------------------
        avg_quality_raw = await conn.fetchval(
            """
            SELECT AVG(quality::float) FROM education.quiz_responses
            WHERE mind_map_id = $1
            """,
            mind_map_id,
        )
        avg_quality_score = (
            round(float(avg_quality_raw), 1) if avg_quality_raw is not None else None
        )

        # ------------------------------------------------------------------
        # 10. Sessions this period (distinct dates in last 30 days,
        #     anchored to snapshot_date for reproducibility)
        # ------------------------------------------------------------------
        sessions_this_period = await conn.fetchval(
            """
            SELECT COUNT(DISTINCT responded_at::date)
            FROM education.quiz_responses
            WHERE mind_map_id = $1
              AND responded_at::date <= $2
              AND responded_at::date > $2 - INTERVAL '30 days'
            """,
            mind_map_id,
            snapshot_date,
        )
        sessions_this_period = int(sessions_this_period or 0)

        # ------------------------------------------------------------------
        # 11. Time-of-day distribution (bounded to the 30-day session period
        #     and anchored to snapshot_date for consistency with sessions_this_period)
        # ------------------------------------------------------------------
        time_rows = await conn.fetch(
            """
            SELECT EXTRACT(HOUR FROM responded_at AT TIME ZONE 'UTC')::int AS hour
            FROM education.quiz_responses
            WHERE mind_map_id = $1
              AND responded_at::date <= $2
              AND responded_at::date > $2 - INTERVAL '30 days'
            """,
            mind_map_id,
            snapshot_date,
        )
        tod_dist: dict[str, int] = {"morning": 0, "afternoon": 0, "evening": 0}
        for row in time_rows:
            bucket = _bucket_hour(row["hour"])
            tod_dist[bucket] += 1

        # ------------------------------------------------------------------
        # Build metrics dict
        # ------------------------------------------------------------------
        metrics: dict[str, Any] = {
            "total_nodes": total_nodes,
            "mastered_nodes": mastered_nodes,
            "mastery_pct": mastery_pct,
            "avg_ease_factor": avg_ease_factor,
            "retention_rate_7d": retention_rate_7d,
            "retention_rate_30d": retention_rate_30d,
            "velocity_nodes_per_week": round(velocity, 4),
            "estimated_completion_days": estimated_completion_days,
            "struggling_nodes": struggling_nodes,
            "strongest_subtree": strongest_subtree,
            "total_quiz_responses": total_quiz_responses,
            "avg_quality_score": avg_quality_score,
            "sessions_this_period": sessions_this_period,
            "time_of_day_distribution": tod_dist,
        }

        # ------------------------------------------------------------------
        # Upsert into analytics_snapshots
        # ------------------------------------------------------------------
        await conn.execute(
            """
            INSERT INTO education.analytics_snapshots (mind_map_id, snapshot_date, metrics)
            VALUES ($1, $2, $3::jsonb)
            ON CONFLICT (mind_map_id, snapshot_date)
            DO UPDATE SET metrics = EXCLUDED.metrics, created_at = now()
            """,
            mind_map_id,
            snapshot_date,
            json.dumps(metrics),
        )

    return metrics


async def _compute_retention_rate(
    conn: asyncpg.Connection,
    mind_map_id: str,
    *,
    snapshot_date: date,
    days: int,
) -> float | None:
    """Compute retention rate for review-type responses in the last N days.

    Window is anchored to snapshot_date so historical backfills are reproducible.

    Returns float [0.0, 1.0] or None if no review responses in the window.
    """
    row = await conn.fetchrow(
        """
        SELECT
            COUNT(*) AS total_review,
            SUM(CASE WHEN quality >= 3 THEN 1 ELSE 0 END) AS passed_review
        FROM education.quiz_responses
        WHERE mind_map_id = $1
          AND response_type = 'review'
          AND responded_at::date <= $2
          AND responded_at::date > $2 - ($3 || ' days')::interval
        """,
        mind_map_id,
        snapshot_date,
        str(days),
    )
    if row is None or int(row["total_review"]) == 0:
        return None
    return round(int(row["passed_review"]) / int(row["total_review"]), 4)


def _compute_velocity(
    node_rows: list[asyncpg.Record],
    *,
    snapshot_date: date,
) -> float:
    """Compute avg nodes mastered per week over last 4 weeks (28 days).

    Operates in-memory on the already-fetched node_rows so no extra DB query
    is needed. The 28-day window is anchored to snapshot_date for reproducibility.

    Each 7-day bucket counts nodes whose mastery_status='mastered' AND
    updated_at falls within that bucket. Then we average the 4 bucket counts.
    """
    # Anchor the reference point to midnight UTC of snapshot_date
    reference_dt = datetime(
        snapshot_date.year, snapshot_date.month, snapshot_date.day, tzinfo=UTC
    ) + timedelta(days=1)  # end-of-day: count everything up to and including snapshot_date
    cutoff_dt = reference_dt - timedelta(days=28)

    bucket_counts = [0, 0, 0, 0]
    for row in node_rows:
        if row["mastery_status"] != "mastered":
            continue
        updated_at = row["updated_at"]
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        if updated_at < cutoff_dt or updated_at >= reference_dt:
            continue
        days_ago = (reference_dt - updated_at).total_seconds() / 86400
        bucket_idx = int(days_ago // 7)
        if 0 <= bucket_idx <= 3:
            bucket_counts[bucket_idx] += 1

    return sum(bucket_counts) / 4.0


async def _compute_struggling_nodes(
    conn: asyncpg.Connection,
    mind_map_id: str,
) -> list[str]:
    """Return list of node UUIDs where last 5 review responses avg quality < 2.5.

    Only nodes with 5+ review responses are considered.
    """
    rows = await conn.fetch(
        """
        WITH ranked AS (
            SELECT
                node_id,
                quality,
                ROW_NUMBER() OVER (PARTITION BY node_id ORDER BY responded_at DESC) AS rn
            FROM education.quiz_responses
            WHERE mind_map_id = $1
              AND response_type = 'review'
        ),
        last5 AS (
            SELECT node_id, quality FROM ranked WHERE rn <= 5
        ),
        node_counts AS (
            SELECT node_id, COUNT(*) AS cnt, AVG(quality::float) AS avg_q
            FROM last5
            GROUP BY node_id
        )
        SELECT node_id::text AS node_id
        FROM node_counts
        WHERE cnt >= 5 AND avg_q < 2.5
        """,
        mind_map_id,
    )
    return [row["node_id"] for row in rows]


async def _compute_strongest_subtree(
    conn: asyncpg.Connection,
    mind_map_id: str,
) -> str | None:
    """Return node_id with highest average mastery_score in its subtree.

    Uses a recursive CTE to compute subtree mastery for each node.
    Uses UNION (not UNION ALL) to deduplicate rows and prevent infinite
    loops when cyclic edges exist (e.g. 'related' edge types that bypass
    the prerequisite-only DAG check).
    Returns None if the mind map has no nodes.
    """
    row = await conn.fetchrow(
        """
        WITH RECURSIVE subtree(root_id, node_id) AS (
            SELECT id AS root_id, id AS node_id
            FROM education.mind_map_nodes
            WHERE mind_map_id = $1
            UNION
            SELECT s.root_id, e.child_node_id AS node_id
            FROM subtree s
            JOIN education.mind_map_edges e ON e.parent_node_id = s.node_id
            JOIN education.mind_map_nodes n ON n.id = e.child_node_id
            WHERE n.mind_map_id = $1
        ),
        subtree_mastery AS (
            SELECT s.root_id, AVG(n.mastery_score) AS avg_mastery
            FROM subtree s
            JOIN education.mind_map_nodes n ON n.id = s.node_id
            GROUP BY s.root_id
        )
        SELECT root_id::text AS root_id
        FROM subtree_mastery
        ORDER BY avg_mastery DESC
        LIMIT 1
        """,
        mind_map_id,
    )
    return row["root_id"] if row else None


# ---------------------------------------------------------------------------
# analytics_compute_all
# ---------------------------------------------------------------------------


async def analytics_compute_all(
    pool: asyncpg.Pool,
    snapshot_date: date | None = None,
    curriculum_replan: CurriculumReplanCallback | None = None,
) -> int:
    """Compute analytics snapshots for all active mind maps.

    "Active" means: has quiz responses in last 90 days OR has unmastered nodes.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    snapshot_date:
        Date for the snapshots. Defaults to today (UTC).
    curriculum_replan:
        Optional async callback ``(mind_map_id, metrics) -> None`` called when
        struggling_nodes >= 3 or retention_rate_7d < 0.60.

    Returns
    -------
    int
        Number of mind maps processed.
    """
    if snapshot_date is None:
        snapshot_date = datetime.now(tz=UTC).date()

    # Find active mind maps
    rows = await pool.fetch(
        """
        SELECT DISTINCT mm.id::text AS id
        FROM education.mind_maps mm
        WHERE mm.status = 'active'
          AND (
              EXISTS (
                  SELECT 1 FROM education.quiz_responses qr
                  WHERE qr.mind_map_id = mm.id
                    AND qr.responded_at >= now() - INTERVAL '90 days'
              )
              OR EXISTS (
                  SELECT 1 FROM education.mind_map_nodes n
                  WHERE n.mind_map_id = mm.id
                    AND n.mastery_status != 'mastered'
              )
          )
        """,
    )

    count = 0
    for row in rows:
        map_id = row["id"]
        metrics = await analytics_compute_snapshot(pool, map_id, snapshot_date)
        count += 1

        # Feedback loop: signal curriculum_replan when needed
        if curriculum_replan is not None:
            struggling_count = len(metrics.get("struggling_nodes", []))
            retention_7d = metrics.get("retention_rate_7d")
            should_replan = struggling_count >= 3 or (
                retention_7d is not None and retention_7d < 0.60
            )
            if should_replan:
                await curriculum_replan(map_id, metrics)

    return count


# ---------------------------------------------------------------------------
# analytics_get_snapshot
# ---------------------------------------------------------------------------


async def analytics_get_snapshot(
    pool: asyncpg.Pool,
    mind_map_id: str,
    date: date | None = None,
) -> dict[str, Any] | None:
    """Return the latest (or specific-date) analytics snapshot for a mind map.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map.
    date:
        If provided, return the snapshot for this specific date.
        If None, return the most recent snapshot.

    Returns
    -------
    dict or None
        Snapshot row as dict, or None if not found.
    """
    if date is not None:
        row = await pool.fetchrow(
            """
            SELECT id, mind_map_id, snapshot_date, metrics, created_at
            FROM education.analytics_snapshots
            WHERE mind_map_id = $1 AND snapshot_date = $2
            """,
            mind_map_id,
            date,
        )
    else:
        row = await pool.fetchrow(
            """
            SELECT id, mind_map_id, snapshot_date, metrics, created_at
            FROM education.analytics_snapshots
            WHERE mind_map_id = $1
            ORDER BY snapshot_date DESC
            LIMIT 1
            """,
            mind_map_id,
        )

    if row is None:
        return None
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# analytics_get_trend
# ---------------------------------------------------------------------------


async def analytics_get_trend(
    pool: asyncpg.Pool,
    mind_map_id: str,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Return a time-series of snapshots within the last N days, oldest first.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map.
    days:
        Number of days to look back. Defaults to 30.

    Returns
    -------
    list of dict
        Snapshot rows ordered by snapshot_date ASC.
    """
    rows = await pool.fetch(
        """
        SELECT id, mind_map_id, snapshot_date, metrics, created_at
        FROM education.analytics_snapshots
        WHERE mind_map_id = $1
          AND snapshot_date >= (CURRENT_DATE - ($2 || ' days')::interval)::date
        ORDER BY snapshot_date ASC
        """,
        mind_map_id,
        str(days),
    )
    return [_row_to_dict(row) for row in rows]


# ---------------------------------------------------------------------------
# analytics_get_cross_topic
# ---------------------------------------------------------------------------


async def analytics_get_cross_topic(
    pool: asyncpg.Pool,
) -> dict[str, Any]:
    """Return comparative analytics across all active mind maps.

    Uses the most recent snapshot per mind map.

    Returns
    -------
    dict with keys:
        topics                 — list of per-map entries
        strongest_topic        — mind_map_id with highest mastery_pct
        weakest_topic          — mind_map_id with lowest retention_rate_7d (excluding NULL)
        portfolio_mastery      — sum(mastered_nodes) / sum(total_nodes) across all maps
    """
    # Get most recent snapshot per active mind map
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (s.mind_map_id)
            s.mind_map_id::text AS mind_map_id,
            mm.title,
            s.metrics
        FROM education.analytics_snapshots s
        JOIN education.mind_maps mm ON mm.id = s.mind_map_id
        WHERE mm.status = 'active'
        ORDER BY s.mind_map_id, s.snapshot_date DESC
        """,
    )

    topics = []
    total_mastered = 0
    total_nodes = 0

    strongest_topic: str | None = None
    strongest_mastery: float = -1.0
    weakest_topic: str | None = None
    weakest_retention: float = 2.0  # sentinel above max (1.0)

    for row in rows:
        raw_metrics = row["metrics"]
        # asyncpg may return JSONB as dict or string
        if isinstance(raw_metrics, str):
            metrics = json.loads(raw_metrics)
        else:
            metrics = raw_metrics or {}

        map_id = row["mind_map_id"]
        title = row["title"]
        mastery_pct = float(metrics.get("mastery_pct", 0.0))
        retention_7d = metrics.get("retention_rate_7d")
        velocity = float(metrics.get("velocity_nodes_per_week", 0.0))

        # Accumulate portfolio totals
        total_mastered += int(metrics.get("mastered_nodes", 0))
        total_nodes += int(metrics.get("total_nodes", 0))

        topics.append(
            {
                "mind_map_id": map_id,
                "title": title,
                "mastery_pct": mastery_pct,
                "retention_rate_7d": retention_7d,
                "velocity": velocity,
            }
        )

        # Strongest: highest mastery_pct
        if mastery_pct > strongest_mastery:
            strongest_mastery = mastery_pct
            strongest_topic = map_id

        # Weakest: lowest retention_rate_7d (ignore NULL)
        if retention_7d is not None and float(retention_7d) < weakest_retention:
            weakest_retention = float(retention_7d)
            weakest_topic = map_id

    portfolio_mastery = round(total_mastered / total_nodes, 4) if total_nodes > 0 else 0.0

    return {
        "topics": topics,
        "strongest_topic": strongest_topic,
        "weakest_topic": weakest_topic,
        "portfolio_mastery": portfolio_mastery,
    }
