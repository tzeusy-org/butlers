# Travel Butler Role

## Purpose
The Travel butler (port 40106) is a travel logistics and itinerary intelligence specialist for flights, hotels, car rentals, and trip planning.

## ADDED Requirements

### Requirement: Travel Butler Identity and Runtime
The travel butler manages trip lifecycle and booking data with structured container models.

#### Scenario: Identity and port
- **WHEN** the travel butler is running
- **THEN** it operates on port 40106 with description "Travel logistics and itinerary intelligence specialist for flights, hotels, car rentals, and trip planning."
- **AND** it uses the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema is `travel` within the consolidated `butlers` database

#### Scenario: Switchboard registration
- **WHEN** the travel butler starts
- **THEN** it registers with the switchboard at `http://localhost:40100/mcp` with `advertise = true`, `liveness_ttl_s = 300`, and route contract version range `route.v1` to `route.v1`

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
The travel butler runs upcoming travel checks and document expiry scans.

#### Scenario: Scheduled task inventory
- **WHEN** the travel butler daemon is running
- **THEN** it executes two native job schedules: `upcoming-travel-check` (0 8 * * *) and `trip-document-expiry` (0 9 * * 1)

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
