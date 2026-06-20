## ADDED Requirements

### Requirement: Calendar Workspace Single-Entry Lookup

The dashboard API SHALL expose `GET /api/calendar/workspace/entries/{entry_id}`,
a read-only single-entry lookup that resolves `entry_id` against the indexed
`calendar_event_instances.id` and returns the `UnifiedCalendarEntry`-shaped
record (including `source_butler` / `source_session_id` provenance) in the
standard `ApiResponse` envelope. The lookup fans out only across
`butlers_with_module('calendar')` and performs no mutation and no migration.

#### Scenario: Existing entry returned

- **WHEN** `GET /api/calendar/workspace/entries/{entry_id}` is called with an
  `entry_id` that maps to a known `calendar_event_instances.id`
- **THEN** the full `UnifiedCalendarEntry` for that instance is returned in the
  `ApiResponse` envelope, including its `source_butler`/`source_session_id`
  provenance fields

#### Scenario: Unknown entry id

- **WHEN** the requested `entry_id` does not resolve to any instance in a
  calendar-module schema
- **THEN** the endpoint returns HTTP 404 with `{"status": "not_found", "entry": null}`
  rather than an empty 200 or a 500

### Requirement: Calendar Butler-Event Recurrence Preview

The dashboard API SHALL expose `POST /api/calendar/workspace/butler-events/preview`,
which dry-runs the existing `dateutil` RRULE / `croniter` expansion for a draft
butler event and returns the projected occurrence datetimes over the existing
90-day projection window, applying the existing "+N more" capping sentinel. The
endpoint MUST persist nothing and MUST NOT spawn an LLM session.

#### Scenario: Preview of a valid recurrence

- **WHEN** `POST /api/calendar/workspace/butler-events/preview` is called with a
  valid `rrule` (or `cron`) draft and optional `until_at`/`timezone`
- **THEN** the projected occurrence datetimes within the 90-day window are
  returned with the "+N more" capping sentinel when the count exceeds the cap
- **AND** no row is written to any calendar table and no event is created

#### Scenario: Lossy conversion surfaced

- **WHEN** the draft uses a recurrence construct that the engine cannot represent
  exactly (e.g. a weekly `BYDAY` that degrades)
- **THEN** the projected dates are still returned **AND** the response `notes`
  field records the lossy conversion so the user is warned before saving

#### Scenario: Invalid recurrence fails fast

- **WHEN** the draft contains an unparseable `rrule` or `cron` expression
- **THEN** the endpoint returns HTTP 422 carrying the parse error detail and
  persists nothing, rather than returning a partial or empty success

### Requirement: Calendar Reminder Dismiss and Snooze

The workspace butler-events mutation surface SHALL accept the `action` values
`dismiss` and `snooze` for due reminders and butler events. `dismiss` SHALL
dispatch the existing `reminder_dismiss` MCP tool; `snooze` SHALL update the
reminder/butler-event `due_at` via the existing butler-event update path. Neither
action introduces a new table or a new MCP tool, and both SHALL preserve the
existing soft-mutation response envelope (`status` / `persisted`).

#### Scenario: Dismiss a due reminder

- **WHEN** the butler-events mutation endpoint is called with `action="dismiss"`
  for a known reminder/butler-event id
- **THEN** the existing `reminder_dismiss` tool is dispatched and the reminder is
  marked dismissed, returned in the soft-mutation envelope

#### Scenario: Snooze moves the due time

- **WHEN** the endpoint is called with `action="snooze"` and a new `due_at` for a
  known id
- **THEN** the reminder/butler-event `due_at` is updated via the existing update
  path and the change is returned in the soft-mutation envelope

#### Scenario: Unknown target id

- **WHEN** `dismiss` or `snooze` targets an id that does not exist
- **THEN** the endpoint returns HTTP 404 rather than silently succeeding
