# Approved invitation response API and inverse

## ADDED Requirements

### Requirement: Approved Calendar Invitation Response Surface

The dashboard API SHALL expose authenticated occurrence-only POST /api/calendar/workspace/respond and mounted Accept/Decline/Tentative controls using one typed mutation handler. A verified click MUST compose the exact canonical pending command with the existing approval decision and owning shared executor, never raw provider dispatch or a caller bypass. Pending, rejected, uncertain, noop, applied and projection-unavailable outcomes MUST remain distinct, and an undo door MUST be earned only by a guarded applied receipt.

ID: REQ-dashboard-api-069
Source: bu-q7vx1q.18 original AC2,5,6; dashboard owner auth; module-calendar Approval-Bound Self Invitation Response; design.md
Scope: v1-mandatory

#### Scenario: Verified owner click decides and dispatches the exact parked command

- **WHEN** an authenticated owner submits a valid response for an eligible workspace occurrence
- **THEN** POST /api/calendar/workspace/respond parks the canonical owning command and uses the existing verified decision and shared executor for that exact pending id/digest
- **AND** no standing rule or caller bypass is created
- **AND** no effect precedes approved execution

#### Scenario: Invalid admission is not a fabricated absence or authority

- **WHEN** lookup is degraded/ambiguous, source identity changed, or caller injects actor, account, recipient, ETag or approval id
- **THEN** the API refuses effect with a safe truthful admission error
- **AND** missing capability is distinct from an empty invitation list
- **AND** it performs no backfill, OAuth scope expansion or provider mutation

#### Scenario: Response controls share one typed handler

- **WHEN** the owner activates Accept, Decline or Tentative by keyboard, palette or contextual control
- **THEN** one handler submits one response command and acknowledges pending state
- **AND** double click reuses the request key
- **AND** focus and accessible control names are preserved

#### Scenario: Pending or uncertain response remains truthful

- **WHEN** approval is pending/rejected or the provider effect is uncertain
- **THEN** the mounted workspace keeps honest invitation/decision state and offers the matching review/error door
- **AND** it adds no optimistic accepted badge, success receipt or enabled undo
- **AND** degraded projection remains visible

#### Scenario: Applied receipt is distinct from current projection

- **WHEN** the own provider result verifies response while projection is unavailable or stale
- **THEN** the API and mounted workspace show the applied receipt and projection availability separately
- **AND** a replacement source/entry cannot receive the stale response
- **AND** undo is offered only with valid guarded applied pre-state

#### Scenario: Existing permission and radar behavior survives

- **WHEN** response verbs and receipts are added to the calendar workspace
- **THEN** existing invitation explicit-status admission, radar, entry details, CRUD permissions and loading/partial/clean-empty contracts remain intact
- **AND** the new operation does not infer consent from missing response/push data or alter provider/tool defaults

## MODIFIED Requirements

### Requirement: Calendar Mutation Undo Endpoint

`src/butlers/api/routers/calendar_workspace.py` SHALL expose
`POST /api/calendar/workspace/undo/{action_id}` that reverses a single previously
logged calendar mutation. The endpoint SHALL synthesize the inverse mutation from
the logged `calendar_action_log` row (`action_payload` plus the captured
pre-mutation state in `action_result`) and dispatch it through the **existing**
calendar MCP tools with a **fresh `request_id`**; it SHALL NOT introduce a new
MCP tool and SHALL NOT reverse an action that was never applied, was already
undone, or whose pre-state is unavailable.
For an applied workspace_user_respond action, this same endpoint SHALL use the same registered calendar_respond operation with a server-owned inverse reference and fresh request_id, not a second undo MCP tool. It MUST bind the exact owning source/account/occurrence/self, retained prior status and unchanged post-status/ETag, use the same verified approval and executor path, and admit at most one inverse write. A started uncertain inverse MUST retain its non-reacceptance fence; existing create/update/delete inverse behavior and all prior scenarios remain unchanged.

ID: REQ-dashboard-api-070
Source: Existing Calendar Mutation Undo Endpoint; bu-q7vx1q.18 AC4 and design.md approved response-only inverse composition.
Scope: v1-mandatory

#### Scenario: Undo an update reverse-applies the captured pre-state

- **WHEN** `POST /api/calendar/workspace/undo/{action_id}` is called for an
  `applied` `workspace_user_update` row whose `action_result` carries the
  pre-mutation event state
- **THEN** an inverse `calendar_update_event` is dispatched that restores the
  event's pre-state fields (title, start/end, timezone, location, description,
  attendees, recurrence, calendar id, and linked people) with a freshly generated
  `request_id`
- **AND** an empty captured people set is dispatched as `entity_ids: []` plus
  `clear_entity_ids: true`, so undo restores an originally-unlinked event rather
  than preserving newly added links
- **AND** the undo dispatch is itself recorded in `calendar_action_log` (so it is
  idempotent and appears in the audit trail)
- **AND** the response reports the undone `action_id`, the inverse tool invoked,
  and the new `request_id`

#### Scenario: Undo a delete recreates the event from the pre-image

- **WHEN** the undone row is an `applied` `workspace_user_delete` whose
  `action_result` carries the pre-deletion event state
- **THEN** an inverse `calendar_create_event` is dispatched from the captured
  pre-image with a fresh `request_id`, recreating the event and its linked people
  on its home calendar

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

- **WHEN** `{action_id}` does not match any `calendar_action_log` row **and every
  targeted calendar schema's owner-lookup fan-out responded successfully**
- **THEN** the endpoint returns HTTP 404 and dispatches no mutation

#### Scenario: Undo owner-lookup inconclusive on a source failure returns 503

- **WHEN** `{action_id}` matches no returned row but at least one targeted calendar
  schema's owner-lookup fan-out FAILED (so the owning schema may simply have been
  unreachable)
- **THEN** the endpoint returns HTTP 503 (retryable) naming that one or more
  calendar sources were unavailable, and dispatches no mutation
- **BECAUSE** a transient source failure MUST NOT be reported as a definitive 404
  "this action does not exist" — that would fabricate absence from a degraded read

#### Scenario: Repeated undo of the same action is rejected

- **WHEN** `POST /api/calendar/workspace/undo/{action_id}` is called for an action
  whose inverse mutation has already been dispatched and recorded
- **THEN** the endpoint fails fast (HTTP 409) reporting the action was already
  undone, rather than dispatching a second inverse

#### Scenario: Response undo uses the same approved self-only operation

- **WHEN** an applied workspace_user_respond receipt retains its prior self status and exact unchanged post-version
- **THEN** the existing undo endpoint dispatches one newly approved calendar_respond inverse restoring only the recorded self status
- **AND** server-recorded needsAction is allowed only as the inverse
- **AND** no new undo tool or attendee/event-field replacement is introduced

#### Scenario: Response undo refuses stale or unearned inverses

- **WHEN** the original is failed/unknown/noop, prior state is absent, ownership is ambiguous or provider version/status/source changed
- **THEN** the endpoint dispatches no inverse and reports the honest refusal
- **AND** no guessed retention window or read-only provider match creates an applied original

#### Scenario: Concurrent response inverse retains its start fence

- **WHEN** concurrent undo or replay encounters an already claimed or uncertain started inverse
- **THEN** only its one bound approved command can own the inverse attempt
- **AND** no repeated egress or generic exception-based claim release occurs after start
