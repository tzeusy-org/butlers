# Travel Butler Role

## Purpose
The Travel butler (port 41106) is a travel logistics and itinerary intelligence specialist for flights, hotels, car rentals, and trip planning.

## Requirements

### Requirement: Travel Butler Identity and Runtime
The travel butler SHALL manage trip lifecycle and booking data with structured container models.

#### Scenario: Identity and port
- **WHEN** the travel butler is running
- **THEN** it operates on port 41106 with description "Travel logistics and itinerary intelligence specialist for flights, hotels, car rentals, and trip planning."
- **AND** it uses the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema is `travel` within the consolidated `butlers` database

#### Scenario: Switchboard registration
- **WHEN** the travel butler starts
- **THEN** it registers with the switchboard at `http://localhost:41100/mcp` with `advertise = true`, `liveness_ttl_s = 300`, and route contract version range `route.v1` to `route.v1`

#### Scenario: Module profile
- **WHEN** the travel butler starts
- **THEN** it loads modules: `email`, `calendar` (Google provider, suggest conflicts policy), and `memory`

### Requirement: Travel Butler Tool Surface
The travel butler SHALL provide booking, itinerary, document management, and narrowly scoped
cross-butler medication-preparation tools.

#### Scenario: Tool inventory
- **WHEN** a runtime instance is spawned for the travel butler
- **THEN** it has access to: `record_booking`, `update_itinerary`,
  `acknowledge_connection_risk`, `list_trips`, `trip_summary`, `upcoming_travel`,
  `add_document`, `health_medication_snapshot`, and calendar tools

### Requirement: Trip Container Model
All travel data SHALL be organized under trip containers with strict status transitions.

#### Scenario: Trip lifecycle
- **WHEN** booking data is recorded
- **THEN** every leg, accommodation, reservation, and document is linked to a `trip_id`
- **AND** if no matching trip exists, one is created first before attaching the entity
- **AND** status transitions follow `planned -> active -> completed` (direct cancellation allowed from `planned` or `active`, but never backward)

### Requirement: Booking Identity and Traveller Party
Flight booking ingestion SHALL converge a provider-scoped record locator into one trip and SHALL
represent the people travelling separately from the shared transport legs.

#### Scenario: Multi-passenger round trip converges
- **WHEN** the legs for one provider and record locator arrive in any order or concurrently
- **THEN** exactly one booking record and one trip container represent the booking
- **AND** each distinct segment index produces exactly one leg even when the segments share a confirmation number
- **AND** each traveller links to a canonical `public.entities` person when one is already resolved
- **AND** an unresolved booking name remains a stable local party member without creating shared identity
- **AND** each traveller-to-leg participation is represented once
- **AND** the trip date range widens to cover every attached segment
- **AND** the canonical record locator is projected through each leg's `pnr` field

#### Scenario: Booking writes preserve atomic identity and enrichment
- **WHEN** a provider-scoped leg contains a value that cannot be bound to the Travel schema
- **THEN** no booking record, trip, leg, traveller participation, or booking event is persisted
- **AND** re-ingesting a known segment with sparse data preserves its populated fields and operational timestamps
- **AND** explicit itinerary or operational changes use the dedicated update paths

#### Scenario: Existing fragmented bookings remain reviewable
- **WHEN** the journey-identity migration finds one normalized operating carrier and record locator across multiple legacy trips
- **THEN** its dry-run inventory reports the candidate trip and leg identifiers without merging or deleting data
- **AND** later ingestion reuses an existing candidate trip rather than creating another fragment

#### Scenario: Record locator identity is incomplete
- **WHEN** a flight booking has no record locator or no nonblank provider scope
- **THEN** Travel may use the existing date-and-destination heuristic
- **AND** the resulting trip carries `metadata.identity_confidence = "weak"` rather than implying confirmed identity

#### Scenario: Traveller identity becomes resolvable
- **WHEN** a name-keyed local traveller later resolves to a live canonical `public.entities` person
- **THEN** Travel promotes or merges the existing party member instead of creating a duplicate traveller or leg participation
- **AND** caller-supplied entity IDs are accepted only when they identify a live, unmerged person
- **AND** re-ingestion after a canonical entity merge repoints and deduplicates stale Travel traveller and leg-participation links

### Requirement: Journey Connection Integrity
Travel SHALL derive connection integrity from adjacent same-journey legs and SHALL never claim a
connection is safe without minimum-connect evidence.

#### Scenario: Connection verdict is exposed
- **WHEN** adjacent legs share a connecting airport within 24 hours
- **THEN** Travel stores and returns a connection verdict of `holds`, `tight`, `broken`, or `unknown`
- **AND** the response states the available minutes and evidence used for the verdict
- **AND** same-carrier and interline minimums are distinguished
- **AND** a negative available-minute gap remains represented as `broken` rather than being removed
- **AND** mutable operational timestamps cannot reorder structurally indexed segments or remove their connection
- **AND** legs without structural segment identity use chronological ordering as an explicit fallback
- **AND** when a structurally indexed same-record segment pair coexists with a cross-record or
  legacy leg, Travel preserves every leg and deterministically interleaves the combined itinerary
  by persisted segment time without violating the record-local segment order

#### Scenario: Minimum-connect evidence is unavailable
- **WHEN** no minimum-connect record exists for the connecting airport
- **THEN** the verdict is `unknown`
- **AND** Travel records an expected-signal gap
- **AND** it emits no alert that claims the connection is safe or broken

#### Scenario: Delay breaks and later restores a connection
- **WHEN** a flight-status estimate moves a leg and changes a connection from holding to broken
- **THEN** the leg's operational timestamps and `updated_at` move
- **AND** Travel raises one deduplicated connection-risk alert and one approval door for that transition
- **AND** a later recovery withdraws the still-pending door
- **AND** concurrent recomputes serialize verdict persistence with approval-door creation or withdrawal
- **AND** a non-accepted connection-risk insight candidate rolls back the verdict transition before
  any prepared action can be orphaned, so a later recompute retries the linked alert and door
- **AND** a failed required approval-door write rolls back the verdict transition so a later recompute retries it

#### Scenario: Journey has no connection
- **WHEN** a trip contains no adjacent connecting leg pair
- **THEN** `GET /api/travel/trips/{trip_id}` returns `connections: []`
- **AND** it returns `connection_reason: "no_connection_on_journey"`
- **AND** an empty derived table without a successful derivation marker returns `connection_reason: null`
- **AND** an unreadable stored connection is excluded and disclosed by ID instead of being reported as no connection

### Requirement: Travel Butler Schedules
The travel butler SHALL run upcoming travel checks, document expiry scans, and insight scans.

#### Scenario: Scheduled task inventory
- **WHEN** the travel butler daemon is running
- **THEN** it executes the domain scheduled tasks: `upcoming-travel-check` (0 0 * * *, which is 08:00 SGT since the scheduler interprets cron in UTC), `trip-document-expiry` (0 9 * * 1), and `insight-scan` (45 7 * * *, job: evaluate travel domain data and generate insight candidates)
- **AND** it also runs the cross-butler contribution jobs `daily_briefing_contribution` (55 6 * * *), `calendar_overlay_contribution` (50 6 * * *), and `calendar_prep_contribution` (56 6 * * *)

### Requirement: Travel Butler Skills
The travel butler SHALL have pre-trip checklist and trip planner skills.

#### Scenario: Skill inventory
- **WHEN** the travel butler operates
- **THEN** it has access to `pre-trip-checklist` (comprehensive pre-departure preparation covering documents, confirmations, logistics, packing, 24h final check) and `trip-planner` (new trip planning workflow covering destination, flights, accommodation, transport, documents, gap detection), plus shared skills `butler-memory` and `butler-notifications`

### Requirement: Travel Memory Taxonomy
The travel butler SHALL use a travel-centric memory taxonomy with loyalty and preference predicates.

#### Scenario: Memory classification
- **WHEN** the travel butler extracts facts
- **THEN** it uses subjects like airline names, hotel chains, or "user"; predicates like `preferred_airline`, `preferred_seat`, `passport_expiry`, `frequent_flyer`, `known_airport`; permanence `stable` for passport info and loyalty numbers, `standard` for current trip context, `volatile` for real-time flight status

### Requirement: Travel Insight Scan Job
The travel butler's `insight-scan` job SHALL evaluate travel domain data and produce insight candidates covering pre-trip preparation, document expiry warnings, and cross-domain coordination hints. All candidates are submitted via the Switchboard's `propose_insight_candidate()` MCP tool; the butler does not write to `public.insight_candidates` directly.

#### Scenario: Insight-scan job handler registration
- **WHEN** the travel butler starts
- **THEN** it SHALL register an `insight-scan` job handler that is invokable by the scheduler's `job` dispatch mode

#### Scenario: Candidate submission via Switchboard MCP
- **WHEN** the `insight-scan` job generates a candidate
- **THEN** it SHALL submit the candidate by calling the Switchboard's `propose_insight_candidate()` MCP tool
- **AND** if the tool returns `{"status": "filtered"}`, the butler SHALL skip remaining candidates (verbosity is off)
- **AND** if the tool returns `{"status": "error"}`, the butler SHALL log the error and continue with remaining candidates

#### Scenario: Pre-trip preparation insights
- **WHEN** the insight-scan job evaluates upcoming trips with status `planned`
- **THEN** it SHALL generate candidates for trips departing within 7 days
- **AND** trips departing within 1 day SHALL have priority 92 (time-critical)
- **AND** trips departing within 3 days SHALL have priority 78
- **AND** trips departing within 7 days SHALL have priority 65
- **AND** the `dedup_key` SHALL be `travel:pre-trip:{trip-id}:{departure-date}`
- **AND** `expires_at` SHALL be the departure date
- **AND** the message SHALL reference the destination and suggest reviewing the pre-trip checklist

#### Scenario: Document expiry insights
- **WHEN** the insight-scan job evaluates travel documents (passports, visas, travel insurance)
- **THEN** it SHALL generate candidates for documents expiring within 90 days
- **AND** documents expiring within 30 days SHALL have priority 85
- **AND** documents expiring within 60 days SHALL have priority 65
- **AND** documents expiring within 90 days SHALL have priority 45
- **AND** the `dedup_key` SHALL be `travel:document-expiry:{document-type}:{expiry-date}`
- **AND** `expires_at` SHALL be 14 days from generation (re-check periodically)
- **AND** `cooldown_days` SHALL be 14 for 90-day warnings, 7 for 60-day, 3 for 30-day

#### Scenario: Medication prep for travel insights
- **WHEN** the insight-scan job evaluates upcoming trips
- **AND** the user has active medications owned by the Health butler
- **THEN** Travel SHALL obtain the active medication snapshot through its
  `health_medication_snapshot` MCP tool, which routes through the Switchboard to Health's
  `medication_travel_snapshot` MCP tool
- **AND** Travel MUST NOT query the Health schema or import Health implementation code
- **AND** it SHALL generate candidates reminding the user to ensure adequate medication supply for the trip duration
- **AND** priority SHALL be 75 for trips within 7 days, 55 for trips within 14 days
- **AND** the `dedup_key` SHALL be `travel:medication-prep:{trip-id}`
- **AND** `expires_at` SHALL be the departure date
- **AND** this insight SHALL only be generated if the trip duration exceeds 3 days

#### Scenario: No insights for past or completed trips
- **WHEN** the insight-scan job evaluates trips
- **THEN** it SHALL exclude trips with status `completed` or `cancelled`
- **AND** it SHALL exclude trips whose departure date is in the past

### Requirement: Travel Health Medication Snapshot MCP Consumer
The Travel butler SHALL expose a parameterless `health_medication_snapshot` MCP tool that retrieves
the minimum active medication preparation view through the Switchboard. The tool SHALL NOT accept a
caller-selected target butler, tool name, scope, or raw query.

#### Scenario: Authorized request routes through Switchboard and Health MCP
- **WHEN** Travel is allowed the `cross_butler` permission and calls
  `health_medication_snapshot`
- **THEN** it SHALL call the connected Switchboard client's `route` MCP tool with
  `target_butler = "health"`, `tool_name = "medication_travel_snapshot"`, and
  `source_butler = "travel"`
- **AND** it SHALL validate the returned `health.medication-travel.v1` envelope strictly before
  returning it
- **AND** it MUST NOT query the Health schema directly

#### Scenario: Permission revocation returns a typed denial
- **WHEN** Travel's `cross_butler` permission is explicitly revoked
- **THEN** `health_medication_snapshot` SHALL return `status = "error"`, an empty medication list,
  and error code `permission_denied` with `retryable = false`
- **AND** it SHALL NOT call the Switchboard

#### Scenario: Provider unavailability is not an empty success
- **WHEN** the Switchboard client is missing, the route times out, Health is unavailable, or the
  routed MCP call fails
- **THEN** `health_medication_snapshot` SHALL return `status = "error"` and an empty medication list
- **AND** the error SHALL distinguish `switchboard_unavailable` from `health_unavailable`
- **AND** the error SHALL have `retryable = true`

#### Scenario: Malformed provider response fails closed
- **WHEN** Health returns an envelope with an unknown version, missing required field, wrong field
  type, or extra field, or the Switchboard response lacks the serialized `result.data`
  `CallToolResult` shape
- **THEN** Travel SHALL return `status = "error"`, error code `invalid_health_response`, and an empty
  medication list
- **AND** it SHALL NOT pass the malformed or extra data to its caller

#### Scenario: Successful empty response remains successful
- **WHEN** Health returns a valid `status = "ok"` envelope with `medications = []`
- **THEN** Travel SHALL return that successful empty envelope without converting it to an error

### Requirement: Upcoming Travel Degraded Disclosure

`GET /api/travel/upcoming` and its dashboard KPI strip consumer SHALL NOT
render a healthy-looking empty or zero result when the upstream query fails
or when an individual trip's row cannot be normalized. A source failure or a
per-trip normalization failure SHALL be disclosed by name rather than
suppressed.

#### Scenario: Upstream query failure never renders as an all-clear zero

- **WHEN** the query backing `GET /api/travel/upcoming` fails or is
  unreachable
- **THEN** the KPI strip SHALL render "unavailable" (never a numeral, never
  `0`) for next departure, active trips, planned trips, and open actions
- **AND** the KPI strip SHALL show a degraded-source notice naming the
  endpoint, with a retry action

#### Scenario: One unreadable trip is excluded and disclosed, not silently dropped

- **WHEN** `GET /api/travel/upcoming` otherwise succeeds but one trip's
  nested legs or accommodations cannot be normalized
- **THEN** that trip SHALL be excluded from `upcoming_trips` and its id SHALL
  appear in the response's `unreadable_trip_ids` list
- **AND** the remaining trips SHALL still render normally
- **AND** the KPI strip SHALL disclose the number of excluded trips rather
  than silently undercounting
