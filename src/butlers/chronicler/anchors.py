"""Owner anchor places for deterministic readers (bu-r3fmja, REQ-butler-chronicler-015).

An anchor is a place the owner's day is organised around: home, work, an airport,
or another named place. :func:`resolve_anchors` picks, per kind, the first source
that has one, in this precedence:

1. the owner place registry (``public.owner_places``, edited from Home);
2. the memory graph: the geocoded ``has-address`` of the entity the owner
   ``works-at`` (work), and the owner's own ``has-address`` or the address of the
   place they ``rental-location``/``lives-at`` (home). Relationship geocodes those
   addresses into ``relationship.address_geocodes``; a geocode counts only while
   its ``address_sha256`` matches the fact's current text;
3. legacy owner reference points (``OWNTRACKS_PLACE_REFERENCES``);
4. for home only, the owner's usual overnight place.

Coordinates, address text and SSIDs stay inside this process: no log line,
payload, prompt or MCP response receives them. Every loader degrades to "no anchor
from this source" when its surface is absent or unreadable.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Any

import asyncpg

from butlers.chronicler.adapters.owntracks_place_cluster import PlaceReference
from butlers.chronicler.adapters.owntracks_trail import haversine_meters

logger = logging.getLogger(__name__)

KIND_HOME = "home"
KIND_WORK = "work"
KIND_AIRPORT = "airport"
KIND_OTHER = "other"
ANCHOR_KINDS: tuple[str, ...] = (KIND_HOME, KIND_WORK, KIND_AIRPORT, KIND_OTHER)

SOURCE_REGISTRY = "owner_places"
SOURCE_GRAPH = "graph"
SOURCE_ENV = "env_reference"
SOURCE_INFERRED = "inferred_overnight"

GRAPH_HOME_RADIUS_M = 150
GRAPH_WORK_RADIUS_M = 200
INFERRED_HOME_RADIUS_M = 150
INFERRED_HOME_LOOKBACK_DAYS = 28
INFERRED_HOME_MIN_NIGHTS = 3
INFERRED_HOME_PROBE = time(3, 0)

_WORK_LABELS = frozenset({"work", "office"})
_HOME_LABELS = frozenset({"", "home"})
_RESIDENCE_PREDICATES = ("rental-location", "lives-at")


@dataclass(frozen=True)
class AnchorPlace:
    """One resolved anchor. ``label`` is owner-facing; coordinates are private."""

    kind: str
    label: str
    lat: float
    lon: float
    radius_m: float
    source: str
    ssids: tuple[str, ...] = ()

    def distance_m(self, lat: float, lon: float) -> float:
        return haversine_meters(self.lat, self.lon, lat, lon)

    def contains(self, lat: float, lon: float) -> bool:
        return self.distance_m(lat, lon) <= self.radius_m


@dataclass(frozen=True)
class GraphAddress:
    """An owner anchor address fact joined to its geocode, if any."""

    via: str  # "self", "works-at", "rental-location" or "lives-at"
    label: str | None  # the fact's metadata label, if any
    target_name: str | None  # canonical name of the works-at/residence entity
    address: str
    is_primary: bool
    updated_at: datetime | None
    geocode_sha256: str | None
    geocode_status: str | None
    lat: float | None
    lon: float | None


@dataclass(frozen=True)
class StaySample:
    """A place stay reduced to what overnight-home inference needs."""

    start_at: datetime
    end_at: datetime
    lat: float
    lon: float


def address_sha256(address: str) -> str:
    """The hash Relationship stores beside a geocode (REQ-butler-relationship-006)."""
    return hashlib.sha256(address.strip().encode("utf-8")).hexdigest()


# ── Pure resolution ──────────────────────────────────────────────────────────


def anchors_from_registry(places: Iterable[Any]) -> list[AnchorPlace]:
    """Anchors from ``butlers.owner_places.OwnerPlace`` rows (already live/unpaused)."""
    return [
        AnchorPlace(
            kind=place.kind if place.kind in ANCHOR_KINDS else KIND_OTHER,
            label=place.label,
            lat=float(place.lat),
            lon=float(place.lon),
            radius_m=float(place.radius_m),
            source=SOURCE_REGISTRY,
            ssids=tuple(place.ssids or ()),
        )
        for place in places
    ]


def _resolved(address: GraphAddress) -> bool:
    return (
        address.geocode_status == "resolved"
        and address.lat is not None
        and address.lon is not None
        and address.geocode_sha256 == address_sha256(address.address)
    )


def _best(addresses: Sequence[GraphAddress]) -> GraphAddress | None:
    epoch = datetime.min.replace(tzinfo=UTC)
    ranked = sorted(
        addresses,
        key=lambda a: (a.is_primary, a.updated_at or epoch),
        reverse=True,
    )
    return ranked[0] if ranked else None


def anchors_from_graph(addresses: Sequence[GraphAddress]) -> list[AnchorPlace]:
    """Home and work anchors from geocoded graph addresses (stale geocodes ignored)."""
    usable = [a for a in addresses if _resolved(a)]

    def label_of(a: GraphAddress) -> str:
        return (a.label or "").strip().lower()

    work_candidates = [a for a in usable if a.via == "works-at"] or [
        a for a in usable if a.via == "self" and label_of(a) in _WORK_LABELS
    ]
    home_candidates = [a for a in usable if a.via == "self" and label_of(a) in _HOME_LABELS] or [
        a for a in usable if a.via in _RESIDENCE_PREDICATES
    ]

    anchors: list[AnchorPlace] = []
    work = _best(work_candidates)
    if work is not None:
        anchors.append(
            AnchorPlace(
                kind=KIND_WORK,
                label=work.target_name or "work",
                lat=float(work.lat),  # type: ignore[arg-type]
                lon=float(work.lon),  # type: ignore[arg-type]
                radius_m=GRAPH_WORK_RADIUS_M,
                source=SOURCE_GRAPH,
            )
        )
    home = _best(home_candidates)
    if home is not None:
        anchors.append(
            AnchorPlace(
                kind=KIND_HOME,
                label="home",
                lat=float(home.lat),  # type: ignore[arg-type]
                lon=float(home.lon),  # type: ignore[arg-type]
                radius_m=GRAPH_HOME_RADIUS_M,
                source=SOURCE_GRAPH,
            )
        )
    return anchors


def anchors_from_references(references: Iterable[PlaceReference]) -> list[AnchorPlace]:
    """Anchors from legacy ``OWNTRACKS_PLACE_REFERENCES`` entries."""
    anchors = []
    for ref in references:
        label = ref.label.strip()
        kind = label.lower() if label.lower() in (KIND_HOME, KIND_WORK) else KIND_OTHER
        anchors.append(
            AnchorPlace(
                kind=kind,
                label=label,
                lat=ref.lat,
                lon=ref.lon,
                radius_m=ref.radius_m,
                source=SOURCE_ENV,
            )
        )
    return anchors


def infer_overnight_home(
    stays: Sequence[StaySample],
    *,
    tz: tzinfo,
    min_nights: int = INFERRED_HOME_MIN_NIGHTS,
    radius_m: float = INFERRED_HOME_RADIUS_M,
) -> AnchorPlace | None:
    """The place most often occupied at 03:00 local, on at least ``min_nights``.

    Each stay contributes the local nights whose 03:00 it covers. Stays are
    grouped greedily by distance to a group's first stay; the group with the most
    distinct nights wins (ties: the earliest-seen group). Pure, deterministic.
    """
    groups: list[tuple[float, float, set[str]]] = []
    for stay in sorted(stays, key=lambda s: s.start_at):
        nights = _probe_nights(stay, tz)
        if not nights:
            continue
        for g_lat, g_lon, g_nights in groups:
            if haversine_meters(g_lat, g_lon, stay.lat, stay.lon) <= radius_m:
                g_nights.update(nights)
                break
        else:
            groups.append((stay.lat, stay.lon, set(nights)))
    best: tuple[float, float, set[str]] | None = None
    for group in groups:
        if best is None or len(group[2]) > len(best[2]):
            best = group
    if best is None or len(best[2]) < min_nights:
        return None
    return AnchorPlace(
        kind=KIND_HOME,
        label="home",
        lat=best[0],
        lon=best[1],
        radius_m=radius_m,
        source=SOURCE_INFERRED,
    )


def _probe_nights(stay: StaySample, tz: tzinfo) -> set[str]:
    nights: set[str] = set()
    day = stay.start_at.astimezone(tz).date()
    last = stay.end_at.astimezone(tz).date()
    while day <= last:
        probe = datetime.combine(day, INFERRED_HOME_PROBE, tz)
        if stay.start_at <= probe < stay.end_at:
            nights.add(day.isoformat())
        day += timedelta(days=1)
    return nights


def resolve_anchors(
    *,
    registry: Sequence[AnchorPlace] = (),
    graph: Sequence[AnchorPlace] = (),
    references: Sequence[AnchorPlace] = (),
    inferred_home: AnchorPlace | None = None,
) -> tuple[AnchorPlace, ...]:
    """Merge sources: per kind, the first source that has any anchor of it wins.

    ``other``/``airport`` anchors from every source are kept (they name places,
    they do not compete); home and work come from exactly one source each.
    """
    anchors: list[AnchorPlace] = []
    for kind in (KIND_HOME, KIND_WORK):
        for source in (registry, graph, references):
            chosen = [a for a in source if a.kind == kind]
            if chosen:
                anchors.extend(chosen)
                break
        else:
            if kind == KIND_HOME and inferred_home is not None:
                anchors.append(inferred_home)
    for source in (registry, graph, references):
        anchors.extend(a for a in source if a.kind in (KIND_AIRPORT, KIND_OTHER))
    return tuple(anchors)


def nearest_anchor(anchors: Sequence[AnchorPlace], lat: float, lon: float) -> AnchorPlace | None:
    """The anchor whose radius contains the point, nearest first; ``None`` if none."""
    best: AnchorPlace | None = None
    best_dist = math.inf
    for anchor in anchors:
        dist = anchor.distance_m(lat, lon)
        if dist <= anchor.radius_m and dist < best_dist:
            best, best_dist = anchor, dist
    return best


def home_anchor(anchors: Sequence[AnchorPlace]) -> AnchorPlace | None:
    return next((a for a in anchors if a.kind == KIND_HOME), None)


# A surface that is structurally absent (not migrated yet, or not granted)
# degrades to "no anchor from this source". Any other failure propagates: a
# transient error must fail the run, not silently re-lane every block until
# the next run restores the anchor.
_SURFACE_ABSENT = (
    asyncpg.UndefinedTableError,
    asyncpg.UndefinedColumnError,
    asyncpg.InvalidSchemaNameError,
    asyncpg.InsufficientPrivilegeError,
)


# ── Loaders (degrade to empty on an absent or unreadable surface) ────────────

_GRAPH_SQL = """
WITH owner AS (
    SELECT id FROM public.entities
    WHERE 'owner' = ANY(roles) AND (metadata ->> 'merged_into') IS NULL
    LIMIT 1
),
links AS (
    SELECT f.predicate AS via, f.object_entity_id AS target,
           f."primary" AS is_primary, f.updated_at
    FROM relationship.entity_facts f JOIN owner ON f.subject = owner.id
    WHERE f.validity = 'active'
      AND f.object_kind = 'entity'
      AND f.predicate IN ('works-at', 'rental-location', 'lives-at')
      AND (f.effective_to IS NULL OR f.effective_to > now())
),
addresses AS (
    SELECT 'self'::text AS via, a.id AS fact_id, a.object AS address,
           a.metadata ->> 'label' AS label, NULL::text AS target_name,
           coalesce(a."primary", false) AS is_primary, a.updated_at
    FROM relationship.entity_facts a JOIN owner ON a.subject = owner.id
    WHERE a.predicate = 'has-address' AND a.validity = 'active'
      AND a.object_kind = 'literal'
    UNION ALL
    SELECT l.via, a.id, a.object, a.metadata ->> 'label', e.canonical_name,
           coalesce(l.is_primary, a."primary", false), greatest(l.updated_at, a.updated_at)
    FROM links l
    JOIN relationship.entity_facts a
      ON a.subject = l.target AND a.predicate = 'has-address'
     AND a.validity = 'active' AND a.object_kind = 'literal'
    JOIN public.entities e ON e.id = l.target
)
SELECT ad.via, ad.label, ad.target_name, ad.address, ad.is_primary, ad.updated_at,
       g.address_sha256, g.status, g.lat, g.lon
FROM addresses ad
LEFT JOIN relationship.address_geocodes g ON g.fact_id = ad.fact_id
"""


async def load_graph_addresses(pool: Any) -> list[GraphAddress]:
    """The owner's anchor address facts with their geocodes; ``[]`` when unavailable."""
    try:
        rows = await pool.fetch(_GRAPH_SQL)
    except _SURFACE_ABSENT:
        logger.info("Owner anchor addresses unavailable; graph anchors skipped")
        return []
    return [
        GraphAddress(
            via=row["via"],
            label=row["label"],
            target_name=row["target_name"],
            address=row["address"] or "",
            is_primary=bool(row["is_primary"]),
            updated_at=row["updated_at"],
            geocode_sha256=row["address_sha256"],
            geocode_status=row["status"],
            lat=float(row["lat"]) if row["lat"] is not None else None,
            lon=float(row["lon"]) if row["lon"] is not None else None,
        )
        for row in rows
    ]


async def load_registry_anchors(pool: Any) -> list[AnchorPlace]:
    from butlers.owner_places import load_active_owner_places

    try:
        places = await load_active_owner_places(pool)
    except _SURFACE_ABSENT:
        logger.info("Owner place registry unavailable; registry anchors skipped")
        return []
    return anchors_from_registry(places)


async def load_stay_samples(
    pool: Any, *, now: datetime, lookback_days: int = INFERRED_HOME_LOOKBACK_DAYS
) -> list[StaySample]:
    """Live place stays (Chronicler's own rows) for overnight-home inference."""
    rows = await pool.fetch(
        """
        SELECT start_at, end_at, payload
        FROM v_episodes_corrected
        WHERE source_name = 'owntracks.place_cluster'
          AND episode_type = 'place_episode'
          AND tombstone_at IS NULL
          AND end_at IS NOT NULL
          AND end_at > $1
        """,
        now - timedelta(days=lookback_days),
    )
    samples = []
    for row in rows:
        lat, lon = centroid_of(row["payload"])
        if lat is not None and lon is not None:
            samples.append(StaySample(row["start_at"], row["end_at"], lat, lon))
    return samples


def centroid_of(payload: Any) -> tuple[float | None, float | None]:
    """A place stay's centroid from its payload, tolerating JSON-string payloads."""
    if isinstance(payload, str):
        import json

        try:
            payload = json.loads(payload)
        except ValueError:
            return None, None
    if not isinstance(payload, Mapping):
        return None, None
    try:
        lat = float(payload["centroid_lat"])
        lon = float(payload["centroid_lon"])
    except (KeyError, TypeError, ValueError):
        return None, None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None, None
    return lat, lon


async def load_anchors(
    pool: Any,
    *,
    tz: tzinfo,
    now: datetime,
    references: Sequence[PlaceReference] = (),
) -> tuple[AnchorPlace, ...]:
    """Resolve the owner's anchors from every source (see module docstring)."""
    registry = await load_registry_anchors(pool)
    graph = anchors_from_graph(await load_graph_addresses(pool))
    refs = anchors_from_references(references)
    inferred = None
    if not any(a.kind == KIND_HOME for a in (*registry, *graph, *refs)):
        inferred = infer_overnight_home(await load_stay_samples(pool, now=now), tz=tz)
    return resolve_anchors(registry=registry, graph=graph, references=refs, inferred_home=inferred)


__all__ = [
    "ANCHOR_KINDS",
    "KIND_AIRPORT",
    "KIND_HOME",
    "KIND_OTHER",
    "KIND_WORK",
    "SOURCE_ENV",
    "SOURCE_GRAPH",
    "SOURCE_INFERRED",
    "SOURCE_REGISTRY",
    "AnchorPlace",
    "GraphAddress",
    "StaySample",
    "address_sha256",
    "anchors_from_graph",
    "anchors_from_references",
    "anchors_from_registry",
    "centroid_of",
    "home_anchor",
    "infer_overnight_home",
    "load_anchors",
    "load_graph_addresses",
    "load_registry_anchors",
    "load_stay_samples",
    "nearest_anchor",
    "resolve_anchors",
]
