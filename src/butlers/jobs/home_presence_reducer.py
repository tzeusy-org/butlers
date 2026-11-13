"""Home adapter for the owner.place presence reducer (RFC 0038 S3.2, bu-jv1moa.17).

Reads typed, coordinate-free observations, runs :func:`butlers.perception.presence.reduce`
and publishes the resulting decisions through ``PerceptionStore.write`` as source
``home.dots``. The reducer state is carried in memory between runs, like the capability
health publisher: a restart forgets it, so the first confirmed side after a restart is an
explicit ``establish`` (preceded by an ``unknown`` reset), never a fabricated leave/enter.

The release is dark. Nothing schedules this runner and no ``home.dots`` login exists; the
writer role, its profile (finite freshness, owner subject, the evidence references below)
and the schedule belong to the owner-authorized activation. The profile must register
each home place id as a ``place`` reference and the evidence references below.
``HEARTBEAT`` is 5 minutes, so the activated freshness maximum must comfortably exceed it.

Inputs: Home Assistant owner entities from ``ha_entity_snapshot`` (Home's own table,
gated by ``ha_source_health``); only ``home`` and ``not_home`` states qualify. OwnTracks
observations arrive as the same :class:`Observation` type from the connector-owned typed
feed (a later slice); this module never reads ``connectors.*`` or raw coordinates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid5

import asyncpg

from butlers.jobs.context_producers import _load_owner_presence_entity_ids
from butlers.jobs.home import HASourceUnmeasurableError, _require_ha_source_healthy
from butlers.owner_places import OwnerPlace, load_active_owner_places
from butlers.perception.model import EventRequest
from butlers.perception.presence import (
    OBSERVATION_FRESH,
    SOURCE,
    Decision,
    Observation,
    PresenceState,
    Side,
    observation_key,
    reduce,
)

logger = logging.getLogger(__name__)

EVIDENCE_NAMESPACE = UUID("4d022d11-1f3c-5ff3-b3e4-16dce5df46c4")
EVIDENCE_SOURCES = ("home_assistant", "owntracks")
#: A confirmed state is asserted for this long; the profile freshness maximum bounds it further.
ASSERTION_SPAN = timedelta(hours=24)
_LABELS = {"version": 1, "label_instr": ["world"], "label_data": ["world"], "label_sens": []}

Outcome = Literal[
    "ok", "unconfigured", "unmeasurable", "no_owner", "no_home", "refused", "unavailable"
]


#: Home Assistant presence states that qualify, mapped to a side (zone names do not).
_HA_SIDES: dict[str, Side] = {"home": "inside", "not_home": "outside"}
#: ``OwnerPlace.kind`` of the owner's home place (a place kind, not a butler name).
_HOME_KIND = "home"


def evidence_ref(label: str) -> UUID:
    """The registered evidence reference for one evidence source label."""
    return uuid5(EVIDENCE_NAMESPACE, f"evidence:{label}")


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def ha_observations(
    rows: list[Any],
    *,
    owner_entity_ids: frozenset[str],
    place_ref: UUID,
    now: datetime,
) -> list[Observation]:
    """Qualifying HA observations: a fresh owner entity reading ``home`` or ``not_home``.

    Other states (zone names, unavailable, unknown) are not a qualifying inside or outside
    signal. Housemates' entities never contribute.
    """
    found: list[Observation] = []
    for row in rows:
        entity_id = row["entity_id"] or ""
        if entity_id not in owner_entity_ids:
            continue
        stamp = row["last_updated"]
        if stamp is None:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        if stamp > now or now - stamp > OBSERVATION_FRESH:
            continue
        state = (row["state"] or "").strip().lower()
        side = _HA_SIDES.get(state)
        if side is None:
            continue
        key = observation_key("ha", entity_id, stamp.isoformat(), state)
        found.append(Observation(stamp, side, place_ref, "home_assistant", key))
    return found


_FEED = "SELECT * FROM public.owner_presence_observations($1, $2, $3)"
_FEED_LIMIT = 500


async def owntracks_observations(pool: Any, *, place_ref: UUID, now: datetime) -> list[Observation]:
    """Typed OwnTracks observations from the connector-side feed, for one place.

    The feed classifies fixes against the owner's live places inside the database and returns
    no coordinate, SSID or payload. A missing or ungranted feed degrades to no observations
    (Home Assistant carries presence alone); it never raises into the reducer run.
    """
    try:
        rows = await pool.fetch(_FEED, now - OBSERVATION_FRESH, now, _FEED_LIMIT)
    except (asyncpg.UndefinedFunctionError, asyncpg.InsufficientPrivilegeError):
        return []
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError):
        logger.warning("presence reducer: OwnTracks feed unavailable")
        return []
    found: list[Observation] = []
    for row in rows:
        if row["place_ref"] != place_ref or row["side"] not in ("inside", "outside"):
            continue
        stamp = row["observed_at"]
        key = observation_key("ot", stamp.isoformat(), str(place_ref), row["side"])
        found.append(
            Observation(
                stamp, row["side"], place_ref, "owntracks", key, ssid_match=bool(row["ssid_match"])
            )
        )
    return found


def _only_place(places: tuple[OwnerPlace, ...], kind: str) -> UUID | None:
    matches = [place.id for place in places if place.kind == kind]
    return matches[0] if len(matches) == 1 else None


def home_place(places: tuple[OwnerPlace, ...]) -> UUID | None:
    """The one active place of kind ``home``; zero or several is unconfigured."""
    return _only_place(places, _HOME_KIND)


def _request(
    decision: Decision, *, subject: UUID, generation: int, reset: bool = False
) -> EventRequest:
    observed = decision.observed_at
    known = not reset
    return EventRequest(
        {
            "version": 1,
            "kind": "owner.place",
            "subject": str(subject),
            "source": SOURCE,
            "event_key": decision.reset_event_key if reset else decision.event_key,
            "expected_generation": generation,
            "state": "known" if known else "unknown",
            "observed_at": _iso(observed),
            "effective_span": {
                "start": _iso(decision.at),
                "end": _iso(decision.at + ASSERTION_SPAN),
            },
            "typed_value_or_ref": (
                {
                    "presence": decision.side,
                    "place_ref": str(decision.place_ref),
                    "locality_ref": None,
                }
                if known
                else None
            ),
            "evidence_refs": [
                {
                    "ref": str(evidence_ref(label)),
                    "source": label,
                    "expires_at": _iso(observed + OBSERVATION_FRESH),
                }
                for label in decision.evidence
            ],
            "labels": _LABELS,
            "at": _iso(decision.at),
            "confirmed_at": _iso(decision.confirmed_at),
            "retracts_event_id": (
                str(decision.retracts) if decision.retracts is not None and known else None
            ),
        }
    )


@dataclass
class PresenceRuntime:
    """In-memory carry between runs: reducer state and the last known slot generation."""

    state: PresenceState = field(default_factory=PresenceState)
    generation: int = 0


class _Refused(Exception):
    pass


async def _write(store: Any, runtime: PresenceRuntime, build: Any) -> None:
    """One compare-and-swap write; a stale generation is retried once with the live one."""
    for attempt in range(2):
        try:
            outcome = await store.write(build(runtime.generation))
        except asyncpg.PostgresError as exc:
            if "changed perception replay" in str(exc):
                # Same event, different confirmation instant: another run already recorded it.
                runtime.generation = 0
                return
            logger.warning(
                "presence reducer: write failed sqlstate=%s", getattr(exc, "sqlstate", None)
            )
            raise _Refused from exc
        status = outcome.get("status") if isinstance(outcome, dict) else None
        if status in ("committed", "historical"):
            runtime.generation = int(outcome["generation"])
            return
        current = outcome.get("generation") if isinstance(outcome, dict) else None
        if status != "conflict" or type(current) is not int or attempt == 1:
            raise _Refused
        runtime.generation = current


async def publish(store: Any, runtime: PresenceRuntime, decision: Decision, subject: UUID) -> None:
    if decision.reset_first:
        await _write(
            store,
            runtime,
            lambda g: _request(decision, subject=subject, generation=g, reset=True),
        )
    await _write(store, runtime, lambda g: _request(decision, subject=subject, generation=g))


async def _owner_subject(pool: Any) -> UUID | None:
    rows = await pool.fetch("SELECT id FROM public.entities WHERE 'owner' = ANY(roles)")
    return rows[0]["id"] if len(rows) == 1 else None


def _silent(runtime: PresenceRuntime, outcome: Outcome) -> dict[str, Any]:
    """Input trouble: forget the carried side so a later confirmation cannot be an edge from it."""
    runtime.state = PresenceState(cursor=runtime.state.cursor, seen=runtime.state.seen)
    return {"outcome": outcome}


async def run_presence_reducer(
    pool: Any,
    store: Any,
    runtime: PresenceRuntime,
    *,
    now: datetime | None = None,
    extra_observations: list[Observation] | None = None,
) -> dict[str, Any]:
    """One reducer run. Input trouble leaves the reducer silent (unknown), never an edge.

    ``pool`` is Home's read pool; ``store`` is a ``PerceptionStore`` already authenticated as
    the ``home.dots`` writer. The returned dict holds counts and an outcome only.
    """
    now = now or datetime.now(UTC)
    try:
        await _require_ha_source_healthy(pool)
    except HASourceUnmeasurableError:
        return _silent(runtime, "unmeasurable")
    owner_entity_ids = await _load_owner_presence_entity_ids(pool)
    if owner_entity_ids is None:
        return _silent(runtime, "unconfigured")
    place_ref = home_place(await load_active_owner_places(pool))
    if place_ref is None:
        return _silent(runtime, "no_home")
    subject = await _owner_subject(pool)
    if subject is None:
        return _silent(runtime, "no_owner")
    rows = await pool.fetch(
        "SELECT entity_id, state, last_updated FROM ha_entity_snapshot WHERE entity_id = ANY($1)",
        list(owner_entity_ids),
    )
    observations = ha_observations(
        rows, owner_entity_ids=owner_entity_ids, place_ref=place_ref, now=now
    ) + await owntracks_observations(pool, place_ref=place_ref, now=now)
    observations += list(extra_observations or [])
    next_state, decisions = reduce(runtime.state, observations, now, place_ref=place_ref)
    published = 0
    try:
        for decision in decisions:
            await publish(store, runtime, decision, subject)
            published += 1
    except _Refused:
        logger.warning("presence reducer: publish refused; state not advanced")
        return {"outcome": "refused", "published": published}
    except (OSError, TimeoutError, asyncpg.PostgresConnectionError):
        return {"outcome": "unavailable", "published": published}
    runtime.state = next_state
    return {"outcome": "ok", "published": published}
