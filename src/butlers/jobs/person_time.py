"""What the encounter ledger says about each person in time (bu-5ii4gj.3 E2b).

A read-only helper for prep, the meeting card and the entity page. "Last met" derives only from
a ledger row the owner or evidence vouches for (``happened_attested`` or
``happened_corroborated``); a meeting nobody vouches for is "last scheduled", never "last met".

The ledger covers a bounded window, so a person with no ledger row at all is not covered: the
caller falls back to the interim rule until the reducer has seen them (``covered`` is False).
A person the ledger does cover with no happened row has ``last_met`` None.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

__all__ = ["PersonTime", "read_person_time"]

#: The chip a reader shows beside "Last met", keyed by the ledger row's basis.
_BASIS_LABEL: Final = {"owner_debrief": "you_confirmed", "chronicler": "evidence"}

_COVERED_SQL: Final = """
    SELECT DISTINCT en.entity_id
    FROM encounters en
    JOIN public.entities pe ON pe.id = en.entity_id
    WHERE en.entity_id = ANY($1::uuid[]) AND COALESCE(pe.posture, 'active') = 'active'
"""

_LAST_SQL: Final = """
    SELECT DISTINCT ON (en.entity_id)
           en.entity_id, en.occurrence_start, en.basis, ce.title AS title
    FROM encounters en
    JOIN public.entities pe ON pe.id = en.entity_id
    LEFT JOIN calendar_events ce ON ce.id = en.event_id
    WHERE en.entity_id = ANY($1::uuid[])
      AND COALESCE(pe.posture, 'active') = 'active'
      AND en.state = ANY($2::text[])
      AND en.occurrence_start < $3
    ORDER BY en.entity_id, en.occurrence_start DESC
"""


@dataclass(frozen=True, slots=True)
class PersonTime:
    covered: bool
    last_met: datetime | None = None
    last_met_event: str | None = None
    last_met_basis: str | None = None
    last_scheduled: datetime | None = None
    last_scheduled_event: str | None = None


async def read_person_time(
    executor: Any, entity_ids: Sequence[uuid.UUID], now: datetime
) -> dict[uuid.UUID, PersonTime]:
    """Per person, last met and last scheduled from the ledger. A failed read propagates.

    A person absent from the result map is treated by callers as not covered. Non-active people
    are never reported.
    """
    ids = list(entity_ids)
    if not ids:
        return {}
    covered = {r["entity_id"] for r in await executor.fetch(_COVERED_SQL, ids)}
    if not covered:
        return {}
    happened = {
        r["entity_id"]: r
        for r in await executor.fetch(
            _LAST_SQL, ids, ["happened_attested", "happened_corroborated"], now
        )
    }
    planned = {
        r["entity_id"]: r
        for r in await executor.fetch(_LAST_SQL, ids, ["scheduled", "unknown"], now)
    }
    out: dict[uuid.UUID, PersonTime] = {}
    for entity_id in covered:
        met, sched = happened.get(entity_id), planned.get(entity_id)
        out[entity_id] = PersonTime(
            covered=True,
            last_met=met["occurrence_start"] if met else None,
            last_met_event=met["title"] if met else None,
            last_met_basis=_BASIS_LABEL.get(met["basis"]) if met else None,
            last_scheduled=sched["occurrence_start"] if sched else None,
            last_scheduled_event=sched["title"] if sched else None,
        )
    return out
