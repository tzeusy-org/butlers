"""Health workouts: answer "when did I last <activity>?" from workout_session facts.

Reads the ``workout_session`` facts written by ``wellness_ingest`` (Google Health
exercise sessions). Read-only, owner data, Health schema only. Time is stored in
UTC and rendered in the owner's configured zone.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

logger = logging.getLogger(__name__)

# Ingest stores Google's exerciseType lowercased with underscores as spaces
# (src/butlers/connectors/google_health.py, _normalize_workout_record). It keeps no
# synonym table, so this small explicit one maps everyday verbs and variants onto one
# token before comparing. A query matches a stored type when every canonical query
# token appears in the canonical stored type; anything else is "no match", never a guess.
_TOKEN_ALIASES: dict[str, str] = {
    "run": "running",
    "jog": "running",
    "jogging": "running",
    "swim": "swimming",
    "ride": "biking",
    "bike": "biking",
    "cycle": "biking",
    "cycling": "biking",
    "walk": "walking",
    "hike": "hiking",
    "lift": "weightlifting",
    "lifting": "weightlifting",
    "weights": "weightlifting",
}

_PAGE_SIZE = 200
# A device record only loses to an owner-logged one that overlaps it, so after the first
# match keep reading back this far for a manual overlap (no session runs longer).
_OVERLAP_LOOKBACK = timedelta(hours=24)

NO_WORKOUT_REASON = "no_workout_recorded"


def _canonical_tokens(text: str) -> frozenset[str]:
    spaced = re.sub(r"[_\-\s]+", " ", text.strip().lower())
    return frozenset(_TOKEN_ALIASES.get(token, token) for token in spaced.split() if token)


def activity_matches(query: str | None, stored: str | None) -> bool:
    """Whether a stored ``activity_type`` satisfies the owner's activity wording."""
    if query is None or not query.strip():
        return True
    wanted = _canonical_tokens(query)
    return bool(wanted) and wanted <= _canonical_tokens(stored or "")


def _interval(start: datetime, metadata: dict[str, Any]) -> tuple[datetime, datetime]:
    end: datetime | None = None
    raw_end = metadata.get("end_time")
    if raw_end:
        try:
            end = datetime.fromisoformat(str(raw_end).replace("Z", "+00:00"))
            if end.tzinfo is None:
                end = end.replace(tzinfo=UTC)
        except ValueError:
            end = None
    if end is None or end < start:
        end = start + timedelta(milliseconds=int(metadata.get("duration_ms") or 0))
    return start, end


def select_last_workout(rows: list[dict[str, Any]], activity: str | None) -> dict[str, Any] | None:
    """Pick the most recent matching session; owner-logged beats an overlapping device record.

    ``rows`` are ``{id, valid_at, metadata}`` of active workout facts in any order.
    """
    matching = [
        row
        for row in rows
        if row["valid_at"] is not None
        and activity_matches(activity, (row.get("metadata") or {}).get("activity_type"))
    ]
    if not matching:
        return None
    matching.sort(key=lambda row: row["valid_at"], reverse=True)
    latest = matching[0]
    if (latest["metadata"] or {}).get("detection") == "manual":
        return latest
    l_start, l_end = _interval(latest["valid_at"], latest["metadata"] or {})
    for row in matching[1:]:
        meta = row["metadata"] or {}
        if meta.get("detection") != "manual":
            continue
        start, end = _interval(row["valid_at"], meta)
        if start <= l_end and l_start <= end:
            return row
    return latest


def _zone(name: str | None) -> tuple[ZoneInfo, str]:
    try:
        return ZoneInfo(name or "UTC"), name or "UTC"
    except Exception:  # noqa: BLE001 - an unknown zone must not break the read
        logger.warning("workout_last: unknown timezone %r; rendering in UTC", name)
        return ZoneInfo("UTC"), "UTC"


def render_workout(row: dict[str, Any], timezone: str | None) -> dict[str, Any]:
    """Shape one workout fact for the owner: local start, duration, source, provenance id."""
    meta = row.get("metadata") or {}
    zone, zone_name = _zone(timezone)
    start_utc = row["valid_at"].astimezone(UTC)
    duration_ms = int(meta.get("duration_ms") or 0)
    return {
        "found": True,
        "id": str(row["id"]),
        "activity": meta.get("activity_type") or "workout",
        "started_at": start_utc.astimezone(zone).isoformat(),
        "started_at_utc": start_utc.isoformat(),
        "timezone": zone_name,
        "duration_minutes": round(duration_ms / 60000, 1),
        "source": "owner_logged" if meta.get("detection") == "manual" else "device",
        "provider": meta.get("source") or "google_health",
    }


async def workout_last(
    pool: asyncpg.Pool,
    activity: str | None = None,
    *,
    timezone: str | None = "UTC",
    page_size: int = _PAGE_SIZE,
) -> dict[str, Any]:
    """Return the most recent recorded workout matching ``activity`` (any if omitted).

    Reads active facts newest-first in keyset pages until the first match (plus the overlap
    lookback), so an older match is never missed behind newer workouts of other activities.
    No match returns ``{"found": False, "reason": "no_workout_recorded", ...}``, never an
    error or a guess.
    """
    candidates: list[dict[str, Any]] = []
    first_match_at: datetime | None = None
    cursor: tuple[datetime, Any] | None = None
    while True:
        if cursor is None:
            rows = await pool.fetch(
                "SELECT id, valid_at, metadata FROM facts"
                " WHERE predicate = 'workout_session' AND validity = 'active'"
                " AND scope = 'health' AND valid_at IS NOT NULL"
                " ORDER BY valid_at DESC, id DESC LIMIT $1",
                page_size,
            )
        else:
            rows = await pool.fetch(
                "SELECT id, valid_at, metadata FROM facts"
                " WHERE predicate = 'workout_session' AND validity = 'active'"
                " AND scope = 'health' AND valid_at IS NOT NULL"
                " AND (valid_at, id) < ($2, $3)"
                " ORDER BY valid_at DESC, id DESC LIMIT $1",
                page_size,
                cursor[0],
                cursor[1],
            )
        if not rows:
            break
        done = False
        for row in rows:
            item = dict(row)
            if first_match_at is not None and item["valid_at"] < first_match_at - _OVERLAP_LOOKBACK:
                done = True
                break
            candidates.append(item)
            if first_match_at is None and activity_matches(
                activity, (item.get("metadata") or {}).get("activity_type")
            ):
                first_match_at = item["valid_at"]
                if (item["metadata"] or {}).get("detection") == "manual":
                    done = True
                    break
        if done or len(rows) < page_size:
            break
        cursor = (rows[-1]["valid_at"], rows[-1]["id"])
    chosen = select_last_workout(candidates, activity)
    if chosen is None:
        return {"found": False, "reason": NO_WORKOUT_REASON, "activity": activity}
    return render_workout(chosen, timezone)
