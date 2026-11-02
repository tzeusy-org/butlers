"""The owner place registry: shared representation and deterministic read helper.

``public.owner_places`` (migration ``core_303``) is the one trusted list of the owner's
named places.  Home owns the dashboard editor that writes it; this module owns the
representation every deterministic reader shares and the single read they use.

Coordinates, SSIDs and Home Assistant zone ids are private: nothing here logs them, and
no model, prompt or MCP tool receives them.  Readers get frozen dataclasses and derive
their own decisions (distance, SSID match) in process.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

PlaceKind = Literal["home", "work", "airport", "other"]

PLACE_KINDS: tuple[PlaceKind, ...] = ("home", "work", "airport", "other")
LABEL_MAX_CHARS = 80
RADIUS_MIN_M = 50
RADIUS_MAX_M = 2000
COORDINATE_DECIMALS = 5
MAX_ALIASES = 10
MAX_SSIDS = 20
SSID_MAX_BYTES = 32


@dataclass(frozen=True)
class OwnerPlace:
    """One live owner place as a deterministic reader sees it."""

    id: UUID
    label: str
    kind: PlaceKind
    lat: float
    lon: float
    radius_m: int
    aliases: tuple[str, ...] = ()
    ssids: tuple[str, ...] = ()
    ha_zone: str | None = None


def owner_place_from_row(row: Any) -> OwnerPlace:
    """Build an :class:`OwnerPlace` from a ``public.owner_places`` row."""
    return OwnerPlace(
        id=row["id"],
        label=row["label"],
        kind=row["kind"],
        lat=float(row["lat"]),
        lon=float(row["lon"]),
        radius_m=int(row["radius_m"]),
        aliases=tuple(row["aliases"] or ()),
        ssids=tuple(row["ssids"] or ()),
        ha_zone=row["ha_zone"],
    )


async def load_active_owner_places(pool: Any) -> tuple[OwnerPlace, ...]:
    """Return every live place: not soft-deleted and not paused, in a stable order.

    A paused or deleted place takes no part in resolution.  The order (kind, then label,
    then id) is deterministic so two readers on the same rows decide the same way.
    """
    rows = await pool.fetch(
        "SELECT id, label, kind, lat, lon, radius_m, aliases, ssids, ha_zone "
        "FROM public.owner_places "
        "WHERE deleted_at IS NULL AND NOT paused "
        "ORDER BY kind, lower(label), id"
    )
    return tuple(owner_place_from_row(row) for row in rows)


__all__ = [
    "COORDINATE_DECIMALS",
    "LABEL_MAX_CHARS",
    "MAX_ALIASES",
    "MAX_SSIDS",
    "PLACE_KINDS",
    "RADIUS_MAX_M",
    "RADIUS_MIN_M",
    "SSID_MAX_BYTES",
    "OwnerPlace",
    "PlaceKind",
    "load_active_owner_places",
    "owner_place_from_row",
]
