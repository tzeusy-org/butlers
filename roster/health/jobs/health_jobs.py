"""Scheduled job handlers for the Health butler.

Each job handler:
- Takes db_pool: asyncpg.Pool as first parameter
- Returns a dict with a summary of work done
- Issues queries directly via db_pool.fetch() / db_pool.fetchrow() / db_pool.execute()
- Reads from the ``facts`` table (resolved via search_path to public.facts) by
  predicate — measurements (``measurement_{type}``), medications (``medication``),
  doses (``took_dose``), and symptoms (``symptom``), all scope='health'. This is
  the same surface the butler's MCP tools write; the legacy health.* relational
  tables (medications, medication_doses, symptoms) are orphaned and never read.
- Is a no-op (returns early with zeros) when no matching data exists
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.tools.health._medication_utils import (
    frequency_to_doses_per_day as _frequency_to_doses_per_day,
)

# A reader that returns Home Assistant environmental readings owned by the
# ``home`` butler. Each reading is a dict with at least::
#     {"captured_at": datetime, "metric": str, "adverse": bool}
# where ``metric`` is e.g. "temperature" or "air_quality" and ``adverse`` marks
# a reading outside the home butler's comfort range (the threshold logic lives
# with the ``home`` butler, never duplicated here). The health insight-scan job
# MUST NOT read the home butler's schema directly; environmental data is reached
# via cross-butler MCP/Switchboard, surfaced through this injected reader.
HaEnvironmentReader = Callable[[], Awaitable[list[dict[str, Any]]]]

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

# ---------------------------------------------------------------------------
# Cross-signal correlation constants
# ---------------------------------------------------------------------------
# Correlation candidates surface co-occurring signals, never causal claims. They
# expire after a week and carry a moderate priority. Messages are framed as
# observations ("coincided with", "the overlap may be worth tracking") so they
# pass the dashboard voice lint and make no diagnostic assertions.
_CORRELATION_EXPIRES_DAYS = 7

# 1. Adherence dip preceding a symptom flare
#    Earlier window = where adherence is measured; later window = where flares
#    are measured. A dip is non-skipped doses below half the prescribed schedule;
#    a flare is several higher-severity symptom entries in the following window.
_ADHERENCE_DIP_WINDOW_DAYS = 7
_ADHERENCE_FLARE_WINDOW_DAYS = 7
_ADHERENCE_DIP_MAX_RATIO = 0.5
_FLARE_MIN_SYMPTOMS = 2
_FLARE_MIN_SEVERITY = 3
_CORRELATION_ADHERENCE_PRIORITY = 60

# 2. Slow measurement drift
#    Compare the median of the oldest third of recent readings against the
#    newest third; a sustained relative change flags a gradual drift.
_DRIFT_HISTORY_LIMIT = 20
_DRIFT_MIN_HISTORY = 6
_DRIFT_MIN_RELATIVE_CHANGE = 0.10
_CORRELATION_DRIFT_PRIORITY = 50

# 3. Home Assistant environment co-occurring with poor sleep / symptoms
#    A reading is "adverse" per the home butler's comfort thresholds; we count
#    days where an adverse reading co-occurs with short sleep or a symptom entry.
_ENV_SLEEP_SHORT_HOURS = 6.0
_ENV_CORRELATION_MIN_DAYS = 2
_CORRELATION_ENV_PRIORITY = 50


async def run_insight_scan(
    db_pool: asyncpg.Pool,
    ha_environment_reader: HaEnvironmentReader | None = None,
) -> dict[str, Any]:
    """Generate proactive insight candidates for the health domain.

    Covers four single-signal categories:
    1. Measurement gaps — types where time since last measurement exceeds 2x typical cadence
    2. Medication refills — active meds estimated to deplete within 14 days
    3. Symptom trends — same symptom 3+ times in 7 days with severity >= 3
    4. Health streaks — consecutive-day logging milestones (7/30/60/90/180/365 days)

    Plus three cross-signal correlation categories (co-occurrence framing only,
    never causal claims):
    5. Adherence dip preceding a symptom flare — a week of low medication
       adherence followed by a week with several higher-severity symptom entries.
    6. Slow measurement drift — a measurement type whose median has gradually
       shifted across recent readings.
    7. Environment overlap — Home Assistant environmental readings (e.g. bedroom
       temperature, air quality) outside the comfort range co-occurring with
       short sleep or symptom entries. This runs only when ``ha_environment_reader``
       is provided; environmental data owned by the ``home`` butler is reached via
       cross-butler MCP/Switchboard, never a direct cross-schema read.

    Each candidate is submitted via ``propose_insight_candidate()`` from the shared
    insight broker.  If the broker returns ``{"status": "filtered"}`` (verbosity=off),
    no further candidates are submitted and the job exits early.

    Args:
        db_pool: Database connection pool.
        ha_environment_reader: Optional async callable returning Home Assistant
            environmental readings (see ``HaEnvironmentReader``). When ``None``
            (the current default in the scheduled job, pending cross-butler MCP
            wiring), the environment correlation is skipped.

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
    # Get all distinct measurement types tracked in the facts table.
    # Each measurement type is encoded in the predicate as "measurement_{type}".
    type_rows = await db_pool.fetch(
        """
        SELECT DISTINCT predicate
        FROM facts
        WHERE predicate LIKE 'measurement~_%' ESCAPE '~'
          AND scope = 'health'
          AND validity = 'active'
        ORDER BY predicate
        """
    )
    measurement_types = [row["predicate"].removeprefix("measurement_") for row in type_rows]

    for mtype in measurement_types:
        # Fetch last N measurements for this type to compute median cadence
        predicate = f"measurement_{mtype}"
        history_rows = await db_pool.fetch(
            """
            SELECT valid_at AS measured_at
            FROM facts
            WHERE predicate = $1
              AND scope = 'health'
              AND validity = 'active'
              AND valid_at IS NOT NULL
            ORDER BY valid_at DESC NULLS LAST
            LIMIT $2
            """,
            predicate,
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
    # Active medications are property facts (predicate='medication', scope='health'),
    # mirroring the dashboard API read surface (roster/health/api/router.py
    # GET /medications) and the medication_add write path: name/frequency/active
    # live in metadata. The legacy health.medications relational table is orphaned.
    med_rows = await db_pool.fetch(
        """
        SELECT id, metadata->>'name' AS name, metadata->>'frequency' AS frequency
        FROM facts
        WHERE predicate = 'medication'
          AND validity = 'active'
          AND scope = 'health'
          AND (metadata->>'active')::boolean = true
        ORDER BY metadata->>'name' ASC
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
        # Doses are temporal facts (predicate='took_dose', scope='health') with
        # valid_at=taken_at; medication_id and skipped live in metadata. Mirrors the
        # medication_log_dose write path and the dashboard GET /medications/{id}/doses
        # read surface. The legacy health.medication_doses relational table is orphaned.
        dose_rows = await db_pool.fetch(
            """
            SELECT valid_at AS taken_at
            FROM facts
            WHERE predicate = 'took_dose'
              AND validity = 'active'
              AND scope = 'health'
              AND metadata->>'medication_id' = $1
              AND valid_at >= $2
              AND COALESCE((metadata->>'skipped')::boolean, false) = false
            ORDER BY valid_at DESC
            """,
            med_id,
            window_start,
        )

        if not dose_rows:
            # No doses logged in 30 days — can't estimate depletion
            continue

        # Estimate depletion using a standard 30-day supply assumption.
        # Without an explicit supply quantity in the schema, we compute how many days
        # of a 30-day supply have been consumed based on the prescribed dose frequency:
        #   days_consumed = doses_logged / doses_per_day
        # Days remaining = 30 - days_consumed.  If remaining <= threshold, fire insight.
        #
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
    # Symptoms are temporal facts (predicate='symptom', scope='health') with
    # content=name, valid_at=occurred_at, and severity in metadata. Mirrors the
    # symptom_log write path and the dashboard GET /symptoms read surface. The
    # legacy health.symptoms relational table is orphaned.
    symptom_rows = await db_pool.fetch(
        """
        SELECT content AS name,
               (metadata->>'severity')::int AS severity,
               valid_at AS occurred_at
        FROM facts
        WHERE predicate = 'symptom'
          AND validity = 'active'
          AND scope = 'health'
          AND valid_at >= $1
          AND (metadata->>'severity')::int >= $2
        ORDER BY content, valid_at DESC
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
    # Reuse measurement_types from the gap-detection query above — no second round-trip needed.
    for mtype in measurement_types:
        predicate = f"measurement_{mtype}"

        # Fetch all distinct calendar days for this type, most recent first
        streak_rows = await db_pool.fetch(
            """
            SELECT DISTINCT DATE(valid_at AT TIME ZONE 'UTC') AS day
            FROM facts
            WHERE predicate = $1
              AND scope = 'health'
              AND validity = 'active'
              AND valid_at IS NOT NULL
            ORDER BY day DESC
            """,
            predicate,
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

    # -----------------------------------------------------------------------
    # 5. Cross-signal correlation: adherence dip preceding a symptom flare
    # -----------------------------------------------------------------------
    if not await _scan_adherence_symptom_correlation(db_pool, now_utc, _submit, med_rows):
        logger.info("Health insight scan: verbosity=off, exiting early after adherence correlation")
        stats["early_exit"] = True
        return stats

    # -----------------------------------------------------------------------
    # 6. Cross-signal correlation: slow measurement drift
    # -----------------------------------------------------------------------
    if not await _scan_measurement_drift_correlation(db_pool, now_utc, _submit, measurement_types):
        logger.info("Health insight scan: verbosity=off, exiting early after drift correlation")
        stats["early_exit"] = True
        return stats

    # -----------------------------------------------------------------------
    # 7. Cross-signal correlation: HA environment vs sleep / symptoms
    # -----------------------------------------------------------------------
    if ha_environment_reader is not None:
        if not await _scan_environment_correlation(
            db_pool, now_utc, _submit, ha_environment_reader
        ):
            logger.info(
                "Health insight scan: verbosity=off, exiting early after environment correlation"
            )
            stats["early_exit"] = True
            return stats
    else:
        logger.info(
            "Health insight scan: no HA environment reader wired; skipping environment correlation"
        )

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


_SubmitFn = Callable[..., Awaitable[bool]]


async def _scan_adherence_symptom_correlation(
    db_pool: asyncpg.Pool,
    now_utc: datetime,
    submit: _SubmitFn,
    med_rows: list[Any],
) -> bool:
    """Correlate a recent medication-adherence dip with a following symptom flare.

    For each active medication, the prior week (``[now-14d, now-7d)``) is the
    *dip window* and the most recent week (``[now-7d, now]``) is the *flare
    window*. A candidate is proposed when the user normally logs the medication
    (dose history exists before the dip window), non-skipped doses in the dip
    window fall below half the prescribed schedule, and several higher-severity
    symptom entries land in the flare window. The framing is co-occurrence only.

    ``med_rows`` is the already-fetched list of active medication rows from the
    caller (same query as section 2 in ``run_insight_scan``); passing it avoids
    a redundant database round-trip.

    Returns False when the broker signals verbosity=off (caller exits early).
    """
    dip_start = now_utc - timedelta(days=_ADHERENCE_DIP_WINDOW_DAYS + _ADHERENCE_FLARE_WINDOW_DAYS)
    dip_end = now_utc - timedelta(days=_ADHERENCE_FLARE_WINDOW_DAYS)
    prior_start = dip_start - timedelta(days=_ADHERENCE_DIP_WINDOW_DAYS)
    flare_start = dip_end
    year_week = now_utc.strftime("%Y-W%W")

    # A symptom flare is shared across medications; count once per run.
    flare_count = await db_pool.fetchval(
        """
        SELECT count(*)
        FROM facts
        WHERE predicate = 'symptom'
          AND validity = 'active'
          AND scope = 'health'
          AND valid_at >= $1
          AND (metadata->>'severity')::int >= $2
        """,
        flare_start,
        _FLARE_MIN_SEVERITY,
    )
    if (flare_count or 0) < _FLARE_MIN_SYMPTOMS:
        return True  # no flare to correlate against

    for med_row in med_rows:
        med_id = str(med_row["id"])
        med_name = med_row["name"]
        frequency = med_row["frequency"] or "daily"
        doses_per_day = _frequency_to_doses_per_day(frequency)

        expected_doses = doses_per_day * _ADHERENCE_DIP_WINDOW_DAYS
        if expected_doses <= 0:
            continue

        # The user must normally log this medication (history before the dip).
        prior_doses = await db_pool.fetchval(
            """
            SELECT count(*)
            FROM facts
            WHERE predicate = 'took_dose'
              AND validity = 'active'
              AND scope = 'health'
              AND metadata->>'medication_id' = $1
              AND valid_at >= $2
              AND valid_at < $3
              AND COALESCE((metadata->>'skipped')::boolean, false) = false
            """,
            med_id,
            prior_start,
            dip_start,
        )
        if (prior_doses or 0) <= 0:
            continue

        dip_doses = await db_pool.fetchval(
            """
            SELECT count(*)
            FROM facts
            WHERE predicate = 'took_dose'
              AND validity = 'active'
              AND scope = 'health'
              AND metadata->>'medication_id' = $1
              AND valid_at >= $2
              AND valid_at < $3
              AND COALESCE((metadata->>'skipped')::boolean, false) = false
            """,
            med_id,
            dip_start,
            dip_end,
        )

        adherence_ratio = (dip_doses or 0) / expected_doses
        if adherence_ratio > _ADHERENCE_DIP_MAX_RATIO:
            continue

        dedup_key = f"health:correlation-adherence:{med_id}:{year_week}"
        expires_at = now_utc + timedelta(days=_CORRELATION_EXPIRES_DAYS)
        message = (
            f"{med_name} adherence dropped to about {adherence_ratio * 100:.0f}% of the "
            f"usual schedule in the prior week, and {flare_count} higher-severity symptom "
            f"entries were logged in the {_ADHERENCE_FLARE_WINDOW_DAYS} days that followed. "
            "Reviewing the timing of the two together may be worthwhile."
        )

        if not await submit(
            priority=_CORRELATION_ADHERENCE_PRIORITY,
            category="correlation-adherence",
            dedup_key=dedup_key,
            message=message,
            expires_at=expires_at,
        ):
            return False

    return True


async def _scan_measurement_drift_correlation(
    db_pool: asyncpg.Pool,
    now_utc: datetime,
    submit: _SubmitFn,
    measurement_types: list[str],
) -> bool:
    """Detect a slow drift in a measurement type's median across recent readings.

    The most recent ``_DRIFT_HISTORY_LIMIT`` numeric readings of a type are split
    into an oldest third and a newest third; a sustained relative change between
    their medians flags a gradual drift. The framing is observational only.

    ``measurement_types`` is the already-computed list of type suffixes (e.g.
    ``["weight", "glucose"]``) from the caller's section-1 query; passing it
    avoids a redundant ``SELECT DISTINCT predicate`` round-trip.

    Returns False when the broker signals verbosity=off (caller exits early).
    """
    year_week = now_utc.strftime("%Y-W%W")

    for mtype in measurement_types:
        predicate = f"measurement_{mtype}"
        rows = await db_pool.fetch(
            """
            SELECT metadata->>'value' AS value
            FROM facts
            WHERE predicate = $1
              AND scope = 'health'
              AND validity = 'active'
              AND valid_at IS NOT NULL
            ORDER BY valid_at DESC
            LIMIT $2
            """,
            predicate,
            _DRIFT_HISTORY_LIMIT,
        )

        # Newest-first numeric values.
        values: list[float] = []
        for row in rows:
            parsed = _parse_float(row["value"])
            if parsed is not None:
                values.append(parsed)

        if len(values) < _DRIFT_MIN_HISTORY:
            continue

        third = len(values) // 3
        if third == 0:
            continue
        newest = values[:third]
        oldest = values[-third:]

        old_median = statistics.median(oldest)
        new_median = statistics.median(newest)
        if old_median == 0:
            continue

        relative_change = abs(new_median - old_median) / abs(old_median)
        if relative_change < _DRIFT_MIN_RELATIVE_CHANGE:
            continue

        direction = "upward" if new_median > old_median else "downward"
        dedup_key = f"health:correlation-drift:{mtype}:{year_week}"
        expires_at = now_utc + timedelta(days=_CORRELATION_EXPIRES_DAYS)
        message = (
            f"{mtype} readings show a gradual {direction} drift, from about "
            f"{old_median:.1f} to about {new_median:.1f} across the last {len(values)} "
            "entries. The slow shift may be worth a closer look."
        )

        if not await submit(
            priority=_CORRELATION_DRIFT_PRIORITY,
            category="correlation-drift",
            dedup_key=dedup_key,
            message=message,
            expires_at=expires_at,
        ):
            return False

    return True


async def _scan_environment_correlation(
    db_pool: asyncpg.Pool,
    now_utc: datetime,
    submit: _SubmitFn,
    ha_environment_reader: HaEnvironmentReader,
) -> bool:
    """Correlate adverse Home Assistant environment readings with poor sleep / symptoms.

    ``ha_environment_reader`` returns readings owned by the ``home`` butler,
    reached via cross-butler MCP/Switchboard (never a direct cross-schema read).
    For each environmental metric, count the recent calendar days where an
    *adverse* reading co-occurs with short sleep or a logged symptom; a recurring
    overlap proposes a co-occurrence candidate.

    Returns False when the broker signals verbosity=off (caller exits early).
    """
    window_start = now_utc - timedelta(days=14)
    year_week = now_utc.strftime("%Y-W%W")

    readings = await ha_environment_reader()
    if not readings:
        return True

    # Calendar days (UTC) with short sleep.
    sleep_rows = await db_pool.fetch(
        """
        SELECT valid_at, metadata->>'duration_ms' AS duration_ms
        FROM facts
        WHERE predicate = 'sleep_session'
          AND scope = 'health'
          AND validity = 'active'
          AND valid_at >= $1
        """,
        window_start,
    )
    short_sleep_days: set[Any] = set()
    for row in sleep_rows:
        dt = _parse_datetime_from_db(row["valid_at"])
        duration_ms = _parse_float(row["duration_ms"])
        if dt is None or duration_ms is None:
            continue
        if duration_ms / 3_600_000 < _ENV_SLEEP_SHORT_HOURS:
            short_sleep_days.add(dt.date())

    # Calendar days (UTC) with a logged symptom.
    symptom_rows = await db_pool.fetch(
        """
        SELECT valid_at
        FROM facts
        WHERE predicate = 'symptom'
          AND scope = 'health'
          AND validity = 'active'
          AND valid_at >= $1
        """,
        window_start,
    )
    symptom_days: set[Any] = set()
    for row in symptom_rows:
        dt = _parse_datetime_from_db(row["valid_at"])
        if dt is not None:
            symptom_days.add(dt.date())

    health_signal_days = short_sleep_days | symptom_days
    if not health_signal_days:
        return True

    # metric -> set of calendar days with an adverse reading co-occurring with a signal.
    overlap_by_metric: dict[str, set[Any]] = {}
    for reading in readings:
        if not reading.get("adverse"):
            continue
        captured = _parse_datetime_from_db(reading.get("captured_at"))
        metric = reading.get("metric")
        if captured is None or not metric:
            continue
        if captured < window_start:
            continue
        day = captured.date()
        if day in health_signal_days:
            overlap_by_metric.setdefault(metric, set()).add(day)

    _metric_labels = {
        "temperature": "bedroom temperature",
        "air_quality": "bedroom air quality",
    }

    for metric in sorted(overlap_by_metric):
        overlap_days = overlap_by_metric[metric]
        if len(overlap_days) < _ENV_CORRELATION_MIN_DAYS:
            continue

        label = _metric_labels.get(metric, f"bedroom {metric.replace('_', ' ')}")
        dedup_key = f"health:correlation-env:{metric}:{year_week}"
        expires_at = now_utc + timedelta(days=_CORRELATION_EXPIRES_DAYS)
        message = (
            f"On {len(overlap_days)} recent days, {label} ran outside the comfort range "
            "and shorter sleep or a symptom entry was logged the same day. "
            "The overlap may be worth tracking."
        )

        if not await submit(
            priority=_CORRELATION_ENV_PRIORITY,
            category="correlation-environment",
            dedup_key=dedup_key,
            message=message,
            expires_at=expires_at,
        ):
            return False

    return True


def _parse_float(value: Any) -> float | None:
    """Parse a float from a DB value (number or numeric string); None if not numeric."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


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
