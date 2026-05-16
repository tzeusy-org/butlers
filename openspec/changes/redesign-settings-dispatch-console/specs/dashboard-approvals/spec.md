## ADDED Requirements

### Requirement: Approvals Flat List API
The dashboard SHALL expose `GET /api/approvals?state=waiting|decided|all` as a flat-list view complementing the existing `GET /api/approvals/actions` paginated list.

#### Scenario: Filter by state
- **WHEN** `GET /api/approvals?state=waiting` is called
- **THEN** the response is `ApiResponse[ApprovalSummary[]]` containing only actions in `pending` state, ordered `created_at DESC`.
- **WHEN** `GET /api/approvals?state=decided` is called
- **THEN** the response contains actions in `approved | rejected | expired | executed` states.
- **WHEN** `GET /api/approvals?state=all` is called or `state` is omitted
- **THEN** all states are included.

### Requirement: Approval Detail API
The dashboard SHALL expose `GET /api/approvals/{id}` returning the full dossier for one approval.

#### Scenario: Detail response shape
- **WHEN** `GET /api/approvals/{id}` is called
- **THEN** the response is `ApiResponse[ApprovalDetail]` with fields `id`, `title`, `butler`, `created_at` (alias `ts`), `expires_at` (alias `expires`), `why` (string | null — serif paragraph), `evidence` (string[] | null — mono lines), `proposed_action` (object describing the tool call being approved).
- **AND** when `why` or `evidence` is null (legacy row), the UI renders a serif-italic empty state for the missing section.

### Requirement: Approval Verbs
The dashboard SHALL expose explicit verb endpoints for approve, deny, and defer.

#### Scenario: Approve with optional edits
- **WHEN** `POST /api/approvals/{id}/approve {edits?: object}` is called
- **THEN** the action is approved with any supplied `edits` applied to its arguments
- **AND** `audit.append("approval.approve", target=action_id, note=json.dumps(edits))` is invoked
- **AND** the underlying tool is executed via the shared executor (existing module-approvals behavior).

#### Scenario: Deny with reason
- **WHEN** `POST /api/approvals/{id}/deny {reason?: str}` is called
- **THEN** the action transitions to `rejected`
- **AND** `audit.append("approval.deny", target=action_id, note=reason)` is invoked.

#### Scenario: Defer with bounded hours
- **WHEN** `POST /api/approvals/{id}/defer {hours: int}` is called
- **THEN** the call is rejected with `422` unless `1 ≤ hours ≤ 168`
- **AND** on success, the action's `expires_at` is extended by `hours` and the notification re-presentation timer is reset to `now + hours`
- **AND** `audit.append("approval.defer", target=action_id, note=str(hours))` is invoked.

### Requirement: Approvals Policy (Quiet Hours)
The dashboard SHALL expose `GET/PUT /api/approvals/policy` to manage notification quiet hours.

#### Scenario: Read policy
- **WHEN** `GET /api/approvals/policy` is called
- **THEN** the response is `ApiResponse[ApprovalsPolicy]` with `quiet_start_hour: int` (0–23), `quiet_end_hour: int` (0–23), `timezone: str` (IANA).

#### Scenario: Update policy
- **WHEN** `PUT /api/approvals/policy` is called with the same shape
- **THEN** the singleton row is updated and `audit.append("approvals.policy")` is invoked.

#### Scenario: Quiet hours suppress paging
- **WHEN** the notification dispatcher is about to page the owner for a new approval
- **THEN** if the current time in `timezone` falls within `[quiet_start_hour, quiet_end_hour)`, the page is deferred until `quiet_end_hour`
- **AND** the approval is still created and visible in the dashboard immediately.

### Requirement: Approvals Live Stream
The dashboard SHALL expose `WS /api/approvals/stream` emitting approval lifecycle events.

#### Scenario: Stream event shape
- **WHEN** an approval transitions state
- **THEN** an event `{type: "created"|"approved"|"rejected"|"deferred"|"executed"|"expired", action_id, ts}` is broadcast.

## MODIFIED Requirements

### Requirement: Approvals action list API
The dashboard API SHALL expose `GET /api/approvals/actions` which returns a paginated list of approval actions.

The endpoint SHALL accept the following query parameters:
- `offset` (integer, optional, default 0) — pagination offset
- `limit` (integer, optional, default 50) — maximum number of actions to return
- `status` (string, optional) — filter by action status: `pending`, `approved`, `rejected`, `expired`, or `executed`
- `tool_name` (string, optional) — filter by the tool that requested the action
- `since` (ISO 8601 timestamp, optional) — include only actions created on or after this timestamp
- `until` (ISO 8601 timestamp, optional) — include only actions created on or before this timestamp

The response MUST be a `PaginatedResponse<ApprovalAction>` where each `ApprovalAction` object contains:
- `id` — string UUID identifying the action
- `tool_name` — string name of the tool requesting approval
- `butler` — string name of the butler that owns the action
- `status` — one of `"pending"`, `"approved"`, `"rejected"`, `"expired"`, `"executed"`
- `description` — string human-readable description of the action
- `why` — string | null serif paragraph explaining why human input is needed (NEW)
- `evidence` — string[] | null array of mono evidence lines (NEW)

#### Scenario: List with new fields populated
- **WHEN** `GET /api/approvals/actions` is called and the underlying `pending_actions` rows have `why` and `evidence` populated
- **THEN** the response includes the `why` paragraph and `evidence` array on each `ApprovalAction` row.

#### Scenario: List with legacy rows
- **WHEN** `GET /api/approvals/actions` is called and the underlying `pending_actions` rows have `why = NULL` and `evidence = []` (pre-migration data)
- **THEN** the response includes `why: null` and `evidence: []` on those rows; the rest of the row is unchanged.

### Requirement: Approvals Page in Dispatch Language
The dashboard SHALL render `/approvals` in the Dispatch design language as a replacement (not a duplicate) for the legacy approvals page.

#### Scenario: Approvals page layout
- **WHEN** a user navigates to `/approvals`
- **THEN** the page renders, in vertical order:
  - **Page header**: title "Approvals", mono eyebrow "system · approvals", clock.
  - **Two-pane body**: left rail of pending approvals (rule-separated rows), right pane dossier of the selected approval.
  - **Dossier body**: `title` headline (sans 500, 22px), `why` serif paragraph (`max-width: 50ch`), `evidence` mono lines (rule-separated), `proposed_action` summary, primary `Approve` commit button, secondary `Deny` and `Defer` pill buttons.
  - **Policy section** below the body: quiet-hours editor (`start_hour`, `end_hour`, `timezone`).
  - **History section** at the bottom: last 30 decided approvals from `GET /api/approvals/history`.
- **AND** the page contains no Kanban-style columns, no charts, no cards.

#### Scenario: Legacy page deleted in same PR
- **WHEN** the new `ApprovalsPage` lands
- **THEN** the legacy approvals component (the prior `ApprovalsPage` content) is REMOVED in the same PR
- **AND** no parallel `/approvals/legacy` route exists.

## Source References
- PLAN.md §5 `/approvals` API surface and §6 Phase 6 implementation order.
- `pr/overview/settings-refactor/settings-expanded.jsx :: ApprovalsPage` is the visual reference.
- Reuses `audit.append()` from dashboard-audit-log on every mutation.
