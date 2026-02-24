## MODIFIED Requirements

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
- `constraints` -- object mapping constraint names to their values (tool-specific structure)
- `created_at` -- ISO 8601 timestamp when the action was created
- `expires_at` -- ISO 8601 timestamp when the action will expire if not decided
- `decided_at` -- ISO 8601 timestamp when the action was approved/rejected (null if pending)
- `decided_by` -- string identifier of who decided (user, rule ID, auto-expired) (null if pending)
- `rule_id` -- string UUID of the rule that auto-approved (null if manual or not approved)
- `execution_count` -- integer count of times this action has been executed (0 if not executed)
- `target_contact` -- object (nullable) containing resolved target contact info: `id` (UUID), `name` (string), `roles` (string array). Null if the action does not target a specific contact.

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
- **AND** the response MUST include `offset`, `limit`, and `total_count` fields

#### Scenario: Pagination with custom offset

- **WHEN** `GET /api/approvals/actions?offset=50&limit=25` is called
- **THEN** the API MUST return actions 50-74 (25 actions starting at offset 50)

#### Scenario: No pending actions

- **WHEN** `GET /api/approvals/actions?status=pending` is called and no pending actions exist
- **THEN** the API MUST return an empty array
- **AND** the response status MUST be 200

---

### Requirement: Approvals queue dashboard page

The frontend SHALL render an approvals dashboard page at `/approvals` displaying pending and managed approval actions with metrics and detail views.

The page MUST contain the following sections:

1. **Metrics cards** at the top:
   - Pending count card
   - Approved count card (with trend from previous day/week)
   - Rejected count card (with trend)
   - Auto-approved count card (with trend)
   - Average time-to-decision card
   - Rules count badge

2. **Approval actions table** with:
   - Columns: tool name, description (truncated), target contact (name and role badges), status badge, created timestamp, expiration countdown, action buttons
   - Filterable by: tool name, status, date range
   - Sortable by: created date, status
   - Row click opens action detail dialog
   - Action buttons: Approve, Reject, View Details

3. **Pagination** for the actions list

#### Scenario: Approvals page loads with metrics

- **WHEN** a user navigates to `/approvals`
- **THEN** the page MUST display metric cards showing current pending count, approved/rejected/auto-approved counts
- **AND** the pending actions table MUST be populated and sorted by created date descending
- **AND** the rules count badge MUST show the total number of active rules

#### Scenario: Target contact shown in actions table

- **WHEN** a pending `notify` action targets contact "Chloe" with `roles = []`
- **THEN** the actions table MUST display "Chloe" in the target contact column
- **AND** the role badges MUST be empty (non-owner)

#### Scenario: Owner-targeted action shows owner badge

- **WHEN** a pending action targets the owner contact with `roles = ['owner']`
- **THEN** the target contact column MUST display the owner's name with an "owner" role badge

#### Scenario: Filter actions by tool and status

- **WHEN** a user selects `tool_name = "notify"` and `status = "pending"` in the filters
- **THEN** the table MUST update to show only pending notify approval actions
- **AND** the metric cards MUST update to reflect the filtered subset

#### Scenario: Action detail dialog

- **WHEN** a user clicks on an action row or "View Details" button
- **THEN** a modal dialog MUST open showing:
  - Action ID
  - Tool name
  - Description
  - Full constraints (formatted JSON or key-value pairs)
  - Target contact name, roles, and link to `/butlers/contacts/{contact_id}` (if applicable)
  - Status and decision timestamp
  - Decided-by information
  - If approved/rejected: option to view or create related rule
  - If pending: Approve and Reject buttons

#### Scenario: Approve action from detail dialog

- **WHEN** a user clicks "Approve" in the action detail dialog
- **THEN** the approval call MUST be sent to `POST /api/approvals/actions/{actionId}/approve`
- **AND** the dialog MUST close
- **AND** the table MUST refresh showing the action's new status

#### Scenario: Expiring action countdown

- **WHEN** an action's expiration time is within 1 hour
- **THEN** the expiration countdown in the table MUST display in a warning color (e.g., red or orange)

#### Scenario: Empty state when no actions

- **WHEN** a user navigates to `/approvals` with no pending or recent actions
- **THEN** the page MUST display an empty state message (e.g., "No pending approvals")
- **AND** the metrics cards MUST show zero values
