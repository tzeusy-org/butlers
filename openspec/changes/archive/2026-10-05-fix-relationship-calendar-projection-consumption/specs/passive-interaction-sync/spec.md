## MODIFIED Requirements

### Requirement: Calendar-based interaction detection

The `interaction_sync` job SHALL also scan past calendar events for social
gatherings and log interactions with attendees who are known contacts.

ID: REQ-passive-interaction-sync-001
Source: Existing passive-interaction-sync calendar scenarios; RFC 0006 (accepted) schema isolation; [Observed] core_003_calendar.py and CalendarModule._upsert_projection_event; bu-2mpps3 bounded repair
Scope: v1-mandatory

#### Scenario: Detect past calendar events with attendees

- **WHEN** `interaction_sync` runs
- **THEN** it SHALL query `relationship.calendar_events` for events where:
  - `starts_at` is within the scan window
  - `status` = `'confirmed'`
  - The event has attendees in `metadata->'attendees'` (JSONB array)

#### Scenario: Resolve attendees to contacts

- **WHEN** a calendar event has attendees
- **THEN** for each attendee email, the job SHALL attempt to resolve it to an
  `entity_id` via `relationship.entity_facts` where `predicate = 'has-email'` and
  `LOWER(object) = LOWER(attendee_email)` (case-insensitive exact match)
- **AND** attendees who are the owner (identified via `public.entities.roles` containing `'owner'`)
  SHALL be excluded

#### Scenario: Calendar interaction fact creation

- **WHEN** an attendee email resolves to an entity_id
- **THEN** the job SHALL call `interaction_log()` with:
  - `entity_id` = the resolved entity UUID
  - `type` = `'calendar_event'`
  - `summary` = the event title (e.g., "Dinner at Mario's")
  - `occurred_at` = the event's `starts_at` timestamp
  - `direction` = `'mutual'`
  - `metadata` = `{"source": "interaction_sync", "event_id": "<uuid>", "event_title": "<title>"}`

#### Scenario: Declined events are excluded

- **WHEN** the owner's RSVP status on the event is `'declined'`
- **THEN** the job SHALL skip that event entirely

#### Scenario: Cancelled events are excluded

- **WHEN** a calendar event has `status = 'cancelled'`
- **THEN** the job SHALL skip that event

#### Scenario: Calendar projection query failure is visible

- **WHEN** the job cannot read `relationship.calendar_events` because the table is missing, a required column is missing, or access is denied
- **THEN** it SHALL increment the existing `errors` return counter and log the calendar query failure with its failure category
- **AND** it SHALL retain already-created message interactions and continue returning the existing job result shape
- **AND** a failed calendar read MUST NOT be represented as a successful empty calendar scan or an all-clear

### Requirement: Scan window and checkpoint

The job SHALL maintain a durable checkpoint to avoid re-scanning the full history
on every run.

ID: REQ-passive-interaction-sync-002
Source: Existing passive-interaction-sync Checkpoint persistence scenario and 30-day bound; RFC 0029 honest absence; bu-2mpps3 bounded failure/recovery correction
Scope: v1-mandatory

#### Scenario: Checkpoint persistence

- **WHEN** the job completes successfully
- **THEN** it SHALL store the scan window end time in the butler's state store
  under key `interaction_sync.last_scan_at`
- **AND** the next run SHALL use this as the scan window start time

#### Scenario: First run without checkpoint

- **WHEN** the job runs for the first time (no checkpoint exists)
- **THEN** it SHALL scan the last 30 days of messages and calendar events
  as a backfill window

#### Scenario: Scan window cap

- **WHEN** the checkpoint is older than 30 days (e.g., after a long outage)
- **THEN** the scan window start SHALL be capped at 30 days ago to prevent
  unbounded backfill

#### Scenario: Calendar query failure retains the checkpoint

- **WHEN** the message portion has completed but the calendar projection query fails
- **THEN** the job SHALL leave `interaction_sync.last_scan_at` unchanged so the next scheduled run can read the unprocessed calendar events in the original bounded window
- **AND** it SHALL preserve message interactions already created by that run
- **AND** after the calendar query becomes available, rerunning SHALL create the recovered calendar interactions and co-attended edges once without duplicating the previously-created message interactions
- **AND** the existing 30-day scan cap SHALL still apply

#### Scenario: A successful empty calendar read advances normally

- **WHEN** the calendar query succeeds and returns no qualifying events, and the job otherwise reaches checkpoint persistence
- **THEN** the job SHALL persist the scan window end normally rather than treating a genuine empty result as a failed read
