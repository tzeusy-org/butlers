"""Last OwnTracks fix of the owner-bound device, as evidence on the custody case (S5f).

bu-s11n0s.1 S5f, REQ-endpoint-custody-holds-009. The owner binds one OwnTracks endpoint to a
custody episode (``public.fleet_case_evidence``, contributor ``custody_owner``, kind
``device_binding``, ref = the endpoint identity; written by
``PUT /api/custody/episodes/{id}/device``).
This deterministic Chronicler job, which owns the OwnTracks projection (``point_events`` and
``place_cluster`` episodes in its own schema), contributes for each open episode's newest binding
one
``chronicler``/``device_last_fix`` evidence row per distinct last fix: the fix time, how old it was
when observed, how long it counts as current, and the named place when the owner's place registry or
OwnTracks region names it. Raw coordinates, accuracy and the raw payload never leave this module.

Honesty rules, in one place:

- No binding: nothing is contributed, the association stays unknown.
- No retained fix (never received, expired, tombstoned, outlier, or restricted): ``status`` is
  ``unknown`` with reason ``no_fix``.
- A fix older than :data:`FRESH_FOR` when observed: ``status`` is ``unknown`` with reason
  ``stale``; the old time is kept apart as ``stale_fix_at`` and no place is named.
- A fix with no named place: ``status`` is ``known`` with ``place`` null, never a guessed place.

The evidence is attributed context. It never answers the security question or proves custody.
Idempotent: the evidence ref names the endpoint and the exact fix, so a replay and an unchanged
fix change nothing. No LLM session.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.chronicler.adapters.owntracks_place_cluster import (
    EPISODE_TYPE_PLACE,
    PLACE_UNKNOWN_LABEL,
)
from butlers.chronicler.adapters.owntracks_place_cluster import SOURCE_NAME as PLACE_SOURCE
from butlers.chronicler.adapters.owntracks_trail import OUTLIER_PAYLOAD_KEY
from butlers.core.fleet_cases import FleetCaseError, contribute_evidence

logger = logging.getLogger(__name__)

CONTRIBUTOR = "chronicler"
EVIDENCE_KIND = "device_last_fix"
BINDING_CONTRIBUTOR = "custody_owner"
BINDING_KIND = "device_binding"
POINT_SOURCE = "owntracks.points"
# A last fix older than this when observed reads as unknown (stale), not as a current location.
FRESH_FOR = timedelta(hours=6)
_BATCH = 50

_BINDINGS_SQL = """
SELECT DISTINCT ON (c.id) c.id::text AS case_id, b.ref AS endpoint_identity
FROM public.fleet_cases c
JOIN public.fleet_case_evidence b ON b.case_id = c.id
WHERE c.correlation_key LIKE 'endpoint\\_custody:%' AND c.state <> 'closed'
  AND b.contributor = 'custody_owner' AND b.kind = 'device_binding'
ORDER BY c.id, b.contributed_at DESC, b.id DESC
LIMIT $1
"""

# Retained, live, policy-accepted, non-restricted fixes only; the newest by device time.
_LAST_FIX_SQL = f"""
SELECT occurred_at
FROM point_events
WHERE source_name = '{POINT_SOURCE}' AND event_type = 'location'
  AND tombstone_at IS NULL AND privacy <> 'restricted'
  AND payload ->> 'endpoint_identity' = $1
  AND NOT (payload ? '{OUTLIER_PAYLOAD_KEY}')
ORDER BY occurred_at DESC
LIMIT 1
"""

_PLACE_SQL = f"""
SELECT payload ->> 'label' AS label
FROM episodes
WHERE source_name = '{PLACE_SOURCE}' AND episode_type = '{EPISODE_TYPE_PLACE}'
  AND tombstone_at IS NULL AND privacy <> 'restricted'
  AND payload ->> 'endpoint_identity' = $1
  AND payload ->> 'label_source' IN ('owner_reference', 'owntracks_region')
  AND start_at <= $2 AND (end_at IS NULL OR end_at >= $2)
ORDER BY start_at DESC
LIMIT 1
"""


def fix_evidence(
    *, fix_at: datetime | None, place: str | None, observed_at: datetime
) -> dict[str, Any]:
    """The coordinate-free evidence payload for one observation. Pure."""
    base: dict[str, Any] = {"observed_at": observed_at.isoformat(), "evidence_only": True}
    if fix_at is None:
        return base | {"status": "unknown", "reason": "no_fix"}
    age = max(0, int((observed_at - fix_at).total_seconds()))
    base |= {
        "age_seconds_at_observation": age,
        "fresh_until": (fix_at + FRESH_FOR).isoformat(),
    }
    if observed_at - fix_at > FRESH_FOR:
        return base | {"status": "unknown", "reason": "stale", "stale_fix_at": fix_at.isoformat()}
    return base | {"status": "known", "fix_at": fix_at.isoformat(), "place": place}


def evidence_ref(endpoint_identity: str, fix_at: datetime | None) -> str:
    """One row per endpoint and exact fix; ``none`` while there is no retained fix."""
    return f"{endpoint_identity}:{int(fix_at.timestamp()) if fix_at else 'none'}"


async def run_custody_device_fix_evidence(
    db_pool: asyncpg.Pool, *, now: datetime | None = None
) -> dict[str, Any]:
    """Contribute the bound device's last-fix evidence to each open custody episode's case."""
    observed_at = now if now is not None else datetime.now(UTC)
    bindings = await db_pool.fetch(_BINDINGS_SQL, _BATCH)
    result = {"bindings": len(bindings), "contributed": 0, "errors": 0}
    for binding in bindings:
        endpoint = binding["endpoint_identity"]
        try:
            fix_at = await db_pool.fetchval(_LAST_FIX_SQL, endpoint)
            place = None
            if fix_at is not None and observed_at - fix_at <= FRESH_FOR:
                label = await db_pool.fetchval(_PLACE_SQL, endpoint, fix_at)
                place = label if label and label != PLACE_UNKNOWN_LABEL else None
            _row, created = await contribute_evidence(
                db_pool,
                case_id=binding["case_id"],
                contributor=CONTRIBUTOR,
                kind=EVIDENCE_KIND,
                ref=evidence_ref(endpoint, fix_at),
                payload=fix_evidence(fix_at=fix_at, place=place, observed_at=observed_at),
            )
        except (asyncpg.PostgresError, FleetCaseError):
            logger.warning("Custody device-fix evidence failed for one binding")
            result["errors"] += 1
            continue
        result["contributed"] += int(created)
    return result
