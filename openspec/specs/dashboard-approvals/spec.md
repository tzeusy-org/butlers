## Purpose

The dashboard-approvals capability defines the API surface and dashboard UX for
the human-in-the-loop approvals queue: listing approval actions, viewing action
detail, deciding actions (approve/deny/defer), managing notification policy
(quiet hours), streaming lifecycle events, and surfacing autonomy promotion/
demotion suggestions.

## Requirements

### Requirement: Approvals action list API

The dashboard API SHALL expose `GET /api/approvals/actions` which returns a paginated list of approval actions.

The endpoint SHALL accept the following query parameters:
- `offset` (integer, optional, default 0) -- pagination offset
- `limit` (integer, optional, default 50) -- maximum number of actions to return
- `status` (string, optional) -- filter by action status: `pending`, `approved`, `rejected`, `expired`, or `executed`
- `tool_name` (string, optional) -- filter by the tool that requested the action
- `since` (ISO 8601 timestamp, optional) -- include only actions created on or after this timestamp
- `until` (ISO 8601 timestamp, optional) -- include only actions created on or before this timestamp

The response MUST be a `PaginatedResponse<ApprovalAction>` where each `ApprovalAction` object contains:
- `id` -- string UUID identifying the action
- `tool_name` -- string name of the tool requesting approval
- `butler` -- string name of the butler that owns the action
- `status` -- one of `"pending"`, `"approved"`, `"rejected"`, `"expired"`, `"executed"`
- `description` -- string human-readable description of the action
- `why` -- string | null serif paragraph explaining why human input is needed
- `evidence` -- string[] | null array of mono evidence lines
- `constraints` -- object mapping constraint names to their values (tool-specific structure)
- `created_at` -- ISO 8601 timestamp when the action was created
- `expires_at` -- ISO 8601 timestamp when the action will expire if not decided
- `decided_at` -- ISO 8601 timestamp when the action was approved/rejected (null if pending)
- `decided_by` -- string identifier of who decided (user, rule ID, auto-expired) (null if pending)
- `rule_id` -- string UUID of the rule that auto-approved (null if manual or not approved)
- `execution_count` -- integer count of times this action has been executed (0 if not executed)
- `target_contact` -- object (nullable) containing resolved target contact info: `id` (UUID), `name` (string), `roles` (string array). Null if the action does not target a specific contact.

#### Scenario: List with new fields populated

- **WHEN** `GET /api/approvals/actions` is called and the underlying `pending_actions` rows have `why` and `evidence` populated
- **THEN** the response includes the `why` paragraph and `evidence` array on each `ApprovalAction` row.

#### Scenario: List with legacy rows

- **WHEN** `GET /api/approvals/actions` is called and the underlying `pending_actions` rows have `why = NULL` and `evidence = []` (pre-migration data)
- **THEN** the response includes `why: null` and `evidence: []` on those rows; the rest of the row is unchanged.

#### Scenario: Fetch pending approval actions

- **WHEN** `GET /api/approvals/actions?status=pending` is called
- **THEN** the API MUST return all actions with `status = "pending"` sorted by `created_at` descending
- **AND** each action MUST include all required fields including `constraints` and `target_contact`
- **AND** the response status MUST be 200

#### Scenario: Target contact populated for notify actions

- **WHEN** a pending `notify` action has `contact_id='abc-123'` in its constraints
- **THEN** the `target_contact` field MUST include the contact's `id`, `name`, and `roles`

#### Scenario: Target contact null for non-contact actions

- **WHEN** a pending action has no contact_id in its constraints
- **THEN** the `target_contact` field MUST be `null`

#### Scenario: Filter actions by tool name

- **WHEN** `GET /api/approvals/actions?tool_name=notify` is called
- **THEN** the API MUST return only actions where `tool_name = "notify"`
- **AND** the results MUST include all statuses unless further filtered

#### Scenario: Filter actions by date range

- **WHEN** `GET /api/approvals/actions?since=2026-02-10T00:00:00Z&until=2026-02-17T23:59:59Z` is called
- **THEN** the API MUST return only actions with `created_at` between the specified timestamps (inclusive)

#### Scenario: Pagination with default limit

- **WHEN** `GET /api/approvals/actions` is called
- **THEN** the API MUST return at most 50 actions (default limit)
- **AND** the response MUST include a `meta` object with `total`, `offset`, and `limit` fields

#### Scenario: Pagination with custom offset

- **WHEN** `GET /api/approvals/actions?offset=50&limit=25` is called
- **THEN** the API MUST return actions 50-74 (25 actions starting at offset 50)

#### Scenario: No pending actions

- **WHEN** `GET /api/approvals/actions?status=pending` is called and no pending actions exist
- **THEN** the API MUST return an empty array
- **AND** the response status MUST be 200

---

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

#### Scenario: Target contact shown in approval dossier

- **WHEN** a pending `notify` action targets contact "Chloe" with `roles = []`
- **THEN** the dossier MUST display "Chloe" as the target contact
- **AND** the role badges MUST be empty (non-owner)

#### Scenario: Owner-targeted action shows owner badge

- **WHEN** a pending action targets the owner contact with `roles = ['owner']`
- **THEN** the dossier MUST display the owner's name with an "owner" role badge

#### Scenario: Expiring action countdown

- **WHEN** an action's expiration time is within 1 hour
- **THEN** the expiration countdown MUST display in a warning color (e.g., red or orange)

#### Scenario: Empty state when no actions

- **WHEN** a user navigates to `/approvals` with no pending or recent approvals
- **THEN** the page MUST display an empty state message (e.g., "No pending approvals")

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

- **WHEN** the notification dispatcher is about to page the owner for a new approval via the owner-default path (no explicit `entity_id` or `recipient`), with intent `send` or `insight` and priority not `high`
- **THEN** if the current hour in `timezone` falls within the inclusive window `[quiet_start_hour, quiet_end_hour]`, the page is suppressed (dropped silently, returning status `suppressed_quiet_hours`); it is NOT deferred or re-presented later
- **AND** high-priority pages and pages with an explicit `entity_id`/`recipient` are always delivered immediately
- **AND** the approval is still created and visible in the dashboard immediately.

### Requirement: Approvals Live Stream

The dashboard SHALL expose `WS /api/approvals/stream` emitting approval lifecycle events.

#### Scenario: Stream event shape

- **WHEN** an approval transitions state
- **THEN** an event `{type: "created"|"approved"|"rejected"|"deferred"|"executed"|"expired", action_id, ts}` is broadcast.

---

### Requirement: Promotion Suggestions API Endpoint

The dashboard API SHALL expose `GET /api/approvals/suggestions` which returns a list of autonomy promotion and demotion suggestions.

The endpoint SHALL accept the following query parameters:
- `status` (string, optional, default `pending`) -- filter by suggestion status: `pending`, `confirmed`, `dismissed`, `superseded`, or `all`
- `suggestion_type` (string, optional) -- filter by type: `promotion` or `demotion`
- `limit` (integer, optional, default 20) -- maximum number of suggestions to return
- `offset` (integer, optional, default 0) -- pagination offset

The response MUST be a `PaginatedResponse<AutonomySuggestion>` where each `AutonomySuggestion` object contains:
- `id` -- string UUID identifying the suggestion
- `suggestion_type` -- `"promotion"` or `"demotion"`
- `pattern_fingerprint` -- string hash identifying the action pattern
- `tool_name` -- string name of the tool
- `representative_args` -- object with the exact tool arguments this suggestion covers
- `scope_description` -- string human-readable description of what the proposed rule would auto-approve
- `status` -- one of `"pending"`, `"confirmed"`, `"dismissed"`, `"superseded"`
- `approval_count_at_creation` -- integer number of approvals that triggered this suggestion
- `created_at` -- ISO 8601 timestamp
- `decided_at` -- ISO 8601 timestamp (null if pending)
- `decided_by` -- string identifier (null if pending)
- `resulting_rule_id` -- string UUID of rule created on confirmation (null otherwise)
- `velocity` -- object (nullable) containing `avg_seconds`, `sample_count`, `fast_approval` from the velocity tracker

#### Scenario: Fetch pending promotion suggestions

- **WHEN** `GET /api/approvals/suggestions?status=pending&suggestion_type=promotion` is called
- **THEN** the API MUST return all pending promotion suggestions sorted by `created_at DESC`
- **AND** each suggestion MUST include `scope_description` and `velocity` data
- **AND** the response status MUST be 200

#### Scenario: Fetch pending demotion suggestions

- **WHEN** `GET /api/approvals/suggestions?status=pending&suggestion_type=demotion` is called
- **THEN** the API MUST return all pending demotion suggestions
- **AND** each suggestion MUST include the error details from the failed execution in metadata

#### Scenario: No pending suggestions

- **WHEN** `GET /api/approvals/suggestions?status=pending` is called and none exist
- **THEN** the API MUST return an empty array with response status 200

### Requirement: Suggestion Confirmation API Endpoint

The dashboard API SHALL expose `POST /api/approvals/suggestions/{suggestionId}/confirm` to confirm a promotion or demotion suggestion.

#### Scenario: Confirm a promotion suggestion via API

- **WHEN** `POST /api/approvals/suggestions/{suggestionId}/confirm` is called with authenticated user context
- **THEN** the API MUST invoke `confirm_promotion_suggestion` on the approvals module
- **AND** the response MUST include the created `rule_id` on success
- **AND** the response status MUST be 200

#### Scenario: Confirm without authentication

- **WHEN** `POST /api/approvals/suggestions/{suggestionId}/confirm` is called without authentication
- **THEN** the response status MUST be 401

### Requirement: Suggestion Dismissal API Endpoint

The dashboard API SHALL expose `POST /api/approvals/suggestions/{suggestionId}/dismiss` to dismiss a promotion or demotion suggestion. The request body MAY include an optional `reason` string.

#### Scenario: Dismiss a suggestion via API

- **WHEN** `POST /api/approvals/suggestions/{suggestionId}/dismiss` is called with `{"reason": "Not needed"}` and authenticated user context
- **THEN** the API MUST invoke `dismiss_promotion_suggestion` on the approvals module
- **AND** the response status MUST be 200

#### Scenario: Dismiss without reason

- **WHEN** `POST /api/approvals/suggestions/{suggestionId}/dismiss` is called with no body
- **THEN** the dismissal MUST proceed with `reason` as `null`

### Requirement: Autonomy Suggestions Dashboard Section

The approvals dashboard page at `/approvals` SHALL include an "Autonomy Suggestions" section displayed above the two-pane approvals body (the pending-approvals rail and dossier) when pending suggestions exist. The prior "actions table" layout referenced here has graduated to the Dispatch two-pane dossier, so the section sits between the page header and that body.

#### Scenario: Promotion suggestion card displayed

- **WHEN** pending promotion suggestions exist
- **THEN** the dashboard MUST display a card for each suggestion containing:
  - The tool name
  - The human-readable scope description (e.g., "Auto-approve send_telegram when chat_id = 'mom_123' AND text = 'Good morning'")
  - The number of times this exact action was manually approved
  - Approval velocity indicator (fast/normal)
  - "Confirm rule" and "Dismiss" action buttons
- **AND** the card MUST visually emphasize that the rule scope is exact-match only

#### Scenario: Demotion suggestion card displayed

- **WHEN** pending demotion suggestions exist
- **THEN** the dashboard MUST display a warning card for each demotion containing:
  - The tool name and rule description
  - The execution error summary
  - "Revoke rule" and "Keep rule" action buttons
- **AND** the card MUST use a warning/alert visual style

#### Scenario: No pending suggestions hides section

- **WHEN** no pending suggestions exist
- **THEN** the autonomy suggestions section MUST NOT be rendered

#### Scenario: Confirm suggestion from card

- **WHEN** a user clicks "Confirm rule" on a promotion suggestion card
- **THEN** the dashboard MUST call `POST /api/approvals/suggestions/{id}/confirm`
- **AND** the card MUST be removed from the suggestions section on success
- **AND** a success toast MUST indicate the new standing rule was created

#### Scenario: Dismiss suggestion from card

- **WHEN** a user clicks "Dismiss" on a promotion suggestion card
- **THEN** the dashboard MAY show an optional reason input
- **AND** MUST call `POST /api/approvals/suggestions/{id}/dismiss`
- **AND** the card MUST be removed from the suggestions section on success

## Source References

- PLAN.md §5 `/approvals` API surface and §6 Phase 6 implementation order.
- Visual reference: the `ApprovalsPage` redesign prototype (graduated; now shipped in `frontend/`).
- Reuses `audit.append()` from dashboard-audit-log on every mutation.
