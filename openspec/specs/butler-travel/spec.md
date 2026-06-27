# Travel Butler Role

## Purpose
The Travel butler (port 41106) is a travel logistics and itinerary intelligence specialist for flights, hotels, car rentals, and trip planning.

## ADDED Requirements

### Requirement: Travel Butler Identity and Runtime
The travel butler manages trip lifecycle and booking data with structured container models.

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
The travel butler provides booking, itinerary, and document management tools.

#### Scenario: Tool inventory
- **WHEN** a runtime instance is spawned for the travel butler
- **THEN** it has access to: `record_booking`, `update_itinerary`, `list_trips`, `trip_summary`, `upcoming_travel`, `add_document`, and calendar tools

### Requirement: Trip Container Model
All travel data is organized under trip containers with strict status transitions.

#### Scenario: Trip lifecycle
- **WHEN** booking data is recorded
- **THEN** every leg, accommodation, reservation, and document is linked to a `trip_id`
- **AND** if no matching trip exists, one is created first before attaching the entity
- **AND** status transitions follow `planned -> active -> completed` (direct cancellation allowed from `planned` or `active`, but never backward)

### Requirement: Travel Butler Schedules
The travel butler runs upcoming travel checks, document expiry scans, and insight scans.

#### Scenario: Scheduled task inventory
- **WHEN** the travel butler daemon is running
- **THEN** it executes the domain scheduled tasks: `upcoming-travel-check` (0 0 * * *, which is 08:00 SGT since the scheduler interprets cron in UTC), `trip-document-expiry` (0 9 * * 1), and `insight-scan` (45 7 * * *, job: evaluate travel domain data and generate insight candidates)
- **AND** it also runs the cross-butler contribution jobs `daily_briefing_contribution` (55 6 * * *), `calendar_overlay_contribution` (50 6 * * *), and `calendar_prep_contribution` (56 6 * * *)

### Requirement: Travel Butler Skills
The travel butler has pre-trip checklist and trip planner skills.

#### Scenario: Skill inventory
- **WHEN** the travel butler operates
- **THEN** it has access to `pre-trip-checklist` (comprehensive pre-departure preparation covering documents, confirmations, logistics, packing, 24h final check) and `trip-planner` (new trip planning workflow covering destination, flights, accommodation, transport, documents, gap detection), plus shared skills `butler-memory` and `butler-notifications`

### Requirement: Travel Memory Taxonomy
The travel butler uses a travel-centric memory taxonomy with loyalty and preference predicates.

#### Scenario: Memory classification
- **WHEN** the travel butler extracts facts
- **THEN** it uses subjects like airline names, hotel chains, or "user"; predicates like `preferred_airline`, `preferred_seat`, `passport_expiry`, `frequent_flyer`, `known_airport`; permanence `stable` for passport info and loyalty numbers, `standard` for current trip context, `volatile` for real-time flight status

### Requirement: Travel Insight Scan Job
The travel butler's `insight-scan` job SHALL evaluate travel domain data and produce insight candidates covering pre-trip preparation, document expiry warnings, and cross-domain coordination hints. All candidates are submitted via the Switchboard's `propose_insight_candidate()` MCP tool — the butler does not write to `public.insight_candidates` directly.

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
- **AND** the user has active medications tracked by the health butler (queryable vithe `public` schema or known from memory facts)
- **THEN** it SHALL generate candidates reminding the user to ensure adequate medication supply for the trip duration
- **AND** priority SHALL be 75 for trips within 7 days, 55 for trips within 14 days
- **AND** the `dedup_key` SHALL be `travel:medication-prep:{trip-id}`
- **AND** `expires_at` SHALL be the departure date
- **AND** this insight SHALL only be generated if the trip duration exceeds 3 days

#### Scenario: No insights for past or completed trips
- **WHEN** the insight-scan job evaluates trips
- **THEN** it SHALL exclude trips with status `completed` or `cancelled`
- **AND** it SHALL exclude trips whose departure date is in the past
