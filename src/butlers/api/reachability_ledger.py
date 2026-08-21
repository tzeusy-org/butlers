"""Durable outage-episode ledger for butler reachability (bu-6jv4m.3).

``GET /api/issues`` learns reachability from a live MCP ping, which is a
*probe*, not a condition.  Projecting the probe's clock straight onto the issue
made every poll look like a brand-new occurrence, so the
acknowledge-until-recurrence contract (core_152) could never hold for an
unreachable butler: the ack watermark was outrun the instant it was written.

This module gives the condition an identity that survives the probe.  One
uninterrupted outage is one row in ``public.butler_reachability_conditions``
with a stable ``started_at``; recovery closes it; a later down transition opens
a genuinely new row.  The Issues router reports that ``started_at`` as the
issue's *recurrence epoch* and keeps ``last_seen_at`` honest (when we last
probed), instead of bending one field to serve both jobs.

Epoch vocabulary
----------------
onset
    ``started_at`` of the open episode. Stable across polls; the value an
    acknowledgement is held against.
observation
    One failed probe. ``last_seen_at``/``observations`` advance per poll and
    are descriptive only -- nothing compares them to an ack.
recovery
    The butler answers again. ``resolved_at`` is stamped and the episode is
    closed for good; it is never reopened.
recurrence
    A down transition with no open episode. Opens a NEW row whose onset is
    strictly later than any prior ack watermark, so the condition correctly
    reappears in the active feed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

_TABLE = "public.butler_reachability_conditions"

#: Close every open condition for butlers that answered this poll. Recovery is
#: terminal: a resolved row is never revived, so the next outage is forced to
#: open a new episode with a new onset (the recurrence signal an ack needs).
RESOLVE_CONDITIONS_SQL = f"""
UPDATE {_TABLE}
   SET resolved_at = now()
 WHERE resolved_at IS NULL
   AND butler = ANY($1::text[])
"""

#: Open-or-extend, atomically, for every butler that failed this poll.
#:
#: The conflict target names the PARTIAL unique index from core_199
#: (``(butler) WHERE resolved_at IS NULL``), so the upsert can only ever
#: collide with an OPEN episode -- a butler's resolved history never blocks a
#: new one. ``started_at`` is deliberately absent from the DO UPDATE list:
#: extending an outage must not move its onset, which is the whole point.
OPEN_OR_EXTEND_CONDITIONS_SQL = f"""
INSERT INTO {_TABLE} AS existing (butler, started_at, last_seen_at, observations, detail)
SELECT probe.butler, now(), now(), 1, probe.detail
  FROM unnest($1::text[], $2::text[]) AS probe(butler, detail)
ON CONFLICT (butler) WHERE resolved_at IS NULL
DO UPDATE SET
    last_seen_at = now(),
    observations = existing.observations + 1,
    detail = EXCLUDED.detail
RETURNING butler, started_at, observations
"""

#: The open episode's onset for one butler, used as the server-derived ack
#: watermark by ``POST /api/issues/dismiss``.
OPEN_CONDITION_ONSET_SQL = f"""
SELECT started_at
  FROM {_TABLE}
 WHERE butler = $1
   AND resolved_at IS NULL
 LIMIT 1
"""


@dataclass(frozen=True)
class ReachabilityEpisode:
    """One butler's currently-open outage episode."""

    butler: str
    #: Episode onset -- stable for the whole outage, and the epoch an
    #: acknowledgement of this condition is held against.
    started_at: datetime
    #: Consecutive failed probes recorded in this episode.
    observations: int


async def record_probe(
    pool,
    *,
    down: dict[str, str],
    recovered: list[str],
) -> dict[str, ReachabilityEpisode]:
    """Apply one reachability probe to the ledger and return the open episodes.

    Args:
        pool: asyncpg pool for the switchboard database.
        down: butler name -> the probe's description of the failure. Each entry
            opens a new episode or extends the butler's existing open one.
        recovered: butlers that answered this poll; any open episode of theirs
            is closed.

    Returns:
        The open episode for every butler in *down*, keyed by butler name.
        Empty when *down* is empty.

    Raises:
        Whatever the pool raises. Callers classify and flag it -- a silently
        swallowed failure here would let the feed claim a durable acknowledgement
        it never established.
    """
    if recovered:
        await pool.execute(RESOLVE_CONDITIONS_SQL, list(recovered))

    if not down:
        return {}

    names = list(down)
    details = [down[name] for name in names]
    rows = await pool.fetch(OPEN_OR_EXTEND_CONDITIONS_SQL, names, details)
    return {
        str(row["butler"]): ReachabilityEpisode(
            butler=str(row["butler"]),
            started_at=row["started_at"],
            observations=int(row["observations"] or 1),
        )
        for row in rows
    }


async def open_condition_onset(pool, butler: str) -> datetime | None:
    """Return the onset of *butler*'s open outage episode, or ``None``.

    ``None`` means the butler currently has no open condition -- it is
    reachable, or has never been observed down.
    """
    rows = await pool.fetch(OPEN_CONDITION_ONSET_SQL, butler)
    return rows[0]["started_at"] if rows else None
