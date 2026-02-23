# Travel Butler: Permanent Definition

Status: Normative (Target State)
Last updated: 2026-02-22
Primary owner: Product/Domain

## 1. Role
The Travel Butler is the domain-specialist role for travel logistics and itinerary intelligence.

It ingests booking confirmations, itinerary updates, and travel documents from email, then normalizes them into a trip-level container model (`trip -> legs/accommodations/reservations/documents`) so changes across providers can be correlated and surfaced as actionable travel state.

## 2. Design Goals
- Preserve high-value structured travel data (PNRs, confirmation numbers, check-in/check-out windows, terminals, seat assignments) without flattening into generic freeform notes.
- Maintain a trip-level aggregate view that can stitch multi-provider records (airline + hotel + car + activity) into one timeline.
- Detect and reconcile itinerary changes over time (rebookings, delays, cancellations, gate/terminal changes) while retaining audit history in metadata.
- Support proactive time-sensitive reminders (upcoming departures, check-in windows, document expiry).
- Keep routing and ingestion deterministic through explicit sender/subject classification signals.

## 2.1 Base Contract Overrides
Inherits unchanged:
- All clauses in `docs/roles/base_butler.md` apply unless explicitly listed in `Overrides`.

Overrides: none.

Additions:
- This role defines a travel-specific domain schema in the dedicated `travel` schema (section 4).
- This role defines travel-specific MCP tools for booking capture, itinerary mutation, and trip summaries (section 5).
- This role defines travel-email classification signals for Switchboard routing (section 6).
- This role requires calendar and memory module configuration for timeline blocks, reminders, and context recall (section 7).
- This role defines travel-specific scheduled tasks for upcoming-travel checks and document expiry scans (section 8).

## 3. Scope and Boundaries

### In scope
- Flight confirmations, changes, delays, rebookings, and cancellations.
- Train/bus/ferry itinerary records with departure/arrival context.
- Accommodation bookings and cancellations (hotel, Airbnb, hostel).
- Ground reservations (car rental, transfers) and optional trip activities/tours.
- Travel document association and expiry tracking (boarding pass, visa, insurance, receipt).
- Trip-level timeline aggregation and user-facing summaries.

### Out of scope
- Direct user-channel delivery (owned by Messenger Butler via `notify` contract).
- Ingress routing policy execution (owned by Switchboard).
- General non-travel reminders and household planning unrelated to a trip container.
- Expense/accounting workflows beyond storing travel receipts/documents metadata.

## 4. Persistence Contract

### 4.1 Core Tables (Base Contract)
Inherited: `state`, `scheduled_tasks`, `sessions`.

### 4.2 Domain Schema (`travel`)
All travel domain tables MUST be created in schema `travel` and managed by Travel Butler migrations.

#### `travel.trips`
Trip-level container and lifecycle state.

| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | PK, default generated |
| `name` | TEXT | NOT NULL |
| `destination` | TEXT | NOT NULL |
| `start_date` | DATE | NOT NULL |
| `end_date` | DATE | NOT NULL, `end_date >= start_date` |
| `status` | TEXT | NOT NULL, CHECK (`status` IN ('planned', 'active', 'completed', 'cancelled')) |
| `metadata` | JSONB | NOT NULL default `{}` |
| `created_at` | TIMESTAMPTZ | NOT NULL default `now()` |
| `updated_at` | TIMESTAMPTZ | NOT NULL default `now()` |

Recommended indexes:
- `idx_trips_dates` on (`start_date`, `end_date`)
- `idx_trips_status` on (`status`)
- `idx_trips_destination` on (`destination`)

#### `travel.legs`
Transport legs for a trip (air/rail/bus/ferry).

| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | PK, default generated |
| `trip_id` | UUID | FK -> `travel.trips(id)` ON DELETE CASCADE |
| `type` | TEXT | NOT NULL, CHECK (`type` IN ('flight', 'train', 'bus', 'ferry')) |
| `carrier` | TEXT | NULL |
| `departure_airport_station` | TEXT | NULL |
| `departure_city` | TEXT | NULL |
| `departure_at` | TIMESTAMPTZ | NOT NULL |
| `arrival_airport_station` | TEXT | NULL |
| `arrival_city` | TEXT | NULL |
| `arrival_at` | TIMESTAMPTZ | NOT NULL, `arrival_at >= departure_at` |
| `confirmation_number` | TEXT | NULL |
| `pnr` | TEXT | NULL |
| `seat` | TEXT | NULL |
| `metadata` | JSONB | NOT NULL default `{}` |
| `created_at` | TIMESTAMPTZ | NOT NULL default `now()` |
| `updated_at` | TIMESTAMPTZ | NOT NULL default `now()` |

Recommended indexes:
- `idx_legs_trip_departure` on (`trip_id`, `departure_at`)
- `idx_legs_confirmation` on (`confirmation_number`)
- `idx_legs_pnr` on (`pnr`)

#### `travel.accommodations`
Lodging bookings attached to a trip.

| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | PK, default generated |
| `trip_id` | UUID | FK -> `travel.trips(id)` ON DELETE CASCADE |
| `type` | TEXT | NOT NULL, CHECK (`type` IN ('hotel', 'airbnb', 'hostel')) |
| `name` | TEXT | NOT NULL |
| `address` | TEXT | NULL |
| `check_in` | TIMESTAMPTZ | NOT NULL |
| `check_out` | TIMESTAMPTZ | NOT NULL, `check_out >= check_in` |
| `confirmation_number` | TEXT | NULL |
| `metadata` | JSONB | NOT NULL default `{}` |
| `created_at` | TIMESTAMPTZ | NOT NULL default `now()` |
| `updated_at` | TIMESTAMPTZ | NOT NULL default `now()` |

Recommended indexes:
- `idx_accommodations_trip_check_in` on (`trip_id`, `check_in`)
- `idx_accommodations_confirmation` on (`confirmation_number`)

#### `travel.reservations`
Trip-linked non-leg and non-lodging reservations.

| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | PK, default generated |
| `trip_id` | UUID | FK -> `travel.trips(id)` ON DELETE CASCADE |
| `type` | TEXT | NOT NULL, CHECK (`type` IN ('car_rental', 'restaurant', 'activity', 'tour')) |
| `provider` | TEXT | NOT NULL |
| `datetime` | TIMESTAMPTZ | NOT NULL |
| `confirmation_number` | TEXT | NULL |
| `metadata` | JSONB | NOT NULL default `{}` |
| `created_at` | TIMESTAMPTZ | NOT NULL default `now()` |
| `updated_at` | TIMESTAMPTZ | NOT NULL default `now()` |

Recommended indexes:
- `idx_reservations_trip_datetime` on (`trip_id`, `datetime`)
- `idx_reservations_confirmation` on (`confirmation_number`)

#### `travel.documents`
Travel documents and receipts linked to trip context.

| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | PK, default generated |
| `trip_id` | UUID | FK -> `travel.trips(id)` ON DELETE CASCADE |
| `type` | TEXT | NOT NULL, CHECK (`type` IN ('boarding_pass', 'visa', 'insurance', 'receipt')) |
| `blob_ref` | TEXT | NOT NULL |
| `expiry_date` | DATE | NULL |
| `metadata` | JSONB | NOT NULL default `{}` |
| `created_at` | TIMESTAMPTZ | NOT NULL default `now()` |
| `updated_at` | TIMESTAMPTZ | NOT NULL default `now()` |

Recommended indexes:
- `idx_documents_trip_type` on (`trip_id`, `type`)
- `idx_documents_expiry` on (`expiry_date`)

### 4.3 Data Integrity Rules
- `trip_id` is the canonical ownership boundary; every leg/accommodation/reservation/document row MUST attach to exactly one trip.
- Confirmation numbers and PNRs SHOULD be treated as correlation keys, not global uniqueness keys (providers can reuse formats across accounts).
- Itinerary updates MUST preserve previous values in `metadata` change history (`prior_values`, `source_message_id`, `updated_by`).
- Status transitions SHOULD follow: `planned -> active -> completed` with direct `-> cancelled` allowed from `planned|active`.

## 5. MCP Tool Surface Contract
All tools follow base request-context and session/audit rules from `docs/roles/base_butler.md`.

- `record_booking(payload: BookingPayload) -> RecordBookingResult`
  - Purpose: Parse and persist a booking/update email payload into trip container tables.
  - `BookingPayload`: provider, source message identifiers, inferred entity type (`leg|accommodation|reservation|document`), extracted structured fields, candidate trip hints.
  - `RecordBookingResult`: `trip_id`, `entity_type`, `entity_id`, `created` (bool), `deduped` (bool), `warnings[]`.

- `update_itinerary(trip_id: UUID, patch: ItineraryPatch, reason?: str) -> UpdateItineraryResult`
  - Purpose: Apply explicit itinerary corrections (time changes, cancellation flags, seat/gate changes, rebooked legs).
  - `ItineraryPatch`: per-entity patch operations with optimistic version/token and optional source event linkage.
  - `UpdateItineraryResult`: `trip_id`, `updated_entities[]`, `conflicts[]`, `new_trip_status`.

- `list_trips(status?: TripStatus, from_date?: date, to_date?: date, limit?: int, offset?: int) -> ListTripsResult`
  - Purpose: Query trip containers by lifecycle/date windows.
  - `TripStatus`: `planned|active|completed|cancelled`.
  - `ListTripsResult`: `items[]`, `total`, `limit`, `offset`.

- `trip_summary(trip_id: UUID, include_documents?: bool = true, include_timeline?: bool = true) -> TripSummaryResult`
  - Purpose: Return normalized trip timeline with legs, stays, reservations, and key document pointers.
  - `TripSummaryResult`: `trip`, `legs[]`, `accommodations[]`, `reservations[]`, `documents[]`, `timeline[]`, `alerts[]`.

- `upcoming_travel(within_days?: int = 14, include_pretrip_actions?: bool = true) -> UpcomingTravelResult`
  - Purpose: Return upcoming departures/check-ins plus urgency-ranked travel actions.
  - `UpcomingTravelResult`: `upcoming_trips[]`, `actions[]`, `window_start`, `window_end`.

- `add_document(trip_id: UUID, type: DocumentType, blob_ref: str, expiry_date?: date, metadata?: object) -> AddDocumentResult`
  - Purpose: Attach a travel document reference to an existing trip.
  - `DocumentType`: `boarding_pass|visa|insurance|receipt`.
  - `AddDocumentResult`: `document_id`, `trip_id`, `type`, `expiry_date`, `created_at`.

## 6. Switchboard Classification Signals
Travel routing heuristics SHOULD combine sender-domain, subject-line, and body-field signals. Matching any strong signal set should classify to `travel`; weak signals should be combined with confidence scoring.

Sender-domain examples:
- Airlines: `delta.com`, `united.com`, `aa.com`, `southwest.com`, `jetblue.com`
- Lodging: `booking.com`, `airbnb.com`, `marriott.com`, `hilton.com`
- OTA/aggregators: `expedia.com`, `kayak.com`, `tripadvisor.com`
- Ground/rail: `hertz.com`, `avis.com`, `amtrak.com`

Subject-pattern examples:
- `Booking confirmation`
- `Itinerary`
- `E-ticket`
- `Your trip to`
- `Reservation confirmed`
- `Check-in reminder`
- `Trip update`
- `Gate change`

Structured-body cues:
- Presence of PNR/record locator formats.
- Paired departure/arrival timestamps with airport/station codes.
- Confirmation-number labels near hotel/car/activity entities.
- Boarding-pass or e-ticket attachment hints.

## 7. Module Configuration Contract (`butler.toml`)
Travel Butler SHOULD enable calendar and memory modules by default.

```toml
[butler]
name = "travel"
port = 40106
description = "Travel itinerary and booking specialist for flights, hotels, car rentals, and trip logistics."

[butler.runtime]
model = "gpt-5.3-codex-spark"
max_concurrent_sessions = 3

[runtime]
type = "codex"

[butler.db]
name = "butlers"
schema = "travel"

[butler.switchboard]
url = "http://localhost:40100/mcp"
advertise = true
liveness_ttl_s = 300
route_contract_min = "route.v1"
route_contract_max = "route.v1"

[modules.email]

[modules.calendar]
provider = "google"
calendar_id = "primary"

[modules.calendar.conflicts]
policy = "suggest"

[modules.memory]

[[butler.schedule]]
name = "upcoming-travel-check"
cron = "0 8 * * *"
dispatch_mode = "job"
job_name = "upcoming_travel_check"

[[butler.schedule]]
name = "trip-document-expiry"
cron = "0 9 * * 1"
dispatch_mode = "job"
job_name = "trip_document_expiry"
```

Configuration notes:
- Calendar blocks itinerary windows and check-in reminders.
- Memory stores travel preferences (seat, airline/hotel preferences, visa constraints) and prior trip context for better extraction/ranking.
- Email module enables ingestion of booking confirmations, itinerary updates, and travel documents.

## 8. Scheduled Tasks

### 8.1 Upcoming Travel Check
- **Schedule:** Daily at 08:00 local time.
- **Job name:** `upcoming-travel-check`
- **Behavior:** Call `upcoming_travel(within_days=7, include_pretrip_actions=true)`; generate reminders for imminent departures/check-ins and unresolved pretrip actions (missing boarding pass, online check-in pending, unassigned seat).

### 8.2 Trip Document Expiry
- **Schedule:** Weekly on Monday at 09:00 local time.
- **Job name:** `trip-document-expiry`
- **Behavior:** Scan `travel.documents.expiry_date` windows (next 30/60/90 days) for visa/insurance/pass-related documents; emit actionable reminders with linked `trip_id` and document type.

## 9. Change Control Rules
- Any schema change to `travel.trips`, `travel.legs`, `travel.accommodations`, `travel.reservations`, or `travel.documents` MUST update this spec before implementation.
- New travel MCP tools MUST include explicit parameter and return contracts in section 5.
- Classification-signal changes that alter routing behavior MUST update section 6 with concrete examples.

## 10. Additional AI-Generated Ideas

The following ideas extend the normative spec above. They are non-normative proposals for consideration in future iterations.

### 10.1 Trip Intelligence
- **Automatic trip detection**: When multiple travel bookings arrive within a close date range for the same destination, auto-create a trip container and associate them without manual trip creation.
- **Layover intelligence**: Detect tight layovers in multi-leg journeys and surface warnings when connection time drops below airline minimum connect times.
- **Alternative routing suggestions**: When a flight is cancelled or significantly delayed, proactively suggest rebooking options based on known itinerary constraints and stored airline preferences.

### 10.2 Travel Preparation
- **Packing list generation**: Auto-generate destination-aware packing suggestions based on trip dates, weather forecasts, and planned activities stored in reservations.
- **Visa and entry requirement checks**: Surface visa and entry requirements based on destination country and stored passport nationality (via memory facts).
- **Time zone awareness**: Surface timezone differences and jet lag implications for multi-zone itineraries; suggest adjustment strategies for long-haul trips.
- **Pre-trip document checklist**: Auto-generate a checklist of required documents (passport, visa, insurance, boarding passes) based on destination and track completion status.

### 10.3 Trip Experience
- **Real-time flight tracking**: Poll flight status APIs for active trips to proactively surface delays, gate changes, and diversions before the user notices.
- **Local recommendations**: Surface dining, activity, and transportation recommendations near accommodation locations using destination metadata.
- **Emergency contact surface**: Store and surface embassy contacts, insurance hotline numbers, and local emergency numbers for the destination country.

### 10.4 Post-Trip
- **Trip journal prompts**: After trip completion, prompt user for reflection and store trip memories via memory module for future preference learning.
- **Trip cost summary**: Aggregate travel-related expenses by cross-referencing Finance Butler transaction records for the trip date range and destination merchants.
- **Loyalty program tracking**: Track airline miles, hotel points, and rental loyalty status across trips with optimization suggestions for status qualification.

### 10.5 Multi-Trip Intelligence
- **Travel pattern analysis**: Analyze historical trips to surface preferences (preferred airlines, seating, hotel chains) and seasonal travel patterns for proactive suggestions.
- **Frequent route optimization**: For recurring trips (e.g., quarterly business travel), suggest optimized booking windows based on historical pricing patterns.
- **Carbon footprint estimation**: Estimate and track travel carbon footprint per trip and over time based on transport type and distance.

### 10.6 Integration Enhancements
- **Calendar blocking**: Auto-create calendar events for flights, hotel check-ins/check-outs, and reservations with all relevant details (confirmation numbers, addresses, terminal info) in the event description.
- **Finance Butler cross-link**: Flag travel-related transactions in Finance Butler and link them to trip containers for per-trip expense tracking and post-trip cost summaries.
- **Itinerary sharing**: Generate shareable trip summary documents for travel companions or family members, with configurable detail level.

## 11. Non-Normative Note
This role is intentionally narrow: it models travel as correlated, time-sensitive operational data. General long-term reflections about trips (journaling, generic ideas, broad planning) can still be delegated to the General Butler via Switchboard when no structured itinerary container is required.
