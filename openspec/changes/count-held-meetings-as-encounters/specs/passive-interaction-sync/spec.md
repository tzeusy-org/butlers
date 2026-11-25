## MODIFIED Requirements

### Requirement: Calendar-based interaction detection

The `interaction_sync` job SHALL also scan past calendar events for social
gatherings and log an interaction for each attendee who may have been met. INTERIM until the
encounter ledger exists (bu-5ii4gj.3): an occurrence counts when the owner accepted it and it has
started; the attendees are read from the occurrence's participant records
(REQ-module-calendar-043), never from the invitation's addresses.

ID: REQ-passive-interaction-sync-001
Source: bu-5ii4gj.3 slice E1; existing passive-interaction-sync calendar scenarios; RFC 0006 (accepted) schema isolation; [Observed] core_003_calendar.py and CalendarModule._upsert_projection_event; bu-2mpps3 bounded repair
Scope: v1-mandatory

#### Scenario: Detect past calendar events with attendees

- **WHEN** `interaction_sync` runs
- **THEN** it SHALL query `relationship.calendar_events` for events where:
  - `starts_at` is within the scan window
  - `status` = `'confirmed'`
  - The event has attendees in `metadata->'attendees'` (JSONB array)

#### Scenario: Resolve attendees to contacts

- **WHEN** a calendar event has attendees
- **THEN** the job SHALL read the event's `calendar_event_participants` rows at the current resolver version and SHALL NOT resolve the invitation's addresses itself
- **AND** a participant counts only when its resolution is `exact` or `manual`, its role is not `resource` and its response status is not `declined`
- **AND** the owner's own row, an `ambiguous` or `unresolved` address and a resource SHALL NOT be logged (the first two are counted in `skipped_owner` and `skipped_unresolved`)

#### Scenario: An occurrence counts only when the owner accepted it

- **WHEN** the owner's own participant row does not say `accepted` (declined, tentative, unanswered or absent)
- **THEN** the job SHALL log no interaction and mint no co-attended edge for that event
- **AND** it SHALL count the event in `calendar_events_not_encounters`
- **AND** this is an interim stand-in for "the meeting happened" that the encounter ledger replaces

#### Scenario: Missing participants are never a fallback

- **WHEN** a provider event has no participant rows, or its rows are not current for its attendee list or the resolver version
- **THEN** the job SHALL log nothing for it, SHALL NOT fall back to the invitation's addresses and SHALL NOT advance the checkpoint past it
- **AND** a failed participant read SHALL increment `errors` and hold the checkpoint, and SHALL NOT read as an empty calendar

#### Scenario: Calendar interaction fact creation

- **WHEN** a participant counts
- **THEN** the job SHALL call `interaction_log()` with:
  - `entity_id` = the participant's entity UUID
  - `type` = `'calendar_event'`
  - `summary` = the event title (e.g., "Dinner at Mario's")
  - `occurred_at` = the event's `starts_at` timestamp
  - `direction` = `'mutual'`
  - `metadata` = `{"source": "interaction_sync", "event_id": "<uuid>", "event_title": "<title>"}`
- **AND** existing `calendar_event` interactions SHALL NOT be rewritten or retracted by this change

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

### Requirement: Job return stats

The `interaction_sync` job SHALL return the stats described below. Because
messages with `interaction_eligible = 'false'` are filtered before grouping and
stats aggregation, the retained `skipped_ineligible` compatibility field SHALL
be zero and does not count those filtered rows.

ID: REQ-passive-interaction-sync-003
Source: bu-5ii4gj.3 slice E1 (backfills the ID onto the existing requirement)
Scope: v1-mandatory

#### Scenario: Return value

- **WHEN** the job completes
- **THEN** it SHALL return a dict containing:
  - `scan_window_start` (ISO8601 string)
  - `scan_window_end` (ISO8601 string)
  - `processed` (int) — total sender-channel-date combinations examined
  - `logged` (int) — interaction facts created (incoming + outgoing combined)
  - `skipped_unresolved` (int) — senders not found in `relationship.entity_facts`, and calendar attendees whose participant record is `ambiguous` or `unresolved`
  - `skipped_owner` (int) — owner's own sender or participant entries excluded
  - `skipped_ineligible` (int) — always zero; ineligible messages are filtered
    before stats aggregation
  - `skipped_group_too_large` (int) — chat groups exceeding the 20-participant gate
  - `calendar_events_scanned` (int)
  - `calendar_events_not_encounters` (int) — scanned occurrences that counted for nothing: the owner did not accept them, or their participants are not resolved yet
  - `errors` (int) — checkpoint I/O failures and other non-fatal errors
  - `resolution_degraded` (bool) — `true` when a non-empty set of sender identities resolved to zero entities, which the fail-open resolver cannot distinguish from a genuine "no known contacts" result; callers MUST NOT read a zero-fact run as an all-clear while this is set
  - `co_attended_edges_minted` (int), count of co-attendance relationship edges created from shared calendar events
  - `knows_edges_minted` (int), count of "knows" relationship edges minted during the sync
