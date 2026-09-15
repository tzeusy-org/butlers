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

#### Scenario: Google event parsing

- **WHEN** a Google Calendar API event payload is received
- **THEN** it is parsed into a `CalendarEvent` via `_google_event_to_calendar_event`
- **AND** cancelled events return `None`
- **AND** attendees are parsed into `AttendeeInfo` objects with email, display_name, response_status, optional, organizer, self_, and comment fields
- **AND** recurrence rules are extracted from the `recurrence` array
- **AND** butler-generated metadata is extracted from `extendedProperties.private`
- **AND** `description` field is mapped to `body` on the model
- **AND** date-only `start.date` and `end.date` boundaries set `all_day=true`

#### Scenario: Authorship annotation on create

- **WHEN** `calendar_create_event` or `calendar_update_event` is called
- **THEN** the resulting event is annotated with `source_butler` (the butler's name) and `source_session_id` (the current runtime session ID)
- **AND** both values are written to the `calendar_events` row in the projection table

#### Scenario: Entity association on create and update

- **WHEN** `calendar_create_event`, `calendar_update_event`, or `calendar_update_butler_event` is called with a non-empty `entity_ids` set
- **THEN** the junction table `calendar_event_entities` is updated via `_upsert_event_entities`
- **AND** existing entity links for the event are replaced with the new set (full replace, not additive)

#### Scenario: Explicitly clear every entity association

- **WHEN** `calendar_update_event` is called with `entity_ids=[]` and `clear_entity_ids=true`
- **THEN** `_upsert_event_entities` deletes every existing `calendar_event_entities` row for that event without attempting an empty insert
- **AND** the eager projection write-through carries the same explicit-clear signal so the workspace reflects the removal before the next provider sync
- **AND** `clear_entity_ids=true` with an omitted or non-empty `entity_ids` value is rejected as ambiguous
- **AND** an omitted `entity_ids`, or an empty list without `clear_entity_ids=true`, remains a no-op that preserves existing links

#### Scenario: Entity association on read

- **WHEN** an event is returned from `calendar_get_event`, `calendar_list_events`, or any projection read path
- **THEN** the event's `entity_ids` field is populated from `calendar_event_entities` via `_fetch_event_entity_ids`
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

#### Scenario: Update captures the pre-mutation event state

- **WHEN** `calendar_update_event` resolves an existing event and applies a patch
- **THEN** the finalized `action_result` for the `workspace_user_update` row
  includes the pre-mutation event state (at least title, start_at, end_at,
  timezone, `all_day`, location, description, attendees, recurrence_rule, the
  resolved calendar id, and `entity_ids`) under a stable key, alongside the
  existing post-mutation outcome
- **AND** the pre-state reuses the `existing_event` already fetched before the
  PATCH and reads local `entity_ids` from the projection when available, adding
  no extra provider round-trip

#### Scenario: Delete captures the pre-deletion event state

- **WHEN** `calendar_delete_event` removes an existing event
- **THEN** the finalized `action_result` for the `workspace_user_delete` row
  includes the pre-deletion event state (the fields needed to recreate the event)
  under the same stable key
- **AND** the captured pre-image is sufficient for an inverse
  `calendar_create_event` to recreate the event and its linked people on its home
  calendar

#### Scenario: Pre-state is absent for non-reversible or non-applied outcomes

- **WHEN** a mutation finalizes with status `failed` or `noop` (e.g. the target
  event was not found), or the mutation is a create (which has no pre-image)
- **THEN** no pre-mutation state is required in `action_result`
- **AND** the undo endpoint treats the absence of pre-state on an otherwise
  reversible action as a fail-fast condition (it does not guess an inverse)

#### Scenario: Idempotent-replay path is unchanged by capture

- **WHEN** a mutation is replayed under the same `request_id` and resolves via
  `_load_projection_action` / `_prepare_workspace_mutation`
- **THEN** the existing replay behavior is preserved (the prior `action_result` is
  returned with `idempotent_replay=true`)
- **AND** capturing pre-state does not alter the `idempotency_key`, the action
  status transitions, or the replay contract
