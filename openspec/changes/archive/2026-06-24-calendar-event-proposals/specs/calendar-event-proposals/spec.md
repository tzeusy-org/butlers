# Calendar Event Proposals

## Purpose

The Calendar Event Proposals capability lets a butler stage an **inferred**
calendar event for human confirmation instead of silently writing it. Ingestion
handlers emit proposals from incoming signals (email, telegram, finance); the
calendar workspace renders them as a third `proposals` lane; the user accepts,
edits, or dismisses each one inline, and accepted proposals route through
`calendar_create_butler_event` onto the dedicated Butlers subcalendar — never the
user's real calendar by inference alone.

## ADDED Requirements

### Requirement: [TARGET-STATE] Calendar Event Proposals Store

The capability SHALL persist butler-inferred event proposals in a new per-schema
`calendar_event_proposals` table that holds an event-shaped payload plus
proposal-specific provenance and lifecycle state. It SHALL NOT reuse
`autonomy_suggestions` (which promotes auto-approve RULES) nor `pending_actions`
(which gates an already-decided butler tool call).

#### Scenario: Proposal row shape

- **WHEN** a proposal is persisted
- **THEN** the `calendar_event_proposals` row carries an event-shaped payload (`title`, `start_at`, `end_at`, `timezone`, `body`, `location`)
- **AND** it carries proposal provenance: `source_event_id` (the originating `public.ingestion_events.id`), `source_snippet` (the human-readable excerpt that triggered the inference), `confidence` (0.0-1.0), and `entity_ids` (resolved participant entities)
- **AND** it carries a lifecycle `status` of `"pending"`, `"accepted"`, or `"dismissed"`, defaulting to `"pending"`, plus a nullable `accepted_event_id`

#### Scenario: Proposals are not autonomy suggestions

- **WHEN** the capability needs to stage an inferred event for confirmation
- **THEN** it writes a `calendar_event_proposals` row, NOT an `autonomy_suggestions` row
- **AND** accepting a proposal creates exactly one event and does NOT widen any auto-approve allowlist
- **BECAUSE** `autonomy_suggestions` promotes a recurring tool-call pattern into an auto-approve RULE (`_generate_scope_description` yields `"Auto-approve <tool> when ..."`), which is a policy change, not an event

#### Scenario: Proposals are not pending actions

- **WHEN** the capability stages an inferred event
- **THEN** it does NOT create a `pending_actions` row
- **BECAUSE** `pending_actions` gates a specific already-decided butler tool call awaiting approval, whereas a proposal is an editable recommendation the butler has deliberately NOT executed and that the user may dismiss as a first-class outcome

### Requirement: [TARGET-STATE] calendar_propose_event Producer

The capability SHALL expose a programmatic `calendar_propose_event` producer that
ingestion handlers call to stage an inferred event. The producer SHALL insert a
`"pending"` proposal and SHALL NOT perform any provider (Google Calendar) write.

#### Scenario: Producer stages a pending proposal

- **WHEN** an ingestion handler calls `calendar_propose_event` with an event-shaped payload, `source_event_id`, `source_snippet`, `confidence`, and `entity_ids`
- **THEN** a `calendar_event_proposals` row is inserted with `status="pending"`
- **AND** no event is created on the provider
- **AND** the new proposal's id is returned

#### Scenario: Producer is idempotent on the originating ingestion event

- **WHEN** `calendar_propose_event` is called twice with the same `source_event_id`
- **THEN** the second call does NOT create a duplicate row and does NOT raise
- **AND** it returns the id of the existing proposal
- **BECAUSE** one originating ingestion signal should yield at most one proposal

### Requirement: [TARGET-STATE] Proposals Workspace Projection View

The capability SHALL extend the calendar workspace read endpoint so
`GET /api/calendar/workspace?view=proposals` projects pending proposals into the
unified entry shape, tagged with a new `source_type` value `"proposed_event"`.

#### Scenario: Proposals view returns pending proposals

- **WHEN** `GET /api/calendar/workspace?view=proposals` is called with a `start`/`end` range
- **THEN** each `calendar_event_proposals` row with `status="pending"` whose `start_at` falls in the range is returned as a unified entry with `source_type="proposed_event"`
- **AND** the entry is non-editable in place (`editable=false`)
- **AND** the entry's `metadata` carries `confidence`, `source_snippet`, and the `source_event_id` provenance link
- **AND** accepted and dismissed proposals are excluded

#### Scenario: Proposals view is fail-open

- **WHEN** the `calendar_event_proposals` table is absent (calendar module disabled or pre-migration) or the projection query fails
- **THEN** the endpoint returns an empty entries list rather than HTTP 500
- **AND** the failure is logged

### Requirement: [TARGET-STATE] Accept and Dismiss Proposal Endpoints

The capability SHALL expose endpoints to accept or dismiss a proposal. Accept
SHALL route the proposal's payload through `calendar_create_butler_event` onto the
dedicated Butlers subcalendar; dismiss SHALL discard the proposal without any
provider write. Both SHALL be idempotent on the proposal's current status.

#### Scenario: Accept creates a butler event on the Butlers subcalendar

- **WHEN** `POST /api/calendar/workspace/proposals/{id}/accept` is called for a pending proposal
- **THEN** the stored payload (with any inline overrides from the request body) is passed to `calendar_create_butler_event`, which creates the event on the dedicated Butlers subcalendar (NOT the user's primary calendar)
- **AND** the proposal row is set to `status="accepted"` with `accepted_event_id` recording the created event
- **AND** the action is recorded in the audit log

#### Scenario: Accept preserves the proposal's description and location

- **WHEN** a pending proposal carries a `description` and/or `location` and accept is called
- **THEN** both fields are forwarded to `calendar_create_butler_event` (which accepts `description` and `location` parameters), are stored on the underlying scheduler/reminder row, surface on the workspace projection, and are pushed to the Butlers subcalendar event
- **AND** an inline `description`/`location` override in the request body takes precedence over the stored value
- **BECAUSE** the create tool previously had no `description`/`location` parameters, so accepting a proposal silently dropped both (bu-cb0ap)

#### Scenario: Dismiss discards without a provider write

- **WHEN** `POST /api/calendar/workspace/proposals/{id}/dismiss` is called for a pending proposal
- **THEN** the proposal row is set to `status="dismissed"`
- **AND** no event is created on the provider
- **AND** the action is recorded in the audit log

#### Scenario: Accept is idempotent

- **WHEN** accept is called for a proposal already in `status="accepted"`
- **THEN** the existing `accepted_event_id` is returned with no second provider write

#### Scenario: Accept fails closed on provider error

- **WHEN** accept is called and the underlying `calendar_create_butler_event` call fails
- **THEN** a structured error is surfaced
- **AND** the proposal row remains `status="pending"` (it is NOT flipped to `accepted`, and no `accepted` row without an `accepted_event_id` is ever persisted) so the user can retry

### Requirement: [TARGET-STATE] Subcalendar Routing Prerequisite

Accepting a proposal SHALL never write to the user's primary calendar by
inference. This depends on butler-authored events routing to the dedicated
Butlers subcalendar, delivered by the
`calendar-route-butler-events-to-dedicated-calendar` change, which MUST land
before this capability.

#### Scenario: Accepted proposal lands on the Butlers subcalendar

- **WHEN** a proposal is accepted
- **THEN** the resulting event is created on the dedicated Butlers subcalendar via `calendar_create_butler_event`
- **AND** it is NOT written to the user's primary Google Calendar
- **BECAUSE** the human-in-the-write-loop doctrine forbids inferred events from reaching the user's real calendar without explicit confirmation routed to the butler-owned calendar
