"""Read ``away-until`` facts for nudge deferral (bu-s11n0s.14, slice S2c).

``automated_evidence_sync`` (S2b) writes one ``away-until <YYYY-MM-DD>`` fact per bound,
authenticated auto-reply. The predicate is multi-valued and never superseded, so a contact can
hold several, most of them already past. A contact is *away* only while the most recently
observed fact whose date is today or later says so; a past date means the contact is back.

Producers defer a nudge to ``away_until + 1 day``. The reconnect suggestion job is weekly, so
deferring means leaving the contact out of this run: the first run after the date reconsiders
them, and :func:`deferred_until` names that day.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from typing import Any

import asyncpg

AWAY_UNTIL_PREDICATE = "away-until"


def _parse(raw: Any) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == raw else None


def current_away_until(rows: Iterable[Mapping[str, Any]], today: date) -> date | None:
    """The date the contact is away until, or ``None`` when no unexpired fact exists.

    ``rows`` carry ``object`` (an ISO date literal) and ``observed_at``. Malformed objects are
    ignored. Among facts dated today or later, the most recently observed wins; a tie on
    ``observed_at`` takes the later date, so replay order never changes the answer.
    """
    best: tuple[datetime, date] | None = None
    for row in rows:
        until = _parse(row.get("object"))
        observed = row.get("observed_at")
        if until is None or until < today or not isinstance(observed, datetime):
            continue
        if best is None or (observed, until) > best:
            best = (observed, until)
    return best[1] if best else None


def deferred_until(away_until: date) -> date:
    """The first day a deferred nudge may fire again: the day after the return date."""
    return away_until + timedelta(days=1)


async def away_until_by_entity(
    pool: asyncpg.Pool, entity_ids: Iterable[uuid.UUID], today: date
) -> dict[uuid.UUID, date]:
    """Map each entity that is away on ``today`` to its current away-until date.

    Reads the active ``away-until`` facts straight from ``relationship.entity_facts``. A read
    failure propagates: a job that cannot tell who is away must not nudge them.
    """
    ids = list(entity_ids)
    if not ids:
        return {}
    rows = await pool.fetch(
        """
        SELECT subject, object, observed_at
        FROM relationship.entity_facts
        WHERE subject = ANY($1::uuid[])
          AND predicate = $2
          AND object_kind = 'literal'
          AND validity = 'active'
        """,
        ids,
        AWAY_UNTIL_PREDICATE,
    )
    grouped: dict[uuid.UUID, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["subject"], []).append(row)
    away: dict[uuid.UUID, date] = {}
    for entity_id, facts in grouped.items():
        until = current_away_until(facts, today)
        if until is not None:
            away[entity_id] = until
    return away
