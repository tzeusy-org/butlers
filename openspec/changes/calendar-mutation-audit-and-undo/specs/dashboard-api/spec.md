## ADDED Requirements

### Requirement: Calendar Mutation Audit-Trail Read Endpoint

`src/butlers/api/routers/calendar_workspace.py` SHALL expose a read-only endpoint
`GET /api/calendar/workspace/audit` that returns the calendar mutation audit
trail from the existing `calendar_action_log` table (written today by the calendar
module's `_record_projection_action`). The endpoint SHALL NOT mutate any state and
SHALL NOT call a provider. It surfaces each logged action with its status,
type, payload summary, and the existing `source_butler` / `source_session_id`
provenance columns (migration `core_076`) so the agent's own writes are
self-explaining and deep-linkable to the originating session.

#### Scenario: Audit feed returns logged mutations newest-first

- **WHEN** `GET /api/calendar/workspace/audit` is called with an optional `limit`
  (and optional `cursor`/`butler` filters)
- **THEN** rows are read from `calendar_action_log` fanned out across
  `butlers_with_module("calendar")` and returned ordered by `created_at DESC`
- **AND** each row carries its `action_id`, `action_type` (e.g.
  `"workspace_user_update"`), `action_status` (one of `applied`, `pending`,
  `failed`, `noop`), `request_id`, a payload summary, `error` (when present), and
  `created_at`/`applied_at` timestamps
- **AND** the response uses the standard `ApiResponse` envelope and performs no
  provider or projection write

#### Scenario: Audit rows surface authorship provenance

- **WHEN** an audit row references an event present in `calendar_events`
- **THEN** the row includes `source_butler` and `source_session_id` joined from
  `calendar_events` (the existing `core_076` columns) via the row's `event_id`
- **AND** when no joined event exists (e.g. a delete whose projection row is gone,
  or a `noop`/`failed` action), `source_butler` falls back to the row's owning
  butler and `source_session_id` is `null`
- **AND** the provenance fields let an Activity tab deep-link the row to the
  originating session log

#### Scenario: UnifiedCalendarEntry exposes authorship for deep-linking

- **WHEN** a calendar workspace entry is returned by the read surface
- **THEN** the entry exposes `source_butler` and `source_session_id` (sourced from
  the existing `calendar_events.source_butler` / `source_session_id` columns)
- **AND** these fields back the Activity-tab deep-link from an event to the
  session that created or last mutated it

#### Scenario: Empty audit log returns an empty feed

- **WHEN** `GET /api/calendar/workspace/audit` is called and no
  `calendar_action_log` rows exist (or the table is absent in a deployment)
- **THEN** the response is HTTP 200 with an empty list (fail-open), not an error

### Requirement: Calendar Mutation Undo Endpoint

`src/butlers/api/routers/calendar_workspace.py` SHALL expose
`POST /api/calendar/workspace/undo/{action_id}` that reverses a single previously
logged calendar mutation. The endpoint SHALL synthesize the inverse mutation from
the logged `calendar_action_log` row (`action_payload` plus the captured
pre-mutation state in `action_result`) and dispatch it through the **existing**
calendar MCP tools with a **fresh `request_id`**; it SHALL NOT introduce a new
MCP tool and SHALL NOT reverse an action that was never applied, was already
undone, or whose pre-state is unavailable.

#### Scenario: Undo an update reverse-applies the captured pre-state

- **WHEN** `POST /api/calendar/workspace/undo/{action_id}` is called for an
  `applied` `workspace_user_update` row whose `action_result` carries the
  pre-mutation event state
- **THEN** an inverse `calendar_update_event` is dispatched that restores the
  event's pre-state fields (title, start/end, timezone, location, description,
  attendees, recurrence, calendar id) with a freshly generated `request_id`
- **AND** the undo dispatch is itself recorded in `calendar_action_log` (so it is
  idempotent and appears in the audit trail)
- **AND** the response reports the undone `action_id`, the inverse tool invoked,
  and the new `request_id`

#### Scenario: Undo a delete recreates the event from the pre-image

- **WHEN** the undone row is an `applied` `workspace_user_delete` whose
  `action_result` carries the pre-deletion event state
- **THEN** an inverse `calendar_create_event` is dispatched from the captured
  pre-image with a fresh `request_id`, recreating the event on its home calendar

#### Scenario: Undo a create deletes the created event

- **WHEN** the undone row is an `applied` `workspace_user_create`
- **THEN** an inverse `calendar_delete_event` is dispatched against the created
  event id (from the row's `origin_ref`/`action_result`) with a fresh `request_id`

#### Scenario: Undo of a non-applied action fails fast

- **WHEN** `POST /api/calendar/workspace/undo/{action_id}` targets a row whose
  `action_status` is `pending`, `failed`, or `noop`
- **THEN** the endpoint returns a fail-fast error (HTTP 409) naming the row's
  status and stating that only an `applied` mutation can be undone
- **AND** no inverse mutation is dispatched

#### Scenario: Undo of a missing or expired pre-state fails fast with diagnostics

- **WHEN** the targeted row exists and is `applied` but its `action_result` lacks
  the captured pre-mutation state (e.g. it was logged before pre-state capture, or
  the event no longer exists to restore against)
- **THEN** the endpoint returns a fail-fast error (HTTP 422) whose detail names
  the `action_id`, the `action_type`, and the reason the inverse could not be
  reconstructed (missing/expired pre-state)
- **AND** no inverse mutation is dispatched
- **BECAUSE** silently guessing an inverse on a single-owner calendar could
  materialize a wrong event or a wrong restore

#### Scenario: Undo of an unknown action id returns not found

- **WHEN** `{action_id}` does not match any `calendar_action_log` row
- **THEN** the endpoint returns HTTP 404 and dispatches no mutation

#### Scenario: Repeated undo of the same action is rejected

- **WHEN** `POST /api/calendar/workspace/undo/{action_id}` is called for an action
  whose inverse mutation has already been dispatched and recorded
- **THEN** the endpoint fails fast (HTTP 409) reporting the action was already
  undone, rather than dispatching a second inverse
