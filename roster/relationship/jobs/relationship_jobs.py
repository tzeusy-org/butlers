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
            COALESCE(e.canonical_name, 'Unknown') AS contact_name,
            cem.entity_id
        FROM important_dates d
        JOIN contact_entity_map cem ON cem.contact_id = d.contact_id
        JOIN public.entities e ON e.id = cem.entity_id
        WHERE e.listed = true
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

    # Query all listed contacts with interaction info via contact_entity_map → entities
    stale_rows = await db_pool.fetch(
        """
        SELECT
            cem.contact_id AS id,
            cem.entity_id,
            e.stay_in_touch_days,
            COALESCE(e.canonical_name, 'Unknown') AS contact_name,
            CASE
                WHEN MAX(f.valid_at) IS NULL THEN NULL
                ELSE EXTRACT(EPOCH FROM (now() - MAX(f.valid_at))) / 86400.0
            END AS days_since_last
        FROM contact_entity_map cem
        JOIN public.entities e ON e.id = cem.entity_id
        LEFT JOIN facts f
            ON f.entity_id = cem.entity_id
           AND f.predicate LIKE 'interaction_%'
           AND f.scope = 'relationship'
           AND f.validity = 'active'
        WHERE e.listed = true
        GROUP BY cem.contact_id, cem.entity_id, e.stay_in_touch_days, e.canonical_name
        ORDER BY e.canonical_name
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
                    cem.contact_id AS id,
                    COALESCE(e.canonical_name, 'Unknown') AS contact_name
                FROM contact_entity_map cem
                JOIN public.entities e ON e.id = cem.entity_id
                WHERE cem.contact_id = ANY($1::uuid[])
                """,
                gift_contact_ids,
            )
            gift_contact_name_map = {row["id"]: row["contact_name"] for row in gift_contact_rows}

            # Find contacts with upcoming important dates within 14 days
            contacts_with_upcoming = await db_pool.fetch(
                """
                SELECT DISTINCT d.contact_id, d.month, d.day, d.label
                FROM important_dates d
                JOIN contact_entity_map cem ON cem.contact_id = d.contact_id
                JOIN public.entities e ON e.id = cem.entity_id
                WHERE e.listed = true
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
            cem.contact_id AS contact_id,
            COALESCE(e.canonical_name, 'Unknown') AS contact_name,
            COUNT(f.id)  AS interaction_count,
            MIN(f.valid_at) AS first_interaction_at
        FROM contact_entity_map cem
        JOIN public.entities e ON e.id = cem.entity_id
        LEFT JOIN facts f
            ON f.entity_id = cem.entity_id
           AND f.predicate LIKE 'interaction_%'
           AND f.scope = 'relationship'
           AND f.validity = 'active'
        WHERE e.listed = true
        GROUP BY cem.contact_id, e.canonical_name
        HAVING COUNT(f.id) > 0
        ORDER BY e.canonical_name
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

# Channels to monitor; maps source_channel → channel type used for lookup.
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

# Minimum interaction_* fact count within _KNOWS_WINDOW_DAYS to derive a 'knows' edge.
_KNOWS_THRESHOLD = 3

# Lookback window (days) for counting interactions when deriving 'knows' edges.
# Wider than _INTERACTION_SYNC_MAX_WINDOW_DAYS so occasional contacts still qualify.
_KNOWS_WINDOW_DAYS = 90


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
    ``public.contacts.entity_id``), and calls
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
        "co_attended_edges_minted": 0,
        "knows_edges_minted": 0,
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

    # Collects resolved non-owner entity IDs per event for co-attended edge derivation.
    event_to_entities: dict[str, list[uuid.UUID]] = {}

    for event_id, event_title, event_starts_at, attendee_emails in event_tasks:
        resolved_for_event: list[uuid.UUID] = []
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

            resolved_for_event.append(entity_id)

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

        if len(resolved_for_event) >= 2:
            event_to_entities[event_id] = resolved_for_event

    # -----------------------------------------------------------------------
    # Step 5: Derive co-attended edges from calendar event co-attendance.
    #
    # For each event where ≥ 2 non-owner attendees were resolved to entities,
    # emit a ``co-attended`` edge in both directions for every pair. Writing
    # both (A→B) and (B→A) makes the edge symmetric for graph traversal.
    # ``relationship_assert_fact`` deduplicates on (subject, predicate, object)
    # with validity='active', so repeated runs are idempotent.
    # -----------------------------------------------------------------------
    if event_to_entities:
        from butlers.tools.relationship.relationship_assert_fact import (
            AssertOutcome,
            relationship_assert_fact,
        )

        for _co_event_id, _entity_ids in event_to_entities.items():
            for _i, _entity_a in enumerate(_entity_ids):
                for _entity_b in _entity_ids[_i + 1 :]:
                    for _subj, _obj in ((_entity_a, _entity_b), (_entity_b, _entity_a)):
                        try:
                            _assert_result = await relationship_assert_fact(
                                db_pool,
                                _subj,
                                "co-attended",
                                str(_obj),
                                src="interaction_sync",
                                object_kind="entity",
                            )
                            if _assert_result.outcome in (
                                AssertOutcome.inserted,
                                AssertOutcome.superseded,
                            ):
                                stats["co_attended_edges_minted"] += 1
                        except Exception:
                            logger.exception(
                                "interaction_sync: error minting co-attended edge "
                                "subject=%s object=%s event=%s",
                                _subj,
                                _obj,
                                _co_event_id,
                            )
                            stats["errors"] += 1

    # -----------------------------------------------------------------------
    # Step 6: Derive 'knows' edges from cumulative interaction count.
    #
    # For each entity whose interaction_* fact count within the last
    # _KNOWS_WINDOW_DAYS meets or exceeds _KNOWS_THRESHOLD, mint a
    # bidirectional 'knows' edge with the owner entity.  Writing both
    # (contact→owner) and (owner→contact) makes the edge symmetric for graph
    # traversal.  relationship_assert_fact deduplicates on
    # (subject, predicate, object) with validity='active', so repeated runs
    # are idempotent.  The (owner→contact) direction is subject to the owner
    # carve-out (RFC 0017 §2.3) and may park in pending_actions.
    # -----------------------------------------------------------------------
    _knows_window_start = now_utc - timedelta(days=_KNOWS_WINDOW_DAYS)
    try:
        _knows_count_rows = await db_pool.fetch(
            """
            SELECT
                f.entity_id,
                COUNT(f.id) AS interaction_count
            FROM relationship.facts f
            WHERE f.predicate LIKE 'interaction_%'
              AND f.scope = 'relationship'
              AND f.validity = 'active'
              AND f.valid_at >= $1
            GROUP BY f.entity_id
            HAVING COUNT(f.id) >= $2
            """,
            _knows_window_start,
            _KNOWS_THRESHOLD,
        )
    except Exception:
        logger.exception("interaction_sync: failed to query interaction counts for knows edges")
        stats["errors"] += 1
        _knows_count_rows = []

    if _knows_count_rows:
        try:
            _owner_row = await db_pool.fetchrow(
                "SELECT id FROM public.entities WHERE 'owner' = ANY(roles) LIMIT 1"
            )
        except Exception:
            logger.exception("interaction_sync: failed to fetch owner entity for knows edges")
            _owner_row = None
            stats["errors"] += 1

        if _owner_row is not None:
            _owner_eid: uuid.UUID = _owner_row["id"]
            if not isinstance(_owner_eid, uuid.UUID):
                _owner_eid = uuid.UUID(str(_owner_eid))

            from butlers.tools.relationship.relationship_assert_fact import (
                AssertOutcome as _KnowsOutcome,
            )
            from butlers.tools.relationship.relationship_assert_fact import (
                relationship_assert_fact as _knows_assert,
            )

            for _kr in _knows_count_rows:
                _contact_eid = _kr["entity_id"]
                if not isinstance(_contact_eid, uuid.UUID):
                    try:
                        _contact_eid = uuid.UUID(str(_contact_eid))
                    except (ValueError, AttributeError):
                        continue

                if _contact_eid == _owner_eid:
                    continue

                for _subj, _obj in (
                    (_contact_eid, _owner_eid),
                    (_owner_eid, _contact_eid),
                ):
                    try:
                        _kassert = await _knows_assert(
                            db_pool,
                            _subj,
                            "knows",
                            str(_obj),
                            src="interaction_sync",
                            object_kind="entity",
                        )
                        if _kassert.outcome in (
                            _KnowsOutcome.inserted,
                            _KnowsOutcome.superseded,
                        ):
                            stats["knows_edges_minted"] += 1
                    except Exception:
                        logger.exception(
                            "interaction_sync: error minting knows edge subject=%s object=%s",
                            _subj,
                            _obj,
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
        "calendar_events_scanned=%d, co_attended_edges_minted=%d, "
        "knows_edges_minted=%d, errors=%d",
        stats["processed"],
        stats["logged"],
        stats["skipped_unresolved"],
        stats["skipped_owner"],
        stats["skipped_ineligible"],
        stats["skipped_group_too_large"],
        stats["calendar_events_scanned"],
        stats["co_attended_edges_minted"],
        stats["knows_edges_minted"],
        stats["errors"],
    )
    return stats


# ---------------------------------------------------------------------------
# Memory curation: object_entity_id authoring backfill
# ---------------------------------------------------------------------------
#
# Relational facts in ``relationship.facts`` must carry ``object_entity_id``
# for the edge-promotion sweep (``run_memory_curation``) to pick them up.
# Historically, some callers stored direct relational predicates as plain
# property facts (content = entity name, no ``object_entity_id``), bypassing
# the resolver. This helper finds those facts and, where the content matches
# exactly one entity by ``canonical_name``, writes the missing
# ``object_entity_id``. The subsequent promotion sweep then treats them as
# normal edge-fact candidates.
#
# Resolution is conservative: ambiguous content (> 1 entity match) and
# unresolved content (0 matches) are skipped and counted. The update is
# guarded on ``object_entity_id IS NULL`` so re-runs are idempotent.


async def _backfill_object_entity_ids(db_pool: asyncpg.Pool) -> dict[str, int]:
    """Resolve missing object_entity_id on relational facts from content.

    Scans active ``relationship.facts`` rows whose predicate is in
    ``_DIRECT_OR_ALIAS_PREDICATES`` and whose ``object_entity_id`` is ``NULL``.
    For each such fact, the ``content`` field is treated as an entity name and
    matched against ``public.entities.canonical_name`` (case-insensitive exact
    match). When exactly one entity is found the fact's ``object_entity_id``
    is updated in place; this makes the row visible to the edge-promotion sweep
    that runs immediately afterwards in :func:`run_memory_curation`.

    Args:
        db_pool: Database connection pool.

    Returns:
        Dictionary with keys: backfill_scanned, backfill_resolved,
        backfill_ambiguous, backfill_unresolved.
    """
    stats: dict[str, int] = {
        "backfill_scanned": 0,
        "backfill_resolved": 0,
        "backfill_ambiguous": 0,
        "backfill_unresolved": 0,
    }

    candidate_predicates = sorted(_DIRECT_OR_ALIAS_PREDICATES)

    try:
        rows = await db_pool.fetch(
            """
            SELECT id, content
            FROM facts
            WHERE validity        = 'active'
              AND scope           = 'relationship'
              AND object_entity_id IS NULL
              AND predicate        = ANY($1::text[])
              AND TRIM(content)   != ''
            ORDER BY created_at ASC NULLS LAST
            """,
            candidate_predicates,
        )
    except Exception:
        logger.exception("memory_curation backfill: failed to query candidate facts")
        return stats

    stats["backfill_scanned"] = len(rows)
    if not rows:
        logger.debug("memory_curation backfill: no candidate facts — nothing to backfill")
        return stats

    logger.info(
        "memory_curation backfill: resolving object_entity_id for %d relational facts",
        len(rows),
    )

    for row in rows:
        content: str = (row["content"] or "").strip()
        fact_id = row["id"]

        try:
            matches = await db_pool.fetch(
                """
                SELECT id FROM public.entities
                WHERE LOWER(TRIM(canonical_name)) = LOWER($1)
                """,
                content,
            )
        except Exception:
            logger.exception(
                "memory_curation backfill: entity lookup failed for fact %s content=%r",
                fact_id,
                content[:60],
            )
            stats["backfill_unresolved"] += 1
            continue

        if len(matches) == 1:
            object_entity_id = matches[0]["id"]
            try:
                await db_pool.execute(
                    """
                    UPDATE facts
                    SET object_entity_id = $1
                    WHERE id              = $2
                      AND object_entity_id IS NULL
                    """,
                    object_entity_id,
                    fact_id,
                )
                stats["backfill_resolved"] += 1
                logger.debug(
                    "memory_curation backfill: fact %s content=%r → entity %s",
                    fact_id,
                    content[:60],
                    object_entity_id,
                )
            except Exception:
                logger.exception("memory_curation backfill: failed to update fact %s", fact_id)
                stats["backfill_unresolved"] += 1
        elif len(matches) > 1:
            logger.debug(
                "memory_curation backfill: skipping fact %s — ambiguous name %r (%d entities)",
                fact_id,
                content[:60],
                len(matches),
            )
            stats["backfill_ambiguous"] += 1
        else:
            logger.debug(
                "memory_curation backfill: no entity found for fact %s content=%r",
                fact_id,
                content[:60],
            )
            stats["backfill_unresolved"] += 1

    logger.info(
        "memory_curation backfill: scanned=%d resolved=%d ambiguous=%d unresolved=%d",
        stats["backfill_scanned"],
        stats["backfill_resolved"],
        stats["backfill_ambiguous"],
        stats["backfill_unresolved"],
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
        # object_entity_id authoring backfill phase
        "backfill_scanned": 0,
        "backfill_resolved": 0,
        "backfill_ambiguous": 0,
        "backfill_unresolved": 0,
        # edge-promotion phase
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
    # Step 0: Authoring backfill — resolve object_entity_id for relational
    # facts stored without it (content = entity name, direct predicate).
    #
    # This runs BEFORE the promotion sweep so that facts resolved here are
    # immediately available to the promotion loop below (same invocation).
    # -----------------------------------------------------------------------
    backfill_stats = await _backfill_object_entity_ids(db_pool)
    stats.update(backfill_stats)

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
        "memory_curation complete: "
        "backfill_scanned=%d backfill_resolved=%d backfill_ambiguous=%d backfill_unresolved=%d "
        "scanned=%d proposed=%d inserted=%d "
        "pending_approval=%d unchanged=%d skipped_no_mapping=%d "
        "skipped_already_exists=%d errors=%d",
        stats["backfill_scanned"],
        stats["backfill_resolved"],
        stats["backfill_ambiguous"],
        stats["backfill_unresolved"],
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


# ---------------------------------------------------------------------------
# Fact retraction curation job (behavior #3: contradicted + low-confidence facts)
# ---------------------------------------------------------------------------

# Facts with confidence below this threshold are flagged for owner review.
# Deliberately conservative (0.6) — genuine uncertainty, not "needs cleanup".
_RETRACTION_LOW_CONF_THRESHOLD: float = 0.6

# State key for the checkpoint timestamp (observability; job is idempotent via
# pending_actions dedup — re-running is safe).
_RETRACTION_CURATION_STATE_KEY = "memory_curation.last_retraction_curation_at"

# Priority for retraction-review insights (lower than pending-action expiry but
# higher than stale-contact, since contradictions imply data integrity issues).
_RETRACTION_PRIORITY_CONTRADICTION = 75
_RETRACTION_PRIORITY_LOW_CONF = 50

# Insight expires after this many days (owner has a week to review).
_RETRACTION_INSIGHT_EXPIRES_DAYS = 7


async def run_fact_retraction_curation(db_pool: asyncpg.Pool) -> dict[str, Any]:
    """Flag contradicted and low-confidence facts for owner review.

    Scans ``relationship.facts`` (the prose-fact / property-fact store) for:

    **Contradictions:** Two or more active rows for the same ``(entity_id,
    predicate)`` pair but with differing ``content``.  When multiple active
    facts disagree on what is true, one or more of them should be retracted.

    **Low confidence:** Active rows whose ``confidence`` column is below
    :data:`_RETRACTION_LOW_CONF_THRESHOLD` (0.6 by default).  Such facts
    were flagged as uncertain at extraction time and may represent
    mis-extractions (the live example: 'has a son' inferred as a parent-of
    edge when the owner has no son).

    **Mutation policy — conservative and owner-approved:**

    * For EVERY flagged fact (contradiction candidate or low-confidence),
      this job creates a ``pending_actions`` row with
      ``tool_name='memory_forget'`` and the ``fact_id`` as the argument.  The
      owner must explicitly approve the retraction; nothing is auto-retracted.
    * Owner-entity facts receive the same treatment as any other fact — they
      are NEVER silently dropped.  The owner-approval loop is the *only* path
      to retraction.
    * Dedup: before inserting a new ``pending_actions`` row the writer checks
      whether a ``status='pending'`` row already exists for the same
      ``fact_id``.  If one exists the flagging is skipped (the first proposal
      is still outstanding).
    * Alongside each ``pending_actions`` row, an insight candidate is
      submitted via :func:`~butlers.tools.switchboard.insight.broker.propose_insight_candidate`
      so the owner receives a Telegram notification prompting them to
      act.

    Args:
        db_pool: Database connection pool (relationship butler schema context).

    Returns:
        Dictionary with keys: facts_scanned_contradiction, facts_scanned_low_conf,
        contradictions_found, low_conf_found, flagged_new, skipped_already_pending,
        skipped_owner_no_auto_retract (always 0 — owner facts go through the
        same pending_actions path), errors.
    """
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    logger.info(
        "Running fact_retraction_curation job "
        "(contradiction + low-confidence facts, threshold=%.2f)",
        _RETRACTION_LOW_CONF_THRESHOLD,
    )

    stats: dict[str, Any] = {
        "facts_scanned_contradiction": 0,
        "facts_scanned_low_conf": 0,
        "contradictions_found": 0,
        "low_conf_found": 0,
        "flagged_new": 0,
        "skipped_already_pending": 0,
        "errors": 0,
    }

    now_utc = datetime.now(UTC)
    expires_at = now_utc + timedelta(days=_RETRACTION_INSIGHT_EXPIRES_DAYS)

    # -----------------------------------------------------------------------
    # Step 1: Detect contradictions.
    #
    # A contradiction is: two or more active rows on the same (entity_id,
    # predicate) with different content.  We flag ALL active rows in such
    # groups — the owner decides which (if any) to retract.  Rows without an
    # entity_id are ignored (they cannot be reliably deduped across contacts).
    # -----------------------------------------------------------------------
    try:
        contradiction_rows = await db_pool.fetch(
            """
            SELECT
                f.id        AS fact_id,
                f.entity_id,
                f.predicate,
                f.content,
                f.confidence,
                f.metadata,
                COUNT(*) OVER (
                    PARTITION BY f.entity_id, f.predicate
                ) AS group_size
            FROM facts f
            WHERE f.validity  = 'active'
              AND f.scope     = 'relationship'
              AND f.entity_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM facts f2
                  WHERE f2.entity_id  = f.entity_id
                    AND f2.predicate  = f.predicate
                    AND f2.validity   = 'active'
                    AND f2.scope      = 'relationship'
                    AND f2.content   <> f.content
                    AND f2.id        <> f.id
              )
            ORDER BY f.entity_id, f.predicate, f.confidence ASC NULLS LAST
            """
        )
    except Exception:
        logger.exception("fact_retraction_curation: failed to query contradiction facts")
        stats["errors"] += 1
        return stats

    stats["facts_scanned_contradiction"] = len(contradiction_rows)
    stats["contradictions_found"] = len(contradiction_rows)
    logger.info(
        "fact_retraction_curation: found %d contradiction-group rows",
        len(contradiction_rows),
    )

    # -----------------------------------------------------------------------
    # Step 2: Detect low-confidence facts.
    #
    # Active facts whose confidence is below the threshold but are NOT already
    # in a contradiction group (to avoid double-flagging the same row).
    # -----------------------------------------------------------------------
    # Collect the UUIDs already surfaced by the contradiction query so we can
    # avoid issuing duplicate pending_actions for the same fact.
    contradiction_fact_ids: set[uuid.UUID] = {row["fact_id"] for row in contradiction_rows}

    try:
        low_conf_rows = await db_pool.fetch(
            """
            SELECT
                f.id        AS fact_id,
                f.entity_id,
                f.predicate,
                f.content,
                f.confidence,
                f.metadata
            FROM facts f
            WHERE f.validity   = 'active'
              AND f.scope      = 'relationship'
              AND f.confidence IS NOT NULL
              AND f.confidence  < $1
            ORDER BY f.confidence ASC NULLS LAST
            """,
            _RETRACTION_LOW_CONF_THRESHOLD,
        )
    except Exception:
        logger.exception("fact_retraction_curation: failed to query low-confidence facts")
        stats["errors"] += 1
        return stats

    # Exclude facts already picked up by the contradiction scan.
    low_conf_rows = [row for row in low_conf_rows if row["fact_id"] not in contradiction_fact_ids]

    stats["facts_scanned_low_conf"] = len(low_conf_rows)
    stats["low_conf_found"] = len(low_conf_rows)
    logger.info(
        "fact_retraction_curation: found %d low-confidence facts (threshold=%.2f)",
        len(low_conf_rows),
        _RETRACTION_LOW_CONF_THRESHOLD,
    )

    # -----------------------------------------------------------------------
    # Step 3: For each flagged fact, create a pending_actions row (deduped)
    # and submit an insight candidate.
    #
    # CRITICAL POLICY: NEVER auto-retract.  All paths go through pending_actions
    # for owner approval — including owner-entity facts.
    # -----------------------------------------------------------------------
    all_flagged = [("contradiction", row) for row in contradiction_rows] + [
        ("low_confidence", row) for row in low_conf_rows
    ]

    async def _ensure_pending_action(
        fact_id: uuid.UUID,
        predicate: str,
        content: str,
        confidence: float | None,
        flag_reason: str,
    ) -> str:
        """Create or return existing pending_actions row for this fact.

        Returns 'new', 'existing', or 'error'.
        """
        try:
            async with db_pool.acquire() as conn:
                # Dedup check: pending row already exists for this fact_id?
                # Pass the dict directly so asyncpg uses the jsonb codec (same pattern
                # as _create_pending_action in relationship_assert_fact.py).
                existing = await conn.fetchval(
                    """
                    SELECT id FROM pending_actions
                     WHERE tool_name = 'memory_forget'
                       AND status    = 'pending'
                       AND (tool_args ->> 'memory_id') = $1
                     LIMIT 1
                    """,
                    str(fact_id),
                )
                if existing is not None:
                    return "existing"

                action_id = uuid.uuid4()
                pending_now = datetime.now(UTC)
                action_expires_at = pending_now + timedelta(hours=72)

                conf_display = f"{confidence:.3f}" if confidence is not None else "null"
                why = (
                    f"Memory curation flagged this fact for owner review "
                    f"(reason: {flag_reason}). "
                    f"Predicate: {predicate!r}. "
                    f"Confidence: {conf_display}. "
                    f"Content preview: {content[:120]}. "
                    "Approving will retract this fact (mark validity='retracted'). "
                    "Rejecting keeps the fact active."
                )
                evidence = [
                    "source=fact_retraction_curation",
                    f"flag_reason={flag_reason}",
                    f"fact_id={fact_id}",
                    f"predicate={predicate}",
                    f"confidence={conf_display}",
                    f"content_preview={content[:120]}",
                ]

                await conn.execute(
                    "INSERT INTO pending_actions "
                    "(id, tool_name, tool_args, agent_summary, session_id, status, "
                    "requested_at, expires_at, why, evidence) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                    action_id,
                    "memory_forget",
                    {"memory_type": "fact", "memory_id": str(fact_id)},
                    f"Retract fact {fact_id} ({flag_reason}): {predicate!r} — {content[:60]}",
                    None,
                    "pending",
                    pending_now,
                    action_expires_at,
                    why,
                    evidence,
                )
                return "new"
        except Exception:
            logger.exception(
                "fact_retraction_curation: error creating pending_action for fact %s",
                fact_id,
            )
            return "error"

    for flag_reason, row in all_flagged:
        fact_id: uuid.UUID = row["fact_id"]
        entity_id: uuid.UUID | None = row.get("entity_id")
        predicate: str = row["predicate"]
        content: str = row["content"] or ""
        confidence: float | None = row["confidence"]

        outcome = await _ensure_pending_action(
            fact_id=fact_id,
            predicate=predicate,
            content=content,
            confidence=confidence,
            flag_reason=flag_reason,
        )

        if outcome == "error":
            stats["errors"] += 1
            continue
        if outcome == "existing":
            stats["skipped_already_pending"] += 1
            logger.debug(
                "fact_retraction_curation: skipping fact %s — pending_action already exists",
                fact_id,
            )
            continue

        # New pending_action created; also surface an insight candidate.
        stats["flagged_new"] += 1

        entity_display = str(entity_id) if entity_id is not None else "(no entity)"
        conf_display = f"{confidence:.3f}" if confidence is not None else "unknown"

        if flag_reason == "contradiction":
            insight_message = (
                f"Contradicted fact flagged for review:\n"
                f"Entity: {entity_display}\n"
                f"Predicate: {predicate!r}\n"
                f"Content: {content[:120]}\n"
                f"Confidence: {conf_display}\n"
                f"Fact ID: {fact_id}\n\n"
                "Multiple active facts on this entity+predicate have conflicting content. "
                "Review and approve retraction of incorrect facts via pending_actions."
            )
            dedup_key = f"relationship:fact-contradiction:{fact_id}"
            priority = _RETRACTION_PRIORITY_CONTRADICTION
        else:
            insight_message = (
                f"Low-confidence fact flagged for review:\n"
                f"Entity: {entity_display}\n"
                f"Predicate: {predicate!r}\n"
                f"Content: {content[:120]}\n"
                f"Confidence: {conf_display} (below threshold {_RETRACTION_LOW_CONF_THRESHOLD})\n"
                f"Fact ID: {fact_id}\n\n"
                "This fact was extracted with low confidence and may be incorrect. "
                "Review and approve retraction if the fact is wrong via pending_actions."
            )
            dedup_key = f"relationship:fact-low-conf:{fact_id}"
            priority = _RETRACTION_PRIORITY_LOW_CONF

        try:
            result = await propose_insight_candidate(
                db_pool,
                origin_butler="relationship",
                priority=priority,
                category=f"fact-retraction-{flag_reason}",
                dedup_key=dedup_key,
                message=insight_message,
                expires_at=expires_at,
                cooldown_days=7,  # Re-surface at most once a week per fact
            )
            status = result.get("status", "error")
            if status not in ("accepted", "filtered"):
                logger.warning(
                    "fact_retraction_curation: propose_insight_candidate error for fact %s: %s",
                    fact_id,
                    result.get("reason", "unknown"),
                )
                stats["errors"] += 1
        except Exception:
            logger.exception(
                "fact_retraction_curation: error surfacing insight for fact %s",
                fact_id,
            )
            stats["errors"] += 1

    # Persist checkpoint timestamp (best-effort).
    try:
        await state_set(db_pool, _RETRACTION_CURATION_STATE_KEY, now_utc.isoformat())
    except Exception:
        logger.warning(
            "fact_retraction_curation: failed to write checkpoint key=%s",
            _RETRACTION_CURATION_STATE_KEY,
            exc_info=True,
        )

    logger.info(
        "fact_retraction_curation complete: "
        "contradiction_rows=%d low_conf_rows=%d flagged_new=%d "
        "skipped_already_pending=%d errors=%d",
        stats["contradictions_found"],
        stats["low_conf_found"],
        stats["flagged_new"],
        stats["skipped_already_pending"],
        stats["errors"],
    )
    return stats


# ---------------------------------------------------------------------------
# Entity dedup curation constants
# ---------------------------------------------------------------------------

# State key for the checkpoint timestamp (observability; job is idempotent via
# pending_actions dedup — re-running is safe).
_ENTITY_DEDUP_CURATION_STATE_KEY = "memory_curation.last_entity_dedup_at"

# Insight priority for duplicate-entity merge candidates.
_ENTITY_DEDUP_PRIORITY = 80

# Pending-action and insight TTL for dedup candidates.
_ENTITY_DEDUP_EXPIRES_DAYS = 14

# Levenshtein distance threshold for "near-identical" canonical name matching.
# Names within this many single-character edits of each other are flagged.
_ENTITY_DEDUP_LEVENSHTEIN_THRESHOLD = 2

# Minimum canonical_name length (after normalisation) for near-identical matching.
# Pairs where either name is shorter than this threshold are skipped for the
# Levenshtein near-identical check.  Short names (e.g. "Sam", "Pam", "Jon",
# "Jan") have a high false-positive rate because a single-character substitution
# changes 33 % of the name — these should NOT be flagged as near-identical merge
# candidates even if their edit distance is within _ENTITY_DEDUP_LEVENSHTEIN_THRESHOLD.
# Exact (case-insensitive) duplicates are caught in the separate exact-match step
# above and are always surfaced regardless of name length.
# A threshold of 5 allows common real-world 5-character names like "Chloe" to
# still participate in near-identical matching while excluding 3-4 character names.
_ENTITY_DEDUP_MIN_NAME_LEN_FOR_NEAR_MATCH = 5


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings (pure Python).

    O(len(a) * len(b)) time and O(len(b)) space.  Adequate for short name
    comparisons; entity names are typically < 100 characters.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + (0 if ca == cb else 1),
            )
        prev = curr
    return prev[len(b)]


async def run_entity_dedup_curation(db_pool: asyncpg.Pool) -> dict[str, Any]:
    """Detect duplicate entities and surface merge candidates for owner review.

    **Behavior #2 — entity dedup/merge.**

    Scans ``public.entities`` for entities whose ``canonical_name`` is either:

    * **Exact duplicate** — two or more entities share the same
      ``LOWER(TRIM(canonical_name))``.
    * **Near-identical** — two entities have ``canonical_name`` values within
      :data:`_ENTITY_DEDUP_LEVENSHTEIN_THRESHOLD` edit-distance of each other
      (case-insensitive).

    Tombstoned entities (``metadata->>'merged_into' IS NOT NULL``) are excluded.

    For every duplicate pair detected, a ``pending_actions`` row is inserted
    with ``tool_name='entity_merge'`` so the owner must explicitly approve the
    merge.  **No autonomous merge is ever performed.**  The ``tool_args``
    format matches the :func:`~butlers.modules.memory.tools.entities.entity_merge`
    call signature::

        {"source_entity_id": "<uuid>", "target_entity_id": "<uuid>"}

    Alongside each new pending_action, an insight candidate is submitted via
    :func:`~butlers.tools.switchboard.insight.broker.propose_insight_candidate`
    so the owner receives a Telegram notification.

    **Dedup guard:** before inserting, the job checks whether a
    ``status='pending'`` row already exists for the same ordered pair
    (source, target).  If one exists the pair is skipped — the existing
    proposal is still outstanding.

    **Merge direction convention:** within each duplicate group, the entity
    with the highest ``created_at`` (newest) is the source (merged away) and
    the entity with the lowest ``created_at`` (oldest, canonical) is the
    target (survives).  For near-identical pairs, the same rule applies.

    Args:
        db_pool: Database connection pool (relationship butler schema context).

    Returns:
        Dictionary with keys:
          - entities_scanned: number of non-tombstoned entities examined.
          - exact_groups_found: number of exact-duplicate groups detected.
          - near_identical_pairs_found: number of near-identical pairs detected.
          - pairs_surfaced: number of new pending_actions rows created.
          - pairs_skipped_already_pending: number of pairs skipped (action exists).
          - errors: number of errors during processing.
    """
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    logger.info("Running entity_dedup_curation job (detect duplicate canonical names)")

    stats: dict[str, Any] = {
        "entities_scanned": 0,
        "exact_groups_found": 0,
        "near_identical_pairs_found": 0,
        "pairs_surfaced": 0,
        "pairs_skipped_already_pending": 0,
        "errors": 0,
    }

    now_utc = datetime.now(UTC)
    expires_at = now_utc + timedelta(days=_ENTITY_DEDUP_EXPIRES_DAYS)

    # -----------------------------------------------------------------------
    # Step 1: Fetch all non-tombstoned entities.
    # -----------------------------------------------------------------------
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, canonical_name, entity_type, roles
                  FROM public.entities
                 WHERE (metadata->>'merged_into') IS NULL
                   AND TRIM(canonical_name) != ''
                 ORDER BY created_at ASC
                """
            )
    except Exception:
        logger.exception("entity_dedup_curation: failed to query public.entities")
        stats["errors"] += 1
        return stats

    stats["entities_scanned"] = len(rows)
    logger.info("entity_dedup_curation: scanned %d non-tombstoned entities", len(rows))

    if not rows:
        logger.info("entity_dedup_curation: no entities found — nothing to check")
        try:
            await state_set(db_pool, _ENTITY_DEDUP_CURATION_STATE_KEY, now_utc.isoformat())
        except Exception:
            logger.warning(
                "entity_dedup_curation: failed to write checkpoint key=%s",
                _ENTITY_DEDUP_CURATION_STATE_KEY,
                exc_info=True,
            )
        return stats

    # -----------------------------------------------------------------------
    # Step 2: Detect exact duplicates.
    #
    # Group entities by LOWER(TRIM(canonical_name)).  Any group with > 1
    # member contains duplicate entities.  Within a group, the oldest
    # (lowest created_at, i.e. first in our ORDER BY ASC result) is the
    # canonical target; all others are sources.  We emit one pair per
    # (source, target) combination — typically one source per group.
    # -----------------------------------------------------------------------
    # Build ordered name → entity list mapping (ORDER BY created_at ASC preserved).
    name_groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = row["canonical_name"].strip().lower()
        name_groups.setdefault(key, []).append(
            {
                "id": str(row["id"]),
                "canonical_name": row["canonical_name"],
                "entity_type": row["entity_type"],
                "roles": list(row["roles"] or []),
            }
        )

    exact_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for name_key, group in name_groups.items():
        if len(group) < 2:
            continue
        stats["exact_groups_found"] += 1
        target = group[0]  # oldest — survives
        for source in group[1:]:
            exact_pairs.append((source, target))

    # -----------------------------------------------------------------------
    # Step 3: Detect near-identical pairs.
    #
    # For entities NOT already in an exact-duplicate group, compare all
    # unique pairs by Levenshtein distance on their normalised canonical
    # name.  Only pairs within the threshold distance are candidates.
    # We exclude entity IDs that already appear in exact_pairs to avoid
    # double-reporting.
    # -----------------------------------------------------------------------
    exact_pair_ids: set[str] = {e["id"] for pair in exact_pairs for e in pair}

    # Collect unique groups (one representative per normalised name).
    unique_entities: list[dict[str, Any]] = [
        group[0] for key, group in name_groups.items() if group[0]["id"] not in exact_pair_ids
    ]

    near_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen_near: set[frozenset[str]] = set()
    for i, a in enumerate(unique_entities):
        for b in unique_entities[i + 1 :]:
            name_a = a["canonical_name"].strip().lower()
            name_b = b["canonical_name"].strip().lower()
            # Skip near-identical matching when either name is too short.  A
            # threshold-2 edit-distance on a 3-character name means any single-
            # character substitution is within threshold (e.g. "Sam"/"Pam",
            # "Jon"/"Jan", "Ana"/"Ava" all have distance 1 or 2), which produces
            # false positives.  Short-name exact duplicates are already caught by
            # the exact-match step above, so nothing is missed.
            if min(len(name_a), len(name_b)) < _ENTITY_DEDUP_MIN_NAME_LEN_FOR_NEAR_MATCH:
                continue
            if _levenshtein(name_a, name_b) <= _ENTITY_DEDUP_LEVENSHTEIN_THRESHOLD:
                pair_key = frozenset({a["id"], b["id"]})
                if pair_key not in seen_near:
                    seen_near.add(pair_key)
                    stats["near_identical_pairs_found"] += 1
                    # Newer entity (b, since we use created_at ASC order) is source.
                    near_pairs.append((b, a))

    all_pairs = exact_pairs + near_pairs
    if not all_pairs:
        logger.info("entity_dedup_curation: no duplicate pairs detected — nothing to surface")
        try:
            await state_set(db_pool, _ENTITY_DEDUP_CURATION_STATE_KEY, now_utc.isoformat())
        except Exception:
            logger.warning(
                "entity_dedup_curation: failed to write checkpoint key=%s",
                _ENTITY_DEDUP_CURATION_STATE_KEY,
                exc_info=True,
            )
        return stats

    logger.info(
        "entity_dedup_curation: found %d candidate pairs (exact=%d, near_identical=%d)",
        len(all_pairs),
        len(exact_pairs),
        len(near_pairs),
    )

    # -----------------------------------------------------------------------
    # Step 4: For each pair, check for existing pending_actions and insert if
    # not already present, then surface an insight candidate.
    # -----------------------------------------------------------------------
    async def _ensure_dedup_pending_action(
        source: dict[str, Any],
        target: dict[str, Any],
        match_type: str,
    ) -> str:
        """Create or return existing pending_actions row for this dedup pair.

        Returns 'new', 'existing', or 'error'.
        """
        source_id = source["id"]
        target_id = target["id"]
        try:
            async with db_pool.acquire() as conn:
                # Dedup check: pending row already exists for this ordered pair?
                existing = await conn.fetchval(
                    """
                    SELECT id FROM pending_actions
                     WHERE tool_name = 'entity_merge'
                       AND status    = 'pending'
                       AND (tool_args ->> 'source_entity_id') = $1
                       AND (tool_args ->> 'target_entity_id') = $2
                     LIMIT 1
                    """,
                    source_id,
                    target_id,
                )
                if existing is not None:
                    return "existing"

                action_id = uuid.uuid4()
                pending_now = datetime.now(UTC)
                action_expires_at = pending_now + timedelta(days=_ENTITY_DEDUP_EXPIRES_DAYS)

                why = (
                    f"Entity dedup curation detected a potential duplicate entity pair "
                    f"({match_type} match).\n"
                    f"Source (newer, to be merged away): {source['canonical_name']!r} "
                    f"(id={source_id}, type={source['entity_type']})\n"
                    f"Target (older, to survive): {target['canonical_name']!r} "
                    f"(id={target_id}, type={target['entity_type']})\n\n"
                    "Approving will merge the source entity into the target: "
                    "all facts, aliases, and metadata are re-pointed to the target; "
                    "the source entity is tombstoned. "
                    "Rejecting keeps both entities separate."
                )
                evidence = [
                    "source=entity_dedup_curation",
                    f"match_type={match_type}",
                    f"source_entity_id={source_id}",
                    f"source_canonical_name={source['canonical_name']}",
                    f"target_entity_id={target_id}",
                    f"target_canonical_name={target['canonical_name']}",
                ]

                await conn.execute(
                    "INSERT INTO pending_actions "
                    "(id, tool_name, tool_args, agent_summary, session_id, status, "
                    "requested_at, expires_at, why, evidence) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                    action_id,
                    "entity_merge",
                    {
                        "source_entity_id": source_id,
                        "target_entity_id": target_id,
                    },
                    (
                        f"Merge duplicate entity {source['canonical_name']!r} "
                        f"({match_type}) into {target['canonical_name']!r}"
                    ),
                    None,
                    "pending",
                    pending_now,
                    action_expires_at,
                    why,
                    evidence,
                )
                return "new"
        except Exception:
            logger.exception(
                "entity_dedup_curation: error creating pending_action for pair %s → %s",
                source_id,
                target_id,
            )
            return "error"

    for match_type, (source, target) in [("exact", pair) for pair in exact_pairs] + [
        ("near_identical", pair) for pair in near_pairs
    ]:
        outcome = await _ensure_dedup_pending_action(source, target, match_type)

        if outcome == "error":
            stats["errors"] += 1
            continue

        if outcome == "existing":
            stats["pairs_skipped_already_pending"] += 1
            logger.debug(
                "entity_dedup_curation: skipping pair %s → %s — pending_action already exists",
                source["id"],
                target["id"],
            )
            continue

        # New pending_action created; surface an insight candidate.
        stats["pairs_surfaced"] += 1
        insight_message = (
            f"Duplicate entity detected ({match_type} name match):\n"
            f"• Source (newer): {source['canonical_name']!r} (id={source['id']})\n"
            f"• Target (older): {target['canonical_name']!r} (id={target['id']})\n\n"
            "A merge candidate has been queued for your review. "
            "Approve via pending_actions to merge the source into the target, "
            "or reject to keep both entities separate."
        )
        dedup_key = f"relationship:entity-dedup:{source['id']}:{target['id']}"

        try:
            result = await propose_insight_candidate(
                db_pool,
                origin_butler="relationship",
                priority=_ENTITY_DEDUP_PRIORITY,
                category="entity-dedup",
                dedup_key=dedup_key,
                message=insight_message,
                expires_at=expires_at,
                cooldown_days=7,
            )
            status = result.get("status", "error")
            if status not in ("accepted", "filtered"):
                logger.warning(
                    "entity_dedup_curation: propose_insight_candidate error for pair %s → %s: %s",
                    source["id"],
                    target["id"],
                    result.get("reason", "unknown"),
                )
                stats["errors"] += 1
        except Exception:
            logger.exception(
                "entity_dedup_curation: error surfacing insight for pair %s → %s",
                source["id"],
                target["id"],
            )
            stats["errors"] += 1

    # Persist checkpoint timestamp (best-effort).
    try:
        await state_set(db_pool, _ENTITY_DEDUP_CURATION_STATE_KEY, now_utc.isoformat())
    except Exception:
        logger.warning(
            "entity_dedup_curation: failed to write checkpoint key=%s",
            _ENTITY_DEDUP_CURATION_STATE_KEY,
            exc_info=True,
        )

    logger.info(
        "entity_dedup_curation complete: "
        "entities_scanned=%d exact_groups=%d near_identical_pairs=%d "
        "pairs_surfaced=%d skipped_already_pending=%d errors=%d",
        stats["entities_scanned"],
        stats["exact_groups_found"],
        stats["near_identical_pairs_found"],
        stats["pairs_surfaced"],
        stats["pairs_skipped_already_pending"],
        stats["errors"],
    )
    return stats


# ---------------------------------------------------------------------------
# Episodic predicate curation job (behavior #5: episodic predicates leaking into
# the durable fact store)
# ---------------------------------------------------------------------------

# ── Predicate taxonomy ──────────────────────────────────────────────────────
#
# EPISODIC predicates are tied to a specific moment in time: they describe a
# transient state, a one-time observation, or a coordination note. They should
# NOT be stored with permanence='stable' or permanence='permanent' because they
# will not remain true across time.
#
#   interaction_note  — free-text summary tied to a single interaction event
#   current_activity  — what someone is doing right now
#   current_mood      — transient emotional state
#   today_note        — a note written for/about a specific day
#   meeting_note      — free-text from a single meeting
#   event_note        — note about a specific event
#   coordination_note — scheduling/coordination text for a specific engagement
#
# Taxonomy boundary rules:
#   • interaction_* predicates written by interaction_log() (interaction_call,
#     interaction_meeting, interaction_email, …) are INTENTIONALLY stored at
#     permanence='stable' — they are temporal interaction records keyed on valid_at
#     and are EXCLUDED from this curation sweep.
#     Exception: interaction_note is INCLUDED. It is an ephemeral free-text annotation
#     that must stay at volatile/ephemeral permanence. It is NOT written by
#     interaction_log() — type='note' is a reserved/rejected type there — so
#     interaction_note at stable means it was mis-stored and must be reclassified.
#   • contact_note is a free-text annotation about a person; it can legitimately
#     live at permanence='stable' as a persistent CRM note. EXCLUDED.
#   • life_event, contact_task, loan, gift are structured CRM records that the
#     owner deliberately persists. EXCLUDED.
#
# DURABLE permanence levels that should NOT be used for episodic predicates:
#   'permanent' — zero decay, truly permanent facts (birth dates, immutable bio)
#   'stable'    — very slow decay (0.002); CRM records, relationship metadata
#
# Appropriate permanence for episodic facts:
#   'volatile' or 'ephemeral' — fast decay; they expire naturally.
#
# ────────────────────────────────────────────────────────────────────────────

_EPISODIC_PREDICATES: frozenset[str] = frozenset(
    {
        "interaction_note",  # Free-text tied to a single interaction event
        "current_activity",  # Transient state: what someone is doing right now
        "current_mood",  # Transient emotional state
        "today_note",  # Note written for/about a specific day
        "meeting_note",  # Free-text from a single meeting
        "event_note",  # Note about a specific event
        "coordination_note",  # Scheduling/coordination text for a specific engagement
    }
)

# Permanence levels that indicate a durable storage intent.  Episodic predicates
# found at these levels have been incorrectly stored and need reclassification.
_EPISODIC_DURABLE_PERMANENCES: frozenset[str] = frozenset({"stable", "permanent"})

# State key for the checkpoint timestamp (observability; job is idempotent via
# pending_actions dedup — re-running is safe).
_EPISODIC_CURATION_STATE_KEY = "memory_curation.last_episodic_predicate_at"

# Insight priority for episodic-predicate-in-durable-store detections.
# Lower than contradictions (75) and retraction low-conf (50); higher than
# milestone insights (30) — these are data hygiene notices, not urgent.
_EPISODIC_CURATION_PRIORITY = 45

# How many days before the pending_action row expires if not acted on.
_EPISODIC_CURATION_EXPIRES_DAYS = 7


async def run_episodic_predicate_curation(db_pool: asyncpg.Pool) -> dict[str, Any]:
    """Flag episodic-predicate facts stored at durable permanence for owner review.

    **Behavior #5 — episodic predicates leaking into the durable fact store.**

    Scans ``relationship.facts`` for active rows whose ``predicate`` belongs to
    :data:`_EPISODIC_PREDICATES` (e.g. ``interaction_note``, ``current_activity``)
    AND whose ``permanence`` is one of :data:`_EPISODIC_DURABLE_PERMANENCES`
    (``'stable'`` or ``'permanent'``).

    Such facts were almost certainly written with the wrong permanence: episodic
    content tied to a specific moment should decay quickly, not persist forever.
    The correct resolution is to reclassify them to ``'volatile'`` or ``'ephemeral'``.

    **Taxonomy:**  The :data:`_EPISODIC_PREDICATES` frozenset documents the
    conservative episodic taxonomy.  Most ``interaction_*`` predicates (written by
    ``interaction_log()``, e.g. ``interaction_call``, ``interaction_meeting``) are
    explicitly EXCLUDED — they are temporal facts keyed on ``valid_at`` and are
    intentionally stored at ``permanence='stable'``.  The single exception is
    ``interaction_note``, which IS included: it is an ephemeral free-text annotation
    that must stay at ``volatile``/``ephemeral`` permanence.  ``interaction_log()``
    rejects ``type='note'`` to enforce this, so ``interaction_note`` at durable
    permanence is always a mis-stored fact that warrants reclassification.

    **Mutation policy — conservative and owner-approved:**

    * For EVERY flagged fact this job creates a ``pending_actions`` row with
      ``tool_name='memory_reclassify'`` and ``permanence_target='volatile'`` in
      ``tool_args``.  The owner must explicitly approve the reclassification.
    * Dedup: before inserting a new row the writer checks whether a
      ``status='pending'`` row already exists for the same ``fact_id`` and
      ``tool_name='memory_reclassify'``.  If one exists the flagging is skipped.
    * Alongside each ``pending_actions`` row an insight candidate is submitted via
      :func:`~butlers.tools.switchboard.insight.broker.propose_insight_candidate`
      so the owner receives a notification.
    * A checkpoint timestamp is written to state after each run.

    Args:
        db_pool: Database connection pool (relationship butler schema context).

    Returns:
        Dictionary with keys: facts_scanned, episodic_found, flagged_new,
        skipped_already_pending, errors.
    """
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    logger.info(
        "Running episodic_predicate_curation job "
        "(episodic predicates in durable fact store, predicates=%d)",
        len(_EPISODIC_PREDICATES),
    )

    stats: dict[str, Any] = {
        "facts_scanned": 0,
        "episodic_found": 0,
        "flagged_new": 0,
        "skipped_already_pending": 0,
        "errors": 0,
    }

    now_utc = datetime.now(UTC)
    expires_at = now_utc + timedelta(days=_EPISODIC_CURATION_EXPIRES_DAYS)

    # -----------------------------------------------------------------------
    # Step 1: Query for episodic-predicate facts stored at durable permanence.
    #
    # Scope: active, relationship-scoped facts only.
    # Predicate list: _EPISODIC_PREDICATES (includes interaction_note as an episodic
    #   exception; interaction_log rejects type='note' to prevent false positives).
    # Permanence filter: 'stable' or 'permanent'.
    # -----------------------------------------------------------------------
    try:
        rows = await db_pool.fetch(
            """
            SELECT
                f.id         AS fact_id,
                f.entity_id,
                f.predicate,
                f.content,
                f.confidence,
                f.permanence
            FROM facts f
            WHERE f.validity    = 'active'
              AND f.scope       = 'relationship'
              AND f.predicate   = ANY($1::text[])
              AND f.permanence  = ANY($2::text[])
            """,
            list(_EPISODIC_PREDICATES),
            list(_EPISODIC_DURABLE_PERMANENCES),
        )
    except Exception:
        logger.exception("episodic_predicate_curation: failed to query facts")
        stats["errors"] += 1
        return stats

    stats["facts_scanned"] = len(rows)
    stats["episodic_found"] = len(rows)
    logger.info(
        "episodic_predicate_curation: found %d episodic facts at durable permanence",
        len(rows),
    )

    if not rows:
        try:
            await state_set(db_pool, _EPISODIC_CURATION_STATE_KEY, now_utc.isoformat())
        except Exception:
            logger.warning(
                "episodic_predicate_curation: failed to write checkpoint key=%s (no rows path)",
                _EPISODIC_CURATION_STATE_KEY,
                exc_info=True,
            )
        return stats

    # -----------------------------------------------------------------------
    # Step 2: For each flagged fact, create a pending_actions row (deduped)
    # and submit an insight candidate.
    #
    # CRITICAL POLICY: NEVER auto-mutate.  All paths go through pending_actions
    # for owner approval — including owner-entity facts.
    # -----------------------------------------------------------------------

    async def _ensure_pending_action(
        fact_id: uuid.UUID,
        predicate: str,
        content: str,
        permanence: str,
        confidence: float | None,
    ) -> str:
        """Create or return existing pending_actions row for this fact.

        Returns 'new', 'existing', or 'error'.
        """
        try:
            async with db_pool.acquire() as conn:
                # Dedup check: pending row already exists for this fact_id and tool?
                existing = await conn.fetchval(
                    """
                    SELECT id FROM pending_actions
                     WHERE tool_name = 'memory_reclassify'
                       AND status    = 'pending'
                       AND (tool_args ->> 'memory_id') = $1
                     LIMIT 1
                    """,
                    str(fact_id),
                )
                if existing is not None:
                    return "existing"

                action_id = uuid.uuid4()
                pending_now = datetime.now(UTC)
                action_expires_at = pending_now + timedelta(hours=72)

                conf_display = f"{confidence:.3f}" if confidence is not None else "null"
                why = (
                    f"Memory curation detected an episodic predicate {predicate!r} "
                    f"stored at permanence={permanence!r}. "
                    f"Episodic facts tied to specific moments should use permanence='volatile' "
                    f"or 'ephemeral' so they decay naturally — not 'stable' or 'permanent'. "
                    f"Confidence: {conf_display}. "
                    f"Content preview: {content[:120]}. "
                    "Approving will reclassify this fact to permanence='volatile'. "
                    "Rejecting keeps the fact at its current permanence."
                )
                evidence = [
                    "source=episodic_predicate_curation",
                    f"predicate={predicate}",
                    f"permanence_current={permanence}",
                    "permanence_target=volatile",
                    f"fact_id={fact_id}",
                    f"confidence={conf_display}",
                    f"content_preview={content[:120]}",
                ]

                await conn.execute(
                    "INSERT INTO pending_actions "
                    "(id, tool_name, tool_args, agent_summary, session_id, status, "
                    "requested_at, expires_at, why, evidence) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                    action_id,
                    "memory_reclassify",
                    {
                        "memory_type": "fact",
                        "memory_id": str(fact_id),
                        "permanence_target": "volatile",
                    },
                    (
                        f"Reclassify fact {fact_id} "
                        f"(episodic-in-durable): {predicate!r} at "
                        f"{permanence!r} — {content[:60]}"
                    ),
                    None,
                    "pending",
                    pending_now,
                    action_expires_at,
                    why,
                    evidence,
                )
                return "new"
        except Exception:
            logger.exception(
                "episodic_predicate_curation: error creating pending_action for fact %s",
                fact_id,
            )
            return "error"

    for row in rows:
        fact_id: uuid.UUID = row["fact_id"]
        entity_id: uuid.UUID | None = row.get("entity_id")
        predicate: str = row["predicate"]
        content: str = row["content"] or ""
        confidence: float | None = row["confidence"]
        permanence: str = row["permanence"]

        outcome = await _ensure_pending_action(
            fact_id=fact_id,
            predicate=predicate,
            content=content,
            permanence=permanence,
            confidence=confidence,
        )

        if outcome == "error":
            stats["errors"] += 1
            continue
        if outcome == "existing":
            stats["skipped_already_pending"] += 1
            logger.debug(
                "episodic_predicate_curation: skipping fact %s — pending_action already exists",
                fact_id,
            )
            continue

        # New pending_action created; also surface an insight candidate.
        stats["flagged_new"] += 1

        entity_display = str(entity_id) if entity_id is not None else "(no entity)"
        conf_display = f"{confidence:.3f}" if confidence is not None else "unknown"

        insight_message = (
            f"Episodic fact stored at durable permanence flagged for reclassification:\n"
            f"Entity: {entity_display}\n"
            f"Predicate: {predicate!r} (episodic — should not be stored as {permanence!r})\n"
            f"Content: {content[:120]}\n"
            f"Confidence: {conf_display}\n"
            f"Fact ID: {fact_id}\n\n"
            f"This fact uses an episodic predicate but was stored at permanence={permanence!r}. "
            "Episodic facts should decay quickly (volatile/ephemeral). "
            "Review and approve reclassification to permanence='volatile' via pending_actions."
        )
        dedup_key = f"relationship:episodic-in-durable:{fact_id}"

        try:
            result = await propose_insight_candidate(
                db_pool,
                origin_butler="relationship",
                priority=_EPISODIC_CURATION_PRIORITY,
                category="episodic-predicate-in-durable",
                dedup_key=dedup_key,
                message=insight_message,
                expires_at=expires_at,
                cooldown_days=7,  # Re-surface at most once a week per fact
            )
            status = result.get("status", "error")
            if status not in ("accepted", "filtered"):
                logger.warning(
                    "episodic_predicate_curation: propose_insight_candidate error for fact %s: %s",
                    fact_id,
                    result.get("reason", "unknown"),
                )
                stats["errors"] += 1
        except Exception:
            logger.exception(
                "episodic_predicate_curation: error surfacing insight for fact %s",
                fact_id,
            )
            stats["errors"] += 1

    # Persist checkpoint timestamp (best-effort).
    try:
        await state_set(db_pool, _EPISODIC_CURATION_STATE_KEY, now_utc.isoformat())
    except Exception:
        logger.warning(
            "episodic_predicate_curation: failed to write checkpoint key=%s",
            _EPISODIC_CURATION_STATE_KEY,
            exc_info=True,
        )

    logger.info(
        "episodic_predicate_curation complete: "
        "facts_scanned=%d episodic_found=%d flagged_new=%d "
        "skipped_already_pending=%d errors=%d",
        stats["facts_scanned"],
        stats["episodic_found"],
        stats["flagged_new"],
        stats["skipped_already_pending"],
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
