---
name: tool-reference
description: Detailed parameter reference and usage patterns for travel butler MCP tools — record_booking, update_itinerary, list_trips, trip_summary, upcoming_travel, and add_document.
version: 1.0.0
tools_required:
  - record_booking
  - update_itinerary
  - list_trips
  - trip_summary
  - upcoming_travel
  - add_document
---

# Travel Butler Tool Reference

Detailed parameter documentation for the travel butler's domain tools. Consult this reference when you need full parameter signatures, field semantics, or deduplication rules.

## `record_booking`

Parse and persist a booking confirmation or update email payload into the trip container. Links the leg, accommodation, or reservation to the correct trip with full structured field extraction.

**Parameters:**
- `payload` (dict, required): Structured booking data extracted from the email or message:
  - `provider` (str): Carrier or provider name (e.g., `"United Airlines"`, `"Marriott"`)
  - `type` (str): Entity type — `"leg"` (flight), `"accommodation"`, `"reservation"`, `"car_rental"`
  - `departure` (str): IATA code for origin airport (legs only)
  - `arrival` (str): IATA code for destination airport (legs only)
  - `departure_at` (str): ISO 8601 datetime with timezone (`TIMESTAMPTZ`) for departure
  - `arrival_at` (str): ISO 8601 datetime with timezone for arrival
  - `pnr` (str): Airline record locator / booking reference
  - `confirmation_number` (str): Provider-specific confirmation number
  - `seat` (str): Assigned seat (e.g., `"22A"`)
  - `terminal` (str): Departure terminal
  - `gate` (str): Departure gate (if known at booking time)
  - `source_message_id` (str): Email or message ID — used for deduplication; always pass this
  - `metadata` (dict): Any additional fields that don't fit the schema
- `trip_id` (UUID, optional): If known, link directly. If omitted, the tool attempts to match by date/destination and creates a new trip if no match is found.

**Key rules:**
- Always pass `source_message_id` — the tool layer uses this for deduplication. Do not manually check for duplicates.
- PNRs are correlation hints, not global unique keys. Always pair with `provider` context.
- Never create floating bookings. If no matching trip exists, create one first with `trip_create`, then call `record_booking`.
- For rebookings, delays, and gate/seat changes, use `update_itinerary` — not `record_booking`.

## `update_itinerary`

Apply itinerary changes to an existing trip — time changes, cancellations, seat/gate reassignments, and rebookings. Always preserves prior values in `metadata.prior_values`.

**Parameters:**
- `trip_id` (UUID, required): The trip container to update
- `patch` (dict, required): Fields to update on the target entity:
  - `leg_id` (UUID): If updating a specific flight leg
  - `accommodation_id` (UUID): If updating an accommodation
  - Any mutable field: `departure_at`, `arrival_at`, `seat`, `gate`, `terminal`, `status`
- `reason` (str, required): Human-readable reason for the change (e.g., `"UA email: flight delay notification"`)
- `source_message_id` (str, optional): Source email ID for audit trail

**Key rules:**
- Always use `update_itinerary` for mutations (not re-calling `record_booking`).
- Prior values are automatically written to `metadata.prior_values` — do not overwrite manually.
- When processing rebooking emails (subject signals: "gate change", "trip update", "delay notification", "rebooking"), default to `update_itinerary`.

## `list_trips`

Query trip containers by lifecycle status and/or date window.

**Parameters:**
- `status` (str, optional): One of `"planned"`, `"active"`, `"completed"`, `"cancelled"`. Omit to return all statuses.
- `departure_after` (str, optional): ISO date — filter trips departing after this date
- `departure_before` (str, optional): ISO date — filter trips departing before this date
- `limit` (int, optional): Maximum results (default: 50)

**Status transitions:**
`planned → active → completed`. Direct cancellation (`→ cancelled`) is allowed from `planned` or `active`. Never transition backward (e.g., `completed → active`).

## `trip_summary`

Return a normalized trip timeline with all linked legs, accommodations, reservations, and document pointers. The single source of truth for a trip's current state.

**Parameters:**
- `trip_id` (UUID, required): Trip to summarize
- `include_documents` (bool, optional): Include attached documents (default: `False`)
- `include_timeline` (bool, optional): Return legs and accommodations sorted by date (default: `False`)

**Returns:** Structured dict with:
- `trip`: Core trip metadata (id, destination, status, dates)
- `legs`: Flight legs sorted by departure time
- `accommodations`: Hotel/lodging sorted by check-in
- `reservations`: Other reservations (car, restaurant, etc.)
- `documents`: Attached documents (if `include_documents=True`)
- `alerts`: Outstanding pre-trip actions (missing documents, pending check-ins)

## `upcoming_travel`

Surface upcoming departures and check-ins within a configurable window, with urgency-ranked pre-trip actions.

**Parameters:**
- `within_days` (int, required): Look-ahead window in days (e.g., `2` for 48-hour scan)
- `include_pretrip_actions` (bool, optional): Include urgency-ranked pre-trip action items (default: `False`)

**Pre-trip action types:**
- `boarding_pass_missing`: Boarding pass not attached
- `checkin_pending`: Online check-in window open but not completed
- `seat_unassigned`: No seat selected
- `tight_layover`: Connection time < 60 minutes
- `hotel_missing`: No accommodation for an upcoming night

## `add_document`

Attach a travel document reference to an existing trip.

**Parameters:**
- `trip_id` (UUID, required): Trip to attach the document to
- `type` (str, required): Document type — `"boarding_pass"`, `"visa"`, `"travel_insurance"`, `"passport"`, `"booking_confirmation"`, `"receipt"`, `"other"`
- `blob_ref` (str, required): Reference to the stored file/attachment
- `expiry_date` (str, optional): ISO date — document expiry (for passports, visas, insurance)
- `metadata` (dict, optional): Additional context:
  - `flight` (str): Flight number (for boarding passes)
  - `seat` (str): Seat assignment
  - `gate` (str): Gate (for boarding passes)
  - `policy_number` (str): Insurance policy number
  - `holder` (str): Document holder name

**Key rules:**
- Boarding passes, visas, and insurance should always include `expiry_date` when known.
- Pass `source_message_id` in metadata when ingesting from email for provenance tracking.

## Memory Classification — Travel Domain

### Subject

- User preferences and identity: `"user"` or the user's name
- Airlines: airline name (e.g., `"United Airlines"`, `"Delta"`, `"ANA"`)
- Hotel chains: chain name (e.g., `"Marriott"`, `"Hilton"`)
- Destinations: city or country (e.g., `"Tokyo"`, `"Japan"`)
- Airports: IATA code (e.g., `"SFO"`, `"NRT"`)

### Predicates

- `preferred_airline`: Preferred carrier for domestic or international travel
- `preferred_seat`: Seat type preference (`window`, `aisle`, `bulkhead`)
- `passport_nationality`: Country of passport (ISO alpha-2 code)
- `passport_expiry`: Passport expiry date (for document expiry alerts)
- `frequent_flyer`: Loyalty program name and membership number
- `hotel_preference`: Preferred chain or room type
- `travel_style`: Budget, business, or luxury
- `known_airport`: Home airport IATA code
- `dietary_preference`: In-flight meal preference
- `tsa_precheck`: TSA PreCheck or Global Entry known traveler number

### Permanence

- `stable`: Passport info, frequent flyer numbers, home airport, TSA/Global Entry numbers, long-standing preferences
- `standard` (default): Current trip context, active booking patterns, recent destination preferences
- `volatile`: Real-time flight status, gate changes, delay notifications, live check-in reminders

### Tags

`travel-preference`, `loyalty`, `passport`, `flight`, `hotel`, `document`, `reminder`, `delay`

### Example Facts

```python
# From: "I always fly United when I can"
memory_store_fact(
    subject="user",
    predicate="preferred_airline",
    content="United Airlines for both domestic and international travel",
    permanence="stable",
    importance=7.0,
    tags=["travel-preference", "flight"]
)

# From: "My passport expires June 2028"
memory_store_fact(
    subject="user",
    predicate="passport_expiry",
    content="US passport expires 2028-06-14",
    permanence="stable",
    importance=9.0,
    tags=["passport", "document"]
)

# From: "I'm a United MileagePlus member, number UA-7382910"
memory_store_fact(
    subject="United Airlines",
    predicate="frequent_flyer",
    content="MileagePlus member number UA-7382910",
    permanence="stable",
    importance=8.0,
    tags=["loyalty", "flight"]
)

# From: "I always book window seats"
memory_store_fact(
    subject="user",
    predicate="preferred_seat",
    content="window seat",
    permanence="stable",
    importance=6.0,
    tags=["travel-preference", "flight"]
)

# From: "I'm flying out of SFO most of the time"
memory_store_fact(
    subject="user",
    predicate="known_airport",
    content="SFO — San Francisco International (home airport)",
    permanence="stable",
    importance=8.0,
    tags=["travel-preference", "flight"]
)
```
