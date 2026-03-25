"""Cross-butler daily briefing jobs.

This module contains:

* Contribution schema — the standard envelope each specialist butler writes
  to its own state store under ``briefing/daily/<YYYY-MM-DD>``.
* Shared helpers — key generation and cleanup for contribution state entries.
* ``collect_briefing_contributions`` — the General butler's aggregation job
  that reads all specialist contributions via ``general.v_briefing_contributions``
  and writes a combined payload to ``briefing/combined/<YYYY-MM-DD>``.

Design reference: openspec/changes/cross-butler-daily-briefing/
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta, timezone
from datetime import date as date_cls
from typing import Any, TypedDict

import asyncpg

from butlers.core.state import state_set

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


def today_sgt() -> str:
    """Return today's date string (YYYY-MM-DD) in SGT (UTC+8)."""
    return datetime.now(tz=SGT).date().isoformat()


def contribution_key(date: str) -> str:
    """Return the state store key for a contribution on *date*."""
    return f"{CONTRIBUTION_KEY_PREFIX}{date}"


def combined_key(date: str) -> str:
    """Return the state store key for the combined briefing on *date*."""
    return f"{COMBINED_KEY_PREFIX}{date}"


def validate_contribution(raw: Any) -> BriefingContribution | None:
    """Validate and return a typed contribution dict, or None if malformed.

    Required fields: ``butler``, ``date``, ``has_updates``.
    Optional (with safe defaults): ``highlights`` (empty list), ``summary`` ("").

    Returns None and logs a warning for any validation failure.
    """
    if not isinstance(raw, dict):
        logger.warning("Briefing contribution is not a dict: %r", type(raw).__name__)
        return None

    missing = [f for f in ("butler", "date", "has_updates") if f not in raw]
    if missing:
        logger.warning(
            "Briefing contribution missing required fields %s (got keys: %s)",
            missing,
            sorted(raw.keys()),
        )
        return None

    butler = raw.get("butler")
    date = raw.get("date")
    has_updates = raw.get("has_updates")

    if not isinstance(butler, str) or not butler:
        logger.warning("Briefing contribution 'butler' must be a non-empty string, got: %r", butler)
        return None
    if not isinstance(date, str) or not date:
        logger.warning("Briefing contribution 'date' must be a non-empty string, got: %r", date)
        return None
    if not isinstance(has_updates, bool):
        logger.warning(
            "Briefing contribution 'has_updates' must be a bool, got: %r",
            type(has_updates).__name__,
        )
        return None

    highlights: list[BriefingHighlight] = []
    raw_highlights = raw.get("highlights", [])
    if isinstance(raw_highlights, list):
        for h in raw_highlights:
            if isinstance(h, dict) and "category" in h and "text" in h and "priority" in h:
                highlights.append(
                    BriefingHighlight(
                        category=str(h["category"]),
                        text=str(h["text"]),
                        priority=str(h["priority"]),
                    )
                )

    summary = raw.get("summary", "")
    if not isinstance(summary, str):
        summary = str(summary)

    return BriefingContribution(
        butler=butler,
        date=date,
        has_updates=has_updates,
        highlights=highlights,
        summary=summary,
    )


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
    rows = await pool.fetch(
        "SELECT key FROM state WHERE key LIKE $1",
        f"{CONTRIBUTION_KEY_PREFIX}%",
    )
    expired_keys = []
    for row in rows:
        key: str = row["key"]
        date_suffix = key[len(CONTRIBUTION_KEY_PREFIX) :]
        try:
            entry_date = date_cls.fromisoformat(date_suffix)
        except ValueError:
            continue
        if entry_date < cutoff:
            expired_keys.append(key)

    if expired_keys:
        await pool.execute(
            "DELETE FROM state WHERE key = ANY($1::text[])",
            expired_keys,
        )

    return len(expired_keys)


# ---------------------------------------------------------------------------
# Specialist contribution job (per-butler)
# ---------------------------------------------------------------------------


async def run_daily_briefing_contribution(*, pool: asyncpg.Pool) -> dict[str, Any]:
    """Write today's domain snapshot as a briefing contribution.

    Queries butler-specific state tables, assembles the contribution envelope
    (butler, date, has_updates, highlights, summary), and persists it to the
    state store under ``briefing/daily/<YYYY-MM-DD>`` (SGT).

    Not yet implemented — registered in daemon._DETERMINISTIC_SCHEDULE_JOB_REGISTRY
    as a placeholder pending tasks 3.x of the cross-butler-daily-briefing spec.
    """
    raise NotImplementedError(
        "run_daily_briefing_contribution is not yet implemented; "
        "see openspec/changes/cross-butler-daily-briefing/tasks.md tasks 3.1-3.6"
    )


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

    date_str = today_sgt()
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
        contribution = validate_contribution(raw_value)
        if contribution is None:
            logger.warning(
                "Briefing contribution from butler=%s failed validation; skipping",
                source_butler,
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


async def run_collect_briefing_contributions(*, pool: asyncpg.Pool) -> dict[str, Any]:
    """Compat shim: daemon registry calls this keyword-only form.

    Delegates to ``collect_briefing_contributions`` with ``job_args=None``.
    """
    return await collect_briefing_contributions(pool, None)
