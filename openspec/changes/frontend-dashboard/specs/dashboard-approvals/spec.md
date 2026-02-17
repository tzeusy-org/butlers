# Dashboard Approvals

Approvals dashboard for managing request approval workflows. Provides approval action queue with metrics, filterable action management, rule-based auto-approval workflow, and rule lifecycle management.

The Approvals system allows tools in the Butlers platform to request explicit approval before executing sensitive operations. Approval actions can be approved/rejected manually or automatically via rules. Rules define constraint patterns that trigger auto-approval when matched.

## ADDED Requirements

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

#### Scenario: Fetch pending approval actions

- **WHEN** `GET /api/approvals/actions?status=pending` is called
- **THEN** the API MUST return all actions with `status = "pending"` sorted by `created_at` descending
- **AND** each action MUST include all required fields including `constraints`
- **AND** the response status MUST be 200

#### Scenario: Filter actions by tool name

- **WHEN** `GET /api/approvals/actions?tool_name=email` is called
- **THEN** the API MUST return only actions where `tool_name = "email"`
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

### Requirement: Approvals action detail API

The dashboard API SHALL expose `GET /api/approvals/actions/{actionId}` which returns full details for a specific approval action.

The response MUST be an `ApiResponse<ApprovalAction>` with all fields from the action list plus any additional context needed for the detail view.

#### Scenario: Fetch full details for a specific action

- **WHEN** `GET /api/approvals/actions/uuid-1234` is called
- **THEN** the API MUST return the full `ApprovalAction` object with all fields
- **AND** the response status MUST be 200

#### Scenario: Action not found

- **WHEN** `GET /api/approvals/actions/nonexistent-id` is called
- **THEN** the API MUST return a 404 response with an error message

---

### Requirement: Approve action API

The dashboard API SHALL expose `POST /api/approvals/actions/{actionId}/approve` which approves a pending approval action.

The request MAY include an optional JSON body with:
- `rule_id` (string UUID, optional) -- the rule ID that triggered the auto-approval (if applicable)

The response MUST be an `ApiResponse<ApprovalAction>` with the action's updated state:
- `status` MUST be changed to `"approved"`
- `decided_at` MUST be set to the current timestamp
- `decided_by` MUST indicate the approver (user, rule ID, or system)
- `rule_id` MUST be set if the approval was triggered by a rule

#### Scenario: Manually approve a pending action

- **WHEN** `POST /api/approvals/actions/uuid-1234/approve` is called
- **THEN** the action's `status` MUST change to `"approved"`
- **AND** `decided_at` MUST be set to the current timestamp
- **AND** `decided_by` MUST be set to the approver identifier (e.g., username or "system")
- **AND** the response status MUST be 200

#### Scenario: Approve action with associated rule

- **WHEN** `POST /api/approvals/actions/uuid-1234/approve` is called with `rule_id` in the request body
- **THEN** the action's `rule_id` field MUST be set to the provided rule ID
- **AND** the rule's usage count MUST be incremented

#### Scenario: Approve a non-pending action

- **WHEN** `POST /api/approvals/actions/uuid-1234/approve` is called and the action already has `status = "approved"`
- **THEN** the API MUST return a 409 response with an error message (conflict)

#### Scenario: Approve action and increment execution count

- **WHEN** `POST /api/approvals/actions/uuid-1234/approve` is called
- **AND** the approved action is subsequently executed
- **THEN** the `execution_count` field MUST be incremented

---

### Requirement: Reject action API

The dashboard API SHALL expose `POST /api/approvals/actions/{actionId}/reject` which rejects a pending approval action.

The request MAY include an optional JSON body with:
- `reason` (string, optional) -- reason for rejection

The response MUST be an `ApiResponse<ApprovalAction>` with the action's updated state:
- `status` MUST be changed to `"rejected"`
- `decided_at` MUST be set to the current timestamp
- `decided_by` MUST indicate who rejected the action

#### Scenario: Reject a pending action

- **WHEN** `POST /api/approvals/actions/uuid-1234/reject` is called
- **THEN** the action's `status` MUST change to `"rejected"`
- **AND** `decided_at` MUST be set to the current timestamp
- **AND** the response status MUST be 200

#### Scenario: Reject action with reason

- **WHEN** `POST /api/approvals/actions/uuid-1234/reject` is called with a `reason` field in the request body
- **THEN** the reason SHOULD be stored and returned in subsequent detail fetches

#### Scenario: Reject a non-pending action

- **WHEN** `POST /api/approvals/actions/uuid-1234/reject` is called and the action has `status = "rejected"`
- **THEN** the API MUST return a 409 response (conflict)

---

### Requirement: Expire stale actions API

The dashboard API SHALL expose `POST /api/approvals/actions/expire-stale` which marks all pending actions past their expiration time as expired.

The response MUST be an `ApiResponse<{ expired_count: number, expired_ids: string[] }>` containing:
- `expired_count` -- number of actions that were expired
- `expired_ids` -- array of action IDs that were expired

This endpoint SHALL:
- Find all actions with `status = "pending"` and `expires_at <= now`
- Set their `status` to `"expired"`
- Set `decided_at` to the current timestamp
- Set `decided_by` to `"auto-expired"`
- Return the count and list of expired action IDs

#### Scenario: Expire stale actions

- **WHEN** `POST /api/approvals/actions/expire-stale` is called and 3 pending actions have exceeded their expiration time
- **THEN** all 3 actions' `status` MUST be changed to `"expired"`
- **AND** all 3 actions' `decided_by` MUST be set to `"auto-expired"`
- **AND** the response MUST include `expired_count = 3` and a list of the 3 action IDs

#### Scenario: No stale actions to expire

- **WHEN** `POST /api/approvals/actions/expire-stale` is called and all pending actions are within their expiration time
- **THEN** the API MUST return `expired_count = 0`
- **AND** an empty `expired_ids` array
- **AND** the response status MUST be 200

---

### Requirement: Executed actions list API

The dashboard API SHALL expose `GET /api/approvals/actions/executed` which returns a paginated list of actions that have been executed (approved and run).

The endpoint SHALL accept the following query parameters:
- `offset` (integer, optional, default 0) -- pagination offset
- `limit` (integer, optional, default 50) -- maximum number of actions to return
- `tool_name` (string, optional) -- filter by tool name
- `rule_id` (string UUID, optional) -- filter by rule ID (show only executions triggered by this rule)
- `since` (ISO 8601 timestamp, optional) -- include only actions executed on or after this timestamp
- `until` (ISO 8601 timestamp, optional) -- include only actions executed on or before this timestamp

The response MUST be a `PaginatedResponse<ApprovalAction>` with the same schema as `/api/approvals/actions`. The results MUST be sorted by `decided_at` descending (most recently executed first).

#### Scenario: Fetch executed actions

- **WHEN** `GET /api/approvals/actions/executed` is called
- **THEN** the API MUST return only actions with `status = "executed"` and `execution_count > 0`
- **AND** the results MUST be sorted by `decided_at` descending
- **AND** pagination MUST work identically to the actions list endpoint

#### Scenario: Filter executed actions by rule

- **WHEN** `GET /api/approvals/actions/executed?rule_id=rule-uuid-5678` is called
- **THEN** the API MUST return only executed actions where `rule_id = "rule-uuid-5678"`

#### Scenario: No executed actions exist

- **WHEN** `GET /api/approvals/actions/executed` is called and no executed actions exist
- **THEN** the API MUST return an empty array
- **AND** the response status MUST be 200

---

### Requirement: Create approval rule API

The dashboard API SHALL expose `POST /api/approvals/rules` which creates a new approval rule.

The request body MUST contain:
- `name` (string) -- human-readable rule name
- `tool_name` (string) -- name of the tool this rule applies to
- `constraints` (object) -- constraint patterns that trigger auto-approval
- `limit` (integer, optional) -- maximum number of times this rule may auto-approve before expiring (null = unlimited)

The response MUST be an `ApiResponse<ApprovalRule>` containing:
- `id` -- string UUID identifying the rule
- `name` -- rule name
- `tool_name` -- tool name the rule applies to
- `constraints` -- constraint patterns
- `limit` -- usage limit (null = unlimited)
- `usage_count` -- current count of times the rule has been used (initially 0)
- `active` -- boolean indicating if the rule is currently active (initially true)
- `created_at` -- ISO 8601 timestamp when the rule was created
- `revoked_at` -- ISO 8601 timestamp when the rule was revoked (null if active)

#### Scenario: Create a simple approval rule

- **WHEN** `POST /api/approvals/rules` is called with:
  ```json
  {
    "name": "Auto-approve internal emails",
    "tool_name": "email",
    "constraints": { "recipient_domain": "example.com" }
  }
  ```
- **THEN** the rule MUST be created with `active = true`
- **AND** the response MUST include the rule ID, creation timestamp, and `usage_count = 0`
- **AND** the response status MUST be 201

#### Scenario: Create a rule with usage limit

- **WHEN** `POST /api/approvals/rules` is called with `limit = 10`
- **THEN** the rule MUST be created with the specified limit
- **AND** subsequent approvals using this rule MUST be blocked once `usage_count >= limit`

#### Scenario: Rule with invalid constraint format

- **WHEN** `POST /api/approvals/rules` is called with malformed or invalid constraints
- **THEN** the API MUST return a 400 response with a validation error

---

### Requirement: Create rule from action API

The dashboard API SHALL expose `POST /api/approvals/rules/from-action` which creates a new approval rule from an existing action.

The request body MUST contain:
- `action_id` (string UUID) -- ID of the action to base the rule on
- `name` (string) -- human-readable rule name
- `limit` (integer, optional) -- usage limit for the new rule (null = unlimited)

The response MUST be an `ApiResponse<ApprovalRule>` with the new rule object.

This endpoint SHALL:
- Fetch the action by ID
- Extract its `tool_name` and `constraints`
- Create a new rule with those values plus the provided name and optional limit
- Return the created rule

#### Scenario: Create rule from pending action

- **WHEN** `POST /api/approvals/rules/from-action` is called with an action ID and a rule name
- **THEN** a new rule MUST be created with:
  - `tool_name` from the action
  - `constraints` from the action
  - `name` from the request
  - `active = true`
  - `usage_count = 0`
- **AND** the action itself is NOT modified
- **AND** the response status MUST be 201

#### Scenario: Create rule from action with usage limit

- **WHEN** `POST /api/approvals/rules/from-action` is called with `limit = 5`
- **THEN** the created rule MUST have `limit = 5`

#### Scenario: Source action does not exist

- **WHEN** `POST /api/approvals/rules/from-action` is called with a non-existent action ID
- **THEN** the API MUST return a 404 response

---

### Requirement: Approval rules list API

The dashboard API SHALL expose `GET /api/approvals/rules` which returns a paginated list of approval rules.

The endpoint SHALL accept the following query parameters:
- `offset` (integer, optional, default 0) -- pagination offset
- `limit` (integer, optional, default 50) -- maximum number of rules to return
- `tool_name` (string, optional) -- filter by tool name
- `active_only` (boolean, optional, default false) -- if true, return only active (non-revoked) rules

The response MUST be a `PaginatedResponse<ApprovalRule>` with the same schema as individual rule objects.

#### Scenario: Fetch all approval rules

- **WHEN** `GET /api/approvals/rules` is called
- **THEN** the API MUST return all rules (active and revoked) with pagination
- **AND** each rule MUST include `id`, `name`, `tool_name`, `constraints`, `limit`, `usage_count`, `active`, `created_at`, and `revoked_at`
- **AND** the response status MUST be 200

#### Scenario: Filter rules by tool name

- **WHEN** `GET /api/approvals/rules?tool_name=email` is called
- **THEN** the API MUST return only rules where `tool_name = "email"`

#### Scenario: Filter to active rules only

- **WHEN** `GET /api/approvals/rules?active_only=true` is called
- **THEN** the API MUST return only rules where `active = true` (i.e., `revoked_at = null`)

#### Scenario: No rules exist

- **WHEN** `GET /api/approvals/rules` is called and no rules have been created
- **THEN** the API MUST return an empty array
- **AND** the response status MUST be 200

---

### Requirement: Approval rule detail API

The dashboard API SHALL expose `GET /api/approvals/rules/{ruleId}` which returns full details for a specific approval rule.

The response MUST be an `ApiResponse<ApprovalRule>` with all rule fields.

#### Scenario: Fetch details for a specific rule

- **WHEN** `GET /api/approvals/rules/rule-uuid-5678` is called
- **THEN** the API MUST return the full rule object
- **AND** the response status MUST be 200

#### Scenario: Rule not found

- **WHEN** `GET /api/approvals/rules/nonexistent-id` is called
- **THEN** the API MUST return a 404 response

---

### Requirement: Revoke approval rule API

The dashboard API SHALL expose `POST /api/approvals/rules/{ruleId}/revoke` which deactivates an approval rule.

The response MUST be an `ApiResponse<ApprovalRule>` with the rule's updated state:
- `active` MUST be changed to `false`
- `revoked_at` MUST be set to the current timestamp

Once revoked, the rule MUST NOT be used to auto-approve any new actions.

#### Scenario: Revoke an active rule

- **WHEN** `POST /api/approvals/rules/rule-uuid-5678/revoke` is called
- **THEN** the rule's `active` MUST be changed to `false`
- **AND** `revoked_at` MUST be set to the current timestamp
- **AND** the response status MUST be 200

#### Scenario: Revoke an already-revoked rule

- **WHEN** `POST /api/approvals/rules/rule-uuid-5678/revoke` is called and the rule is already revoked
- **THEN** the API MUST return a 409 response (conflict)

#### Scenario: Revoked rule no longer matches actions

- **WHEN** a rule has been revoked and a new action arrives matching its constraints
- **THEN** the action MUST NOT be auto-approved
- **AND** it MUST remain in `pending` status

---

### Requirement: Rule constraint suggestions API

The dashboard API SHALL expose `GET /api/approvals/rules/suggestions/{actionId}` which returns suggested constraint patterns for creating a rule based on an action.

The response MUST be an `ApiResponse<RuleConstraintSuggestion>` containing:
- `action_id` -- string UUID of the source action
- `tool_name` -- the action's tool name
- `current_constraints` -- the action's current constraints
- `suggested_constraints` -- suggested subset or variant of constraints that might be useful for a rule

The suggested constraints SHOULD represent a generalization or filtering of the current action's constraints that would be practical for a rule.

#### Scenario: Get suggestions for creating a rule from an action

- **WHEN** `GET /api/approvals/rules/suggestions/action-uuid-1234` is called
- **THEN** the API MUST return the action's constraints and suggested constraint patterns
- **AND** the suggestions SHOULD include practical variations (e.g., if the action has `recipient_domain`, suggest that as a filterable constraint)
- **AND** the response status MUST be 200

#### Scenario: Action does not exist

- **WHEN** `GET /api/approvals/rules/suggestions/nonexistent-id` is called
- **THEN** the API MUST return a 404 response

---

### Requirement: Approvals metrics API

The dashboard API SHALL expose `GET /api/approvals/metrics` which returns aggregated metrics about approvals activity.

The response MUST be an `ApiResponse<ApprovalMetrics>` containing:
- `pending_count` -- integer count of pending actions
- `approved_count` -- integer count of approved actions (lifetime or recent window, e.g., today)
- `rejected_count` -- integer count of rejected actions (lifetime or recent window)
- `expired_count` -- integer count of expired actions (lifetime or recent window)
- `auto_approved_count` -- integer count of actions auto-approved by rules (lifetime or recent window)
- `manual_approved_count` -- integer count of manually approved actions
- `total_rules` -- integer count of all rules (active and revoked)
- `active_rules` -- integer count of active (non-revoked) rules
- `rules_by_tool` -- object mapping tool names to their rule counts
- `actions_by_status_today` -- object mapping status values to their counts for today

#### Scenario: Fetch approvals metrics

- **WHEN** `GET /api/approvals/metrics` is called
- **THEN** the API MUST return a metrics object with all required fields
- **AND** the `pending_count` MUST reflect only actions with `status = "pending"`
- **AND** the `auto_approved_count` MUST reflect only actions with a non-null `rule_id`
- **AND** the response status MUST be 200

#### Scenario: No approvals activity yet

- **WHEN** `GET /api/approvals/metrics` is called and no actions or rules exist
- **THEN** all counts MUST be 0
- **AND** all mapping objects (e.g., `rules_by_tool`) MUST be empty
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
   - Columns: tool name, description (truncated), status badge, created timestamp, expiration countdown, action buttons
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

#### Scenario: Filter actions by tool and status

- **WHEN** a user selects `tool_name = "email"` and `status = "pending"` in the filters
- **THEN** the table MUST update to show only pending email approval actions
- **AND** the metric cards MUST update to reflect the filtered subset

#### Scenario: Action detail dialog

- **WHEN** a user clicks on an action row or "View Details" button
- **THEN** a modal dialog MUST open showing:
  - Action ID
  - Tool name
  - Description
  - Full constraints (formatted JSON or key-value pairs)
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

---

### Requirement: Approval rules management page

The frontend SHALL render an approval rules management page at `/approvals/rules` displaying all rules with detail views and lifecycle management.

The page MUST contain the following sections:

1. **Rules table** with:
   - Columns: rule name, tool name, active/revoked status badge, usage count, limit (if set), created date, action buttons
   - Filterable by: tool name, active status
   - Sortable by: name, created date, usage count
   - Row click opens rule detail dialog
   - Action buttons: View Details, Revoke/Unrevoke

2. **Create new rule button** that opens a dialog for manual rule creation or rule-from-action selection

3. **Pagination** for the rules list

#### Scenario: Rules management page loads

- **WHEN** a user navigates to `/approvals/rules`
- **THEN** the page MUST display all rules (active and revoked) in a table
- **AND** active rules MUST be visually distinguished from revoked rules
- **AND** each rule MUST display its name, tool, usage count, and limit

#### Scenario: Filter rules by tool and active status

- **WHEN** a user selects `tool_name = "email"` and `active_only = true`
- **THEN** the table MUST update to show only active email rules
- **AND** revoked rules MUST be filtered out

#### Scenario: Rule detail dialog

- **WHEN** a user clicks on a rule row or "View Details" button
- **THEN** a modal dialog MUST open showing:
  - Rule name
  - Tool name
  - Constraints (formatted as key-value pairs or JSON)
  - Usage count and limit (if set)
  - Active status with revocation timestamp (if revoked)
  - If active: Revoke button
  - If revoked: Unrevoke button or message

#### Scenario: Revoke rule from detail dialog

- **WHEN** a user clicks "Revoke" in the rule detail dialog
- **THEN** a confirmation dialog MUST appear asking to confirm revocation
- **THEN** the revoke API call MUST be sent
- **AND** the detail dialog MUST update showing the rule as revoked
- **AND** the table MUST refresh with updated status

#### Scenario: Create new rule from action

- **WHEN** a user in the approvals actions page clicks "Create Rule" on an action detail dialog
- **THEN** the page MUST navigate to `/approvals/rules` with a create-from-action dialog pre-populated
- **AND** the dialog MUST show the action's constraints and ask for a rule name and optional limit
- **AND** submitting MUST call `POST /api/approvals/rules/from-action` with the action ID

#### Scenario: Rule at usage limit

- **WHEN** a rule has `limit = 10` and `usage_count = 10`
- **THEN** the rule detail dialog MUST display a "limit reached" indicator
- **AND** subsequent matching actions MUST NOT be auto-approved by this rule

#### Scenario: Empty state when no rules exist

- **WHEN** a user navigates to `/approvals/rules` with no rules created
- **THEN** the page MUST display an empty state message (e.g., "No approval rules yet")
- **AND** a prominent "Create your first rule" button or card MUST be displayed

---

### Requirement: Stale action expiry management

The backend SHALL automatically expire pending approval actions that exceed their expiration time. The system SHOULD:

1. Run the `POST /api/approvals/actions/expire-stale` endpoint periodically (e.g., every 5 minutes or as a background job).
2. Log expired action events.
3. Allow the frontend to manually trigger expiry via an admin action if needed.

#### Scenario: Pending action expires automatically

- **WHEN** a pending action's `expires_at` timestamp is reached
- **AND** the stale expiry job runs
- **THEN** the action's `status` MUST be changed to `"expired"`
- **AND** `decided_by` MUST be set to `"auto-expired"`

#### Scenario: Expired action appears in metrics

- **WHEN** an action is expired
- **THEN** the expired action count in the metrics MUST increase
- **AND** it MUST NOT be included in the pending count

---

### Requirement: Inline action approval from detail view

The frontend detail view for approval actions MUST support approving or rejecting actions directly without leaving the page.

When an action is approved or rejected from the detail view:
- The action's status MUST be updated
- If approved with a rule, the rule's usage count MUST be incremented
- The table view MUST refresh to reflect the status change
- A toast notification MUST confirm the decision

#### Scenario: Approve action and optionally create rule

- **WHEN** a user approves an action from the detail dialog
- **THEN** the action MUST be approved via the API
- **AND** the user MAY optionally click "Create Rule from This Action" to start the rule creation flow
- **AND** the dialog MUST close or transition to the rule creation form

---

### Requirement: Real-time metrics updates on approvals page

The frontend SHALL refresh the approvals metrics cards and actions table periodically (e.g., every 30 seconds) to reflect near-real-time updates.

The page SHOULD support:
- Auto-refresh toggle (enabled by default)
- Manual refresh button
- Configurable refresh interval (via settings or default 30 seconds)
- Toast notification when new actions arrive or pending count changes significantly

#### Scenario: Metrics card updates when new action arrives

- **WHEN** the `/approvals` page is open with auto-refresh enabled
- **AND** a new pending action is created in another session
- **THEN** the page MUST fetch updated metrics and refresh the metrics cards
- **AND** the new action MUST appear in the table
- **AND** an optional toast notification MAY inform the user of the new action

#### Scenario: User can disable auto-refresh

- **WHEN** a user toggles auto-refresh off on the `/approvals` page
- **THEN** the page MUST stop automatically fetching metrics and actions
- **AND** the user can still manually click "Refresh" to update immediately

