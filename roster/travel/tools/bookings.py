"""Travel butler booking tools — record bookings and mutate itineraries."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

import asyncpg

# ---------------------------------------------------------------------------
# Valid entity type values
# ---------------------------------------------------------------------------

_VALID_ENTITY_TYPES = ("leg", "accommodation", "reservation", "document")
_VALID_LEG_TYPES = ("flight", "train", "bus", "ferry")
_VALID_ACCOMMODATION_TYPES = ("hotel", "airbnb", "hostel")
_VALID_RESERVATION_TYPES = ("car_rental", "restaurant", "activity", "tour")

_VALID_TRIP_STATUSES = ("planned", "active", "completed", "cancelled")

# Status transition rules: forward-only, no backward transitions.
_ALLOWED_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "planned": {"active", "cancelled"},
    "active": {"completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


# ---------------------------------------------------------------------------
# Datetime/date normalization helpers
# ---------------------------------------------------------------------------


def _normalize_datetime(value: str | datetime | None) -> datetime | None:
    """Normalize a string or datetime to a datetime object, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _normalize_date(value: str | date | None) -> date | None:
    """Normalize a string or date to a date object, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


# ---------------------------------------------------------------------------
# Trip auto-matching
# ---------------------------------------------------------------------------


async def _find_matching_trip(
    pool: asyncpg.Pool,
    candidate_dates: list[date],
    candidate_destinations: list[str],
) -> str | None:
    """Find an existing trip that overlaps with the given date and destination hints.

    Returns the trip_id (str) if a match is found, else None.
    Matching criteria: destination keyword overlap AND date range overlap.
    """
    if not candidate_dates and not candidate_destinations:
        return None

    rows = await pool.fetch(
        "SELECT id, destination, start_date, end_date FROM travel.trips"
        " WHERE status IN ('planned', 'active') ORDER BY start_date ASC"
    )

    for row in rows:
        trip_id = str(row["id"])
        trip_dest = (row["destination"] or "").lower()
        trip_start: date = row["start_date"]
        trip_end: date = row["end_date"]

        # Check destination overlap (substring match)
        dest_match = any(
            d.lower() in trip_dest or trip_dest in d.lower() for d in candidate_destinations
        )

        # Check date range overlap (±1 day tolerance for timezone edge cases)
        date_match = any(
            trip_start <= d <= trip_end
            or abs((d - trip_start).days) <= 1
            or abs((d - trip_end).days) <= 1
            for d in candidate_dates
        )

        if dest_match and date_match:
            return trip_id
        if date_match and not candidate_destinations:
            return trip_id
        if dest_match and not candidate_dates:
            return trip_id

    return None


async def _create_trip_from_payload(
    pool: asyncpg.Pool,
    payload: dict[str, Any],
) -> str:
    """Create a minimal trip container from booking payload hints.

    Returns the new trip_id (str).
    """
    destination = (
        payload.get("arrival_city")
        or payload.get("arrival_airport_station")
        or payload.get("arrival")
        or "Unknown destination"
    )
    provider = payload.get("provider", "")

    # Determine date range from payload dates
    dates: list[date] = []
    for field in ("departure_at", "arrival_at", "check_in", "check_out", "datetime"):
        raw = payload.get(field)
        if raw:
            try:
                dt = _normalize_datetime(raw)
                if dt is not None:
                    dates.append(dt.date())
            except (ValueError, TypeError):
                pass

    start_date = min(dates) if dates else date.today()
    end_date = max(dates) if dates else start_date

    name = f"Trip to {destination}"
    if provider:
        name = f"Trip to {destination} ({provider})"

    row = await pool.fetchrow(
        """
        INSERT INTO travel.trips (name, destination, start_date, end_date, status)
        VALUES ($1, $2, $3, $4, 'planned')
        RETURNING id
        """,
        name,
        destination,
        start_date,
        end_date,
    )
    return str(row["id"])


# ---------------------------------------------------------------------------
# Entity insertion helpers
# ---------------------------------------------------------------------------


async def _insert_leg(
    pool: asyncpg.Pool,
    trip_id: str,
    payload: dict[str, Any],
    source_message_id: str | None,
) -> tuple[str, bool, bool]:
    """Insert a leg entity. Returns (entity_id, created, deduped)."""
    confirmation_number = payload.get("confirmation_number")

    # Dedup check: match on confirmation_number + trip_id
    if confirmation_number and source_message_id:
        existing = await pool.fetchrow(
            "SELECT id FROM travel.legs WHERE confirmation_number = $1 AND trip_id = $2::uuid",
            confirmation_number,
            trip_id,
        )
        if existing:
            return str(existing["id"]), False, True

    departure_at = _normalize_datetime(payload.get("departure_at"))
    arrival_at = _normalize_datetime(payload.get("arrival_at"))

    if departure_at is None:
        raise ValueError("record_booking: leg requires 'departure_at' in payload")
    if arrival_at is None:
        raise ValueError("record_booking: leg requires 'arrival_at' in payload")

    leg_type = payload.get("type") if payload.get("type") in _VALID_LEG_TYPES else "flight"
    meta: dict[str, Any] = dict(payload.get("metadata") or {})
    if source_message_id:
        meta["source_message_id"] = source_message_id

    row = await pool.fetchrow(
        """
        INSERT INTO travel.legs (
            trip_id, type, carrier,
            departure_airport_station, departure_city,
            departure_at,
            arrival_airport_station, arrival_city,
            arrival_at,
            confirmation_number, pnr, seat, metadata
        ) VALUES (
            $1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb
        )
        RETURNING id
        """,
        trip_id,
        leg_type,
        payload.get("carrier") or payload.get("provider"),
        payload.get("departure_airport_station") or payload.get("departure"),
        payload.get("departure_city"),
        departure_at,
        payload.get("arrival_airport_station") or payload.get("arrival"),
        payload.get("arrival_city"),
        arrival_at,
        confirmation_number,
        payload.get("pnr"),
        payload.get("seat"),
        json.dumps(meta),
    )
    return str(row["id"]), True, False


async def _insert_accommodation(
    pool: asyncpg.Pool,
    trip_id: str,
    payload: dict[str, Any],
    source_message_id: str | None,
) -> tuple[str, bool, bool]:
    """Insert an accommodation entity. Returns (entity_id, created, deduped)."""
    confirmation_number = payload.get("confirmation_number")

    if confirmation_number and source_message_id:
        existing = await pool.fetchrow(
            "SELECT id FROM travel.accommodations"
            " WHERE confirmation_number = $1 AND trip_id = $2::uuid",
            confirmation_number,
            trip_id,
        )
        if existing:
            return str(existing["id"]), False, True

    accom_type = (
        payload.get("type") if payload.get("type") in _VALID_ACCOMMODATION_TYPES else "hotel"
    )
    meta: dict[str, Any] = dict(payload.get("metadata") or {})
    if source_message_id:
        meta["source_message_id"] = source_message_id

    check_in = _normalize_datetime(payload.get("check_in"))
    check_out = _normalize_datetime(payload.get("check_out"))

    row = await pool.fetchrow(
        """
        INSERT INTO travel.accommodations (
            trip_id, type, name, address, check_in, check_out,
            confirmation_number, metadata
        ) VALUES (
            $1::uuid, $2, $3, $4, $5, $6, $7, $8::jsonb
        )
        RETURNING id
        """,
        trip_id,
        accom_type,
        payload.get("name"),
        payload.get("address"),
        check_in,
        check_out,
        confirmation_number,
        json.dumps(meta),
    )
    return str(row["id"]), True, False


async def _insert_reservation(
    pool: asyncpg.Pool,
    trip_id: str,
    payload: dict[str, Any],
    source_message_id: str | None,
) -> tuple[str, bool, bool]:
    """Insert a reservation entity. Returns (entity_id, created, deduped)."""
    confirmation_number = payload.get("confirmation_number")

    if confirmation_number and source_message_id:
        existing = await pool.fetchrow(
            "SELECT id FROM travel.reservations"
            " WHERE confirmation_number = $1 AND trip_id = $2::uuid",
            confirmation_number,
            trip_id,
        )
        if existing:
            return str(existing["id"]), False, True

    res_type = (
        payload.get("type") if payload.get("type") in _VALID_RESERVATION_TYPES else "activity"
    )
    meta: dict[str, Any] = dict(payload.get("metadata") or {})
    if source_message_id:
        meta["source_message_id"] = source_message_id

    event_dt = _normalize_datetime(payload.get("datetime"))

    row = await pool.fetchrow(
        """
        INSERT INTO travel.reservations (
            trip_id, type, provider, datetime, confirmation_number, metadata
        ) VALUES (
            $1::uuid, $2, $3, $4, $5, $6::jsonb
        )
        RETURNING id
        """,
        trip_id,
        res_type,
        payload.get("provider"),
        event_dt,
        confirmation_number,
        json.dumps(meta),
    )
    return str(row["id"]), True, False


async def _insert_document(
    pool: asyncpg.Pool,
    trip_id: str,
    payload: dict[str, Any],
    source_message_id: str | None,
) -> tuple[str, bool, bool]:
    """Insert a document entity from booking payload. Returns (entity_id, created, deduped)."""
    doc_type = payload.get("doc_type") or payload.get("document_type") or "receipt"
    meta: dict[str, Any] = dict(payload.get("metadata") or {})
    if source_message_id:
        meta["source_message_id"] = source_message_id

    expiry = _normalize_date(payload.get("expiry_date"))

    row = await pool.fetchrow(
        """
        INSERT INTO travel.documents (
            trip_id, type, blob_ref, expiry_date, metadata
        ) VALUES (
            $1::uuid, $2, $3, $4, $5::jsonb
        )
        RETURNING id
        """,
        trip_id,
        doc_type,
        payload.get("blob_ref"),
        expiry,
        json.dumps(meta),
    )
    return str(row["id"]), True, False


# ---------------------------------------------------------------------------
# Public: record_booking
# ---------------------------------------------------------------------------


async def record_booking(
    pool: asyncpg.Pool,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Parse and persist a booking/update email payload into trip container tables.

    The payload dict supports the following fields:

    - ``provider`` (str): Booking provider (e.g. "United Airlines", "Marriott").
    - ``source_message_id`` (str | None): Source email or message ID for dedup.
    - ``entity_type`` (str): One of ``leg``, ``accommodation``, ``reservation``,
      ``document``. Defaults to ``leg`` when not provided.
    - ``candidate_trip_hints`` (dict | None): Dict with optional
      ``dates`` (list of ISO date strings) and ``destinations`` (list of str)
      for trip auto-matching.
    - Structured booking fields passed through to the relevant entity table
      (e.g. ``departure_at``, ``arrival_at``, ``confirmation_number``,
      ``pnr``, ``seat`` for legs; ``check_in``, ``check_out`` for
      accommodations; ``datetime`` for reservations).

    Trip auto-matching: if ``candidate_trip_hints`` are provided, the tool
    tries to match an existing trip by date+destination overlap before
    creating a new trip container.

    Deduplication: when both ``confirmation_number`` and ``source_message_id``
    are present, the tool checks for an existing entity and returns it without
    inserting a duplicate.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    payload:
        Booking payload dict.

    Returns
    -------
    dict
        RecordBookingResult with keys:
        ``trip_id``, ``entity_type``, ``entity_id``, ``created``,
        ``deduped``, ``warnings``.
    """
    warnings: list[str] = []

    source_message_id: str | None = payload.get("source_message_id")
    entity_type: str = payload.get("entity_type", "leg")

    if entity_type not in _VALID_ENTITY_TYPES:
        warnings.append(f"Unknown entity_type {entity_type!r}; defaulting to 'leg'.")
        entity_type = "leg"

    # --- Trip auto-matching ---
    hints = payload.get("candidate_trip_hints") or {}
    candidate_dates: list[date] = []
    candidate_destinations: list[str] = list(hints.get("destinations") or [])

    for ds in hints.get("dates") or []:
        try:
            d = _normalize_date(ds)
            if d:
                candidate_dates.append(d)
        except (ValueError, TypeError):
            warnings.append(f"Could not parse candidate date hint: {ds!r}")

    # Also infer dates from the payload itself
    for field in ("departure_at", "arrival_at", "check_in", "check_out", "datetime"):
        raw = payload.get(field)
        if raw:
            try:
                dt = _normalize_datetime(raw)
                if dt:
                    candidate_dates.append(dt.date())
            except (ValueError, TypeError):
                pass

    # Infer destinations from payload
    for field in ("arrival_city", "arrival_airport_station", "arrival", "destination"):
        val = payload.get(field)
        if val and val not in candidate_destinations:
            candidate_destinations.append(str(val))

    trip_id = await _find_matching_trip(pool, candidate_dates, candidate_destinations)
    if trip_id is None:
        trip_id = await _create_trip_from_payload(pool, payload)

    # --- Insert entity ---
    entity_id: str
    created: bool
    deduped: bool

    try:
        if entity_type == "leg":
            entity_id, created, deduped = await _insert_leg(
                pool, trip_id, payload, source_message_id
            )
        elif entity_type == "accommodation":
            entity_id, created, deduped = await _insert_accommodation(
                pool, trip_id, payload, source_message_id
            )
        elif entity_type == "reservation":
            entity_id, created, deduped = await _insert_reservation(
                pool, trip_id, payload, source_message_id
            )
        else:  # document
            entity_id, created, deduped = await _insert_document(
                pool, trip_id, payload, source_message_id
            )
    except ValueError as exc:
        warnings.append(str(exc))
        return {
            "trip_id": trip_id,
            "entity_type": entity_type,
            "entity_id": None,
            "created": False,
            "deduped": False,
            "warnings": warnings,
        }

    return {
        "trip_id": trip_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "created": created,
        "deduped": deduped,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Public: update_itinerary
# ---------------------------------------------------------------------------


async def update_itinerary(
    pool: asyncpg.Pool,
    trip_id: str,
    patch: dict[str, Any],
    reason: str | None = None,
) -> dict[str, Any]:
    """Apply explicit itinerary corrections to an existing trip.

    Applies time changes, cancellation flags, seat/gate updates, and rebooking
    patches to a trip's entities. Prior values are preserved in
    ``metadata.change_history`` for full audit traceability.

    Supported patch fields:

    Trip-level:
    - ``status``: New trip status (``planned``, ``active``, ``completed``,
      ``cancelled``). Forward-only transitions are enforced.
    - ``name``, ``destination``, ``start_date``, ``end_date``: Direct trip
      field updates.

    Entity-level patches (provide ``entity_type`` + ``entity_id`` or use
    ``leg_id``, ``accommodation_id``, ``reservation_id`` shortcuts):
    - For legs: ``departure_at``, ``arrival_at``, ``seat``, ``carrier``,
      ``departure_airport_station``, ``arrival_airport_station``,
      ``departure_city``, ``arrival_city``, ``pnr``, ``confirmation_number``.
    - For accommodations: ``check_in``, ``check_out``, ``name``, ``address``,
      ``confirmation_number``.
    - For reservations: ``datetime``, ``provider``, ``type``,
      ``confirmation_number``.

    Change history format (appended to entity ``metadata.change_history``):
    ```json
    {
      "prior_values": { "<field>": "<old_value>", ... },
      "source_message_id": "<id or null>",
      "updated_by": "update_itinerary",
      "reason": "<reason>",
      "updated_at": "<ISO timestamp>"
    }
    ```

    Optimistic concurrency: if ``version_token`` is provided in ``patch``, it
    is compared against the current ``updated_at`` ISO string. On mismatch, the
    entity is added to ``conflicts`` and skipped.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    trip_id:
        UUID string of the trip to patch.
    patch:
        Dict of field changes to apply. May include ``entity_type``,
        ``entity_id`` (or ``leg_id``, ``accommodation_id``, ``reservation_id``),
        ``status``, and entity-specific field overrides.
    reason:
        Human-readable reason for the change (e.g. "UA email: flight delay").

    Returns
    -------
    dict
        UpdateItineraryResult with keys:
        ``trip_id``, ``updated_entities``, ``conflicts``, ``new_trip_status``.

    Raises
    ------
    ValueError
        If the trip does not exist or a status value is invalid.
    """
    updated_entities: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    source_message_id: str | None = patch.get("source_message_id")
    version_token: str | None = patch.get("version_token")
    now_iso = datetime.now(UTC).isoformat()

    # --- Validate trip exists ---
    trip_row = await pool.fetchrow(
        "SELECT id, status, name, destination, start_date, end_date, metadata"
        " FROM travel.trips WHERE id = $1::uuid",
        trip_id,
    )
    if trip_row is None:
        raise ValueError(f"update_itinerary: trip {trip_id!r} not found")

    current_trip_status = trip_row["status"]
    new_trip_status = current_trip_status

    # --- Trip-level field updates ---
    trip_fields_to_update: dict[str, Any] = {}
    trip_prior_values: dict[str, Any] = {}

    if "status" in patch:
        new_status = patch["status"]
        if new_status not in _VALID_TRIP_STATUSES:
            raise ValueError(
                f"update_itinerary: invalid status {new_status!r}. "
                f"Must be one of {_VALID_TRIP_STATUSES}"
            )
        allowed = _ALLOWED_STATUS_TRANSITIONS.get(current_trip_status, set())
        if new_status != current_trip_status and new_status not in allowed:
            conflicts.append(
                {
                    "entity_type": "trip",
                    "entity_id": trip_id,
                    "field": "status",
                    "reason": (f"Cannot transition from {current_trip_status!r} to {new_status!r}"),
                }
            )
        else:
            trip_prior_values["status"] = current_trip_status
            trip_fields_to_update["status"] = new_status
            new_trip_status = new_status

    for field in ("name", "destination"):
        if field in patch:
            trip_prior_values[field] = trip_row[field]
            trip_fields_to_update[field] = patch[field]

    for field in ("start_date", "end_date"):
        if field in patch:
            raw_val = trip_row[field]
            trip_prior_values[field] = raw_val.isoformat() if isinstance(raw_val, date) else raw_val
            trip_fields_to_update[field] = _normalize_date(patch[field])

    if trip_fields_to_update:
        existing_meta_raw = trip_row["metadata"]
        if isinstance(existing_meta_raw, str):
            existing_meta: dict[str, Any] = (
                json.loads(existing_meta_raw) if existing_meta_raw else {}
            )
        else:
            existing_meta = dict(existing_meta_raw) if existing_meta_raw else {}

        history = list(existing_meta.get("change_history") or [])
        history.append(
            {
                "prior_values": trip_prior_values,
                "source_message_id": source_message_id,
                "updated_by": "update_itinerary",
                "reason": reason,
                "updated_at": now_iso,
            }
        )
        existing_meta["change_history"] = history

        set_clauses = []
        params: list[Any] = []
        idx = 1
        for col, val in trip_fields_to_update.items():
            set_clauses.append(f"{col} = ${idx}")
            params.append(val)
            idx += 1
        set_clauses.append(f"metadata = ${idx}::jsonb")
        params.append(json.dumps(existing_meta))
        idx += 1
        set_clauses.append(f"updated_at = ${idx}")
        params.append(datetime.now(UTC))
        idx += 1
        params.append(trip_id)

        await pool.execute(
            f"UPDATE travel.trips SET {', '.join(set_clauses)} WHERE id = ${idx}::uuid",
            *params,
        )
        updated_entities.append(
            {
                "entity_type": "trip",
                "entity_id": trip_id,
                "fields": list(trip_fields_to_update.keys()),
            }
        )

    # --- Entity-level patches ---
    # Resolve entity_type / entity_id from patch
    entity_type = patch.get("entity_type")
    entity_id = (
        patch.get("entity_id")
        or patch.get("leg_id")
        or patch.get("accommodation_id")
        or patch.get("reservation_id")
    )
    if not entity_type:
        if patch.get("leg_id"):
            entity_type = "leg"
        elif patch.get("accommodation_id"):
            entity_type = "accommodation"
        elif patch.get("reservation_id"):
            entity_type = "reservation"

    if entity_type and entity_id:
        table_map: dict[str, tuple[str, list[str]]] = {
            "leg": (
                "travel.legs",
                [
                    "departure_at",
                    "arrival_at",
                    "seat",
                    "carrier",
                    "departure_airport_station",
                    "arrival_airport_station",
                    "departure_city",
                    "arrival_city",
                    "pnr",
                    "confirmation_number",
                ],
            ),
            "accommodation": (
                "travel.accommodations",
                ["check_in", "check_out", "name", "address", "confirmation_number"],
            ),
            "reservation": (
                "travel.reservations",
                ["datetime", "provider", "type", "confirmation_number"],
            ),
        }

        if entity_type not in table_map:
            conflicts.append(
                {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "reason": f"Unknown entity_type {entity_type!r} for entity patch",
                }
            )
        else:
            table, patchable_fields = table_map[entity_type]
            entity_row = await pool.fetchrow(
                f"SELECT * FROM {table} WHERE id = $1::uuid AND trip_id = $2::uuid",
                entity_id,
                trip_id,
            )
            if entity_row is None:
                conflicts.append(
                    {
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "reason": (f"{entity_type} {entity_id!r} not found in trip {trip_id!r}"),
                    }
                )
            else:
                # Optimistic concurrency check
                if version_token is not None:
                    current_updated_at = entity_row["updated_at"]
                    current_token = (
                        current_updated_at.isoformat()
                        if isinstance(current_updated_at, datetime)
                        else str(current_updated_at)
                    )
                    if version_token != current_token:
                        conflicts.append(
                            {
                                "entity_type": entity_type,
                                "entity_id": entity_id,
                                "reason": (
                                    f"Optimistic concurrency conflict:"
                                    f" version_token mismatch"
                                    f" (expected {version_token!r},"
                                    f" got {current_token!r})"
                                ),
                            }
                        )
                        return {
                            "trip_id": trip_id,
                            "updated_entities": updated_entities,
                            "conflicts": conflicts,
                            "new_trip_status": new_trip_status,
                        }

                entity_prior: dict[str, Any] = {}
                entity_updates: dict[str, Any] = {}

                for field in patchable_fields:
                    if field not in patch:
                        continue
                    old_val = entity_row[field]
                    if isinstance(old_val, datetime):
                        old_val = old_val.isoformat()
                    elif isinstance(old_val, date):
                        old_val = old_val.isoformat()
                    elif isinstance(old_val, uuid.UUID):
                        old_val = str(old_val)
                    entity_prior[field] = old_val

                    new_val = patch[field]
                    # Normalize datetime fields
                    dt_fields = {
                        "departure_at",
                        "arrival_at",
                        "check_in",
                        "check_out",
                        "datetime",
                    }
                    if field in dt_fields:
                        new_val = _normalize_datetime(new_val)
                    entity_updates[field] = new_val

                if entity_updates:
                    entity_meta_raw = entity_row["metadata"]
                    if isinstance(entity_meta_raw, str):
                        entity_meta: dict[str, Any] = (
                            json.loads(entity_meta_raw) if entity_meta_raw else {}
                        )
                    else:
                        entity_meta = dict(entity_meta_raw) if entity_meta_raw else {}

                    history_e = list(entity_meta.get("change_history") or [])
                    history_e.append(
                        {
                            "prior_values": entity_prior,
                            "source_message_id": source_message_id,
                            "updated_by": "update_itinerary",
                            "reason": reason,
                            "updated_at": now_iso,
                        }
                    )
                    entity_meta["change_history"] = history_e

                    set_clauses_e = []
                    params_e: list[Any] = []
                    idx_e = 1
                    for col, val in entity_updates.items():
                        set_clauses_e.append(f"{col} = ${idx_e}")
                        params_e.append(val)
                        idx_e += 1
                    set_clauses_e.append(f"metadata = ${idx_e}::jsonb")
                    params_e.append(json.dumps(entity_meta))
                    idx_e += 1
                    set_clauses_e.append(f"updated_at = ${idx_e}")
                    params_e.append(datetime.now(UTC))
                    idx_e += 1
                    params_e.append(entity_id)
                    params_e.append(trip_id)

                    await pool.execute(
                        f"UPDATE {table} SET {', '.join(set_clauses_e)}"
                        f" WHERE id = ${idx_e}::uuid AND trip_id = ${idx_e + 1}::uuid",
                        *params_e,
                    )
                    updated_entities.append(
                        {
                            "entity_type": entity_type,
                            "entity_id": entity_id,
                            "fields": list(entity_updates.keys()),
                        }
                    )

    return {
        "trip_id": trip_id,
        "updated_entities": updated_entities,
        "conflicts": conflicts,
        "new_trip_status": new_trip_status,
    }
