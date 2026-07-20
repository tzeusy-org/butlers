## ADDED Requirements

### Requirement: Projection Provenance Truth and Source-Ledger Hygiene

The Calendar module SHALL preserve provider events in the workspace projection
while carrying durable provenance needed by downstream analysis. A Google
date-only event (both boundaries use the provider `date` form) SHALL project
with `all_day=true`. A legacy event with `all_day=false` whose duration is at
least 24 hours and whose boundaries are both local midnight in its valid stored
IANA timezone SHALL be recognized as a non-meeting by analysis consumers.

The module SHALL retain provider rows whose `metadata.butler_generated` value
is true in the workspace projection. It SHALL not delete, hide, or change the
provider-authoritative state of those rows merely because they are
butler-generated.

On startup, the module SHALL idempotently delete source-ledger rows with
exactly these source keys: `internal_scheduler:butler`,
`internal_scheduler:butlers`, and `internal_reminders:butlers`. The purge SHALL
use no wildcard or source-name policy and SHALL preserve normal source/event/
instance cascade semantics. Internal source registration SHALL reject an
invalid roster butler name without writing a source row, and SHALL continue to
register valid roster names. All of these projection paths SHALL retain their
existing fail-open behavior when projection tables are unavailable.

#### Scenario: Google date-only event projects as all-day

- **WHEN** a Google event payload has date-only `start.date` and `end.date`
- **THEN** the parsed provider event and its projected `calendar_events` row
  have `all_day=true`
- **AND** the original date boundaries and provider source remain preserved

#### Scenario: Butler-generated provider event remains visible

- **WHEN** a provider event carries `metadata.butler_generated=true`
- **THEN** it is upserted into the existing workspace projection with that
  provenance retained
- **AND** no source or event row is deleted or hidden because of the marker

#### Scenario: Only obsolete internal source keys are purged

- **WHEN** startup ledger hygiene runs with obsolete rows and a valid internal
  source row present
- **THEN** it deletes only `internal_scheduler:butler`,
  `internal_scheduler:butlers`, and `internal_reminders:butlers`
- **AND** the valid source row remains registered with its existing events and
  instances intact

#### Scenario: Invalid roster name cannot create an internal source

- **WHEN** internal source registration is requested for a butler name that is
  not present in the roster
- **THEN** no `calendar_sources` row is written
- **AND** a request for a valid roster name still performs the normal idempotent
  source upsert

## MODIFIED Requirements

### Requirement: CalendarEvent Model

The canonical `CalendarEvent` model SHALL be provider-neutral with fields:
`event_id`, `title`, `start_at`, `end_at`, `timezone`, `all_day`,
`description`, `body`, `location`, `attendees` (list of `AttendeeInfo`),
`recurrence_rule`, `color_id`, `butler_generated`, `butler_name`,
`source_butler`, `source_session_id`, `entity_ids`, `status`, `organizer`,
`visibility`, `etag`, `created_at`, and `updated_at`.

- `all_day` is provider-authoritative boolean truth. Google `start.date` and
  `end.date` boundaries set it to `true`, and all-day writes SHALL preserve the
  same date-only representation.

#### Scenario: Google date-only event preserves all-day truth

- **WHEN** a Google event payload has date-only `start.date` and `end.date`
  boundaries
- **THEN** its `CalendarEvent` has `all_day=true`
- **AND** a create or update carrying that truth serializes Google `start.date`
  and `end.date`, never `dateTime` boundaries

### Requirement: Reversible Mutation Pre-State Capture

The calendar module SHALL capture the pre-mutation event state of every
reversible user-lane mutation into the recorded `action_result` so an inverse
is reconstructable. For `workspace_user_update` and `workspace_user_delete`,
the captured pre-image MUST include `all_day` with the existing title, start,
end, timezone, recurrence, calendar, and linked-people fields. The dashboard
undo endpoint SHALL pass the nullable `all_day` truth through the inverse
`calendar_update_event` or `calendar_create_event` payload without an extra
provider read.

#### Scenario: All-day pre-state round-trips through undo

- **WHEN** an applied user-lane update or delete has a captured pre-state with
  `all_day=true` and the dashboard reverses it
- **THEN** the inverse `calendar_update_event` or `calendar_create_event` call
  carries `all_day=true` with the captured start and end boundaries
- **AND** Google receives `start.date` and `end.date`, with no `dateTime`
  boundary in the inverse write
- **AND** the existing `this`, `following`, and `series` recurrence-scope
  semantics remain unchanged
