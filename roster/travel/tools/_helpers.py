"""Shared helpers for travel butler tools."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any

import asyncpg


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """Convert an asyncpg Record to a dict with UUID/datetime/date serialization.

    - UUID values are converted to strings.
    - datetime values are ISO-formatted strings.
    - date values are ISO-formatted strings.
    - JSONB fields (metadata): asyncpg normally deserializes JSONB to Python
      dicts on read, but the ``isinstance(val, str)`` guard is kept as a
      defensive fallback for environments where the JSONB codec is not
      registered.
    """
    d = dict(row)
    for key, val in d.items():
        if isinstance(val, uuid.UUID):
            d[key] = str(val)
        elif isinstance(val, datetime):
            d[key] = val.isoformat()
        elif isinstance(val, date):
            d[key] = val.isoformat()
        elif isinstance(val, str) and key == "metadata":
            try:
                d[key] = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                pass
    return d


def _build_timeline(
    legs: list[dict[str, Any]],
    accommodations: list[dict[str, Any]],
    reservations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a chronological timeline from legs, accommodations, and reservations.

    Each timeline entry has:
        - ``entity_type``: one of ``leg``, ``accommodation``, ``reservation``
        - ``entity_id``: the UUID string of the entity
        - ``sort_key``: the ISO datetime string used for sorting
        - ``summary``: a short human-readable label

    Entities with no timestamp are placed at the end, ordered by ``entity_id``
    for determinism.
    """
    entries: list[dict[str, Any]] = []

    for leg in legs:
        sort_key = leg.get("departure_at")
        summary_parts = []
        if leg.get("type"):
            summary_parts.append(leg["type"].capitalize())
        if leg.get("departure_city") or leg.get("departure_airport_station"):
            summary_parts.append(leg.get("departure_city") or leg.get("departure_airport_station"))
        if leg.get("arrival_city") or leg.get("arrival_airport_station"):
            summary_parts.append(
                "→ " + (leg.get("arrival_city") or leg.get("arrival_airport_station"))
            )
        if leg.get("carrier"):
            summary_parts.append(f"({leg['carrier']})")
        entries.append(
            {
                "entity_type": "leg",
                "entity_id": leg["id"],
                "sort_key": sort_key,
                "summary": " ".join(summary_parts) if summary_parts else "Transport leg",
            }
        )

    for acc in accommodations:
        sort_key = acc.get("check_in")
        summary_parts = []
        if acc.get("type"):
            summary_parts.append(acc["type"].capitalize())
        if acc.get("name"):
            summary_parts.append(acc["name"])
        entries.append(
            {
                "entity_type": "accommodation",
                "entity_id": acc["id"],
                "sort_key": sort_key,
                "summary": " ".join(summary_parts) if summary_parts else "Accommodation",
            }
        )

    for res in reservations:
        sort_key = res.get("datetime")
        summary_parts = []
        if res.get("type"):
            summary_parts.append(res["type"].replace("_", " ").capitalize())
        if res.get("provider"):
            summary_parts.append(f"— {res['provider']}")
        entries.append(
            {
                "entity_type": "reservation",
                "entity_id": res["id"],
                "sort_key": sort_key,
                "summary": " ".join(summary_parts) if summary_parts else "Reservation",
            }
        )

    # Sort: entries with a sort_key first (chronological), then None-keyed by entity_id.
    def _sort_key(entry: dict[str, Any]) -> tuple[int, str, str]:
        sk = entry.get("sort_key")
        if sk is not None:
            return (0, str(sk), entry["entity_id"])
        return (1, "", entry["entity_id"])

    entries.sort(key=_sort_key)
    return entries
