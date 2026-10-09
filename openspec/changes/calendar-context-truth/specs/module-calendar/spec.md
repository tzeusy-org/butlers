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
- The canonical event SHALL additionally expose `event_type` (non-empty provider type, default `default`) and nullable typed `working_location`. Working location SHALL retain only its discriminant (`homeOffice`, `officeLocation`, `customLocation`) and a non-empty optional office/custom label. The fields are read-side truth; this change SHALL NOT expose typed-event creation, alteration or Google write-back.

ID: REQ-module-calendar-001
Source: RFC 0009; RFC 0020; vision.md shared situational awareness; approved bu-s11n0s.4 Outcome
Scope: v1-mandatory

#### Scenario: Google date-only event preserves all-day truth

- **WHEN** a Google event payload has date-only `start.date` and `end.date`
  boundaries
- **THEN** its `CalendarEvent` has `all_day=true`
- **AND** a create or update carrying that truth serializes Google `start.date`
  and `end.date`, never `dateTime` boundaries

#### Scenario: Google event parsing

- **WHEN** a Google Calendar API event payload is received
- **THEN** it is parsed into a `CalendarEvent`
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
- **THEN** the junction table `calendar_event_entities` is updated
- **AND** existing entity links for the event are replaced with the new set (full replace, not additive)

#### Scenario: Explicitly clear every entity association

- **WHEN** `calendar_update_event` is called with `entity_ids=[]` and `clear_entity_ids=true`
- **THEN** every existing `calendar_event_entities` row for that event is deleted without attempting an empty insert
- **AND** the eager projection write-through carries the same explicit-clear signal so the workspace reflects the removal before the next provider sync
- **AND** `clear_entity_ids=true` with an omitted or non-empty `entity_ids` value is rejected as ambiguous
- **AND** an omitted `entity_ids`, or an empty list without `clear_entity_ids=true`, remains a no-op that preserves existing links

#### Scenario: Entity association on read

- **WHEN** an event is returned from `calendar_get_event`, `calendar_list_events`, or any projection read path
- **THEN** the event's `entity_ids` field is populated from `calendar_event_entities`


#### Scenario: Typed provider truth reaches canonical reads
- **WHEN** a Google payload declares focusTime, outOfOffice or workingLocation
- **THEN** list/get normalization retains the exact recognized event type
- **AND** a location retains only its matching discriminant and display label


#### Scenario: Old and unknown event types remain compatible
- **WHEN** a provider payload omits eventType or carries a future unknown string
- **THEN** missing/malformed type resolves to default and a bounded unknown string remains recorded
- **AND** unknown behavior follows the ordinary default-family path



### Requirement: Projection Provenance Truth and Source-Ledger Hygiene
The Calendar module SHALL preserve provider events in the workspace projection
while carrying durable provenance needed by downstream analysis. A Google
date-only event (both boundaries use the provider `date` form) SHALL project
with `all_day=true`. A legacy event with `all_day=false` whose duration is at
least 24 hours and whose boundaries are both local midnight in its valid stored
IANA timezone SHALL be recognized as a non-meeting by analysis consumers.
- The module SHALL retain provider rows whose `metadata.butler_generated` value
is true in the workspace projection. It SHALL not delete, hide, or change the
provider-authoritative state of those rows merely because they are
butler-generated.
- On startup, the module SHALL idempotently delete source-ledger rows with
exactly these source keys: `internal_scheduler:butler`,
`internal_scheduler:butlers`, and `internal_reminders:butlers`. The purge SHALL
use no wildcard or source-name policy and SHALL preserve normal source/event/
instance cascade semantics. Internal source registration SHALL reject an
invalid roster butler name without writing a source row, and SHALL continue to
register valid roster names. All of these projection paths SHALL retain their
existing fail-open behavior when projection tables are unavailable.
- Provider projection SHALL persist normalized `event_type` and nullable `working_location` in the existing owning calendar event row. Updates SHALL replace them from the current provider payload so a removed location cannot survive as stale truth. Existing source/event/instance keys, generated-row visibility, entity-link preservation, stale-instance pruning, status, authorship, date-only truth and fail-open projection behavior SHALL remain intact.

ID: REQ-module-calendar-002
Source: RFC 0009; RFC 0020; vision.md shared situational awareness; approved bu-s11n0s.4 Outcome
Scope: v1-mandatory

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


#### Scenario: Typed projection survives independent readback
- **WHEN** a normalized OOO, focus or working-location event is projected
- **THEN** a separate read of the owning calendar row observes the exact event type and minimized location
- **AND** ordinary, generated and date-only positive rows remain visible


#### Scenario: Removed location does not remain projected
- **WHEN** the provider updates a location event without valid location properties
- **THEN** the canonical nullable location and projected column become null
- **AND** no label is inferred from title, physical location or building identifiers
