"""Scheduled job handlers for the Relationship butler.

Each job handler:
- Takes db_pool: asyncpg.Pool as first parameter
- Returns a dict with a summary of work done
- Uses the relationship schema tables (contacts, important_dates, facts, etc.)
- Is a no-op (returns early with zeros) when no matching data exists
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Upcoming date insight constants
# ---------------------------------------------------------------------------

_DATE_WINDOW_DAYS = 7  # Scan window for upcoming birthdays/anniversaries

_DATE_CRITICAL_DAYS = 1  # <= 1 day → priority 95
_DATE_URGENT_DAYS = 3  # <= 3 days → priority 80
# anything within 7 days → priority 70

_DATE_PRIORITY_CRITICAL = 95
_DATE_PRIORITY_URGENT = 80
_DATE_PRIORITY_INFO = 70

_DATE_COOLDOWN_CRITICAL = 1
_DATE_COOLDOWN_URGENT = 3
_DATE_COOLDOWN_INFO = 7

# ---------------------------------------------------------------------------
# Stale contact insight constants
# ---------------------------------------------------------------------------

_STALE_PRIORITY_SEVERE = 45  # overdue by > 2x cadence
_STALE_PRIORITY_MODERATE = 35  # overdue by 1–2x cadence
_STALE_EXPIRES_DAYS = 7  # expires 7 days from generation

# ---------------------------------------------------------------------------
# Pending gift insight constants
# ---------------------------------------------------------------------------

_GIFT_WINDOW_DAYS = 14  # Look for upcoming dates within 14 days
_GIFT_PRIORITY = 60
_GIFT_STATUSES = ("idea", "purchased")

# ---------------------------------------------------------------------------
# Interaction milestone insight constants
# ---------------------------------------------------------------------------

_MILESTONE_PRIORITY = 30
_MILESTONE_COOLDOWN_DAYS = 30
_MILESTONE_EXPIRES_DAYS = 7
_MILESTONE_COUNTS = (10, 25, 50, 100, 250, 500)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _parse_date_from_db(value: Any) -> date | None:
    """Parse a date value that may be a date, datetime, or ISO string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Main insight scan job
# ---------------------------------------------------------------------------


async def run_insight_scan(db_pool: asyncpg.Pool) -> dict[str, Any]:
    """Generate proactive insight candidates for the relationship domain.

    Covers four categories:
    1. Upcoming dates   — birthdays/anniversaries in the next 7 days
    2. Stale contacts   — overdue contacts by tier-aware cadence
    3. Pending gifts    — gifts (idea/purchased) for contacts with upcoming dates
    4. Interaction milestones — notable interaction count/anniversary events

    Each candidate is submitted via ``propose_insight_candidate()`` from the
    shared insight broker.  If the broker returns ``{"status": "filtered"}``
    (verbosity=off), no further candidates in that category are submitted and
    the job exits early.

    Args:
        db_pool: Database connection pool.

    Returns:
        Dictionary with keys: candidates_proposed, candidates_accepted,
        candidates_filtered, candidates_errored, early_exit.
    """
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    logger.info("Running relationship insight scan job")

    today = datetime.now(UTC).date()
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
        """Submit one candidate; return False if verbosity=off (early-exit signal)."""
        stats["candidates_proposed"] += 1
        result = await propose_insight_candidate(
            db_pool,
            origin_butler="relationship",
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
                "Relationship insight scan: propose_insight_candidate error: %s",
                result.get("reason", "unknown"),
            )
        return True  # continue submitting

    # -----------------------------------------------------------------------
    # 1. Upcoming date insights (birthdays/anniversaries within 7 days)
    # -----------------------------------------------------------------------
    window_end = today + timedelta(days=_DATE_WINDOW_DAYS)

    date_rows = await db_pool.fetch(
        """
        SELECT
            d.id         AS date_id,
            d.contact_id,
            d.label,
            d.month,
            d.day,
            COALESCE(
                NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.last_name)), ''),
                c.nickname,
                'Unknown'
            ) AS contact_name,
            c.entity_id
        FROM important_dates d
        JOIN contacts c ON d.contact_id = c.id
        WHERE c.listed = true
        ORDER BY d.month, d.day
        """
    )

    for row in date_rows:
        label = (row["label"] or "").lower()
        month = row["month"]
        day = row["day"]
        contact_name = row["contact_name"]
        contact_id = row["contact_id"]
        entity_id = row["entity_id"]

        # Try current year first, then next year for date wrapping
        upcoming_date: date | None = None
        for try_year in [today.year, today.year + 1]:
            try:
                candidate = date(try_year, month, day)
                if today <= candidate <= window_end:
                    upcoming_date = candidate
                    break
            except ValueError:
                # Invalid date (e.g., Feb 29 in non-leap year)
                continue

        if upcoming_date is None:
            continue

        days_until = (upcoming_date - today).days

        if days_until <= _DATE_CRITICAL_DAYS:
            priority = _DATE_PRIORITY_CRITICAL
            cooldown = _DATE_COOLDOWN_CRITICAL
        elif days_until <= _DATE_URGENT_DAYS:
            priority = _DATE_PRIORITY_URGENT
            cooldown = _DATE_COOLDOWN_URGENT
        else:
            priority = _DATE_PRIORITY_INFO
            cooldown = _DATE_COOLDOWN_INFO

        # Determine category (birthday vs anniversary vs other)
        # Dedup key uses shared namespace per spec for cross-butler dedup
        if "birthday" in label or "birth" in label:
            category = "birthday"
            entity_key = str(entity_id) if entity_id else str(contact_id)
            dedup_key = f"birthday:{entity_key}:{upcoming_date.year}"
            message = f"{contact_name}'s birthday is " + (
                "today!" if days_until == 0 else f"in {days_until} day(s)."
            )
        elif "anniversary" in label:
            category = "anniversary"
            entity_key = str(entity_id) if entity_id else str(contact_id)
            dedup_key = f"anniversary:{entity_key}:{upcoming_date.year}"
            message = f"{contact_name}'s anniversary ({label}) is " + (
                "today!" if days_until == 0 else f"in {days_until} day(s)."
            )
        else:
            category = "upcoming-date"
            entity_key = str(entity_id) if entity_id else str(contact_id)
            dedup_key = f"relationship:upcoming-date:{entity_key}:{upcoming_date.isoformat()}"
            message = f"{contact_name} has an upcoming date ({label}) " + (
                "today!" if days_until == 0 else f"in {days_until} day(s)."
            )

        expires_at = datetime(
            upcoming_date.year, upcoming_date.month, upcoming_date.day, tzinfo=UTC
        )
        # expires_at must be in the future; if the date is today add a day buffer
        if expires_at <= now_utc:
            expires_at = now_utc + timedelta(hours=12)

        should_continue = await _submit(
            priority=priority,
            category=category,
            dedup_key=dedup_key,
            message=message,
            expires_at=expires_at,
            cooldown_days=cooldown,
        )
        if not should_continue:
            logger.info(
                "Relationship insight scan: verbosity=off, exiting early after upcoming-dates"
            )
            stats["early_exit"] = True
            return stats

    # -----------------------------------------------------------------------
    # 2. Stale contact insights (tier-aware cadence)
    # -----------------------------------------------------------------------
    from roster.relationship.tools.dunbar import (
        TIER_CADENCES,
        _fetch_overrides,
        compute_dunbar_scores,
        get_tier_ranking,
    )

    # Build dunbar tier map for entity-linked contacts (for tier-based cadences)
    all_scores = await compute_dunbar_scores(db_pool)
    overrides = await _fetch_overrides(db_pool)
    tier_ranking = get_tier_ranking(all_scores, overrides)
    dunbar_by_entity: dict = {entry["entity_id"]: entry for entry in tier_ranking}

    # Query all listed contacts with interaction info (including those without entity_id)
    stale_rows = await db_pool.fetch(
        """
        SELECT
            c.id,
            c.entity_id,
            c.stay_in_touch_days,
            COALESCE(
                NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.last_name)), ''),
                c.nickname,
                'Unknown'
            ) AS contact_name,
            CASE
                WHEN MAX(f.valid_at) IS NULL THEN NULL
                ELSE EXTRACT(EPOCH FROM (now() - MAX(f.valid_at))) / 86400.0
            END AS days_since_last
        FROM contacts c
        LEFT JOIN facts f
            ON f.subject = 'contact:' || c.id::text
           AND f.predicate = 'interaction'
           AND f.scope = 'relationship'
           AND f.validity = 'active'
        WHERE c.listed = true
        GROUP BY c.id, c.entity_id, c.stay_in_touch_days, c.first_name, c.last_name, c.nickname
        ORDER BY c.first_name, c.last_name, c.nickname
        """
    )

    # Compute year-week for dedup key granularity
    iso_year, iso_week, _ = today.isocalendar()
    year_week = f"{iso_year}-W{iso_week:02d}"
    stale_expires_at = now_utc + timedelta(days=_STALE_EXPIRES_DAYS)

    for row in stale_rows:
        contact_id = row["id"]
        entity_id = row["entity_id"]
        stay_in_touch = row["stay_in_touch_days"]
        contact_name = row["contact_name"]

        # Determine effective cadence:
        # 1. Explicit stay_in_touch_days override
        # 2. Tier-based cadence from dunbar scoring (requires entity_id)
        # 3. Tier 1500 → excluded (no cadence)
        if stay_in_touch is not None:
            effective_cadence: int | None = stay_in_touch
        elif entity_id is not None and entity_id in dunbar_by_entity:
            dunbar_tier = dunbar_by_entity[entity_id]["dunbar_tier"]
            effective_cadence = TIER_CADENCES.get(dunbar_tier)
        else:
            # No entity_id or not in dunbar ranking → skip (tier 1500 excluded per spec)
            continue

        # Tier 1500 without stay_in_touch_days → excluded per spec
        if effective_cadence is None:
            continue

        days_since = row["days_since_last"]
        if days_since is None:
            # No interactions ever — treat as fully overdue
            days_since = float(effective_cadence) + 1

        days_since = float(days_since)
        if days_since <= effective_cadence:
            continue  # Not yet overdue

        days_overdue = days_since - effective_cadence

        if days_overdue > effective_cadence:
            priority = _STALE_PRIORITY_SEVERE  # > 2x cadence
        else:
            priority = _STALE_PRIORITY_MODERATE  # 1–2x cadence

        dedup_key = f"relationship:stale-contact:{contact_id}:{year_week}"
        message = (
            f"{contact_name} is overdue for a check-in "
            f"({int(days_since)} days since last interaction, "
            f"cadence: {effective_cadence} days)."
        )

        should_continue = await _submit(
            priority=priority,
            category="stale-contact",
            dedup_key=dedup_key,
            message=message,
            expires_at=stale_expires_at,
        )
        if not should_continue:
            logger.info(
                "Relationship insight scan: verbosity=off, exiting early after stale-contacts"
            )
            stats["early_exit"] = True
            return stats

    # -----------------------------------------------------------------------
    # 3. Pending gift insights (gifts for contacts with upcoming dates)
    # -----------------------------------------------------------------------
    gift_window_end = today + timedelta(days=_GIFT_WINDOW_DAYS)

    # Fetch pending gifts (idea or purchased status) from the facts table
    gift_rows = await db_pool.fetch(
        """
        SELECT
            f.id AS gift_id,
            f.subject,
            f.content AS description,
            f.metadata,
            -- extract contact_id from subject: 'contact:{uuid}:gift:{slug}'
            (regexp_match(f.subject, '^contact:([0-9a-f-]{36}):'))[1]::uuid AS contact_id
        FROM facts f
        WHERE f.predicate = 'gift'
          AND f.scope = 'relationship'
          AND f.validity = 'active'
          AND f.valid_at IS NULL
          AND (f.metadata->>'status' = 'idea' OR f.metadata->>'status' = 'purchased')
        ORDER BY f.created_at ASC
        """
    )

    if gift_rows:
        # Get contact names for these gift recipients
        gift_contact_ids = [row["contact_id"] for row in gift_rows if row["contact_id"]]
        if gift_contact_ids:
            gift_contact_rows = await db_pool.fetch(
                """
                SELECT
                    c.id,
                    COALESCE(
                        NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.last_name)), ''),
                        c.nickname,
                        'Unknown'
                    ) AS contact_name
                FROM contacts c
                WHERE c.id = ANY($1::uuid[])
                """,
                gift_contact_ids,
            )
            gift_contact_name_map = {row["id"]: row["contact_name"] for row in gift_contact_rows}

            # Find contacts with upcoming important dates within 14 days
            contacts_with_upcoming = await db_pool.fetch(
                """
                SELECT DISTINCT d.contact_id, d.month, d.day, d.label
                FROM important_dates d
                JOIN contacts c ON d.contact_id = c.id
                WHERE c.listed = true
                  AND d.contact_id = ANY($1::uuid[])
                ORDER BY d.month, d.day
                """,
                gift_contact_ids,
            )

            # Build map of contact_id → upcoming date info
            contact_upcoming_date: dict = {}
            for row in contacts_with_upcoming:
                cid = row["contact_id"]
                if cid in contact_upcoming_date:
                    continue
                month = row["month"]
                day = row["day"]
                for try_year in [today.year, today.year + 1]:
                    try:
                        candidate = date(try_year, month, day)
                        if today <= candidate <= gift_window_end:
                            contact_upcoming_date[cid] = {
                                "date": candidate,
                                "label": row["label"],
                            }
                            break
                    except ValueError:
                        continue

            for row in gift_rows:
                contact_id = row["contact_id"]
                if contact_id is None:
                    continue

                upcoming_info = contact_upcoming_date.get(contact_id)
                if upcoming_info is None:
                    continue  # No upcoming date for this contact

                gift_id = str(row["gift_id"])
                description = row["description"] or "gift"
                contact_name = gift_contact_name_map.get(contact_id, "Unknown")
                upcoming_date = upcoming_info["date"]
                date_label = upcoming_info["label"]

                dedup_key = f"relationship:pending-gift:{gift_id}"
                expires_at = datetime(
                    upcoming_date.year, upcoming_date.month, upcoming_date.day, tzinfo=UTC
                )
                if expires_at <= now_utc:
                    expires_at = now_utc + timedelta(hours=12)

                days_until = (upcoming_date - today).days
                message = (
                    f"Pending gift for {contact_name}: '{description}' — "
                    f"{contact_name}'s {date_label} is "
                    + ("today!" if days_until == 0 else f"in {days_until} day(s).")
                )

                should_continue = await _submit(
                    priority=_GIFT_PRIORITY,
                    category="pending-gift",
                    dedup_key=dedup_key,
                    message=message,
                    expires_at=expires_at,
                )
                if not should_continue:
                    logger.info(
                        "Relationship insight scan: verbosity=off, "
                        "exiting early after pending-gifts"
                    )
                    stats["early_exit"] = True
                    return stats

    # -----------------------------------------------------------------------
    # 4. Interaction milestone insights
    # -----------------------------------------------------------------------
    milestone_expires_at = now_utc + timedelta(days=_MILESTONE_EXPIRES_DAYS)

    # Count interactions per contact, look for contacts hitting notable milestones
    # Also check for 1-year anniversary of first interaction
    interaction_stats_rows = await db_pool.fetch(
        """
        SELECT
            c.id AS contact_id,
            COALESCE(
                NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.last_name)), ''),
                c.nickname,
                'Unknown'
            ) AS contact_name,
            COUNT(f.id)  AS interaction_count,
            MIN(f.valid_at) AS first_interaction_at
        FROM contacts c
        LEFT JOIN facts f
            ON f.subject = 'contact:' || c.id::text
           AND f.predicate = 'interaction'
           AND f.scope = 'relationship'
           AND f.validity = 'active'
        WHERE c.listed = true
        GROUP BY c.id, c.first_name, c.last_name, c.nickname
        HAVING COUNT(f.id) > 0
        ORDER BY c.first_name, c.last_name, c.nickname
        """
    )

    for row in interaction_stats_rows:
        contact_id = row["contact_id"]
        contact_name = row["contact_name"]
        interaction_count = int(row["interaction_count"])
        first_interaction_at = row["first_interaction_at"]

        # Check notable interaction count milestones
        if interaction_count in _MILESTONE_COUNTS:
            dedup_key = f"relationship:milestone:{contact_id}:count-{interaction_count}"
            message = (
                f"{interaction_count}th interaction with {contact_name} — "
                "a notable connection milestone!"
            )
            should_continue = await _submit(
                priority=_MILESTONE_PRIORITY,
                category="milestone",
                dedup_key=dedup_key,
                message=message,
                expires_at=milestone_expires_at,
                cooldown_days=_MILESTONE_COOLDOWN_DAYS,
            )
            if not should_continue:
                logger.info(
                    "Relationship insight scan: verbosity=off, exiting early after milestones"
                )
                stats["early_exit"] = True
                return stats

        # Check 1-year anniversary of first interaction
        if first_interaction_at is not None:
            first_at = first_interaction_at
            if hasattr(first_at, "tzinfo") and first_at.tzinfo is None:
                first_at = first_at.replace(tzinfo=UTC)
            first_date = first_at.date() if hasattr(first_at, "date") else None
            if first_date is not None:
                # Check if today is the anniversary (same month/day, different year)
                try:
                    anniversary_this_year = date(today.year, first_date.month, first_date.day)
                    years_elapsed = today.year - first_date.year
                    if anniversary_this_year == today and years_elapsed > 0:
                        dedup_key = (
                            f"relationship:milestone:{contact_id}:first-interaction-anniversary"
                        )
                        message = (
                            f"{years_elapsed}-year anniversary of your first interaction "
                            f"with {contact_name}!"
                        )
                        should_continue = await _submit(
                            priority=_MILESTONE_PRIORITY,
                            category="milestone",
                            dedup_key=dedup_key,
                            message=message,
                            expires_at=milestone_expires_at,
                            cooldown_days=_MILESTONE_COOLDOWN_DAYS,
                        )
                        if not should_continue:
                            logger.info(
                                "Relationship insight scan: verbosity=off, "
                                "exiting early after milestones"
                            )
                            stats["early_exit"] = True
                            return stats
                except ValueError:
                    pass  # Feb 29 on non-leap year

    logger.info(
        "Relationship insight scan complete: proposed=%d, accepted=%d, "
        "filtered=%d, errored=%d, early_exit=%s",
        stats["candidates_proposed"],
        stats["candidates_accepted"],
        stats["candidates_filtered"],
        stats["candidates_errored"],
        stats["early_exit"],
    )
    return stats
