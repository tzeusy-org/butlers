"""``trips`` over published trips, legs and travel documents (RFC 0039 D10, REQ-...-008).

Only city-level places, carriers, times, statuses and document types and expiry dates are
published: confirmation codes, document numbers, booking payloads and location fixes never
reach the projection, so the reader has nothing to filter.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from butlers.knowledge_read.core import (
    Arguments,
    ReadContext,
    ReadError,
    fields,
    fit,
    in_snapshot,
    published,
    record_uri,
)

TRIPS_LIMIT = 50
TRIP_STATUSES = ("planned", "active", "completed", "cancelled")
_LEG_FIELDS = (
    "type",
    "carrier",
    "departure_city",
    "departure_at",
    "arrival_city",
    "arrival_at",
)


async def trips(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """Trips newest first, each with its legs (by departure) and document expiry dates."""
    args = Arguments(arguments, {"status", "trip_id", "limit"})
    status = args.text("status", max_length=16)
    if status is not None and status not in TRIP_STATUSES:
        raise ReadError("invalid_argument", argument="status")
    trip_id = args.optional_uuid("trip_id")
    limit = args.integer("limit", low=1, high=TRIPS_LIMIT, default=20)
    context.require("travel")
    trip_table, trip_view = published("travel.trips", "trips")
    leg_table, leg_view = published("travel.legs", "trips")
    document_table, document_view = published("travel.documents", "trips")

    async def read() -> tuple[list[asyncpg.Record], list[asyncpg.Record], list[asyncpg.Record]]:
        rows = await conn.fetch(
            f"""SELECT source_id, fields FROM {trip_view}
            WHERE ($1::text IS NULL OR fields->>'status' = $1)
              AND ($2::text IS NULL OR source_id = $2)
            ORDER BY (fields->>'start_date')::date DESC NULLS LAST, source_id LIMIT $3""",
            status,
            trip_id,
            limit,
        )
        ids = [r["source_id"] for r in rows]
        legs = await conn.fetch(
            f"""SELECT source_id, fields FROM {leg_view} WHERE fields->>'trip_id' = ANY($1)
            ORDER BY (fields->>'departure_at')::timestamptz NULLS LAST, source_id""",
            ids,
        )
        documents = await conn.fetch(
            f"""SELECT source_id, fields FROM {document_view} WHERE fields->>'trip_id' = ANY($1)
            ORDER BY (fields->>'expiry_date')::date NULLS LAST, source_id""",
            ids,
        )
        return rows, legs, documents

    rows, legs, documents = await in_snapshot(conn, read)
    if trip_id is not None and not rows:
        raise ReadError("not_found", domain="travel")
    by_trip: dict[str, dict[str, list]] = {
        r["source_id"]: {"legs": [], "documents": []} for r in rows
    }
    for row in legs:
        data = fields(row["fields"])
        by_trip[data["trip_id"]]["legs"].append(
            {
                "id": row["source_id"],
                **{k: data.get(k) for k in _LEG_FIELDS},
                "source": context.source(*record_uri(leg_table, row["source_id"])),
            }
        )
    for row in documents:
        data = fields(row["fields"])
        by_trip[data["trip_id"]]["documents"].append(
            {
                "id": row["source_id"],
                "type": data.get("type"),
                "expiry_date": data.get("expiry_date"),
                "source": context.source(*record_uri(document_table, row["source_id"])),
            }
        )
    items = []
    for row in rows:
        data = fields(row["fields"])
        items.append(
            {
                "id": row["source_id"],
                "name": data.get("name"),
                "destination": data.get("destination"),
                "status": data.get("status"),
                "start_date": data.get("start_date"),
                "end_date": data.get("end_date"),
                **by_trip[row["source_id"]],
                "source": context.source(*record_uri(trip_table, row["source_id"])),
            }
        )
    return fit(items, lambda kept, _cut: {"trips": kept})
