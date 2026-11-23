"""Prepared retention grants for raw OwnTracks fixes (bu-s11n0s.7, slices 4a and 4b).

Raw expiry has two committed stages. Chronicler first prepares a grant: the exact raw rows it
has finished projecting, frozen under the current policy (tables ``chronicler_036``). A later
slice lets the connector delete exactly those rows under its own role.

Preparation is irreversible for Chronicler's own copies. In the grant's transaction it deletes
the granted rows' location point events (each leaves a tombstone and the run a receipt, via
:func:`~butlers.chronicler.location_forgetting.record_forgetting`, so the resurrection floor
refuses them forever), snaps every coordinate in the place, movement and away summaries those
rows fed to its ~150 m cell, and coarsens trail anchors resting on them in carryover. A
summary it cannot coarsen completely fails the whole run. Cached day-close prose that cites a
granted row or a summary it fed, or that cites nothing yet covers a granted fix, is deleted to
regenerate from coarsened data; a cached row over a granted fix whose provenance cannot be read
fails the whole run.

Only the location retention job calls :func:`prepare_retention_grant`,
:func:`append_grant_state` and :func:`record_holder_receipt`
(``tests/chronicler/test_location_retention_dark_guard.py``), and preparation refuses with
:class:`~butlers.chronicler.location_retention.EnforcementOff`, writing nothing, unless the
owner's switch (``location_retention_policy.enforced``, bu-s11n0s.75) is on under the policy
lock.

A raw row is granted only when all of these hold, checked in one statement:

* every covered adapter (:data:`~butlers.chronicler.projection_coverage.COVERED_ADAPTERS`) holds
  a coverage record for it at that adapter's **current** config revision and for the row's
  **current** content digest. A record under another revision (including the ``legacy``
  records chronicler_034 left) or for other content does not count;
* no adapter holds it in an open carry (``location_projection_open_members``);
* its immutable ``retention_at`` is strictly older than the cutoff (``now() - days``, the
  transaction's clock), and its arrival has settled (``recorded_at`` at least
  :data:`~butlers.chronicler.projection_coverage.DISCOVERY_SETTLE` old);
* no earlier grant names it (preparation is irreversible, so a row is granted at most once).

The caller passes the adapters' current config revisions, computed by constructing the
adapters from their configuration (each adapter's ``coverage_revision``). Under the locks they
must equal each adapter's last recorded run (``location_projection_attempts.config_revision``);
otherwise :class:`StaleRevisions` is raised and nothing is written. Preparation holds the
policy row (``FOR SHARE``) and every adapter's projection lock in source-name order for the
whole transaction, so no projection run, coverage write or policy change interleaves with it.

Each run writes its items, then its grant row, in that transaction: the grant row is the run's
receipt, written even when it grants nothing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.adapters.owntracks_trail import (
    TRAIL_ANCHORS_CARRYOVER_KEY,
    coarsen_trail_anchors,
)
from butlers.chronicler.location_forgetting import (
    OWNTRACKS_SOURCE_REF_PREFIX,
    ForgottenPointEvent,
    record_forgetting,
)
from butlers.chronicler.location_retention import EnforcementOff, PolicyUnavailable
from butlers.chronicler.projection_coverage import (
    COVERED_ADAPTERS,
    DISCOVERY_SETTLE,
    is_digest,
    raw_row_digest_sql,
)
from butlers.chronicler.spatial_precision import coarsen_location_payload
from butlers.chronicler.storage import owntracks_projection_lock_key

__all__ = [
    "GRANT_LIMIT",
    "GRANT_STATES",
    "GrantStateRefused",
    "HoldersIncomplete",
    "REQUIRED_HOLDERS",
    "PreparedGrant",
    "StaleRevisions",
    "append_grant_state",
    "record_holder_receipt",
    "check_grant_transition",
    "prepare_retention_grant",
]

#: Rows one preparation run grants at most; the rest wait for the next run.
GRANT_LIMIT = 500

_LOCK_POLICY = (
    "SELECT days, version, enforced FROM location_retention_policy WHERE id = 1 FOR SHARE"
)
_LOCK_ADAPTER = "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))"
_CUTOFF = "SELECT now() - make_interval(days => $1)"
_ATTEMPT_REVISIONS = (
    "SELECT adapter_name, config_revision FROM location_projection_attempts "
    "WHERE adapter_name = ANY($1::text[])"
)


class StaleRevisions(ValueError):
    """The caller's revisions are not the ones every adapter last ran with: nothing is granted."""


def _eligible_sql() -> str:
    return f"""
WITH current_revision AS (
    SELECT * FROM unnest($1::text[], $2::text[]) AS r(adapter_name, config_revision)
)
SELECT p.id, p.idempotency_key, p.retention_at, d.content_digest
FROM connectors.owntracks_points AS p
CROSS JOIN LATERAL (SELECT {raw_row_digest_sql("p")} AS content_digest) AS d
WHERE p.retention_at < $3
  AND p.recorded_at <= now() - $4::interval
  AND NOT EXISTS (
      SELECT 1 FROM location_retention_grant_items AS g WHERE g.raw_source_id = p.id
  )
  AND NOT EXISTS (
      SELECT 1 FROM location_projection_open_members AS m WHERE m.raw_source_id = p.id
  )
  AND NOT EXISTS (
      SELECT 1 FROM current_revision AS r
      WHERE NOT EXISTS (
          SELECT 1 FROM location_projection_coverage AS c
          WHERE c.adapter_name = r.adapter_name
            AND c.raw_source_id = p.id
            AND c.config_revision = r.config_revision
            AND c.content_digest = d.content_digest
      )
  )
ORDER BY p.retention_at, p.id
LIMIT $5
"""


_INSERT_ITEM = (
    "INSERT INTO location_retention_grant_items "
    "(grant_id, raw_source_id, logical_source_key, retention_at, content_digest) "
    "VALUES ($1, $2, $3, $4, $5)"
)
_INSERT_GRANT = (
    "INSERT INTO location_retention_grants (id, policy_version, retention_days, cutoff, "
    "config_revisions, item_count, capped) VALUES ($1, $2, $3, $4, ($5::text)::jsonb, $6, $7)"
)


@dataclass(frozen=True)
class PreparedGrant:
    """What one preparation run committed."""

    id: UUID
    policy_version: int
    retention_days: int
    cutoff: datetime
    item_count: int
    capped: bool
    point_events_forgotten: int = 0
    episodes_coarsened: int = 0
    anchors_coarsened: int = 0
    cache_rows_invalidated: int = 0


# ── Forgetting the granted rows' Chronicler copies (S4b-2) ────────────────────

#: Adapters whose summaries carry coordinates. SSID presence stores none.
_COORDINATE_ADAPTERS = ("owntracks.points", "owntracks.place_cluster")
_POINT_SOURCE = "owntracks.points"

_GRANTED_EPISODES = """
WITH current_revision AS (
    SELECT * FROM unnest($2::text[], $3::text[]) AS r(adapter_name, config_revision)
)
SELECT DISTINCT unnest(c.output_episode_ids) AS episode_id
FROM location_projection_coverage AS c
JOIN current_revision AS r USING (adapter_name, config_revision)
WHERE c.raw_source_id = ANY($1::uuid[]) AND c.adapter_name = ANY($4::text[])
"""
_GRANTED_POINT_EVENTS = """
SELECT pe.id, pe.source_ref,
       COALESCE(array_agg(l.episode_id ORDER BY l.episode_id)
                FILTER (WHERE l.episode_id IS NOT NULL), '{}') AS episode_ids
FROM point_events AS pe
LEFT JOIN episode_event_links AS l ON l.event_id = pe.id
WHERE pe.source_name = $2 AND pe.source_ref = ANY($1::text[])
GROUP BY pe.id, pe.source_ref
ORDER BY pe.source_ref
"""
_COORDINATE_EPISODES = (
    "SELECT id, payload FROM episodes WHERE id = ANY($1::uuid[]) "
    "AND source_name = ANY($2::text[]) ORDER BY id"
)
_DELETE_POINT_EVENTS = "DELETE FROM point_events WHERE id = ANY($1::uuid[])"
_CARRYOVER = (
    "SELECT carryover FROM projection_checkpoints WHERE source_name = $1 AND subsource = ''"
)
_UPDATE_ANCHORS = (
    "UPDATE projection_checkpoints "
    "SET carryover = jsonb_set(carryover, ARRAY[$2::text], ($3::text)::jsonb), "
    "updated_at = now() WHERE source_name = $1 AND subsource = ''"
)


_EPISODE_REFS = "SELECT source_ref FROM episodes WHERE id = ANY($1::uuid[])"
#: Every cached prose (superseded or not) that cites a granted ref, or whose window holds a
#: granted fix: those are the rows whose provenance must be read.
_CACHE_CANDIDATES = """
SELECT c.cache_key, c.provenance_refs::text AS refs,
       EXISTS (
           SELECT 1 FROM unnest($2::timestamptz[]) AS t(at)
           WHERE t.at >= c.start_at AND t.at <= c.end_at
       ) AS holds_fix
FROM tier2_cache AS c
WHERE (jsonb_typeof(c.provenance_refs) = 'array' AND c.provenance_refs ?| $1::text[])
   OR EXISTS (
       SELECT 1 FROM unnest($2::timestamptz[]) AS t(at)
       WHERE t.at >= c.start_at AND t.at <= c.end_at
   )
ORDER BY c.cache_key
"""
_DELETE_CACHE = "DELETE FROM tier2_cache WHERE cache_key = ANY($1::text[])"


async def _invalidate_cached_prose(conn: Any, refs: set[str], fixes_at: list[datetime]) -> int:
    """Delete the day-close prose built on granted rows, so it regenerates from coarsened data.

    Prose and its provenance cannot be coarsened, so a cached row that cites a granted point
    event or a summary it fed, or that cites nothing yet covers a granted fix's time, is deleted
    (``superseded_at`` would keep the prose). A row covering a granted fix whose provenance is
    not a list of refs raises ``ValueError``: its overlap cannot be determined, so the whole
    preparation rolls back.
    """
    stale: list[str] = []
    for row in await conn.fetch(_CACHE_CANDIDATES, sorted(refs), fixes_at):
        cited = json.loads(row["refs"])
        readable = isinstance(cited, list) and all(isinstance(ref, str) for ref in cited)
        strings = {ref for ref in cited if isinstance(ref, str)} if isinstance(cited, list) else ()
        if refs.intersection(strings):
            stale.append(row["cache_key"])
        elif row["holds_fix"] and not readable:
            raise ValueError("a cached day-close prose has unreadable provenance")
        elif row["holds_fix"] and not cited:
            stale.append(row["cache_key"])
    if stale:
        await conn.execute(_DELETE_CACHE, stale)
    return len(stale)


def _as_object(value: Any) -> dict[str, Any]:
    loaded = json.loads(value) if isinstance(value, str) else value
    return dict(loaded) if isinstance(loaded, Mapping) else {}


async def _coarsen_episodes(conn: Any, episode_ids: set[UUID]) -> int:
    """Snap every coordinate in the granted rows' location summaries; count those changed.

    Raises ``ValueError`` (rolling the whole preparation back) on a payload it cannot coarsen
    completely: an exact coordinate never survives silently.
    """
    changed = 0
    rows = await conn.fetch(_COORDINATE_EPISODES, sorted(episode_ids), list(_COORDINATE_ADAPTERS))
    for row in rows:
        payload = _as_object(row["payload"])
        coarse = coarsen_location_payload(payload)
        if coarse != payload:
            await conn.execute(
                "UPDATE episodes SET payload = ($2::text)::jsonb, updated_at = now() WHERE id = $1",
                row["id"],
                json.dumps(coarse, sort_keys=True),
            )
            changed += 1
    return changed


async def _coarsen_anchors(conn: Any, granted_ids: set[str], cutoff: datetime) -> int:
    """Requirement 3: trail anchors on expiring fixes leave carryover coarsened."""
    changed = 0
    for source in _COORDINATE_ADAPTERS:
        carryover = _as_object(await conn.fetchval(_CARRYOVER, source))
        coarse, count = coarsen_trail_anchors(carryover, granted_ids=granted_ids, cutoff=cutoff)
        if count:
            await conn.execute(
                _UPDATE_ANCHORS,
                source,
                TRAIL_ANCHORS_CARRYOVER_KEY,
                json.dumps(coarse[TRAIL_ANCHORS_CARRYOVER_KEY], sort_keys=True),
            )
            changed += count
    return changed


async def _forget_granted(
    conn: Any,
    granted: list[Any],
    *,
    revisions: dict[str, str],
    policy_version: int,
    cutoff: datetime,
) -> tuple[int, int, int]:
    """Delete the granted rows' point events (tombstoned), coarsen their summaries and delete
    the cached prose built on them.

    Returns ``(point_events_forgotten, episodes_coarsened, cache_rows_invalidated)``. Runs
    inside the grant transaction, under its locks: a failure rolls the whole preparation back.
    """
    raw_ids = [row["id"] for row in granted]
    refs = [OWNTRACKS_SOURCE_REF_PREFIX + row["idempotency_key"] for row in granted]
    episode_ids = {
        row["episode_id"]
        for row in await conn.fetch(
            _GRANTED_EPISODES,
            raw_ids,
            list(revisions),
            list(revisions.values()),
            list(_COORDINATE_ADAPTERS),
        )
    }
    events = await conn.fetch(_GRANTED_POINT_EVENTS, refs, _POINT_SOURCE)
    forgotten = [
        ForgottenPointEvent(
            point_event_id=event["id"],
            source_ref=event["source_ref"],
            episode_ids=tuple(event["episode_ids"]),
        )
        for event in events
    ]
    for event in forgotten:
        episode_ids.update(event.episode_ids)
    cited = set(refs)
    cited.update(row["source_ref"] for row in await conn.fetch(_EPISODE_REFS, sorted(episode_ids)))
    invalidated = await _invalidate_cached_prose(
        conn, cited, sorted(row["retention_at"] for row in granted)
    )
    coarsened = await _coarsen_episodes(conn, episode_ids)
    if forgotten:
        await conn.execute(_DELETE_POINT_EVENTS, [event.point_event_id for event in forgotten])
    receipt = await record_forgetting(
        conn,
        policy_version=policy_version,
        cutoff=cutoff,
        episodes_coarsened=coarsened,
        forgotten=forgotten,
    )
    return receipt.point_events_forgotten, coarsened, invalidated


# ── Later grant states (chronicler_037) ───────────────────────────────────────

#: States a grant reaches after ``prepared``; each is a new row, never an update.
GRANT_STATES = ("ready", "unknown", "reconciled", "failed")
_TERMINAL_STATES = frozenset({"reconciled", "failed"})

_LOCK_GRANT = "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))"
_GRANT_EXISTS = "SELECT EXISTS (SELECT 1 FROM location_retention_grants WHERE id = $1)"
_GRANT_HISTORY = "SELECT state FROM location_retention_grant_states WHERE grant_id = $1 ORDER BY id"
_INSERT_STATE = (
    "INSERT INTO location_retention_grant_states (grant_id, state, receipt) "
    "VALUES ($1, $2, ($3::text)::jsonb)"
)


class GrantStateRefused(ValueError):
    """The state is not a legal next state for this grant: nothing is written."""


class HoldersIncomplete(GrantStateRefused):
    """A grant becomes ready only with every holder's complete receipt (slice S4c)."""


# ── Holder receipts (chronicler_039) ──────────────────────────────────────────

#: Every holder of copies derived from granted raw rows, outside Chronicler's own tables (which
#: preparation already forgot). Switchboard's inbox keeps each envelope's normalized text, which
#: carries exact coordinates; its receipt is ``blocked`` while a copy is still processing or was
#: routed onward. The connector's own ``filtered_events`` copies are withheld in the deletion
#: transaction itself (S4c-4), so they are not a holder here.
REQUIRED_HOLDERS = ("switchboard.message_inbox",)
HOLDER_OUTCOMES = ("complete", "blocked")

_HOLDER_COMPLETE = (
    "SELECT holder FROM location_retention_holder_receipts "
    "WHERE grant_id = $1 AND outcome = 'complete'"
)
_INSERT_HOLDER_RECEIPT = (
    "INSERT INTO location_retention_holder_receipts (grant_id, holder, outcome, receipt) "
    "VALUES ($1, $2, $3, ($4::text)::jsonb)"
)


async def record_holder_receipt(
    conn: Any,
    grant_id: UUID,
    holder: str,
    *,
    outcome: str,
    receipt: Mapping[str, Any] | None = None,
) -> None:
    """Append what Chronicler read back from ``holder`` for a grant not yet ready.

    ``complete`` at most once per grant and holder, and nothing after it; ``blocked`` may repeat
    until then and never counts. Raises ``ValueError`` for an unknown holder or outcome or a
    receipt that is not count-only, and :class:`GrantStateRefused` for a missing or
    already-ready grant or any receipt from a holder that already completed, writing nothing.
    """
    if holder not in REQUIRED_HOLDERS:
        raise ValueError(f"not a retention holder: {holder!r}")
    if outcome not in HOLDER_OUTCOMES:
        raise ValueError(f"not a holder outcome: {outcome!r}")
    checked = _checked_receipt(receipt)
    async with conn.transaction():
        await conn.execute(_LOCK_GRANT, f"chronicler.retention_grant:{grant_id}")
        if not await conn.fetchval(_GRANT_EXISTS, grant_id):
            raise GrantStateRefused("no such grant")
        if [row["state"] for row in await conn.fetch(_GRANT_HISTORY, grant_id)]:
            raise GrantStateRefused("the grant is already ready")
        complete = {row["holder"] for row in await conn.fetch(_HOLDER_COMPLETE, grant_id)}
        # Any later reading from a completed holder is refused, so ready never rests on a
        # complete that a newer blocked reading contradicts.
        if holder in complete:
            raise GrantStateRefused(f"{holder} already completed this grant")
        await conn.execute(
            _INSERT_HOLDER_RECEIPT,
            grant_id,
            holder,
            outcome,
            json.dumps(checked, sort_keys=True),
        )


def check_grant_transition(history: Sequence[str], state: str) -> None:
    """Refuse ``state`` unless it legally follows ``history`` (oldest first).

    ``ready`` at most once. ``unknown``, ``reconciled`` and ``failed`` only after ``ready``;
    ``reconciled`` and ``failed`` exclude each other, and nothing follows either.
    """
    if state not in GRANT_STATES:
        raise GrantStateRefused(f"not a grant state: {state!r}")
    if state == "ready":
        if "ready" in history:
            raise GrantStateRefused("the grant is already ready")
        return
    if "ready" not in history:
        raise GrantStateRefused(f"{state} needs a ready grant")
    if _TERMINAL_STATES.intersection(history):
        raise GrantStateRefused("the grant is already reconciled or failed")


def _checked_receipt(receipt: Mapping[str, Any] | None) -> dict[str, int]:
    if receipt is None:
        return {}
    if not isinstance(receipt, Mapping) or not all(
        isinstance(key, str)
        and not isinstance(value, bool)
        and isinstance(value, int)
        and value >= 0
        for key, value in receipt.items()
    ):
        raise ValueError("receipt must map names to non-negative counts")
    return dict(receipt)


async def append_grant_state(
    conn: Any,
    grant_id: UUID,
    state: str,
    *,
    receipt: Mapping[str, Any] | None = None,
) -> None:
    """Append ``state`` to the grant's history, under a per-grant lock, if it may follow.

    ``ready`` is refused (:class:`HoldersIncomplete`) unless every holder in
    :data:`REQUIRED_HOLDERS` has a complete receipt for this grant, read under the same lock.
    Raises :class:`GrantStateRefused` for a missing grant or an illegal transition, writing
    nothing.
    """
    checked = _checked_receipt(receipt)
    if state not in GRANT_STATES:
        raise GrantStateRefused(f"not a grant state: {state!r}")
    async with conn.transaction():
        await conn.execute(_LOCK_GRANT, f"chronicler.retention_grant:{grant_id}")
        if not await conn.fetchval(_GRANT_EXISTS, grant_id):
            raise GrantStateRefused("no such grant")
        history = [row["state"] for row in await conn.fetch(_GRANT_HISTORY, grant_id)]
        check_grant_transition(history, state)
        if state == "ready":
            complete = {row["holder"] for row in await conn.fetch(_HOLDER_COMPLETE, grant_id)}
            if missing := sorted(set(REQUIRED_HOLDERS) - complete):
                raise HoldersIncomplete(f"no complete receipt from {', '.join(missing)}")
        await conn.execute(_INSERT_STATE, grant_id, state, json.dumps(checked, sort_keys=True))


def _checked_revisions(config_revisions: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(config_revisions, Mapping) or set(config_revisions) != set(COVERED_ADAPTERS):
        raise ValueError(f"config_revisions must name exactly {', '.join(COVERED_ADAPTERS)}")
    if not all(is_digest(value) for value in config_revisions.values()):
        raise ValueError("config_revisions must be current config_revision() digests")
    return {adapter: config_revisions[adapter] for adapter in sorted(COVERED_ADAPTERS)}


async def prepare_retention_grant(
    conn: Any,
    *,
    config_revisions: Mapping[str, str],
    limit: int = GRANT_LIMIT,
    settle: timedelta = DISCOVERY_SETTLE,
) -> PreparedGrant:
    """Grant the expired raw rows every covered adapter has finished with, at most ``limit``.

    ``conn`` is a Chronicler connection (not a pool): the run is one transaction. Raises
    ``ValueError`` before touching the database for incomplete or malformed revisions or a bad
    limit, :class:`PolicyUnavailable` (writing nothing) when the policy row is missing, and
    :class:`EnforcementOff` (writing nothing) while the owner has raw expiry off.
    """
    revisions = _checked_revisions(config_revisions)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")

    async with conn.transaction():
        policy = await conn.fetchrow(_LOCK_POLICY)
        if policy is None:
            raise PolicyUnavailable("location retention policy row is missing")
        if not policy["enforced"]:
            raise EnforcementOff("the owner has raw location expiry off")
        for adapter in revisions:  # source-name order
            await conn.execute(_LOCK_ADAPTER, owntracks_projection_lock_key(adapter))
        # Under the locks, each adapter's last recorded run must be at the revision the caller
        # computed; otherwise the caller's configuration is not the one the coverage reflects.
        recorded = {
            row["adapter_name"]: row["config_revision"]
            for row in await conn.fetch(_ATTEMPT_REVISIONS, list(revisions))
        }
        if recorded != revisions:
            raise StaleRevisions("config_revisions differ from the adapters' last recorded runs")
        cutoff = await conn.fetchval(_CUTOFF, policy["days"])
        rows = await conn.fetch(
            _eligible_sql(),
            list(revisions),
            list(revisions.values()),
            cutoff,
            settle,
            limit + 1,
        )
        capped = len(rows) > limit
        granted = rows[:limit]
        forgotten = coarsened = invalidated = 0
        if granted:
            forgotten, coarsened, invalidated = await _forget_granted(
                conn,
                granted,
                revisions=revisions,
                policy_version=policy["version"],
                cutoff=cutoff,
            )
        anchors = await _coarsen_anchors(conn, {str(row["id"]) for row in granted}, cutoff)
        grant_id = uuid4()
        if granted:
            await conn.executemany(
                _INSERT_ITEM,
                [
                    (
                        grant_id,
                        row["id"],
                        row["idempotency_key"],
                        row["retention_at"],
                        row["content_digest"],
                    )
                    for row in granted
                ],
            )
        await conn.execute(
            _INSERT_GRANT,
            grant_id,
            policy["version"],
            policy["days"],
            cutoff,
            json.dumps(revisions, sort_keys=True),
            len(granted),
            capped,
        )
    return PreparedGrant(
        id=grant_id,
        policy_version=policy["version"],
        retention_days=policy["days"],
        cutoff=cutoff,
        item_count=len(granted),
        capped=capped,
        point_events_forgotten=forgotten,
        episodes_coarsened=coarsened,
        anchors_coarsened=anchors,
        cache_rows_invalidated=invalidated,
    )
