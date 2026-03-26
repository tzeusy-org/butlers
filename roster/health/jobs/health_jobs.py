"""Scheduled job handlers for the Health butler.

Each job handler:
- Takes db_pool: asyncpg.Pool as first parameter
- Returns a dict with a summary of work done
- Uses async with db_pool.acquire() as conn for queries
- Uses the health schema prefix (health.measurements, health.medications, etc.)
- Is a no-op (returns early with zeros) when no matching data exists
"""

from __future__ import annotations

import logging
import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Insight scan constants
# ---------------------------------------------------------------------------

# Measurement gap: time-since-last vs typical cadence multipliers
_GAP_CRITICAL_MULTIPLIER = 3  # 3x cadence → priority 75
_GAP_WARNING_MULTIPLIER = 2  # 2x cadence → priority 55
_GAP_MIN_HISTORY_COUNT = 3  # minimum historical entries required
_GAP_HISTORY_LIMIT = 10  # last N measurements to compute median cadence

_GAP_PRIORITY_CRITICAL = 75
_GAP_PRIORITY_WARNING = 55
_GAP_EXPIRES_DAYS = 3

# Medication refill: estimated depletion within N days
_REFILL_CRITICAL_DAYS = 3  # priority 90
_REFILL_URGENT_DAYS = 7  # priority 75
_REFILL_WARNING_DAYS = 14  # priority 60

_REFILL_PRIORITY_CRITICAL = 90
_REFILL_PRIORITY_URGENT = 75
_REFILL_PRIORITY_WARNING = 60

# Symptom trend: N occurrences in past M days with severity >= S
_SYMPTOM_WINDOW_DAYS = 7
_SYMPTOM_MIN_COUNT = 3
_SYMPTOM_MIN_SEVERITY = 3  # on a 1-10 scale (spec says 1-5 scale; see implementation note)
_SYMPTOM_PRIORITY = 70
_SYMPTOM_EXPIRES_DAYS = 3

# Health streak milestones (days)
_STREAK_MILESTONES = [7, 30, 60, 90, 180, 365]
_STREAK_PRIORITY = 25
_STREAK_COOLDOWN_DAYS = 30
_STREAK_EXPIRES_DAYS = 7

# Medication frequency factors (doses per day)
_FREQUENCY_DOSES_PER_DAY: dict[str, float] = {
    "daily": 1.0,
    "once daily": 1.0,
    "twice daily": 2.0,
    "twice a day": 2.0,
    "bid": 2.0,
    "three times daily": 3.0,
    "three times a day": 3.0,
    "tid": 3.0,
    "four times daily": 4.0,
    "four times a day": 4.0,
    "qid": 4.0,
    "weekly": 1 / 7,
    "once a week": 1 / 7,
    "every other day": 0.5,
    "as needed": 1.0,  # fallback: assume once daily
    "prn": 1.0,
}


def _frequency_to_doses_per_day(frequency: str) -> float:
    """Convert a textual frequency to doses per day. Defaults to 1.0 if unknown."""
    normalized = frequency.strip().lower()
    return _FREQUENCY_DOSES_PER_DAY.get(normalized, 1.0)


async def run_insight_scan(db_pool: asyncpg.Pool) -> dict[str, Any]:
    """Generate proactive insight candidates for the health domain.

    Covers four categories:
    1. Measurement gaps — types where time since last measurement exceeds 2x typical cadence
    2. Medication refills — active meds estimated to deplete within 14 days
    3. Symptom trends — same symptom 3+ times in 7 days with severity >= 3
    4. Health streaks — consecutive-day logging milestones (7/30/60/90/180/365 days)

    Each candidate is submitted via ``propose_insight_candidate()`` from the shared
    insight broker.  If the broker returns ``{"status": "filtered"}`` (verbosity=off),
    no further candidates are submitted and the job exits early.

    Args:
        db_pool: Database connection pool.

    Returns:
        Dictionary with keys: candidates_proposed, candidates_accepted,
        candidates_filtered, candidates_errored, early_exit.
    """
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    logger.info("Running health insight scan job")

    now_utc = datetime.now(UTC)
    stats: dict[str, Any] = {
        "candidates_proposed": 0,
        "candidates_accepted": 0,
        "candidates_filtered": 0,
        "candidates_errored": 0,
        "early_exit": False,
    }

    async def _submit(
        *,
        priority: int,
        category: str,
        dedup_key: str,
        message: str,
        expires_at: datetime,
        cooldown_days: int | None = None,
    ) -> bool:
        """Submit one candidate; return False if verbosity=off (early exit signal)."""
        stats["candidates_proposed"] += 1
        result = await propose_insight_candidate(
            db_pool,
            origin_butler="health",
            priority=priority,
            category=category,
            dedup_key=dedup_key,
            message=message,
            expires_at=expires_at,
            cooldown_days=cooldown_days,
        )
        status = result.get("status", "error")
        if status == "accepted":
            stats["candidates_accepted"] += 1
        elif status == "filtered":
            stats["candidates_filtered"] += 1
            reason = result.get("reason", "")
            if "verbosity is off" in reason:
                return False  # signal early exit
        else:
            stats["candidates_errored"] += 1
            logger.warning(
                "Health insight scan: propose_insight_candidate error: %s",
                result.get("reason", "unknown"),
            )
        return True  # continue submitting

    # -----------------------------------------------------------------------
    # 1. Measurement gap insights
    # -----------------------------------------------------------------------
    # Get all distinct measurement types tracked in the health schema.
    type_rows = await db_pool.fetch("SELECT DISTINCT type FROM health.measurements ORDER BY type")
    measurement_types = [row["type"] for row in type_rows]

    for mtype in measurement_types:
        # Fetch last N measurements for this type to compute median cadence
        history_rows = await db_pool.fetch(
            """
            SELECT measured_at
            FROM health.measurements
            WHERE type = $1
            ORDER BY measured_at DESC
            LIMIT $2
            """,
            mtype,
            _GAP_HISTORY_LIMIT,
        )

        if len(history_rows) < _GAP_MIN_HISTORY_COUNT:
            # Not enough history to compute reliable cadence
            continue

        # Compute intervals between consecutive measurements (in seconds)
        timestamps = sorted(
            [_parse_datetime_from_db(row["measured_at"]) for row in history_rows],
            reverse=True,
        )
        # Filter out None values
        timestamps = [t for t in timestamps if t is not None]
        if len(timestamps) < _GAP_MIN_HISTORY_COUNT:
            continue

        intervals_seconds: list[float] = []
        for i in range(len(timestamps) - 1):
            delta = timestamps[i] - timestamps[i + 1]
            intervals_seconds.append(delta.total_seconds())

        if not intervals_seconds:
            continue

        median_cadence_seconds = statistics.median(intervals_seconds)
        if median_cadence_seconds <= 0:
            continue

        # Time since most recent measurement
        most_recent = timestamps[0]
        time_since_seconds = (now_utc - most_recent).total_seconds()

        if time_since_seconds < _GAP_WARNING_MULTIPLIER * median_cadence_seconds:
            continue

        if time_since_seconds >= _GAP_CRITICAL_MULTIPLIER * median_cadence_seconds:
            priority = _GAP_PRIORITY_CRITICAL
        else:
            priority = _GAP_PRIORITY_WARNING

        cadence_days = median_cadence_seconds / 86400
        overdue_days = time_since_seconds / 86400

        dedup_key = f"health:measurement-gap:{mtype}"
        expires_at = now_utc + timedelta(days=_GAP_EXPIRES_DAYS)
        message = (
            f"No {mtype} measurement in {overdue_days:.0f} days "
            f"(typical cadence: every {cadence_days:.1f} days). "
            "Consider logging a new measurement."
        )

        should_continue = await _submit(
            priority=priority,
            category="measurement-gap",
            dedup_key=dedup_key,
            message=message,
            expires_at=expires_at,
        )
        if not should_continue:
            logger.info("Health insight scan: verbosity=off, exiting early after measurement-gap")
            stats["early_exit"] = True
            return stats

    # -----------------------------------------------------------------------
    # 2. Medication refill insights
    # -----------------------------------------------------------------------
    med_rows = await db_pool.fetch(
        """
        SELECT id, name, frequency
        FROM health.medications
        WHERE active = true
        ORDER BY name ASC
        """
    )

    for med_row in med_rows:
        med_id = str(med_row["id"])
        med_name = med_row["name"]
        frequency = med_row["frequency"] or "daily"

        doses_per_day = _frequency_to_doses_per_day(frequency)

        # Count doses logged in the past 30 days to estimate supply remaining
        # We use actual logging rate to estimate how fast the supply is depleted.
        # The spec says: depletion estimation based on prescribed frequency vs logged doses.
        # We assume the user logs every dose taken, so dose log rate ≈ consumption rate.
        # Supply estimate requires knowing the initial quantity — since we don't track
        # quantity, we use a proxy: check if there's a gap in recent logging vs expected.
        # The approach: compute how many doses should have been logged in the past N days
        # vs how many were actually logged, then project forward.
        #
        # Simpler heuristic per spec: estimate days of supply based on prescribed frequency
        # and compare to dose logging pattern. We check the last 30 days of doses.
        window_start = now_utc - timedelta(days=30)
        dose_rows = await db_pool.fetch(
            """
            SELECT taken_at
            FROM health.medication_doses
            WHERE medication_id = $1::uuid
              AND taken_at >= $2
              AND skipped = false
            ORDER BY taken_at DESC
            """,
            med_id,
            window_start,
        )

        if not dose_rows:
            # No doses logged in 30 days — can't estimate depletion
            continue

        # Estimate: if expected doses per day and we have N doses logged over D days,
        # actual consumption rate = N / D doses/day.
        # Days of supply remaining = unknown without quantity.
        #
        # Alternative approach per spec: use "dose logging frequency suggests supply runout".
        # We interpret this as: if we know doses_per_day (prescribed) and the last dose
        # was logged recently, estimate when supply would run out based on a standard 30-day
        # supply assumption (common default). Days remaining = supply_days - days_since_fill.
        #
        # Without actual supply quantity in the schema, we estimate based on recent dose
        # frequency. If the user logs at the prescribed rate, we compute when a 30-day
        # supply (the most common refill period) would be exhausted based on the first
        # dose in the current 30-day window.
        earliest_in_window = min(
            _parse_datetime_from_db(row["taken_at"])
            for row in dose_rows
            if _parse_datetime_from_db(row["taken_at"]) is not None
        )
        if earliest_in_window is None:
            continue

        # dose_rows already filters skipped=false, so all rows are non-skipped doses
        doses_logged = len(dose_rows)

        if doses_logged <= 0:
            continue

        # Estimate how many days of supply were consumed based on dose frequency
        # Assume a standard refill is 30 days. Days of supply already used:
        days_consumed = doses_logged / doses_per_day if doses_per_day > 0 else float(doses_logged)

        # Project days remaining assuming a 30-day supply was available at window start
        supply_days = 30  # standard assumption
        days_remaining = max(0.0, supply_days - days_consumed)

        if days_remaining > _REFILL_WARNING_DAYS:
            continue

        if days_remaining <= _REFILL_CRITICAL_DAYS:
            priority = _REFILL_PRIORITY_CRITICAL
        elif days_remaining <= _REFILL_URGENT_DAYS:
            priority = _REFILL_PRIORITY_URGENT
        else:
            priority = _REFILL_PRIORITY_WARNING

        dedup_key = f"health:medication-refill:{med_id}"
        depletion_date = now_utc + timedelta(days=days_remaining)
        expires_at = depletion_date
        message = (
            f"{med_name} supply estimated to run out in {days_remaining:.0f} day(s) "
            f"(~{depletion_date.strftime('%b %d')}). Consider requesting a refill."
        )

        should_continue = await _submit(
            priority=priority,
            category="medication-refill",
            dedup_key=dedup_key,
            message=message,
            expires_at=expires_at,
        )
        if not should_continue:
            logger.info("Health insight scan: verbosity=off, exiting early after medication-refill")
            stats["early_exit"] = True
            return stats

    # -----------------------------------------------------------------------
    # 3. Symptom trend insights
    # -----------------------------------------------------------------------
    symptom_window_start = now_utc - timedelta(days=_SYMPTOM_WINDOW_DAYS)
    symptom_rows = await db_pool.fetch(
        """
        SELECT name, severity, occurred_at
        FROM health.symptoms
        WHERE occurred_at >= $1
          AND severity >= $2
        ORDER BY name, occurred_at DESC
        """,
        symptom_window_start,
        _SYMPTOM_MIN_SEVERITY,
    )

    # Group by symptom name
    symptom_groups: dict[str, list[int]] = {}
    for row in symptom_rows:
        name = row["name"]
        severity = row["severity"]
        if name not in symptom_groups:
            symptom_groups[name] = []
        symptom_groups[name].append(severity)

    # Determine ISO year-week for dedup_key
    year_week = now_utc.strftime("%Y-W%W")

    for symptom_name, severities in symptom_groups.items():
        if len(severities) < _SYMPTOM_MIN_COUNT:
            continue

        avg_severity = sum(severities) / len(severities)
        dedup_key = f"health:symptom-trend:{symptom_name}:{year_week}"
        expires_at = now_utc + timedelta(days=_SYMPTOM_EXPIRES_DAYS)
        message = (
            f"{symptom_name} has been logged {len(severities)} times in the past "
            f"{_SYMPTOM_WINDOW_DAYS} days (average severity {avg_severity:.1f}/10). "
            "Consider tracking patterns or consulting a healthcare provider."
        )

        should_continue = await _submit(
            priority=_SYMPTOM_PRIORITY,
            category="symptom-trend",
            dedup_key=dedup_key,
            message=message,
            expires_at=expires_at,
        )
        if not should_continue:
            logger.info("Health insight scan: verbosity=off, exiting early after symptom-trend")
            stats["early_exit"] = True
            return stats

    # -----------------------------------------------------------------------
    # 4. Health streak insights
    # -----------------------------------------------------------------------
    # Check consecutive-day logging streaks for each measurement type.
    # A "streak" is consecutive calendar days with at least one measurement of that type.
    streak_type_rows = await db_pool.fetch(
        "SELECT DISTINCT type FROM health.measurements ORDER BY type"
    )

    for type_row in streak_type_rows:
        mtype = type_row["type"]

        # Fetch all distinct calendar days for this type, most recent first
        streak_rows = await db_pool.fetch(
            """
            SELECT DISTINCT DATE(measured_at AT TIME ZONE 'UTC') AS day
            FROM health.measurements
            WHERE type = $1
            ORDER BY day DESC
            """,
            mtype,
        )

        if not streak_rows:
            continue

        # Compute current streak: count consecutive days from today backward
        today_date = now_utc.date()
        logged_days = {row["day"] for row in streak_rows}

        streak_count = 0
        check_date = today_date
        while check_date in logged_days:
            streak_count += 1
            check_date -= timedelta(days=1)

        if streak_count == 0:
            continue

        # Check if streak_count hits any milestone
        for milestone in _STREAK_MILESTONES:
            if streak_count == milestone:
                dedup_key = f"health:streak:{mtype}:{milestone}"
                expires_at = now_utc + timedelta(days=_STREAK_EXPIRES_DAYS)
                message = (
                    f"{streak_count}-day streak of logging {mtype} measurements! "
                    "Keep it up — consistent tracking helps identify health patterns."
                )

                should_continue = await _submit(
                    priority=_STREAK_PRIORITY,
                    category="health-streak",
                    dedup_key=dedup_key,
                    message=message,
                    expires_at=expires_at,
                    cooldown_days=_STREAK_COOLDOWN_DAYS,
                )
                if not should_continue:
                    logger.info(
                        "Health insight scan: verbosity=off, exiting early after health-streak"
                    )
                    stats["early_exit"] = True
                    return stats
                break  # only one milestone per type per run

    logger.info(
        "Health insight scan complete: proposed=%d, accepted=%d, "
        "filtered=%d, errored=%d, early_exit=%s",
        stats["candidates_proposed"],
        stats["candidates_accepted"],
        stats["candidates_filtered"],
        stats["candidates_errored"],
        stats["early_exit"],
    )
    return stats


def _parse_datetime_from_db(value: Any) -> datetime | None:
    """Parse a datetime value from a database row which may be a datetime or string."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            return None
    return None
