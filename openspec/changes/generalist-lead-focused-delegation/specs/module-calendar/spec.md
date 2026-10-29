## MODIFIED Requirements

### Requirement: CalendarEvent Model

The canonical `CalendarEvent` model SHALL be provider-neutral with fields:
`event_id`, `title`, `start_at`, `end_at`, `timezone`, `all_day`,
`description`, `body`, `location`, `attendees` (list of `AttendeeInfo`),
`recurrence_rule`, `color_id`, `butler_generated`, `butler_name`,
`source_butler`, `source_goal_id`, `source_invocation_id`, the legacy
`source_session_id` (pre-cutover rows only), `entity_ids`, `status`, `organizer`,
`visibility`, `etag`, `created_at`, and `updated_at`.

- `all_day` is provider-authoritative boolean truth. Google `start.date` and
  `end.date` boundaries set it to `true`, and all-day writes SHALL preserve the
  same date-only representation.
- `source_goal_id` and `source_invocation_id` SHALL be taken only from the
  verified broker assertion of the writing call, never from call arguments or
  spawner context, and SHALL be null when the call carries no assertion.

Source: RFC 0038 D7, D17
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
- **THEN** the resulting event is annotated with `source_butler` (the butler's name) and with `source_goal_id` and `source_invocation_id` from the call's verified assertion
- **AND** these values are written to the `calendar_events` row in the projection table

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

## ADDED Requirements

### Requirement: Calendar Writes Reaching Non-Owner Parties Require Owner Approval

Every calendar capability whose call can invite, notify or share with a party
(`calendar_create_event` and `calendar_update_event` with `attendees`,
`calendar_update_event_instance`, `calendar_delete_event` and
`calendar_delete_event_instance` with updates sent, and `calendar_respond`) SHALL declare its
attendee and update-sending arguments as party arguments. Because the broker
cannot see attendees already stored on an event, the module's own approval
gate SHALL also resolve them: a call that would add, invite, update or notify
any party that does not resolve uniquely to the owner SHALL be dispatched only
under per-event owner approval or a matching standing rule the owner created,
whatever the goal's origin, grant or exposure and whatever the configured
notification policy. On a butler that loads the calendar module with no
approvals enqueuer wired, such a call SHALL always be refused with a typed
error that names the missing gate, because the broker parks effects only
through the owning domain's approvals module (RFC 0038 D13); a missing
approvals module can therefore never bypass this rule, and a lead that needs
to invite a non-owner uses a domain whose calendar is approval-gated; creating an event without an enqueuer (the
"Allow overlap with approval gate" behavior) applies only to calls that reach
no party other than the owner. A call that names only the owner, or that
changes an event without attendees other than the owner and sends no updates,
SHALL keep today's approval behavior.

ID: REQ-module-calendar-030
Source: RFC 0038 D4 and owner decision 9; `REQ-capability-authorization-002`, `REQ-capability-authorization-016`
Scope: v1-mandatory

#### Scenario: Creating an event with a friend as attendee is parked

- **WHEN** an unexposed owner goal calls `calendar_create_event` with an attendee who is a known contact other than the owner, and no standing rule matches
- **THEN** the call is parked for per-event owner approval showing the attendees, and no event is created and no invitation is sent until the owner approves

#### Scenario: A butler without an approvals module cannot invite a non-owner

- **WHEN** a goal calls `calendar_create_event` with a non-owner attendee on a butler whose calendar module has no approvals enqueuer wired
- **THEN** the module refuses the call with a typed error naming the missing gate, and no event is created and no invitation is sent

#### Scenario: Notifying existing attendees of a change is parked

- **WHEN** a goal updates or deletes an event that has attendees other than the owner and asks for updates to be sent
- **THEN** the call is parked for per-event owner approval before any update notification is sent

#### Scenario: A private event for the owner keeps today's behavior

- **WHEN** a goal creates an event whose only attendee is the owner, or that has no attendees
- **THEN** the non-owner rule does not apply and the event follows the module's existing conflict and approval policy
