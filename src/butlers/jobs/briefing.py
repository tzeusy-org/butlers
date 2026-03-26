"""Cross-butler daily briefing jobs.

This module contains:

* Contribution schema — the standard envelope each specialist butler writes
  to its own state store under ``briefing/daily/<YYYY-MM-DD>``.
* Shared helpers — key generation and cleanup for contribution state entries.
* Specialist contribution jobs — one per domain butler (health, finance,
  relationship, travel, education, home), each querying domain-specific tables
  and writing a contribution envelope.
* ``collect_briefing_contributions`` — the General butler's aggregation job
  that reads all specialist contributions via ``general.v_briefing_contributions``
  and writes a combined payload to ``briefing/combined/<YYYY-MM-DD>``.

Design reference: openspec/changes/cross-butler-daily-briefing/
Tasks reference:   openspec/changes/cross-butler-daily-briefing/tasks.md (sections 2, 3, 5)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta, timezone
from datetime import date as date_cls
from typing import Any, TypedDict

import asyncpg

from butlers.core.state import state_delete, state_list, state_set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SGT = timezone(timedelta(hours=8), name="SGT")

SPECIALIST_BUTLERS: tuple[str, ...] = (
    "education",
    "finance",
    "health",
    "home",
    "lifestyle",
    "relationship",
    "travel",
)

CONTRIBUTION_KEY_PREFIX = "briefing/daily/"
COMBINED_KEY_PREFIX = "briefing/combined/"
CONTRIBUTION_RETENTION_DAYS = 7


# ---------------------------------------------------------------------------
# Contribution schema
# ---------------------------------------------------------------------------


class BriefingHighlight(TypedDict):
    """A single highlight entry within a butler's contribution."""

    category: str
    text: str
    priority: str  # "high" | "medium" | "low"


class BriefingContribution(TypedDict):
    """Standard envelope for a specialist butler's daily briefing contribution."""

    butler: str
    date: str  # ISO date YYYY-MM-DD
    has_updates: bool
    highlights: list[BriefingHighlight]
    summary: str


class CombinedBriefingPayload(TypedDict):
    """Aggregated payload written by the General butler's aggregation job."""

    date: str  # ISO date YYYY-MM-DD
    generated_at: str  # ISO datetime with timezone
    contributions: list[BriefingContribution]
    missing_butlers: list[str]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def today_sgt() -> date_cls:
    """Return today's date in SGT (UTC+8)."""
    return datetime.now(tz=SGT).date()


def contribution_key(date: str) -> str:
    """Return the state store key for a contribution on *date* (YYYY-MM-DD string)."""
    return f"{CONTRIBUTION_KEY_PREFIX}{date}"


def combined_key(date: str) -> str:
    """Return the state store key for the combined briefing on *date* (YYYY-MM-DD string)."""
    return f"{COMBINED_KEY_PREFIX}{date}"


def validate_contribution(raw: Any) -> BriefingContribution:
    """Validate and return a typed contribution dict.

    Required fields: ``butler``, ``date``, ``has_updates``, ``highlights``, ``summary``.

    Raises:
        ValueError: if required fields are missing or have wrong types.

    Returns:
        The validated envelope (same object, typed).
    """
    if not isinstance(raw, dict):
        raise ValueError(f"Contribution envelope must be a dict, got {type(raw).__name__}")

    required_str_fields = ("butler", "date", "summary")
    for field in required_str_fields:
        if field not in raw:
            raise ValueError(f"Contribution envelope missing required field: {field!r}")
        if not isinstance(raw[field], str):
            raise ValueError(
                f"Contribution envelope field {field!r} must be str, "
                f"got {type(raw[field]).__name__}"
            )

    if "has_updates" not in raw:
        raise ValueError("Contribution envelope missing required field: 'has_updates'")
    if not isinstance(raw["has_updates"], bool):
        raise ValueError(
            f"Contribution envelope field 'has_updates' must be bool, "
            f"got {type(raw['has_updates']).__name__}"
        )

    if "highlights" not in raw:
        raise ValueError("Contribution envelope missing required field: 'highlights'")
    if not isinstance(raw["highlights"], list):
        raise ValueError(
            f"Contribution envelope field 'highlights' must be list, "
            f"got {type(raw['highlights']).__name__}"
        )

    for i, h in enumerate(raw["highlights"]):
        if not isinstance(h, dict):
            raise ValueError(f"Highlight[{i}] must be a dict, got {type(h).__name__}")
        for hf in ("category", "text", "priority"):
            if hf not in h:
                raise ValueError(f"Highlight[{i}] missing required field: {hf!r}")
            if not isinstance(h[hf], str):
                raise ValueError(f"Highlight[{i}].{hf} must be str, got {type(h[hf]).__name__}")

    return raw  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Cleanup helper (used by specialist contribution jobs)
# ---------------------------------------------------------------------------


async def delete_old_contributions(pool: asyncpg.Pool, *, today: str) -> int:
    """Delete contribution state entries older than CONTRIBUTION_RETENTION_DAYS.

    Deletes keys matching ``briefing/daily/<date>`` where the date is more than
    ``CONTRIBUTION_RETENTION_DAYS`` days before *today*.

    Returns the number of deleted rows.
    """
    today_dt = date_cls.fromisoformat(today)
    cutoff = today_dt - timedelta(days=CONTRIBUTION_RETENTION_DAYS)

    # Collect all keys with the prefix, then delete those whose date suffix is
    # before the cutoff.  This avoids SQL date parsing of arbitrary key suffixes.
    old_keys: list[str] = await state_list(pool, prefix=CONTRIBUTION_KEY_PREFIX)  # type: ignore[assignment]
    expired_keys = []
    for key in old_keys:
        date_suffix = key[len(CONTRIBUTION_KEY_PREFIX) :]
        try:
            entry_date = date_cls.fromisoformat(date_suffix)
        except ValueError:
            continue
        if entry_date < cutoff:
            expired_keys.append(key)

    for key in expired_keys:
        await state_delete(pool, key)
        logger.debug("Cleaned up stale contribution key: %s", key)

    return len(expired_keys)


# ---------------------------------------------------------------------------
# Internal write helper (used by all specialist contribution jobs)
# ---------------------------------------------------------------------------


async def _write_contribution(
    pool: asyncpg.Pool,
    envelope: BriefingContribution,
) -> None:
    """Write a contribution envelope to the state store and clean up old entries."""
    date_str = envelope["date"]
    key = contribution_key(date_str)
    await state_set(pool, key, envelope)
    logger.info(
        "Wrote daily briefing contribution: butler=%s date=%s has_updates=%s",
        envelope["butler"],
        date_str,
        envelope["has_updates"],
    )
    await delete_old_contributions(pool, today=date_str)


# ---------------------------------------------------------------------------
# Health butler contribution job
# ---------------------------------------------------------------------------


async def run_health_briefing_contribution(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Health butler daily briefing contribution job.

    Queries:
    - Medication adherence today (missed doses)
    - Latest weight measurement
    - Next upcoming appointment (from state or calendar notes — calendar not queried here)

    State key: ``briefing/daily/<YYYY-MM-DD>``
    """
    del job_args

    today_dt = today_sgt()
    today_str = today_dt.isoformat()
    # Compute SGT day boundaries (midnight SGT -> next midnight SGT) and convert to UTC
    sgt_midnight = datetime(today_dt.year, today_dt.month, today_dt.day, tzinfo=SGT)
    today_start = sgt_midnight.astimezone(UTC)
    today_end = (sgt_midnight + timedelta(days=1)).astimezone(UTC)

    highlights: list[BriefingHighlight] = []

    # --- Medication adherence: missed doses today ---
    missed_rows = await pool.fetch(
        """
        SELECT m.name, m.frequency, m.schedule
        FROM medications m
        WHERE m.active = true
          AND NOT EXISTS (
            SELECT 1 FROM medication_doses d
            WHERE d.medication_id = m.id
              AND d.taken_at >= $1
              AND d.taken_at < $2
              AND d.skipped = false
          )
        ORDER BY m.name
        """,
        today_start,
        today_end,
    )

    missed_count = len(missed_rows)
    if missed_count > 0:
        missed_names = ", ".join(r["name"] for r in missed_rows)
        highlights.append(
            {
                "category": "medication",
                "text": f"Missed dose(s) today: {missed_names}",
                "priority": "high",
            }
        )

    # --- Taken doses today (for adherence context) ---
    taken_rows = await pool.fetch(
        """
        SELECT COUNT(*) AS cnt
        FROM medication_doses d
        WHERE d.taken_at >= $1
          AND d.taken_at < $2
          AND d.skipped = false
        """,
        today_start,
        today_end,
    )
    taken_count = taken_rows[0]["cnt"] if taken_rows else 0

    # --- Latest weight measurement (past 7 days) ---
    weight_row = await pool.fetchrow(
        """
        SELECT value, measured_at
        FROM measurements
        WHERE type = 'weight'
          AND measured_at >= $1
        ORDER BY measured_at DESC
        LIMIT 1
        """,
        today_start - timedelta(days=7),
    )

    latest_weight_text: str | None = None
    if weight_row:
        w_val = weight_row["value"]
        # value is JSONB; could be {"value": 70.5, "unit": "kg"} or scalar
        if isinstance(w_val, dict):
            val = w_val.get("value", "")
            unit = w_val.get("unit", "")
            latest_weight_text = f"{val} {unit}".strip()
        else:
            latest_weight_text = str(w_val)
        highlights.append(
            {
                "category": "weight",
                "text": f"Latest weight: {latest_weight_text}",
                "priority": "low",
            }
        )

    has_updates = len(highlights) > 0

    # Build summary
    parts: list[str] = []
    if missed_count > 0:
        parts.append(f"Missed {missed_count} dose(s) today.")
    if taken_count > 0:
        parts.append(f"Took {taken_count} dose(s).")
    if latest_weight_text:
        parts.append(f"Weight: {latest_weight_text}.")
    summary = " ".join(parts) if parts else "No health updates today."

    envelope: BriefingContribution = {
        "butler": "health",
        "date": today_str,
        "has_updates": has_updates,
        "highlights": highlights,
        "summary": summary,
    }

    await _write_contribution(pool, envelope)
    return {
        "butler": "health",
        "date": today_str,
        "has_updates": has_updates,
        "missed_doses": missed_count,
        "taken_doses": taken_count,
    }


# ---------------------------------------------------------------------------
# Finance butler contribution job
# ---------------------------------------------------------------------------


async def run_finance_briefing_contribution(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Finance butler daily briefing contribution job.

    Queries:
    - Bills due within 48 hours (status = 'pending' or 'overdue')
    - Spending anomalies: transactions in the past 7 days that are 2x the 30-day rolling average
      for the same category
    - Subscription renewals this week (next_renewal within 7 days)
    """
    del job_args

    today_dt = today_sgt()
    today_str = today_dt.isoformat()
    now_utc = datetime.now(tz=UTC)
    cutoff_48h = today_dt + timedelta(days=2)
    week_ahead = today_dt + timedelta(days=7)

    highlights: list[BriefingHighlight] = []

    # --- Bills due in 48 hours ---
    bills_rows = await pool.fetch(
        """
        SELECT payee, amount, currency, due_date, status
        FROM bills
        WHERE status IN ('pending', 'overdue')
          AND due_date <= $1
        ORDER BY due_date ASC, amount DESC
        """,
        cutoff_48h,
    )

    for row in bills_rows:
        priority = "high" if row["status"] == "overdue" or row["due_date"] <= today_dt else "medium"
        status_label = "OVERDUE" if row["status"] == "overdue" else f"due {row['due_date']}"
        highlights.append(
            {
                "category": "bills",
                "text": (f"{row['payee']}: {row['currency']} {row['amount']:.2f} ({status_label})"),
                "priority": priority,
            }
        )

    # --- Spending anomalies: 2x rolling average ---
    # Baseline: transactions from 30-37 days ago (7-day-excluded prior period).
    # Recent: transactions from 7 days ago to now.
    # Divide each sum by its actual window length to get a true daily average.
    thirty_days_ago = now_utc - timedelta(days=30)
    seven_days_ago = now_utc - timedelta(days=7)

    anomaly_rows = await pool.fetch(
        """
        WITH rolling_avg AS (
            SELECT category,
                   SUM(amount) / GREATEST(1, EXTRACT(DAY FROM $2 - $1)) AS daily_avg
            FROM transactions
            WHERE direction = 'debit'
              AND posted_at >= $1
              AND posted_at < $2
            GROUP BY category
            HAVING COUNT(*) > 0
        ),
        recent_spend AS (
            SELECT category,
                   SUM(amount) / 7.0 AS daily_recent
            FROM transactions
            WHERE direction = 'debit'
              AND posted_at >= $2
            GROUP BY category
        )
        SELECT r.category,
               r.daily_recent,
               a.daily_avg,
               r.daily_recent / NULLIF(a.daily_avg, 0) AS ratio
        FROM recent_spend r
        JOIN rolling_avg a ON r.category = a.category
        WHERE a.daily_avg > 0
          AND r.daily_recent >= a.daily_avg * 2
        ORDER BY ratio DESC
        """,
        thirty_days_ago,
        seven_days_ago,
    )

    for row in anomaly_rows:
        ratio = float(row["ratio"]) if row["ratio"] is not None else 0.0
        highlights.append(
            {
                "category": "spending",
                "text": (
                    f"Spending anomaly in {row['category']}: {ratio:.1f}x daily average this week"
                ),
                "priority": "medium",
            }
        )

    # --- Subscription renewals this week ---
    sub_rows = await pool.fetch(
        """
        SELECT service, amount, currency, next_renewal, auto_renew
        FROM subscriptions
        WHERE status = 'active'
          AND next_renewal <= $1
        ORDER BY next_renewal ASC
        """,
        week_ahead,
    )

    for row in sub_rows:
        days_until = (row["next_renewal"] - today_dt).days
        auto_label = "auto-renews" if row["auto_renew"] else "manual renewal"
        priority = "medium" if days_until <= 2 else "low"
        highlights.append(
            {
                "category": "subscriptions",
                "text": (
                    f"{row['service']}: {row['currency']} {row['amount']:.2f} "
                    f"({auto_label} in {days_until}d)"
                ),
                "priority": priority,
            }
        )

    has_updates = len(highlights) > 0

    # Build summary
    parts: list[str] = []
    bill_count = len(bills_rows)
    if bill_count:
        parts.append(f"{bill_count} bill(s) due within 48h.")
    if anomaly_rows:
        parts.append(f"{len(anomaly_rows)} spending anomaly/ies detected.")
    if sub_rows:
        parts.append(f"{len(sub_rows)} subscription renewal(s) this week.")
    summary = " ".join(parts) if parts else "No finance updates today."

    envelope: BriefingContribution = {
        "butler": "finance",
        "date": today_str,
        "has_updates": has_updates,
        "highlights": highlights,
        "summary": summary,
    }

    await _write_contribution(pool, envelope)
    return {
        "butler": "finance",
        "date": today_str,
        "has_updates": has_updates,
        "bills_due_48h": bill_count,
        "spending_anomalies": len(anomaly_rows),
        "subscription_renewals": len(sub_rows),
    }


# ---------------------------------------------------------------------------
# Relationship butler contribution job
# ---------------------------------------------------------------------------


async def run_relationship_briefing_contribution(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Relationship butler daily briefing contribution job.

    Queries:
    - Upcoming birthdays in the next 7 days (important_dates with label ILIKE '%birthday%')
    - Reminders that are due or overdue (next_trigger_at <= now)
    - Contacts with interaction gaps exceeding their stay_in_touch_days threshold
    """
    del job_args

    today_dt = today_sgt()
    today_str = today_dt.isoformat()
    now_utc = datetime.now(tz=UTC)

    highlights: list[BriefingHighlight] = []

    # --- Birthdays in the next 7 days ---
    # important_dates has (month, day) — check if (month, day) falls in next 7 days
    birthday_rows = await pool.fetch(
        """
        SELECT c.name, id.label, id.month, id.day, id.year
        FROM important_dates id
        JOIN contacts c ON c.id = id.contact_id
        WHERE LOWER(id.label) LIKE '%birthday%'
          AND EXISTS (
            SELECT 1 FROM unnest($1::int[], $2::int[]) AS t(m, d)
            WHERE t.m = id.month AND t.d = id.day
          )
        ORDER BY id.month, id.day, c.name
        """,
        [d.month for d in (today_dt + timedelta(days=i) for i in range(7))],
        [d.day for d in (today_dt + timedelta(days=i) for i in range(7))],
    )

    for row in birthday_rows:
        days_until = next(
            (
                i
                for i in range(7)
                if (today_dt + timedelta(days=i)).month == row["month"]
                and (today_dt + timedelta(days=i)).day == row["day"]
            ),
            0,
        )
        priority = "high" if days_until == 0 else "medium"
        when = "today" if days_until == 0 else f"in {days_until}d"
        highlights.append(
            {
                "category": "birthdays",
                "text": f"{row['name']} birthday {when} ({row['month']}/{row['day']})",
                "priority": priority,
            }
        )

    # --- Follow-ups due or overdue ---
    reminder_rows = await pool.fetch(
        """
        SELECT c.name, r.label, r.next_trigger_at,
               r.next_trigger_at < now() AS is_overdue
        FROM reminders r
        JOIN contacts c ON c.id = r.contact_id
        WHERE r.next_trigger_at IS NOT NULL
          AND r.next_trigger_at <= $1
        ORDER BY r.next_trigger_at ASC
        LIMIT 10
        """,
        now_utc + timedelta(days=1),  # due today or overdue
    )

    overdue_count = 0
    due_count = 0
    for row in reminder_rows:
        is_overdue = row["is_overdue"]
        priority = "high" if is_overdue else "medium"
        status = "OVERDUE" if is_overdue else "due"
        label = row["label"] or "follow-up"
        highlights.append(
            {
                "category": "follow-ups",
                "text": f"{row['name']}: {label} ({status})",
                "priority": priority,
            }
        )
        if is_overdue:
            overdue_count += 1
        else:
            due_count += 1

    # --- Interaction gaps exceeding stay_in_touch threshold ---
    gap_rows = await pool.fetch(
        """
        SELECT c.name, c.stay_in_touch_days,
               EXTRACT(DAY FROM now() - MAX(i.occurred_at)) AS days_since_last
        FROM contacts c
        JOIN interactions i ON i.contact_id = c.id
        WHERE c.stay_in_touch_days IS NOT NULL
          AND c.archived_at IS NULL
        GROUP BY c.id, c.name, c.stay_in_touch_days
        HAVING EXTRACT(DAY FROM now() - MAX(i.occurred_at)) > c.stay_in_touch_days
        ORDER BY (EXTRACT(DAY FROM now() - MAX(i.occurred_at)) - c.stay_in_touch_days) DESC
        LIMIT 5
        """,
    )

    for row in gap_rows:
        days_gap = int(row["days_since_last"])
        threshold = row["stay_in_touch_days"]
        highlights.append(
            {
                "category": "interaction-gaps",
                "text": (f"{row['name']}: no contact in {days_gap}d (threshold: {threshold}d)"),
                "priority": "low",
            }
        )

    has_updates = len(highlights) > 0

    parts: list[str] = []
    if birthday_rows:
        parts.append(f"{len(birthday_rows)} birthday(s) in next 7 days.")
    if overdue_count:
        parts.append(f"{overdue_count} overdue follow-up(s).")
    if due_count:
        parts.append(f"{due_count} follow-up(s) due.")
    if gap_rows:
        parts.append(f"{len(gap_rows)} contact(s) overdue for check-in.")
    summary = " ".join(parts) if parts else "No relationship updates today."

    envelope: BriefingContribution = {
        "butler": "relationship",
        "date": today_str,
        "has_updates": has_updates,
        "highlights": highlights,
        "summary": summary,
    }

    await _write_contribution(pool, envelope)
    return {
        "butler": "relationship",
        "date": today_str,
        "has_updates": has_updates,
        "birthdays_upcoming": len(birthday_rows),
        "follow_ups_due": due_count,
        "follow_ups_overdue": overdue_count,
        "interaction_gaps": len(gap_rows),
    }


# ---------------------------------------------------------------------------
# Travel butler contribution job
# ---------------------------------------------------------------------------


async def run_travel_briefing_contribution(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Travel butler daily briefing contribution job.

    Queries:
    - Departures (flight legs) within 48 hours
    - Accommodation check-in windows opening today
    - Trips within 48h that have no documents attached
    """
    del job_args

    today_str = today_sgt().isoformat()
    now_utc = datetime.now(tz=UTC)
    cutoff_48h = now_utc + timedelta(hours=48)

    highlights: list[BriefingHighlight] = []

    # --- Departures within 48 hours ---
    dep_rows = await pool.fetch(
        """
        SELECT l.type, l.carrier, l.departure_at, l.departure_city,
               l.arrival_city, l.confirmation_number, l.pnr, l.seat,
               t.name AS trip_name, t.id AS trip_id
        FROM travel.legs l
        JOIN travel.trips t ON t.id = l.trip_id
        WHERE l.departure_at > $1
          AND l.departure_at <= $2
          AND t.status IN ('planned', 'active')
        ORDER BY l.departure_at ASC
        """,
        now_utc,
        cutoff_48h,
    )

    for row in dep_rows:
        hours_until = (row["departure_at"] - now_utc).total_seconds() / 3600
        priority = "high" if hours_until <= 6 else "medium"
        carrier = row["carrier"] or row["type"]
        pnr = f" (PNR: {row['pnr']})" if row["pnr"] else ""
        seat = f" Seat {row['seat']}" if row["seat"] else ""
        highlights.append(
            {
                "category": "departures",
                "text": (
                    f"{carrier}: {row['departure_city']} → {row['arrival_city']} "
                    f"in {hours_until:.0f}h{pnr}{seat}"
                ),
                "priority": priority,
            }
        )

    # --- Check-in windows opening today ---
    checkin_rows = await pool.fetch(
        """
        SELECT a.name, a.check_in, a.type, t.name AS trip_name
        FROM travel.accommodations a
        JOIN travel.trips t ON t.id = a.trip_id
        WHERE a.check_in >= $1
          AND a.check_in < $2
          AND t.status IN ('planned', 'active')
        ORDER BY a.check_in ASC
        """,
        now_utc,
        now_utc + timedelta(days=1),
    )

    for row in checkin_rows:
        name = row["name"] or row["type"]
        highlights.append(
            {
                "category": "check-ins",
                "text": f"Check-in today: {name} ({row['trip_name']})",
                "priority": "medium",
            }
        )

    # --- Missing documents for trips departing within 48h ---
    trips_48h = {row["trip_id"] for row in dep_rows}
    missing_doc_count = 0
    if trips_48h:
        doc_rows = await pool.fetch(
            """
            SELECT t.id, t.name,
                   COUNT(d.id) AS doc_count
            FROM travel.trips t
            LEFT JOIN travel.documents d ON d.trip_id = t.id
            WHERE t.id = ANY($1::uuid[])
            GROUP BY t.id, t.name
            HAVING COUNT(d.id) = 0
            """,
            list(trips_48h),
        )
        for row in doc_rows:
            missing_doc_count += 1
            highlights.append(
                {
                    "category": "documents",
                    "text": f"No documents attached for trip: {row['name']}",
                    "priority": "high",
                }
            )

    has_updates = len(highlights) > 0

    parts: list[str] = []
    if dep_rows:
        parts.append(f"{len(dep_rows)} departure(s) in next 48h.")
    if checkin_rows:
        parts.append(f"{len(checkin_rows)} check-in(s) today.")
    if missing_doc_count:
        parts.append(f"{missing_doc_count} trip(s) with missing documents.")
    summary = " ".join(parts) if parts else "No travel updates today."

    envelope: BriefingContribution = {
        "butler": "travel",
        "date": today_str,
        "has_updates": has_updates,
        "highlights": highlights,
        "summary": summary,
    }

    await _write_contribution(pool, envelope)
    return {
        "butler": "travel",
        "date": today_str,
        "has_updates": has_updates,
        "departures_48h": len(dep_rows),
        "checkins_today": len(checkin_rows),
        "missing_documents": missing_doc_count,
    }


# ---------------------------------------------------------------------------
# Education butler contribution job
# ---------------------------------------------------------------------------


async def run_education_briefing_contribution(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Education butler daily briefing contribution job.

    Queries:
    - Pending spaced-repetition reviews (next_review_at <= now)
    - Streak at-risk detection: any active mind map with >= 3 day streak and no review today
    - Current topic (most recently active mind map)
    """
    del job_args

    today_dt = today_sgt()
    today_str = today_dt.isoformat()
    now_utc = datetime.now(tz=UTC)
    # Compute SGT midnight boundary and convert to UTC for timestamp comparisons
    today_start_sgt = datetime(today_dt.year, today_dt.month, today_dt.day, tzinfo=SGT)
    today_start = today_start_sgt.astimezone(UTC)

    highlights: list[BriefingHighlight] = []

    # --- Pending reviews ---
    pending_rows = await pool.fetch(
        """
        SELECT n.id, n.label, mm.title AS map_title, n.next_review_at
        FROM education.mind_map_nodes n
        JOIN education.mind_maps mm ON mm.id = n.mind_map_id
        WHERE n.next_review_at <= $1
          AND mm.status = 'active'
          AND n.mastery_status NOT IN ('mastered', 'unseen')
        ORDER BY n.next_review_at ASC
        """,
        now_utc,
    )

    pending_count = len(pending_rows)
    if pending_count > 0:
        # Group by mind map
        map_counts: dict[str, int] = {}
        for row in pending_rows:
            map_counts[row["map_title"]] = map_counts.get(row["map_title"], 0) + 1
        for map_title, count in map_counts.items():
            highlights.append(
                {
                    "category": "reviews",
                    "text": f"{count} review(s) pending for {map_title}",
                    "priority": "medium",
                }
            )

    # --- Streak at-risk: active map with >= 3 day streak, no review today ---
    # A "streak" here = the number of consecutive days with quiz_responses
    # We approximate: check if there was a review today vs. the last 3+ days
    streak_risk_rows = await pool.fetch(
        """
        WITH recent_activity AS (
            SELECT mm.id AS map_id, mm.title,
                   COUNT(DISTINCT DATE(qr.responded_at AT TIME ZONE 'UTC')) AS days_active_last_4
            FROM education.mind_maps mm
            LEFT JOIN education.quiz_responses qr
                   ON qr.mind_map_id = mm.id
                  AND qr.responded_at >= $1
            WHERE mm.status = 'active'
            GROUP BY mm.id, mm.title
        )
        SELECT map_id, title, days_active_last_4
        FROM recent_activity
        WHERE days_active_last_4 >= 3
          AND NOT EXISTS (
            SELECT 1 FROM education.quiz_responses qr2
            WHERE qr2.mind_map_id = recent_activity.map_id
              AND qr2.responded_at >= $2
          )
        """,
        now_utc - timedelta(days=4),
        today_start,
    )

    for row in streak_risk_rows:
        highlights.append(
            {
                "category": "streak",
                "text": (
                    f"Streak at risk for {row['title']}: "
                    f"{row['days_active_last_4']} day streak, no review yet today"
                ),
                "priority": "medium",
            }
        )

    # --- Current topic (most recently active mind map) ---
    current_topic_row = await pool.fetchrow(
        """
        SELECT mm.title,
               COUNT(n.id) FILTER (WHERE n.mastery_status = 'mastered') AS mastered,
               COUNT(n.id) AS total
        FROM education.mind_maps mm
        LEFT JOIN education.mind_map_nodes n ON n.mind_map_id = mm.id
        WHERE mm.status = 'active'
        GROUP BY mm.id, mm.title, mm.updated_at
        ORDER BY mm.updated_at DESC
        LIMIT 1
        """,
    )

    if current_topic_row:
        mastered = current_topic_row["mastered"] or 0
        total = current_topic_row["total"] or 0
        progress = f"{mastered}/{total}" if total > 0 else "no nodes"
        highlights.append(
            {
                "category": "current-topic",
                "text": f"Current topic: {current_topic_row['title']} ({progress} mastered)",
                "priority": "low",
            }
        )

    has_updates = len(highlights) > 0

    parts: list[str] = []
    if pending_count:
        parts.append(f"{pending_count} review(s) pending.")
    if streak_risk_rows:
        parts.append(f"{len(streak_risk_rows)} streak(s) at risk.")
    if current_topic_row:
        parts.append(f"Active topic: {current_topic_row['title']}.")
    summary = " ".join(parts) if parts else "No education updates today."

    envelope: BriefingContribution = {
        "butler": "education",
        "date": today_str,
        "has_updates": has_updates,
        "highlights": highlights,
        "summary": summary,
    }

    await _write_contribution(pool, envelope)
    return {
        "butler": "education",
        "date": today_str,
        "has_updates": has_updates,
        "pending_reviews": pending_count,
        "streaks_at_risk": len(streak_risk_rows),
        "current_topic": current_topic_row["title"] if current_topic_row else None,
    }


# ---------------------------------------------------------------------------
# Home butler contribution job
# ---------------------------------------------------------------------------


async def run_home_briefing_contribution(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Home butler daily briefing contribution job.

    Queries:
    - Active device alerts (ha_entity_snapshot with state 'unavailable' or 'unknown')
    - Environment sensor outliers (temperature/humidity outside normal range)
    - Energy anomalies stored in state under 'home/energy/anomaly/*' keys
    """
    del job_args

    today_str = today_sgt().isoformat()

    highlights: list[BriefingHighlight] = []

    # --- Active device alerts (unavailable / unknown entities) ---
    device_alert_rows = await pool.fetch(
        """
        SELECT entity_id, state, attributes
        FROM ha_entity_snapshot
        WHERE state IN ('unavailable', 'unknown')
        ORDER BY entity_id
        """,
    )

    device_alert_count = len(device_alert_rows)
    for row in device_alert_rows[:5]:  # limit to top 5 alerts in highlights
        entity = row["entity_id"]
        attrs = row["attributes"] or {}
        friendly = attrs.get("friendly_name", entity) if isinstance(attrs, dict) else entity
        highlights.append(
            {
                "category": "device-alerts",
                "text": f"Device offline/unavailable: {friendly} ({entity})",
                "priority": "high",
            }
        )

    if device_alert_count > 5:
        highlights.append(
            {
                "category": "device-alerts",
                "text": f"...and {device_alert_count - 5} more device(s) offline",
                "priority": "medium",
            }
        )

    # --- Environment sensor outliers ---
    # Check temperature sensors for values outside 16-30°C (broad indoor tolerance range)
    temp_rows = await pool.fetch(
        """
        SELECT entity_id, state, attributes
        FROM ha_entity_snapshot
        WHERE entity_id LIKE 'sensor.%temperature%'
          AND state ~ '^[0-9]+\\.?[0-9]*$'
        ORDER BY entity_id
        """,
    )

    outlier_count = 0
    for row in temp_rows:
        try:
            temp_val = float(row["state"])
        except (ValueError, TypeError):
            continue
        attrs = row["attributes"] or {}
        unit = attrs.get("unit_of_measurement", "°C") if isinstance(attrs, dict) else "°C"
        friendly = (
            attrs.get("friendly_name", row["entity_id"])
            if isinstance(attrs, dict)
            else row["entity_id"]
        )
        # Flag outliers: below 16°C or above 30°C
        if temp_val < 16.0 or temp_val > 30.0:
            outlier_count += 1
            priority = "high" if temp_val < 10.0 or temp_val > 35.0 else "medium"
            highlights.append(
                {
                    "category": "environment",
                    "text": f"Temperature outlier: {friendly} at {temp_val}{unit}",
                    "priority": priority,
                }
            )

    # --- Energy anomalies from state store ---
    energy_anomaly_keys: list[str] = await state_list(  # type: ignore[assignment]
        pool, prefix="home/energy/anomaly/"
    )
    energy_anomaly_count = len(energy_anomaly_keys)
    if energy_anomaly_count > 0:
        highlights.append(
            {
                "category": "energy",
                "text": f"{energy_anomaly_count} energy anomaly/ies flagged",
                "priority": "medium",
            }
        )

    has_updates = len(highlights) > 0

    parts: list[str] = []
    if device_alert_count:
        parts.append(f"{device_alert_count} device(s) offline/unavailable.")
    if outlier_count:
        parts.append(f"{outlier_count} temperature outlier(s).")
    if energy_anomaly_count:
        parts.append(f"{energy_anomaly_count} energy anomaly/ies.")
    summary = " ".join(parts) if parts else "Home systems nominal."

    envelope: BriefingContribution = {
        "butler": "home",
        "date": today_str,
        "has_updates": has_updates,
        "highlights": highlights,
        "summary": summary,
    }

    await _write_contribution(pool, envelope)
    return {
        "butler": "home",
        "date": today_str,
        "has_updates": has_updates,
        "device_alerts": device_alert_count,
        "temp_outliers": outlier_count,
        "energy_anomalies": energy_anomaly_count,
    }


# ---------------------------------------------------------------------------
# Aggregation job (General butler)
# ---------------------------------------------------------------------------


async def collect_briefing_contributions(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Aggregate specialist briefing contributions into a combined payload.

    Steps:
    1. Determine today's date in SGT.
    2. Query ``general.v_briefing_contributions`` for today's date.
    3. Validate each contribution; log warnings for malformed entries.
    4. Assemble combined payload with ``contributions`` and ``missing_butlers``.
    5. Write to ``briefing/combined/<date>`` via state_set.

    Args:
        pool: asyncpg connection pool for the General butler's database.
        job_args: Optional job arguments (currently unused; reserved for future use).

    Returns:
        Summary dict with ``date``, ``contributions_count``, ``missing_count``,
        ``missing_butlers``, and ``state_key``.
    """
    del job_args  # reserved for future parameterisation

    date_str = today_sgt().isoformat()
    contribution_state_key = contribution_key(date_str)

    # ---------------------------------------------------------------------------
    # Query the cross-schema view for today's contributions
    # ---------------------------------------------------------------------------
    try:
        rows = await pool.fetch(
            """
            SELECT butler, key, value
            FROM general.v_briefing_contributions
            WHERE key = $1
            """,
            contribution_state_key,
        )
    except Exception:
        logger.exception(
            "Failed to query general.v_briefing_contributions for date=%s; "
            "check that the view exists and SELECT grants are active",
            date_str,
        )
        raise

    # ---------------------------------------------------------------------------
    # Validate contributions; track which specialists are present
    # ---------------------------------------------------------------------------
    contributions: list[BriefingContribution] = []
    seen_butlers: set[str] = set()

    for row in rows:
        source_butler: str = row["butler"]
        raw_value = row["value"]

        # Only aggregate contributions from known specialist butlers
        if source_butler not in SPECIALIST_BUTLERS:
            logger.warning(
                "Briefing contribution from unexpected butler=%s; skipping (not in SPECIALIST_BUTLERS)",  # noqa: E501
                source_butler,
            )
            continue

        # Decode JSON if returned as string
        if isinstance(raw_value, str):
            try:
                raw_value = json.loads(raw_value)
            except (json.JSONDecodeError, ValueError):
                logger.warning(
                    "Briefing contribution from butler=%s has invalid JSON; skipping",
                    source_butler,
                )
                continue

        # Validate the envelope
        try:
            contribution = validate_contribution(raw_value)
        except ValueError as exc:
            logger.warning(
                "Briefing contribution from butler=%s failed validation; skipping: %s",
                source_butler,
                exc,
            )
            continue

        # Cross-check: source column must match payload butler field
        if contribution["butler"] != source_butler:
            logger.warning(
                "Briefing contribution butler mismatch: view source=%r, payload butler=%r; "
                "skipping (possible data tampering or misconfiguration)",
                source_butler,
                contribution["butler"],
            )
            continue

        # Cross-check: contribution date must match aggregation date
        if contribution["date"] != date_str:
            logger.warning(
                "Briefing contribution date mismatch for butler=%s: payload date=%r, expected=%r; "
                "skipping",
                source_butler,
                contribution["date"],
                date_str,
            )
            continue

        seen_butlers.add(source_butler)
        contributions.append(contribution)

    # Sort contributions by butler name for deterministic output
    contributions.sort(key=lambda c: c["butler"])

    missing_butlers = sorted(set(SPECIALIST_BUTLERS) - seen_butlers)

    if missing_butlers:
        logger.info(
            "Daily briefing aggregation: missing contributions from %s",
            missing_butlers,
        )

    # ---------------------------------------------------------------------------
    # Assemble and write combined payload
    # ---------------------------------------------------------------------------
    generated_at = datetime.now(tz=UTC).isoformat()
    payload: CombinedBriefingPayload = CombinedBriefingPayload(
        date=date_str,
        generated_at=generated_at,
        contributions=contributions,
        missing_butlers=missing_butlers,
    )

    state_key = combined_key(date_str)
    version = await state_set(pool, state_key, payload)

    logger.info(
        "Daily briefing combined payload written: key=%s, contributions=%d, missing=%d, version=%d",
        state_key,
        len(contributions),
        len(missing_butlers),
        version,
    )

    return {
        "date": date_str,
        "contributions_count": len(contributions),
        "missing_count": len(missing_butlers),
        "missing_butlers": missing_butlers,
        "state_key": state_key,
    }


# ---------------------------------------------------------------------------
# Lifestyle butler contribution job
# ---------------------------------------------------------------------------


async def run_lifestyle_briefing_contribution(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Lifestyle butler daily briefing contribution job.

    Queries recent TRANSIENT memory facts to surface what the user has been
    consuming or enjoying in the past 24 hours:
    - New TRANSIENT facts added today (watches, reads, plays, listens_to)
    - Newly captured DURABLE taste preferences (likes_genre, likes_artist,
      likes_cuisine, favorite_restaurant, hobby, food_preference, food_dislike)
    """
    del job_args

    today_dt = today_sgt()
    today_str = today_dt.isoformat()
    sgt_midnight = datetime(today_dt.year, today_dt.month, today_dt.day, tzinfo=SGT)
    today_start = sgt_midnight.astimezone(UTC)

    highlights: list[BriefingHighlight] = []

    # --- Recent consumption facts (volatile permanence, added today) ---
    transient_rows = await pool.fetch(
        """
        SELECT predicate, content, created_at
        FROM facts
        WHERE validity = 'active'
          AND permanence = 'volatile'
          AND predicate = ANY($1::text[])
          AND created_at >= $2
        ORDER BY created_at DESC
        LIMIT 10
        """,
        ["watches", "reads", "plays", "listens_to"],
        today_start,
    )

    for row in transient_rows:
        predicate = row["predicate"]
        content = row["content"]
        label_map = {
            "watches": "Watching",
            "reads": "Reading",
            "plays": "Playing",
            "listens_to": "Listening to",
        }
        label = label_map.get(predicate, predicate.replace("_", " ").capitalize())
        highlights.append(
            {
                "category": "consumption",
                "text": f"{label}: {content[:100]}",
                "priority": "low",
            }
        )

    # --- Newly captured stable taste preferences (added today) ---
    durable_rows = await pool.fetch(
        """
        SELECT predicate, content, created_at
        FROM facts
        WHERE validity = 'active'
          AND permanence = 'stable'
          AND predicate = ANY($1::text[])
          AND created_at >= $2
        ORDER BY created_at DESC
        LIMIT 5
        """,
        [
            "likes_genre",
            "likes_artist",
            "likes_cuisine",
            "favorite_restaurant",
            "hobby",
            "food_preference",
            "food_dislike",
        ],
        today_start,
    )

    for row in durable_rows:
        predicate = row["predicate"]
        content = row["content"]
        label = predicate.replace("_", " ").capitalize()
        highlights.append(
            {
                "category": "taste-updates",
                "text": f"{label}: {content[:100]}",
                "priority": "low",
            }
        )

    has_updates = len(highlights) > 0

    parts: list[str] = []
    if transient_rows:
        parts.append(f"{len(transient_rows)} new consumption note(s) today.")
    if durable_rows:
        parts.append(f"{len(durable_rows)} taste preference(s) captured today.")
    summary = " ".join(parts) if parts else "No lifestyle updates today."

    envelope: BriefingContribution = {
        "butler": "lifestyle",
        "date": today_str,
        "has_updates": has_updates,
        "highlights": highlights,
        "summary": summary,
    }

    await _write_contribution(pool, envelope)
    return {
        "butler": "lifestyle",
        "date": today_str,
        "has_updates": has_updates,
        "consumption_notes": len(transient_rows),
        "taste_updates": len(durable_rows),
    }


async def run_collect_briefing_contributions(*, pool: asyncpg.Pool) -> dict[str, Any]:
    """Compat shim: daemon registry calls this keyword-only form.

    Delegates to ``collect_briefing_contributions`` with ``job_args=None``.
    """
    return await collect_briefing_contributions(pool, None)
