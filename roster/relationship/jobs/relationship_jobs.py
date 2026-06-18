"""Scheduled job handlers for the Relationship butler.

Each job handler:
- Takes db_pool: asyncpg.Pool as first parameter
- Returns a dict with a summary of work done
- Uses the relationship schema tables (contacts, important_dates, facts, etc.)
- Is a no-op (returns early with zeros) when no matching data exists
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg

from butlers.core.state import state_get, state_set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Memory curation constants
# ---------------------------------------------------------------------------

# State key for recording the last backfill run timestamp (observability only;
# the backfill is idempotent so re-running is safe even without the checkpoint).
_CURATION_STATE_KEY = "memory_curation.last_backfill_at"

# Alias predicate names (underscore / legacy forms) that relationship_assert_fact
# already normalises via its internal _PREDICATE_ALIAS_MAP.  We include them here
# so that prose facts stored with the legacy name are also picked up by the sweep.
_ALIAS_PREDICATES: frozenset[str] = frozenset(
    {
        "works_at",
        "friend_of",
        "child_of",
        "parent_of",
        "colleague_of",
        "family_of",
        "partner_of",
        "member_of",
        "sibling_of",
        "married_to",
        "managed_by",
        "manages_property",
        "participant_of",
        "invited_by",
        "rental_agent",
        "rental_location",
    }
)

# Canonical relational predicates registered in entity_predicate_registry.
_DIRECT_RELATIONAL_PREDICATES: frozenset[str] = frozenset(
    {
        "partner-of",
        "child-of",
        "parent-of",
        "family-of",
        "friend-of",
        "colleague-of",
        "knows",
        "works-at",
        "member-of",
        "managed-by",
        "manages-property",
        "participant-of",
        "invited-by",
        "rental-agent",
        "rental-location",
    }
)

# All predicates (direct + alias) that can be passed directly to
# relationship_assert_fact without any content analysis.  The writer normalises
# aliases itself, so we just forward them.
_DIRECT_OR_ALIAS_PREDICATES: frozenset[str] = _DIRECT_RELATIONAL_PREDICATES | _ALIAS_PREDICATES

# Prose predicates that carry relational meaning in their *content* text.
# These require keyword analysis to determine the target edge predicate.
_PROSE_PREDICATE_SET: frozenset[str] = frozenset(
    {
        "living_arrangement",
        "relationship_status",
        "relationship_type",
        "family_relationship",
    }
)

# Keyword sets for content analysis — matched case-insensitively.
_PARTNER_KEYWORDS: frozenset[str] = frozenset(
    {
        "partner",
        "cohabit",
        "spouse",
        "married",
        "wife",
        "husband",
        "boyfriend",
        "girlfriend",
        "dating",
        "fiancé",
        "fiancée",
        "fiance",
        "engaged",
    }
)
_PARENT_KEYWORDS: frozenset[str] = frozenset(
    {
        "mother",
        "mom",
        "mum",
        "mummy",
        "mommy",
        "father",
        "dad",
        "daddy",
        "papa",
        "parent",
        "parents",
    }
)
_CHILD_KEYWORDS: frozenset[str] = frozenset(
    {
        "son",
        "daughter",
        "child",
        "kid",
    }
)
_SIBLING_KEYWORDS: frozenset[str] = frozenset(
    {
        "sibling",
        "sister",
        "brother",
    }
)


def _infer_predicate_from_prose(predicate: str, content: str) -> tuple[str, float] | None:
    """Infer a registry predicate from a prose fact predicate + content.

    Returns ``(registry_predicate, confidence)`` or ``None`` if no safe mapping
    can be determined.

    Confidence is set conservatively:
    - ``partner-of``: 0.9 — explicit partner/cohabiting content (NOT a kinship
      predicate, so the family confidence gate does not apply).
    - Kinship predicates (``parent-of``, ``child-of``, ``family-of``): 0.7 —
      deliberately below the 0.8 family-gate threshold so they are always routed
      to ``pending_approval`` for owner review before any hard edge is written.
    """
    content_lower = content.lower()

    if predicate in ("living_arrangement", "relationship_status", "relationship_type"):
        for kw in _PARTNER_KEYWORDS:
            if kw in content_lower:
                return ("partner-of", 0.9)
        return None  # Cannot safely determine the edge type without more context

    if predicate == "family_relationship":
        # Parent keywords — the subject is likely the *child* (child-of the object).
        for kw in _PARENT_KEYWORDS:
            if kw in content_lower:
                # Low confidence: the directionality may be wrong; route to review.
                return ("child-of", 0.7)
        # Child keywords — the subject is likely the *parent* (parent-of the object).
        for kw in _CHILD_KEYWORDS:
            if kw in content_lower:
                return ("parent-of", 0.7)
        # Sibling keywords — undirected family relationship.
        for kw in _SIBLING_KEYWORDS:
            if kw in content_lower:
                return ("family-of", 0.7)
        # Generic family relationship — undirected, conservative confidence.
        return ("family-of", 0.7)

    return None


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

    now_utc = datetime.now(UTC)
    today = now_utc.date()

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
    from butlers.tools.relationship.dunbar import (
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
            ON f.entity_id = c.entity_id
           AND f.predicate LIKE 'interaction_%'
           AND f.scope = 'relationship'
           AND f.validity = 'active'
        WHERE c.listed = true
          AND c.entity_id IS NOT NULL
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
            ON f.entity_id = c.entity_id
           AND f.predicate LIKE 'interaction_%'
           AND f.scope = 'relationship'
           AND f.validity = 'active'
        WHERE c.listed = true
          AND c.entity_id IS NOT NULL
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


# ---------------------------------------------------------------------------
# Interaction sync constants
# ---------------------------------------------------------------------------

# Channels to monitor; maps source_channel → contact_info.type used for lookup.
_INTERACTION_SYNC_CHANNEL_MAP: dict[str, str] = {
    "telegram_user_client": "telegram_chat_id",
    "whatsapp_user_client": "whatsapp_jid",
    "email": "email",
}

# Per-channel incoming hour offset within a day to ensure unique occurred_at timestamps.
# store_fact() derives its idempotency key from (entity_id, scope, predicate, valid_at).
# Two channels for the same contact on the same day would collide on that key unless
# occurred_at differs.  Using stable hour offsets ensures each (contact, channel, day)
# triple maps to a distinct timestamp.
_INTERACTION_SYNC_CHANNEL_HOUR_OFFSET: dict[str, int] = {
    "telegram_user_client": 0,
    "whatsapp_user_client": 1,
    "email": 2,
}

# Per-channel outgoing hour offset (+12 from incoming) so that both an incoming and
# an outgoing interaction fact can coexist for the same contact on the same day.
# RFC 0013 D4: incoming and outgoing share the same metadata->>'type', so they would
# otherwise collide under the interaction_log() deduplication guard.
_INTERACTION_SYNC_CHANNEL_HOUR_OFFSET_OUTGOING: dict[str, int] = {
    "telegram_user_client": 12,
    "whatsapp_user_client": 13,
    "email": 14,
}

# Maximum number of participants in a chat for interaction tracking.
# Chats exceeding this threshold are skipped entirely (RFC 0013 D3).
_INTERACTION_SYNC_MAX_GROUP_SIZE = 20

# Checkpoint key for scan window persistence.
_INTERACTION_SYNC_STATE_KEY = "interaction_sync.last_scan_at"

# Maximum lookback window (prevents unbounded backfill after long outages).
_INTERACTION_SYNC_MAX_WINDOW_DAYS = 30


# ---------------------------------------------------------------------------
# Interaction sync job
# ---------------------------------------------------------------------------


async def run_interaction_sync(db_pool: asyncpg.Pool) -> dict[str, Any]:
    """Sync interactions from messages and calendar events to interaction facts.

    **Message-based sync (group-aware):** Queries ``switchboard.message_inbox``
    for recent inbound messages on user-to-person channels
    (``telegram_user_client``, ``whatsapp_user_client``, ``email``).

    Groups by ``(source_thread_identity, source_channel, DATE(received_at))``
    — a chat-centric view — rather than by individual sender.  Per RFC 0013 D4:

    1. Messages where ``request_context->>'interaction_eligible'`` is ``'false'``
       are skipped entirely before grouping.
    2. For each (chat, channel, date) group the distinct set of senders is
       collected.  ``participant_count`` is read from ``request_context`` if
       present; otherwise it falls back to the count of distinct senders.
    3. Groups with ``participant_count > 20`` are skipped (D3 gate).
    4. The owner's sender identity is identified via ``public.entities.roles``
       (joined through ``public.contacts.entity_id``).
       If the owner sent at least one message in the chat on that day, each
       non-owner contact in the group receives both an **incoming** fact (they
       messaged) and an **outgoing** fact (owner engaged).  If the owner did
       not send, only an incoming fact is logged.
    5. Both fact kinds carry ``group_size`` in metadata.  Outgoing facts use
       an hour offset of +12 relative to the incoming offset so that the two
       facts coexist without colliding under the ``interaction_log()`` dedup
       guard (which checks ``valid_at::date + direction``).

    The scan window is checkpoint-based:
    - ``scan_window_start``: read from state key ``interaction_sync.last_scan_at``.
      If absent (first run), defaults to ``now() - 30 days``. Capped to at most
      30 days ago to prevent unbounded backfill after outages.
    - ``scan_window_end``: ``now()`` at job start.

    On successful completion the job writes ``scan_window_end`` back to the state
    store so the next run continues from where this one left off.

    **Calendar-based sync:** Queries ``public.calendar_events`` for confirmed
    events within the scan window.  For each event, extracts the
    ``metadata->'attendees'`` JSONB array, resolves attendee emails to
    contact_ids via ``relationship.entity_facts`` (``has-email`` →
    ``public.contacts.entity_id``; ``public.contact_info`` was dropped in
    bead 10 / core_115), and calls
    ``interaction_log()`` with ``type='calendar_event'``.  Events where the
    owner's RSVP is ``declined`` are skipped entirely.  The owner's own
    attendee entry (``self=true``) is excluded from attendee resolution.

    Args:
        db_pool: Database connection pool (relationship butler pool).

    Returns:
        Dictionary with keys: scan_window_start (ISO8601), scan_window_end
        (ISO8601), processed, logged, skipped_unresolved, skipped_owner,
        skipped_ineligible, skipped_group_too_large,
        calendar_events_scanned, errors.
    """
    from butlers.tools.relationship.interactions import interaction_log

    logger.info("Running relationship interaction sync job")

    now_utc = datetime.now(UTC)
    max_lookback = now_utc - timedelta(days=_INTERACTION_SYNC_MAX_WINDOW_DAYS)

    # Fail-open on checkpoint I/O: this is a periodic job whose 30-day max_lookback
    # bounds the cost of a missed checkpoint, and interaction_log() already dedups
    # re-scanned windows; raising would trigger scheduler retry storms and hide the
    # work the job did complete. Failures are surfaced via stats["errors"] and
    # WARNING logs because the scan can still complete using the bounded fallback.
    checkpoint_errors = 0
    try:
        last_scan_at_raw = await state_get(db_pool, _INTERACTION_SYNC_STATE_KEY)
    except Exception:
        logger.warning(
            "interaction_sync: failed to read checkpoint key=%s; falling back to %d-day lookback",
            _INTERACTION_SYNC_STATE_KEY,
            _INTERACTION_SYNC_MAX_WINDOW_DAYS,
            exc_info=True,
        )
        last_scan_at_raw = None
        checkpoint_errors += 1
    if last_scan_at_raw is not None:
        try:
            scan_window_start = datetime.fromisoformat(str(last_scan_at_raw))
            # Ensure timezone-aware.
            if scan_window_start.tzinfo is None:
                scan_window_start = scan_window_start.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            logger.warning(
                "interaction_sync: invalid checkpoint value key=%s value=%r; "
                "falling back to %d-day lookback",
                _INTERACTION_SYNC_STATE_KEY,
                last_scan_at_raw,
                _INTERACTION_SYNC_MAX_WINDOW_DAYS,
            )
            scan_window_start = max_lookback
    else:
        scan_window_start = max_lookback

    # Cap to at most 30 days ago.
    if scan_window_start < max_lookback:
        scan_window_start = max_lookback

    scan_window_end = now_utc

    # Clamp checkpoints in the future so the scan window remains ordered.
    if scan_window_start > now_utc:
        logger.warning(
            "interaction_sync: checkpoint %s is in the future relative to now %s — clamping to now",
            scan_window_start.isoformat(),
            now_utc.isoformat(),
        )
        scan_window_start = now_utc

    logger.info(
        "interaction_sync: window [%s, %s]",
        scan_window_start.isoformat(),
        scan_window_end.isoformat(),
    )

    stats: dict[str, Any] = {
        "scan_window_start": scan_window_start.isoformat(),
        "scan_window_end": scan_window_end.isoformat(),
        "processed": 0,
        "logged": 0,
        "skipped_unresolved": 0,
        "skipped_owner": 0,
        "skipped_ineligible": 0,
        "skipped_group_too_large": 0,
        "calendar_events_scanned": 0,
        "errors": checkpoint_errors,
    }

    channels = list(_INTERACTION_SYNC_CHANNEL_MAP.keys())

    # -----------------------------------------------------------------------
    # Step 1: Query switchboard.message_inbox grouped by chat identity.
    #
    # RFC 0013 D4: group by (source_thread_identity, source_channel, date)
    # and collect the distinct set of senders per group.  Messages flagged
    # as interaction_eligible=false are excluded before grouping.
    #
    # When source_thread_identity is NULL (legacy/connectors that don't set it),
    # fall back to source_sender_identity as the grouping key so that each sender
    # forms its own "chat" group rather than being merged into a NULL mega-group.
    # -----------------------------------------------------------------------
    try:
        rows = await db_pool.fetch(
            """
            SELECT
                COALESCE(
                    request_context ->> 'source_thread_identity',
                    request_context ->> 'source_sender_identity'
                )                                              AS thread_identity,
                request_context ->> 'source_channel'           AS source_channel,
                (received_at AT TIME ZONE 'UTC')::date         AS interaction_date,
                array_agg(DISTINCT request_context ->> 'source_sender_identity')
                                                               AS sender_identities,
                COUNT(*)                                       AS message_count,
                MAX(
                    CASE
                        WHEN request_context ->> 'participant_count' IS NOT NULL
                        THEN (request_context ->> 'participant_count')::int
                        ELSE NULL
                    END
                )                                              AS participant_count
            FROM switchboard.message_inbox
            WHERE direction = 'inbound'
              AND received_at >= $1
              AND request_context ->> 'source_channel' = ANY($2::text[])
              AND request_context ->> 'source_sender_identity' IS NOT NULL
              AND request_context ->> 'source_sender_identity' != 'unknown'
              AND COALESCE(request_context ->> 'interaction_eligible', 'true') != 'false'
            GROUP BY
                COALESCE(
                    request_context ->> 'source_thread_identity',
                    request_context ->> 'source_sender_identity'
                ),
                request_context ->> 'source_channel',
                (received_at AT TIME ZONE 'UTC')::date
            ORDER BY interaction_date DESC
            """,
            scan_window_start,
            channels,
        )
    except Exception:
        logger.exception("interaction_sync: failed to query switchboard.message_inbox")
        stats["errors"] += 1
        return stats

    if not rows:
        logger.info("interaction_sync: no recent inbound messages found")

    # -----------------------------------------------------------------------
    # Step 2: Batch-resolve all sender identities to contact_ids in one query.
    #
    # Build the full set of (ci_type, sender_identity) pairs across all groups,
    # then resolve them in a single UNNEST-based join.  Owner contacts are
    # identified by roles and tracked separately.
    # -----------------------------------------------------------------------
    lookup_pairs: list[tuple[str, str]] = []
    for row in rows:
        source_channel = row["source_channel"]
        ci_type = _INTERACTION_SYNC_CHANNEL_MAP.get(source_channel)
        if not ci_type:
            continue
        for sender_identity in row["sender_identities"] or []:
            if sender_identity:
                lookup_pairs.append((ci_type, sender_identity))

    # Resolve (ci_type, value) → entity_id via relationship.entity_facts.
    # interaction_log() now accepts entity_id directly, so the LEFT JOIN to
    # public.contacts that previously bridged entity_id→contact_id is removed.
    resolved: dict[tuple[str, str], uuid.UUID] = {}  # (ci_type, value) -> entity_id
    owner_entity_ids: set[uuid.UUID] = set()

    if lookup_pairs:
        ci_types = [t for t, _ in lookup_pairs]
        ci_values = [v for _, v in lookup_pairs]

        try:
            contact_rows = await db_pool.fetch(
                """
                SELECT
                    pairs.ci_type,
                    pairs.ci_value,
                    ef.subject                  AS entity_id,
                    COALESCE(e.roles, '{}')     AS roles
                FROM (
                    SELECT DISTINCT p.ci_type, p.ci_value
                    FROM UNNEST($1::text[], $2::text[]) AS p(ci_type, ci_value)
                ) pairs
                JOIN relationship.entity_facts ef
                  ON ef.predicate = CASE pairs.ci_type
                        WHEN 'email'            THEN 'has-email'
                        WHEN 'phone'            THEN 'has-phone'
                        WHEN 'telegram_chat_id' THEN 'has-handle'
                        WHEN 'whatsapp_jid'     THEN 'has-handle'
                        ELSE 'has-handle'
                     END
                 AND ef.object      = pairs.ci_value
                 AND ef.object_kind = 'literal'
                 AND ef.validity    = 'active'
                JOIN public.entities e ON e.id = ef.subject
                """,
                ci_types,
                ci_values,
            )
        except Exception:
            logger.exception("interaction_sync: failed to resolve contact identities")
            stats["errors"] += 1
            return stats

        for cr in contact_rows:
            entity_id = cr["entity_id"]
            if entity_id is None:
                continue
            if not isinstance(entity_id, uuid.UUID):
                try:
                    entity_id = uuid.UUID(str(entity_id))
                except (ValueError, AttributeError):
                    continue
            key = (cr["ci_type"], cr["ci_value"])
            resolved[key] = entity_id
            roles: list[str] = list(cr["roles"] or [])
            if "owner" in roles:
                owner_entity_ids.add(entity_id)

    # -----------------------------------------------------------------------
    # Step 3: For each (chat, channel, date) group apply the group-aware logic:
    #   a. Determine participant_count; skip groups above the gate threshold.
    #   b. Detect owner presence to determine direction.
    #   c. For each non-owner sender: log incoming (and optionally outgoing) facts.
    # -----------------------------------------------------------------------
    for row in rows:
        source_channel = row["source_channel"]
        thread_identity = row["thread_identity"]
        interaction_date = row["interaction_date"]
        sender_identities: list[str] = [s for s in (row["sender_identities"] or []) if s]
        message_count: int = int(row["message_count"])

        ci_type = _INTERACTION_SYNC_CHANNEL_MAP.get(source_channel)
        if not ci_type:
            continue

        # Resolve each sender_identity → entity_id (may include owner).
        sender_entities: list[uuid.UUID] = []
        for si in sender_identities:
            eid = resolved.get((ci_type, si))
            if eid is not None:
                sender_entities.append(eid)

        # --- participant count ---
        # Prefer the envelope-reported participant_count from request_context;
        # fall back to distinct sender count for backward compatibility.
        raw_participant_count = row["participant_count"]
        if raw_participant_count is not None:
            participant_count = int(raw_participant_count)
        else:
            participant_count = len(sender_identities)

        if participant_count > _INTERACTION_SYNC_MAX_GROUP_SIZE:
            logger.debug(
                "interaction_sync: skipping group thread=%s channel=%s date=%s "
                "(participant_count=%d > %d)",
                thread_identity,
                source_channel,
                interaction_date,
                participant_count,
                _INTERACTION_SYNC_MAX_GROUP_SIZE,
            )
            stats["skipped_group_too_large"] += 1
            continue

        # --- owner presence and group_size ---
        owner_sent = any(e in owner_entity_ids for e in sender_entities)

        # group_size = participant_count for group chats.
        # For DMs (only one non-owner participant), clamp to 1 so the
        # interaction receives full weight (RFC 0013 D2, D4).
        #
        # participant_count covers both owner and non-owner participants.
        # A DM has at most 2 participants (owner + 1 contact), so:
        # participant_count <= 2 → treat as DM → group_size = 1.
        # This correctly handles bidirectional DMs where the owner also sent
        # (participant_count=2 should still yield group_size=1, not 2).
        if participant_count <= 2:
            group_size = 1
        else:
            group_size = max(participant_count, 1)

        incoming_hour = _INTERACTION_SYNC_CHANNEL_HOUR_OFFSET.get(source_channel, 0)
        outgoing_hour = _INTERACTION_SYNC_CHANNEL_HOUR_OFFSET_OUTGOING.get(source_channel, 12)

        # --- per-sender logging ---
        for si in sender_identities:
            stats["processed"] += 1

            eid = resolved.get((ci_type, si))

            if eid is None:
                logger.debug(
                    "interaction_sync: unresolved sender %s (channel=%s)",
                    si,
                    source_channel,
                )
                stats["skipped_unresolved"] += 1
                continue

            if eid in owner_entity_ids:
                # Owner's presence is used for direction detection, not logged as a contact.
                stats["skipped_owner"] += 1
                continue

            # Build shared metadata for both directions.
            fact_metadata: dict[str, Any] = {
                "source": "interaction_sync",
                "message_count": message_count,
                "group_size": group_size,
            }

            # --- incoming interaction ---
            incoming_occurred_at = datetime(
                interaction_date.year,
                interaction_date.month,
                interaction_date.day,
                incoming_hour,
                0,
                0,
                tzinfo=UTC,
            )
            try:
                result = await interaction_log(
                    db_pool,
                    entity_id=eid,
                    type=source_channel,
                    direction="incoming",
                    occurred_at=incoming_occurred_at,
                    summary=None,
                    metadata=fact_metadata,
                )
                if result.get("skipped") == "duplicate":
                    logger.debug(
                        "interaction_sync: duplicate incoming skipped entity=%s channel=%s date=%s",
                        eid,
                        source_channel,
                        interaction_date,
                    )
                else:
                    stats["logged"] += 1
                    logger.debug(
                        "interaction_sync: logged incoming entity=%s channel=%s date=%s",
                        eid,
                        source_channel,
                        interaction_date,
                    )
            except Exception:
                logger.exception(
                    "interaction_sync: error logging incoming for entity=%s channel=%s",
                    eid,
                    source_channel,
                )
                stats["errors"] += 1

            # --- outgoing interaction (only when owner also sent in this chat) ---
            if owner_sent:
                outgoing_occurred_at = datetime(
                    interaction_date.year,
                    interaction_date.month,
                    interaction_date.day,
                    outgoing_hour,
                    0,
                    0,
                    tzinfo=UTC,
                )
                try:
                    result = await interaction_log(
                        db_pool,
                        entity_id=eid,
                        type=source_channel,
                        direction="outgoing",
                        occurred_at=outgoing_occurred_at,
                        summary=None,
                        metadata=fact_metadata,
                    )
                    if result.get("skipped") == "duplicate":
                        logger.debug(
                            "interaction_sync: duplicate outgoing skipped "
                            "entity=%s channel=%s date=%s",
                            eid,
                            source_channel,
                            interaction_date,
                        )
                    else:
                        stats["logged"] += 1
                        logger.debug(
                            "interaction_sync: logged outgoing entity=%s channel=%s date=%s",
                            eid,
                            source_channel,
                            interaction_date,
                        )
                except Exception:
                    logger.exception(
                        "interaction_sync: error logging outgoing for entity=%s channel=%s",
                        eid,
                        source_channel,
                    )
                    stats["errors"] += 1

    # -----------------------------------------------------------------------
    # Step 4: Scan public.calendar_events for confirmed events within the
    # scan window, extract attendees, and log interactions.
    # -----------------------------------------------------------------------
    try:
        cal_rows = await db_pool.fetch(
            """
            SELECT
                id,
                title,
                starts_at,
                metadata
            FROM public.calendar_events
            WHERE status = 'confirmed'
              AND starts_at >= $1
              AND starts_at <= now()
              AND metadata->'attendees' IS NOT NULL
            ORDER BY starts_at DESC
            """,
            scan_window_start,
        )
    except asyncpg.exceptions.UndefinedTableError:
        logger.info(
            "interaction_sync: public.calendar_events unavailable; skipping calendar-based sync"
        )
        cal_rows = []
    except Exception:
        logger.exception("interaction_sync: failed to query public.calendar_events")
        stats["errors"] += 1
        cal_rows = []

    # -----------------------------------------------------------------------
    # Pre-process all calendar rows: parse metadata, skip declined/no-attendee
    # events, collect attendee emails, and batch-resolve all emails at once.
    # -----------------------------------------------------------------------

    # event_tasks: list of (event_id, event_title, event_starts_at, attendee_emails)
    # for events that pass the owner-declined and attendee checks.
    event_tasks: list[tuple[str, str, datetime, list[str]]] = []
    all_attendee_emails: set[str] = set()

    for cal_row in cal_rows:
        event_id = str(cal_row["id"])
        event_title: str = cal_row["title"] or ""
        event_starts_at: datetime = cal_row["starts_at"]

        raw_meta = cal_row["metadata"]
        if isinstance(raw_meta, str):
            try:
                meta = json.loads(raw_meta)
            except (ValueError, TypeError):
                logger.warning(
                    "interaction_sync: failed to parse metadata JSON for event %s",
                    event_id,
                )
                meta = {}
        elif isinstance(raw_meta, dict):
            meta = raw_meta
        else:
            meta = {}

        attendees_raw = meta.get("attendees")
        if not isinstance(attendees_raw, list) or not attendees_raw:
            continue

        # Check if the owner declined this event via their self=true attendee entry.
        owner_declined = False
        for att in attendees_raw:
            if not isinstance(att, dict):
                continue
            if att.get("self") is True and att.get("responseStatus") == "declined":
                owner_declined = True
                break

        if owner_declined:
            logger.debug(
                "interaction_sync: skipping calendar event %s — owner declined",
                event_id,
            )
            continue

        # Count all confirmed, non-declined events as scanned.
        stats["calendar_events_scanned"] += 1

        # Collect attendee emails (exclude self/owner's own entry).
        # Use a dict keyed by normalised email to deduplicate within the event.
        seen_emails: dict[str, None] = {}
        for att in attendees_raw:
            if not isinstance(att, dict):
                continue
            if att.get("self") is True:
                continue  # owner's own entry
            email = att.get("email")
            if isinstance(email, str):
                email = email.strip().lower()
                if email:
                    seen_emails[email] = None

        attendee_emails = list(seen_emails)
        if not attendee_emails:
            continue

        # Stage for batch resolution.
        event_tasks.append((event_id, event_title, event_starts_at, attendee_emails))
        all_attendee_emails.update(attendee_emails)

    # Batch-resolve all attendee emails across all events in a single query.
    # interaction_log() now accepts entity_id directly, so we resolve to entity_id
    # (= ef.subject) without joining to public.contacts.
    email_to_entity: dict[str, uuid.UUID] = {}
    calendar_owner_entity_ids: set[uuid.UUID] = set()

    if all_attendee_emails:
        try:
            resolved_rows = await db_pool.fetch(
                """
                SELECT
                    ef.subject                  AS entity_id,
                    LOWER(ef.object)            AS email,
                    COALESCE(e.roles, '{}')     AS roles
                FROM relationship.entity_facts ef
                JOIN public.entities e ON e.id = ef.subject
                WHERE ef.predicate   = 'has-email'
                  AND ef.object_kind = 'literal'
                  AND ef.validity    = 'active'
                  AND LOWER(ef.object) = ANY($1::text[])
                """,
                list(all_attendee_emails),
            )
        except Exception:
            logger.exception("interaction_sync: failed to resolve calendar attendee emails")
            stats["errors"] += 1
            resolved_rows = []

        for rr in resolved_rows:
            eid = rr["entity_id"]
            if eid is None:
                continue
            if not isinstance(eid, uuid.UUID):
                try:
                    eid = uuid.UUID(str(eid))
                except (ValueError, AttributeError):
                    continue
            email_key = rr["email"]
            email_to_entity[email_key] = eid
            roles: list[str] = list(rr["roles"] or [])
            if "owner" in roles:
                calendar_owner_entity_ids.add(eid)

    for event_id, event_title, event_starts_at, attendee_emails in event_tasks:
        for email in attendee_emails:
            entity_id = email_to_entity.get(email)
            if entity_id is None:
                stats["skipped_unresolved"] += 1
                logger.debug(
                    "interaction_sync: unresolved calendar attendee email=%s event=%s",
                    email,
                    event_id,
                )
                continue

            if entity_id in calendar_owner_entity_ids:
                stats["skipped_owner"] += 1
                logger.debug(
                    "interaction_sync: skipping owner attendee entity=%s event=%s",
                    entity_id,
                    event_id,
                )
                continue

            try:
                result = await interaction_log(
                    db_pool,
                    entity_id=entity_id,
                    type="calendar_event",
                    direction="mutual",
                    occurred_at=event_starts_at,
                    summary=event_title,
                    metadata={
                        "source": "interaction_sync",
                        "event_id": event_id,
                        "event_title": event_title,
                    },
                )
                if result.get("skipped") == "duplicate":
                    logger.debug(
                        "interaction_sync: duplicate calendar event skipped entity=%s event=%s",
                        entity_id,
                        event_id,
                    )
                else:
                    stats["logged"] += 1
                    logger.debug(
                        "interaction_sync: logged calendar_event interaction entity=%s event=%s",
                        entity_id,
                        event_id,
                    )
            except Exception:
                logger.exception(
                    "interaction_sync: error logging calendar interaction entity=%s event=%s",
                    entity_id,
                    event_id,
                )
                stats["errors"] += 1

    # Persist the end of this scan window as the next checkpoint. Fail-open for
    # the same reasons as the read above (interaction_log() dedups, scheduler
    # storm avoidance). Surface via stats["errors"] + WARNING logs so monitoring
    # catches recurring write failures without turning a completed fallback scan
    # into an ERROR-level job failure.
    next_checkpoint = scan_window_end.isoformat()
    try:
        await state_set(db_pool, _INTERACTION_SYNC_STATE_KEY, next_checkpoint)
    except Exception:
        logger.warning(
            "interaction_sync: failed to write checkpoint key=%s value=%s",
            _INTERACTION_SYNC_STATE_KEY,
            next_checkpoint,
            exc_info=True,
        )
        stats["errors"] += 1

    logger.info(
        "Interaction sync complete: processed=%d, logged=%d, "
        "skipped_unresolved=%d, skipped_owner=%d, "
        "skipped_ineligible=%d, skipped_group_too_large=%d, "
        "calendar_events_scanned=%d, errors=%d",
        stats["processed"],
        stats["logged"],
        stats["skipped_unresolved"],
        stats["skipped_owner"],
        stats["skipped_ineligible"],
        stats["skipped_group_too_large"],
        stats["calendar_events_scanned"],
        stats["errors"],
    )
    return stats


# ---------------------------------------------------------------------------
# Dual-write reconciler constants
# ---------------------------------------------------------------------------

# Env var name for configuring the reconciler interval (minutes).
_RECONCILER_INTERVAL_ENV = "BUTLERS_CONTACT_INFO_RECONCILER_INTERVAL_MINUTES"

# Default reconcile interval: 30 minutes (configurable via env var).
_RECONCILER_DEFAULT_INTERVAL_MINUTES = 30

# State key used to persist the last successful run timestamp.
_RECONCILER_STATE_KEY = "contact_info_reconciler.last_run_at"

# Mapping from contact_info.type → relationship.entity_facts predicate.
# These map through the registered predicate catalog (migration rel_014). Types
# without a 1-to-1 predicate (telegram, telegram_user_id, telegram_username,
# linkedin, twitter, other) all collapse to the channel-scoped "has-handle"
# predicate.
#
# Must stay in sync with:
#   roster/relationship/tools/relationship_assert_fact._CI_TYPE_TO_PREDICATE
#   src/butlers/identity._CHANNEL_TYPE_TO_PREDICATE
#
# IMPORTANT: the NOT EXISTS sweep clause in run_contact_info_reconciler must
# use this same mapping (via a SQL CASE expression) so the idempotency check
# checks the predicate that the reconciler will actually write, not 'has-' || ci.type.
_CI_TYPE_TO_PREDICATE: dict[str, str] = {
    "email": "has-email",
    "phone": "has-phone",
    "telegram": "has-handle",
    "telegram_user_id": "has-handle",
    "telegram_username": "has-handle",
    "linkedin": "has-handle",
    "twitter": "has-handle",
    "website": "has-website",
    "other": "has-handle",
}


async def _registered_contact_info_predicates(db_pool: asyncpg.Pool) -> set[str] | None:
    """Return mapped contact-info predicates present in the registry.

    The reconciler has a static contact_info.type -> predicate map, but the
    central writer validates against ``relationship.entity_predicate_registry``.
    Checking the registry once lets the reconciler count registry drift as a
    skipped predicate instead of repeatedly surfacing writer errors per row.
    """
    mapped_predicates = sorted(set(_CI_TYPE_TO_PREDICATE.values()))
    try:
        rows = await db_pool.fetch(
            """
            SELECT predicate
            FROM relationship.entity_predicate_registry
            WHERE predicate = ANY($1::text[])
            """,
            mapped_predicates,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "contact_info_reconciler: failed to load predicate registry; aborting run",
            exc_info=True,
        )
        return None

    return {row["predicate"] for row in rows}


def _reconciler_interval_minutes() -> int:
    """Return the reconciler interval in minutes (default 30, env-overridable)."""
    raw = os.environ.get(_RECONCILER_INTERVAL_ENV)
    if raw is not None:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return _RECONCILER_DEFAULT_INTERVAL_MINUTES


# ---------------------------------------------------------------------------
# Dual-write reconciler job (Amendment 14)
# ---------------------------------------------------------------------------


async def run_contact_info_reconciler(db_pool: asyncpg.Pool) -> dict[str, Any]:
    """Sweep public.contact_info for rows missing a matching active triple.

    RETIRED (migration bead 10, bu-e2ja9 / core_115): ``public.contact_info`` is
    dropped, so this reconciler is no longer wired into the job registry or
    ``butler.toml``. The body is retained for historical reference and exercised
    by unit tests against fixture tables; a runtime guard below makes it a
    crash-safe no-op if a stale schedule ever dispatches it.

    Implements the Amendment 14 safety net: EVENTUAL parity (within 24h) between
    ``public.contact_info`` (the legacy contact store) and
    ``relationship.entity_facts`` (the new triple store).

    For each ``public.contact_info`` row that does NOT have a corresponding active
    triple in ``relationship.entity_facts``, the reconciler calls
    ``relationship_assert_fact()`` to emit the triple.

    Skipped rows (per Brief §6b Amendment 1.1.A.4 and Amendment 14):
    - ``secured = true``              — credentials, not facts.
    - ``contact_id → entity_id IS NULL`` — orphaned contacts; no subject to assert on.
    - No registered predicate for the ``contact_info.type``.

    Owner carve-out (Amendment 12a / RFC 0017 §2.3):
    - When the contact's entity carries the ``'owner'`` role, the triple write is
      intercepted by ``relationship_assert_fact()`` itself, which emits a
      ``pending_actions`` row instead. The reconciler counts these separately.

    Metrics returned:
        rows_scanned    : total public.contact_info rows examined.
        rows_reconciled : triples successfully asserted (inserted or superseded).
        rows_skipped    : rows that already had an active triple (no write needed).
        rows_carveout   : owner-role contacts → pending_approval outcome.
        rows_error      : rows where relationship_assert_fact raised an exception.
        rows_skipped_credential : secured=true rows.
        rows_skipped_orphan     : entity_id IS NULL rows.
        rows_skipped_no_predicate: ci.type has no registered predicate mapping.

    Args:
        db_pool: Database connection pool (relationship butler pool).

    Returns:
        Dictionary with the metric keys listed above plus ``interval_minutes``.
    """
    from butlers.tools.relationship._ef_channel_helpers import encode_handle_object
    from butlers.tools.relationship.relationship_assert_fact import (
        AssertOutcome,
        relationship_assert_fact,
    )

    logger.info("Running contact_info_reconciler job")

    interval_minutes = _reconciler_interval_minutes()

    stats: dict[str, Any] = {
        "interval_minutes": interval_minutes,
        "rows_scanned": 0,
        "rows_reconciled": 0,
        "rows_skipped": 0,
        "rows_carveout": 0,
        "rows_error": 0,
        "rows_skipped_credential": 0,
        "rows_skipped_orphan": 0,
        "rows_skipped_no_predicate": 0,
        "rows_skipped_empty_value": 0,
    }

    # Retired guard (bu-e2ja9 / core_115): if public.contact_info has been
    # dropped there is nothing to sweep. Return crash-safe instead of letting the
    # sweep raise UndefinedTableError.
    if await db_pool.fetchval("SELECT to_regclass('public.contact_info')") is None:
        logger.info(
            "contact_info_reconciler: public.contact_info no longer exists "
            "(retired, bu-e2ja9); nothing to reconcile"
        )
        stats["retired"] = True
        return stats

    registered_predicates = await _registered_contact_info_predicates(db_pool)
    if registered_predicates is None:
        stats["rows_error"] += 1
        return stats

    unregistered_warned: set[str] = set()

    # -----------------------------------------------------------------------
    # Step 1: Sweep public.contact_info rows that DON'T have an active triple.
    #
    # Filters applied in SQL:
    #   - secured = false (credentials carve-out).
    #   - c.entity_id IS NOT NULL (orphan guard — no subject to assert on).
    #   - INNER JOIN public.entities excludes tombstoned entities
    #     (metadata->>'merged_into' IS NULL).
    #   - NOT EXISTS checks the predicate the reconciler will actually write,
    #     using the same CASE mapping as _CI_TYPE_TO_PREDICATE. Without this,
    #     rows for ci.type IN ('telegram', 'linkedin', 'twitter', 'other') would
    #     always appear "missing" because the reconciler writes 'has-handle' but
    #     'has-' || ci.type resolves to 'has-telegram' etc.
    #
    # We include rows with unmapped ci.types (e.g. 'fax') and handle them in
    # Python so the metrics accurately reflect why rows were skipped.
    # -----------------------------------------------------------------------
    try:
        rows = await db_pool.fetch(
            """
            SELECT
                ci.id           AS ci_id,
                ci.contact_id,
                ci.type         AS ci_type,
                ci.value        AS ci_value,
                ci.is_primary,
                ci.secured,
                ci.created_at   AS ci_created_at,
                c.entity_id
            FROM public.contact_info ci
            JOIN public.contacts c ON c.id = ci.contact_id
            JOIN public.entities e ON e.id = c.entity_id
            WHERE ci.secured = false
              AND c.entity_id IS NOT NULL
              AND (e.metadata->>'merged_into') IS NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM relationship.entity_facts ef
                  WHERE ef.subject   = c.entity_id
                    AND ef.predicate = CASE ci.type
                        WHEN 'email'             THEN 'has-email'
                        WHEN 'phone'             THEN 'has-phone'
                        WHEN 'website'           THEN 'has-website'
                        WHEN 'telegram'          THEN 'has-handle'
                        WHEN 'telegram_user_id'  THEN 'has-handle'
                        WHEN 'telegram_username' THEN 'has-handle'
                        WHEN 'linkedin'          THEN 'has-handle'
                        WHEN 'twitter'           THEN 'has-handle'
                        WHEN 'other'             THEN 'has-handle'
                        ELSE 'has-' || ci.type
                    END
                    -- Telegram types are stored with a "telegram:" prefix in entity_facts
                    -- (canonical encoding, bead bu-wni4z).  Check both the prefixed form
                    -- (new canonical) and the verbatim ci.value (legacy rows written before
                    -- the fix) so already-written rows are not re-reconciled a second time.
                    AND ef.object IN (
                        ci.value,
                        CASE ci.type
                            WHEN 'telegram'          THEN 'telegram:' || ci.value
                            WHEN 'telegram_user_id'  THEN 'telegram:' || ci.value
                            WHEN 'telegram_username' THEN 'telegram:' || ci.value
                            ELSE ci.value
                        END
                    )
                    AND ef.validity  = 'active'
              )
            ORDER BY ci.created_at ASC NULLS LAST
            """
        )
    except Exception:
        logger.exception("contact_info_reconciler: failed to query sweep rows")
        stats["rows_error"] += 1
        return stats

    stats["rows_scanned"] = len(rows)
    logger.info("contact_info_reconciler: sweep found %d rows to reconcile", len(rows))

    for row in rows:
        ci_type: str = row["ci_type"]
        ci_value: str = row["ci_value"]
        entity_id: uuid.UUID | None = row["entity_id"]
        ci_id = row["ci_id"]

        # Double-check: secured rows are excluded in the query, but guard anyway.
        if row["secured"]:
            stats["rows_skipped_credential"] += 1
            continue

        # Orphan guard (also excluded in query, but defensive).
        if entity_id is None:
            stats["rows_skipped_orphan"] += 1
            continue

        # Predicate mapping — skip types with no registered predicate.
        predicate = _CI_TYPE_TO_PREDICATE.get(ci_type)
        if predicate is None:
            # Fallback: attempt the canonical has-{type} form if the type is
            # non-empty. Unknown types (e.g. 'other') still map through the
            # dict above; truly unknown ones are skipped with a warning.
            if ci_type:
                logger.warning(
                    "contact_info_reconciler: unrecognised ci_type=%r for ci_id=%s; skipping",
                    ci_type,
                    ci_id,
                )
            stats["rows_skipped_no_predicate"] += 1
            continue

        if predicate not in registered_predicates:
            stats["rows_skipped_no_predicate"] += 1
            if predicate not in unregistered_warned:
                logger.warning(
                    "contact_info_reconciler: predicate=%s for ci_type=%r is not registered; "
                    "skipping ci_id=%s (and subsequent rows with this predicate)",
                    predicate,
                    ci_type,
                    ci_id,
                )
                unregistered_warned.add(predicate)
            continue

        # Belt-and-suspenders: skip rows whose value is empty or whitespace-only.
        # DB NOT NULL prevents NULL, but an empty string could slip through and
        # produce a degenerate object like "telegram:" after encoding.
        if not (ci_value or "").strip():
            stats["rows_skipped_empty_value"] += 1
            logger.warning(
                "contact_info_reconciler: empty ci_value for ci_id=%s ci_type=%s entity=%s; "
                "skipping to avoid degenerate triple",
                ci_id,
                ci_type,
                entity_id,
            )
            continue

        # Provenance fields.
        last_seen: datetime | None = row.get("ci_created_at")
        is_primary: bool = row["is_primary"]

        # Encode the object value: telegram types must carry the "telegram:" prefix
        # so the read path (daemon._resolve_contact_channel_identifier, ef_predicate_to_ci_type)
        # can distinguish them from linkedin/twitter/other has-handle entries.
        ef_object = encode_handle_object(ci_type, ci_value)

        # Owner-facing rationale + evidence.  These surface in the approvals
        # UI when the writer hits the owner carve-out.  Without them the
        # dossier shows blank cells for every reconciler-generated approval.
        ci_value_preview = ci_value if len(ci_value) <= 80 else ci_value[:77] + "..."
        why = (
            f"The contact-info reconciler found a `public.contact_info` row "
            f"({ci_type}) on your own contact with no matching active triple "
            f"in `relationship.entity_facts`. Approve to backfill the "
            f"`{predicate}` triple ({ci_value_preview}) so the entity graph "
            f"matches the legacy contact store. Rejecting leaves the triple "
            f"missing and the next sweep will surface it again."
        )
        evidence_list: list[str] = [
            "source=contact_info_reconciler",
            f"contact_info.id={ci_id}",
            f"contact_id={row['contact_id']}",
            f"contact_info.type={ci_type}",
            f"contact_info.value={ci_value_preview}",
            f"is_primary={is_primary}",
        ]
        if last_seen is not None:
            evidence_list.append(f"first_seen={last_seen.isoformat()}")

        try:
            result = await relationship_assert_fact(
                db_pool,
                entity_id,
                predicate,
                ef_object,
                src="reconciler",
                object_kind="literal",
                conf=1.0,
                last_seen=last_seen,
                verified=False,
                primary=is_primary,
                why=why,
                evidence=evidence_list,
            )

            if result.outcome == AssertOutcome.pending_approval:
                # Owner carve-out: pending_actions row was created by the writer.
                stats["rows_carveout"] += 1
                logger.debug(
                    "contact_info_reconciler: owner carve-out for entity=%s predicate=%s",
                    entity_id,
                    predicate,
                )
            elif result.outcome in (AssertOutcome.inserted, AssertOutcome.superseded):
                stats["rows_reconciled"] += 1
                logger.debug(
                    "contact_info_reconciler: reconciled entity=%s predicate=%s object=%s "
                    "outcome=%s",
                    entity_id,
                    predicate,
                    ef_object[:80],
                    result.outcome.value,
                )
            else:
                # AssertOutcome.unchanged — triple already exists (sweep query
                # had a race with a concurrent write; no action needed).
                stats["rows_skipped"] += 1
                logger.debug(
                    "contact_info_reconciler: already-active triple for entity=%s predicate=%s "
                    "(race with concurrent write)",
                    entity_id,
                    predicate,
                )
        except Exception:
            logger.exception(
                "contact_info_reconciler: error asserting triple for ci_id=%s "
                "entity=%s predicate=%s",
                ci_id,
                entity_id,
                predicate,
            )
            stats["rows_error"] += 1

    # Persist the last-run timestamp for observability.
    try:
        await state_set(db_pool, _RECONCILER_STATE_KEY, datetime.now(UTC).isoformat())
    except Exception:
        logger.warning(
            "contact_info_reconciler: failed to write last-run checkpoint",
            exc_info=True,
        )

    logger.info(
        "contact_info_reconciler complete: scanned=%d reconciled=%d skipped=%d "
        "carveout=%d errors=%d skipped_credential=%d skipped_orphan=%d "
        "skipped_no_predicate=%d skipped_empty_value=%d",
        stats["rows_scanned"],
        stats["rows_reconciled"],
        stats["rows_skipped"],
        stats["rows_carveout"],
        stats["rows_error"],
        stats["rows_skipped_credential"],
        stats["rows_skipped_orphan"],
        stats["rows_skipped_no_predicate"],
        stats["rows_skipped_empty_value"],
    )
    return stats


# ---------------------------------------------------------------------------
# Memory curation job (behavior #1: backfill structured edges from prose facts)
# ---------------------------------------------------------------------------


async def run_memory_curation(db_pool: asyncpg.Pool) -> dict[str, Any]:
    """Backfill structured entity edges from existing prose facts.

    Scans ``relationship.facts`` for active rows that have a non-NULL
    ``object_entity_id`` and a predicate that can be mapped to a registered
    relational predicate in ``relationship.entity_predicate_registry``.  For
    each fact that has no corresponding active triple in
    ``relationship.entity_facts``, the job proposes the edge via
    :func:`~butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact`.

    **Mutation policy**: every edge is proposed through ``relationship_assert_fact``.
    This means:
    - Owner-entity subjects: always routed to ``pending_actions`` for approval
      (RFC 0017 §2.3 owner carve-out).
    - Non-owner kinship edges (``parent-of``, ``child-of``, ``family-of``) with
      ``conf < 0.8``: also routed to ``pending_actions`` (family confidence gate).
    - All other non-owner edges with ``conf ≥ 0.8``: written directly.

    The job is idempotent: repeated runs skip edges that are already active.

    Behavior #1 of the memory-curation task (bead bu-34dvk).  Deferred
    behaviors (entity dedup/merge, fact retraction, RFC-0017 surfacing,
    episodic predicate detection) are tracked as separate follow-up beads.

    Args:
        db_pool: Database connection pool.

    Returns:
        Dictionary with keys: facts_scanned, edges_proposed, edges_inserted,
        edges_pending_approval, edges_unchanged, edges_skipped_no_mapping,
        edges_skipped_already_exists, errors.
    """
    from butlers.tools.relationship.relationship_assert_fact import (
        AssertOutcome,
        relationship_assert_fact,
    )

    logger.info("Running memory_curation job (backfill structured edges from prose facts)")

    stats: dict[str, Any] = {
        "facts_scanned": 0,
        "edges_proposed": 0,
        "edges_inserted": 0,
        "edges_pending_approval": 0,
        "edges_unchanged": 0,
        "edges_skipped_no_mapping": 0,
        "edges_skipped_already_exists": 0,
        "errors": 0,
    }

    # -----------------------------------------------------------------------
    # Step 1: Fetch candidate prose facts that carry an object_entity_id.
    #
    # Scope to: validity='active', object_entity_id IS NOT NULL, predicate in
    # our known set (_DIRECT_OR_ALIAS_PREDICATES | _PROSE_PREDICATE_SET).
    # We include both the direct/alias predicates and the prose predicates so
    # a single query fetches everything we need.
    # -----------------------------------------------------------------------
    candidate_predicates = sorted(_DIRECT_OR_ALIAS_PREDICATES | _PROSE_PREDICATE_SET)

    try:
        rows = await db_pool.fetch(
            """
            SELECT
                f.id            AS fact_id,
                f.entity_id     AS subject_entity_id,
                f.object_entity_id,
                f.predicate,
                f.content,
                f.scope
            FROM facts f
            WHERE f.validity = 'active'
              AND f.object_entity_id IS NOT NULL
              AND f.predicate = ANY($1::text[])
            ORDER BY f.created_at ASC NULLS LAST
            """,
            candidate_predicates,
        )
    except Exception:
        logger.exception("memory_curation: failed to query candidate facts")
        stats["errors"] += 1
        return stats

    stats["facts_scanned"] = len(rows)
    logger.info("memory_curation: found %d candidate facts to inspect", len(rows))

    if not rows:
        logger.info("memory_curation: no candidate facts found — nothing to backfill")
        _stamp_checkpoint(db_pool)
        return stats

    # -----------------------------------------------------------------------
    # Step 2: For each candidate fact, determine the target edge predicate.
    #
    # Predicates in _DIRECT_OR_ALIAS_PREDICATES are forwarded to
    # relationship_assert_fact directly (it handles alias normalisation).
    # Predicates in _PROSE_PREDICATE_SET require content keyword analysis.
    # -----------------------------------------------------------------------
    for row in rows:
        subject_entity_id: uuid.UUID | None = row["subject_entity_id"]
        object_entity_id: uuid.UUID = row["object_entity_id"]
        predicate: str = row["predicate"]
        content: str = row["content"] or ""
        fact_id = row["fact_id"]

        if subject_entity_id is None:
            # No subject entity — cannot assert a triple without a subject.
            logger.debug(
                "memory_curation: skipping fact %s — subject_entity_id is NULL",
                fact_id,
            )
            stats["edges_skipped_no_mapping"] += 1
            continue

        # Determine target predicate and confidence.
        if predicate in _DIRECT_OR_ALIAS_PREDICATES:
            # Pass through; relationship_assert_fact normalises aliases internally.
            target_predicate = predicate
            conf = 1.0
        elif predicate in _PROSE_PREDICATE_SET:
            inferred = _infer_predicate_from_prose(predicate, content)
            if inferred is None:
                logger.debug(
                    "memory_curation: no mapping for prose predicate=%r content=%r — skipping",
                    predicate,
                    content[:80],
                )
                stats["edges_skipped_no_mapping"] += 1
                continue
            target_predicate, conf = inferred
        else:
            # Unreachable because of the SQL filter, but defensive.
            stats["edges_skipped_no_mapping"] += 1
            continue

        # -----------------------------------------------------------------------
        # Step 3: Propose the edge through the central writer.
        #
        # relationship_assert_fact handles:
        # - Predicate validation against entity_predicate_registry
        # - Idempotency (unchanged outcome if the triple already exists)
        # - Owner carve-out (pending_approval for owner-entity subjects)
        # - Family confidence gate (pending_approval for kinship at low conf)
        # -----------------------------------------------------------------------
        stats["edges_proposed"] += 1
        why = (
            f"Memory curation backfill: found an active `{predicate}` prose fact "
            f"(id={fact_id}) with a linked entity but no corresponding structured "
            f"edge in `relationship.entity_facts`. Approve to create the "
            f"`{target_predicate}` edge so the entity graph reflects this "
            f"relationship. Rejecting will cause this proposal to recur on the "
            f"next curation run until a matching active edge exists."
        )
        evidence = [
            "source=memory_curation_backfill",
            f"prose_fact.id={fact_id}",
            f"prose_predicate={predicate}",
            f"inferred_edge_predicate={target_predicate}",
            f"conf={conf}",
            f"content_preview={content[:120]}",
        ]

        try:
            result = await relationship_assert_fact(
                db_pool,
                subject_entity_id,
                target_predicate,
                str(object_entity_id),
                src="memory_curation",
                object_kind="entity",
                conf=conf,
                verified=False,
                why=why,
                evidence=evidence,
            )

            if result.outcome == AssertOutcome.pending_approval:
                stats["edges_pending_approval"] += 1
                logger.debug(
                    "memory_curation: pending_approval for subject=%s predicate=%s object=%s",
                    subject_entity_id,
                    target_predicate,
                    object_entity_id,
                )
            elif result.outcome in (AssertOutcome.inserted, AssertOutcome.superseded):
                stats["edges_inserted"] += 1
                logger.debug(
                    "memory_curation: %s subject=%s predicate=%s object=%s fact_id=%s",
                    result.outcome.value,
                    subject_entity_id,
                    target_predicate,
                    object_entity_id,
                    result.fact_id,
                )
            elif result.outcome == AssertOutcome.unchanged:
                stats["edges_unchanged"] += 1
                logger.debug(
                    "memory_curation: unchanged (already active) subject=%s predicate=%s object=%s",
                    subject_entity_id,
                    target_predicate,
                    object_entity_id,
                )
            else:
                logger.warning(
                    "memory_curation: unexpected outcome=%s for fact %s",
                    result.outcome,
                    fact_id,
                )
        except ValueError as exc:
            # Unregistered predicate or invalid conf — log and skip.
            logger.warning(
                "memory_curation: skipping fact %s — relationship_assert_fact rejected: %s",
                fact_id,
                exc,
            )
            stats["edges_skipped_no_mapping"] += 1
        except Exception:
            logger.exception(
                "memory_curation: error proposing edge for fact %s subject=%s predicate=%s",
                fact_id,
                subject_entity_id,
                target_predicate,
            )
            stats["errors"] += 1

    # Persist checkpoint timestamp (best-effort).
    try:
        await state_set(db_pool, _CURATION_STATE_KEY, datetime.now(UTC).isoformat())
    except Exception:
        logger.warning(
            "memory_curation: failed to write checkpoint key=%s",
            _CURATION_STATE_KEY,
            exc_info=True,
        )

    logger.info(
        "memory_curation complete: scanned=%d proposed=%d inserted=%d "
        "pending_approval=%d unchanged=%d skipped_no_mapping=%d "
        "skipped_already_exists=%d errors=%d",
        stats["facts_scanned"],
        stats["edges_proposed"],
        stats["edges_inserted"],
        stats["edges_pending_approval"],
        stats["edges_unchanged"],
        stats["edges_skipped_no_mapping"],
        stats["edges_skipped_already_exists"],
        stats["errors"],
    )
    return stats


# ---------------------------------------------------------------------------
# Pending-actions curation job (behavior #4: RFC-0017 owner carve-out expiry)
# ---------------------------------------------------------------------------

# Surface pending_actions whose expires_at is within this many hours of now.
_PENDING_ACTIONS_WARN_HOURS = 24

# State key for recording last surface timestamp (observability only; job is
# idempotent — the insight broker deduplication prevents double-notification).
_PENDING_ACTIONS_CURATION_STATE_KEY = "memory_curation.last_pending_actions_surface_at"

# Priority for pending-action expiry insights (high urgency: owner must act).
_PENDING_ACTIONS_PRIORITY = 85


async def run_pending_actions_curation(db_pool: asyncpg.Pool) -> dict[str, Any]:
    """Surface owner carve-out pending_actions approaching expiry to the owner.

    Scans the ``pending_actions`` table for rows with ``status='pending'``
    whose ``expires_at`` is within the next 24 hours.  For each such row,
    proposes an insight candidate via the durable insight broker so the owner
    receives a Telegram notification and has a second chance to review before
    the action silently expires.

    Aligns with RFC-0017 §2.3 intent: owner-contact mutations are queued as
    pending_actions rather than applied directly, but they expire silently after
    72 h if never reviewed.  This curation pass surfaces the ones about to expire.

    Args:
        db_pool: Database connection pool (relationship schema context).

    Returns:
        Dictionary with keys: scanned, surfaced, skipped_no_expiry,
        skipped_not_approaching, errors.
    """
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    logger.info(
        "Running pending_actions_curation job (RFC-0017 §2.3 expiry surface, warn_hours=%d)",
        _PENDING_ACTIONS_WARN_HOURS,
    )

    stats: dict[str, Any] = {
        "scanned": 0,
        "surfaced": 0,
        "skipped_no_expiry": 0,
        "skipped_not_approaching": 0,
        "skipped_already_expired": 0,
        "errors": 0,
    }

    now_utc = datetime.now(UTC)
    warn_cutoff = now_utc + timedelta(hours=_PENDING_ACTIONS_WARN_HOURS)

    # Fetch all pending actions regardless of expires_at (we filter in Python so
    # we can increment skipped_no_expiry accurately for observability).
    try:
        rows = await db_pool.fetch(
            """
            SELECT
                id,
                tool_name,
                tool_args,
                agent_summary,
                why,
                requested_at,
                expires_at
            FROM pending_actions
            WHERE status = 'pending'
            ORDER BY expires_at ASC NULLS LAST
            """
        )
    except Exception:
        logger.exception("pending_actions_curation: failed to query pending_actions")
        stats["errors"] += 1
        return stats

    stats["scanned"] = len(rows)
    logger.info("pending_actions_curation: found %d pending rows to inspect", len(rows))

    if not rows:
        logger.info("pending_actions_curation: no pending actions found — nothing to surface")
        return stats

    for row in rows:
        action_id = row["id"]
        tool_name: str = row["tool_name"]
        expires_at = row["expires_at"]  # datetime | None

        # Skip rows with no expiry — they cannot silently expire.
        if expires_at is None:
            logger.debug("pending_actions_curation: skipping action %s — no expires_at", action_id)
            stats["skipped_no_expiry"] += 1
            continue

        # Normalise to UTC-aware if the DB returns a naive datetime.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)

        # Skip rows that have already expired — the insight broker requires a
        # future expires_at, and there is nothing actionable the owner can do.
        if expires_at <= now_utc:
            logger.warning(
                "pending_actions_curation: action %s (tool=%s) already expired at %s — skipping",
                action_id,
                tool_name,
                expires_at.isoformat(),
            )
            stats["skipped_already_expired"] += 1
            continue

        # Skip rows that are not yet approaching expiry.
        if expires_at > warn_cutoff:
            logger.debug(
                "pending_actions_curation: skipping action %s — expires_at %s "
                "is beyond warn cutoff %s",
                action_id,
                expires_at.isoformat(),
                warn_cutoff.isoformat(),
            )
            stats["skipped_not_approaching"] += 1
            continue

        # Calculate human-readable time remaining (expires_at > now is guaranteed here).
        delta = expires_at - now_utc
        hours_left = int(delta.total_seconds() // 3600)
        minutes_left = int((delta.total_seconds() % 3600) // 60)
        if hours_left > 0:
            time_remaining = f"{hours_left}h {minutes_left}m"
        else:
            time_remaining = f"{minutes_left}m"

        why_text = row["why"] or row["agent_summary"] or "(no reason recorded)"
        tool_args_display = json.dumps(dict(row["tool_args"]), ensure_ascii=False)

        message = (
            f"Pending action expiring soon ({time_remaining} remaining):\n"
            f"Tool: {tool_name}\n"
            f"Args: {tool_args_display}\n"
            f"Reason: {why_text}\n"
            f"Action ID: {action_id}\n\n"
            "This was queued for your review (RFC-0017 owner carve-out). "
            "Approve or reject it before it expires."
        )

        dedup_key = f"relationship:pending-action-expiry:{action_id}"

        try:
            result = await propose_insight_candidate(
                db_pool,
                origin_butler="relationship",
                priority=_PENDING_ACTIONS_PRIORITY,
                category="pending-action-expiry",
                dedup_key=dedup_key,
                message=message,
                expires_at=expires_at,
                cooldown_days=None,  # No cooldown: each expiry window is a unique event
            )
            status = result.get("status", "error")
            if status == "accepted":
                stats["surfaced"] += 1
                logger.info(
                    "pending_actions_curation: surfaced action %s (tool=%s, "
                    "expires=%s, time_remaining=%s)",
                    action_id,
                    tool_name,
                    expires_at.isoformat(),
                    time_remaining,
                )
            elif status == "filtered":
                # Verbosity off or cooldown active — still counts as an attempt.
                logger.debug(
                    "pending_actions_curation: action %s filtered: %s",
                    action_id,
                    result.get("reason", "unknown"),
                )
                stats["surfaced"] += 1  # We tried; dedup/budget gated it
            else:
                logger.warning(
                    "pending_actions_curation: propose_insight_candidate error for action %s: %s",
                    action_id,
                    result.get("reason", "unknown"),
                )
                stats["errors"] += 1
        except Exception:
            logger.exception(
                "pending_actions_curation: error surfacing action %s (tool=%s)",
                action_id,
                tool_name,
            )
            stats["errors"] += 1

    # Persist checkpoint timestamp (best-effort).
    try:
        await state_set(db_pool, _PENDING_ACTIONS_CURATION_STATE_KEY, now_utc.isoformat())
    except Exception:
        logger.warning(
            "pending_actions_curation: failed to write checkpoint key=%s",
            _PENDING_ACTIONS_CURATION_STATE_KEY,
            exc_info=True,
        )

    logger.info(
        "pending_actions_curation complete: scanned=%d surfaced=%d "
        "skipped_no_expiry=%d skipped_not_approaching=%d "
        "skipped_already_expired=%d errors=%d",
        stats["scanned"],
        stats["surfaced"],
        stats["skipped_no_expiry"],
        stats["skipped_not_approaching"],
        stats["skipped_already_expired"],
        stats["errors"],
    )
    return stats


def _stamp_checkpoint(db_pool: asyncpg.Pool) -> None:  # pragma: no cover
    """Fire-and-forget checkpoint stamp via asyncio.create_task when pool is live.

    Used on the early-exit (no rows found) path where we do not need to await.
    Not called from test paths; excluded from coverage.
    """
    import asyncio

    async def _write() -> None:
        try:
            await state_set(db_pool, _CURATION_STATE_KEY, datetime.now(UTC).isoformat())
        except Exception:
            logger.debug("memory_curation: checkpoint write failed (no-rows path)", exc_info=True)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_write())
    except RuntimeError:
        pass  # No running loop in sync context (e.g. tests that call synchronously)
