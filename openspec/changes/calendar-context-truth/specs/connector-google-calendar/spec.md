## MODIFIED Requirements

### Requirement: Event Starting Soon Notifications
The connector SHALL synthesize time-triggered notifications for upcoming events.
- Starting-soon meeting-prep notifications SHALL exclude explicit `outOfOffice` and `workingLocation` events in both ordinary polling and restart recovery. Their ordinary created/updated/deleted ingestion SHALL remain preserved. Default-family and focus-time notifications retain existing lead-time, seen-set and idempotency behavior.

ID: REQ-connector-google-calendar-001
Source: RFC 0009; RFC 0020; vision.md shared situational awareness; approved bu-s11n0s.4 Outcome
Scope: v1-mandatory

#### Scenario: Lead time configuration
- **WHEN** the connector is configured
- **THEN** the lead time for "starting soon" notifications SHALL be configurable via `GCAL_STARTING_SOON_LEAD_MINUTES` (default 15 minutes)
- **AND** setting the lead time to 0 SHALL disable starting-soon notifications

#### Scenario: Starting soon detection
- **WHEN** the connector completes a sync cycle for an account
- **THEN** it SHALL scan upcoming events within the lead-time window
- **AND** for each eligible event entering the window for the first time, it SHALL emit an `event_starting_soon` ingest envelope

#### Scenario: Deduplication of starting soon notifications
- **WHEN** the connector considers emitting a starting-soon notification
- **THEN** it SHALL check an in-memory seen-set keyed by `(event_id, lead_time_minutes)`
- **AND** events already in the seen-set SHALL NOT trigger duplicate notifications
- **AND** the seen-set SHALL be pruned of past events periodically to prevent unbounded growth

#### Scenario: Missed notifications on restart
- **WHEN** the connector restarts
- **THEN** it SHALL check upcoming events within the lead-time window and emit starting-soon notifications for eligible events that have not yet started
- **AND** the Switchboard's deduplication layer provides additional protection against duplicates


#### Scenario: Status blocks do not trigger meeting preparation
- **WHEN** OOO and working-location events enter the lead window in normal polling or restart recovery
- **THEN** neither produces a starting-soon meeting-prep envelope
- **AND** a planted ordinary timed event still does and retains its established identity



### Requirement: ingest.v1 Field Mapping
The Google Calendar connector SHALL normalize every ingested calendar event
change to the `ingest.v1` envelope using exactly the field mappings defined
below.
- Google resource `eventType` SHALL remain distinct from connector change classification (`created`, `updated`, `deleted`, `starting_soon`). Ordinary ingestion SHALL preserve the existing full raw resource, including its eventType and workingLocationProperties, without widening routing, credentials, endpoint identity, normalized text, actor or idempotency authority.

ID: REQ-connector-google-calendar-002
Source: RFC 0009; RFC 0020; vision.md shared situational awareness; approved bu-s11n0s.4 Outcome
Scope: v1-mandatory

#### Scenario: Google Calendar event field mapping
- **WHEN** a Google Calendar event change is normalized to `ingest.v1`
- **THEN** the mapping SHALL be:
  - `source.channel` = `"google_calendar"`
  - `source.provider` = `"google_calendar"`
  - `source.endpoint_identity` = `"google_calendar:user:<email_address>"`
  - `event.external_event_id` = Google Calendar event ID
  - `event.external_thread_id` = Google Calendar event ID (events are their own thread)
  - `event.observed_at` = connector-observed timestamp (RFC3339)
  - `sender.identity` = event organizer email address (or the account email for self-created events)
  - `payload.raw` = full Google Calendar API event payload
  - `payload.normalized_text` = structured summary (see normalized text format)
  - `control.idempotency_key` = `"gcal:<endpoint_identity>:<event_id>:<updated_timestamp>"`
  - `control.ingestion_tier` = `"full"`
  - `control.policy_tier` = `"default"`

#### Scenario: Starting soon event field mapping
- **WHEN** an "event starting soon" notification is normalized to `ingest.v1`
- **THEN** the mapping SHALL follow the standard mapping with these overrides:
  - `event.external_event_id` = `"starting_soon:<event_id>"`
  - `control.idempotency_key` = `"gcal:<endpoint_identity>:starting_soon:<event_id>:<lead_minutes>"`
  - `control.policy_tier` = `"interactive"` (time-sensitive notification)

#### Scenario: Normalized text format
- **WHEN** `payload.normalized_text` is constructed
- **THEN** it SHALL contain a human-readable summary including: event type (`created`, `updated`, `deleted`, `starting_soon`), event title, start time, end time, location (if present), attendee count, and organizer
- **AND** the format SHALL be: `"[Calendar: <event_type>] <title> | <start> - <end> | <location> | <attendee_count> attendees | Organizer: <organizer>"`


#### Scenario: Resource type does not replace change identity
- **WHEN** an outOfOffice resource changes
- **THEN** ordinary ingestion retains the resource type in its existing raw payload
- **AND** the change kind, endpoint and dedup identity remain the established created/updated/deleted form
