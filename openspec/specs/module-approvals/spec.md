# Approval Gating Module

## Purpose

The Approvals module is a reusable execution-control module that butlers load locally to intercept configured high-impact tool invocations before execution, park unapproved invocations as durable pending actions, support manual approve/reject/expire workflows, and auto-approve matching invocations through standing approval rules.

## ADDED Requirements

### Requirement: Gate Wrapper Interception

The module wraps configured MCP tools at FastMCP registration time so that gated tool calls are serialized into pending actions before the original handler executes. Unknown configured gated tools are skipped during wrapping with warning logs.

#### Scenario: Gated tool is called with no matching standing rule

- **WHEN** a gated tool is invoked by an LLM session
- **THEN** the wrapper serializes the call into a PendingAction with status `pending` and a computed `expires_at`
- **AND** a structured `{"status":"pending_approval","action_id":"...","message":"...","risk_tier":"..."}` response is returned to the caller
- **AND** an `action_queued` audit event is recorded with path `pending`

#### Scenario: Gated tool is called with a matching standing rule

- **WHEN** a gated tool is invoked and a standing rule matches the tool name and arguments
- **THEN** the action is persisted with status `approved` and `approval_rule_id` set to the matching rule
- **AND** the original tool function is executed immediately via the shared executor
- **AND** `action_queued` and `action_auto_approved` audit events are recorded
- **AND** the rule's `use_count` is incremented

#### Scenario: Unknown gated tool configured

- **WHEN** a tool name in `gated_tools` config is not found in registered MCP tools
- **THEN** the gate wrapper skips wrapping for that tool with a warning log
- **AND** remaining valid gated tools are still wrapped

### Requirement: Pending Actions Queue

The `pending_actions` table is a durable queue and audit log for approval-gated tool invocations. It stores `id`, `tool_name`, `tool_args` (JSONB), `status`, `requested_at`, and optional fields `agent_summary`, `session_id`, `expires_at`, `decided_by`, `decided_at`, `execution_result`, `approval_rule_id`.

#### Scenario: List pending actions with status filter

- **WHEN** `list_pending_actions` is called with an optional status filter and limit
- **THEN** matching rows are returned ordered by `requested_at DESC`
- **AND** an invalid status value returns an error dict

#### Scenario: Show pending action detail

- **WHEN** `show_pending_action` is called with an action_id
- **THEN** the full PendingAction row is returned as a serialized dict
- **AND** an invalid UUID or missing action returns an error dict

#### Scenario: Count pending actions by status

- **WHEN** `pending_action_count` is called
- **THEN** a dict with `total` and `by_status` counts is returned

### Requirement: Status Transition Contract

Valid status transitions are `pending -> approved|rejected|expired`, `approved -> executed`, and `rejected|expired|executed` are terminal. Invalid transitions raise `InvalidTransitionError`.

#### Scenario: Approve a pending action

- **WHEN** `approve_action` is called with a valid action_id and authenticated human actor context
- **THEN** a compare-and-set UPDATE transitions status from `pending` to `approved`
- **AND** the shared executor runs the original tool function
- **AND** status advances to `executed` with `execution_result` persisted
- **AND** an `action_approved` audit event is recorded

#### Scenario: Approve with concurrent race

- **WHEN** two concurrent approve calls target the same pending action
- **THEN** the compare-and-set ensures only one succeeds (WHERE status = 'pending')
- **AND** the losing call receives a transition error with the current status

#### Scenario: Reject a pending action

- **WHEN** `reject_action` is called with a valid action_id and authenticated human actor
- **THEN** status transitions from `pending` to `rejected` with `decided_by` set to `human:<actor_id> (reason: <escaped_reason>)`
- **AND** an `action_rejected` audit event is recorded

#### Scenario: Expire stale actions

- **WHEN** `expire_stale_actions` is called
- **THEN** all pending actions where `expires_at < now()` are transitioned to `expired`
- **AND** an `action_expired` audit event is recorded for each

#### Scenario: Already-executed action is replayed

- **WHEN** the executor is called for an action that is already `executed`
- **THEN** the stored `execution_result` is returned (idempotent replay)
- **AND** no second execution occurs

### Requirement: Standing Rules CRUD and Matching

Standing approval rules enable auto-approval of repeatable safe invocations. Each rule has `id`, `tool_name`, `arg_constraints` (JSONB), `description`, `created_at`, `active`, and optional `created_from`, `expires_at`, `max_uses`, `use_count`.

#### Scenario: Create a standing rule

- **WHEN** `create_approval_rule` is called with authenticated human actor, tool name, constraints, and description
- **THEN** a new active rule is persisted
- **AND** a `rule_created` audit event is recorded
- **AND** high-risk tools require at least one `exact` or `pattern` constraint and bounded scope

#### Scenario: Create rule from pending action

- **WHEN** `create_rule_from_action` is called with an action_id
- **THEN** sensitivity classification generates suggested constraints (sensitive args get `exact`, others get `any`)
- **AND** optional `constraint_overrides` are merged on top
- **AND** the rule is persisted with `created_from` linking to the source action

#### Scenario: Rule matching precedence

- **WHEN** multiple standing rules match a tool invocation
- **THEN** the most specific rule wins using deterministic precedence: (1) higher constraint specificity, (2) bounded scope before unbounded, (3) newer rule before older, (4) lexical rule ID tie-breaker

#### Scenario: Rule constraint evaluation

- **WHEN** a rule's `arg_constraints` are checked against tool args
- **THEN** typed constraints (`exact`, `pattern`, `any`) are evaluated
- **AND** legacy formats (`"*"` wildcard, plain exact values) remain supported
- **AND** empty constraints `{}` match any invocation of the tool
- **AND** `pattern` type uses fnmatch-style glob matching

#### Scenario: Rule eligibility filtering

- **WHEN** rules are fetched for matching
- **THEN** only active rules are considered
- **AND** expired rules (`expires_at < now`) are excluded
- **AND** exhausted rules (`use_count >= max_uses`) are excluded

#### Scenario: Revoke a standing rule

- **WHEN** `revoke_approval_rule` is called with authenticated human actor
- **THEN** the rule's `active` flag is set to `false`
- **AND** a `rule_revoked` audit event is recorded
- **AND** already-revoked rules return an error

#### Scenario: Suggest rule constraints

- **WHEN** `suggest_rule_constraints` is called for a pending action
- **THEN** the sensitivity classifier returns per-arg constraints without creating a rule
- **AND** sensitive args (heuristic or module-declared) suggest `exact` pinned to current value
- **AND** non-sensitive args suggest `any`

### Requirement: Risk Tier Enforcement

Tools are classified into explicit risk tiers (`low`, `medium`, `high`, `critical`) via policy metadata in `[modules.approvals]` config. Default risk tier is `medium`.

#### Scenario: High-risk rule creation requires narrow constraints

- **WHEN** a standing rule is created for a `high` or `critical` risk-tier tool
- **THEN** at least one `exact` or `pattern` arg constraint is required
- **AND** bounded scope via `expires_at` or `max_uses` is required
- **AND** validation fails with a descriptive error if either is missing

#### Scenario: Low/medium risk rules allow broad constraints

- **WHEN** a standing rule is created for a `low` or `medium` risk-tier tool
- **THEN** empty constraints and unbounded scope are permitted

### Requirement: Shared Executor Path

Both auto-approved and manually approved actions execute through `execute_approved_action()`. The executor calls the original tool function, normalizes non-dict return values to `{"value": ...}`, persists `execution_result` with `success`/`executed_at` (and `result` or `error`), updates action status to `executed`, and increments `use_count` for auto-approved executions.

#### Scenario: Tool execution succeeds

- **WHEN** the executor calls the original tool function successfully
- **THEN** the result is persisted as `{"success": true, "result": {...}, "executed_at": "..."}`
- **AND** status transitions from `approved` to `executed`
- **AND** an `action_execution_succeeded` audit event is recorded

#### Scenario: Tool execution fails with exception

- **WHEN** the tool function raises an exception
- **THEN** the error is captured and persisted as `{"success": false, "error": "...", "executed_at": "..."}`
- **AND** status still advances to `executed` (execution was attempted)
- **AND** an `action_execution_failed` audit event is recorded

#### Scenario: Manual approval without executor wired

- **WHEN** `approve_action` is called but no tool executor is wired
- **THEN** status advances to `executed` with `execution_result = null`

#### Scenario: At-most-once execution with concurrency lock

- **WHEN** concurrent execution attempts target the same action
- **THEN** a per-action asyncio lock (WeakValueDictionary-based) ensures at-most-once execution

### Requirement: Immutable Audit Events

The `approval_events` table is an append-only audit log. Events include `event_type`, `action_id`, `rule_id`, `actor`, `reason`, `event_metadata` (JSONB), and `occurred_at`. A database trigger prevents UPDATE and DELETE operations.

#### Scenario: Audit event creation for all state transitions

- **WHEN** any approval state transition occurs (queued, auto-approved, approved, rejected, expired, execution succeeded, execution failed, rule created, rule revoked)
- **THEN** an immutable event row is inserted with the corresponding `ApprovalEventType` value
- **AND** actor, action_id/rule_id, reason, and metadata are captured

#### Scenario: Audit event types

- **WHEN** events are recorded
- **THEN** the following canonical event types are used: `action_queued`, `action_auto_approved`, `action_approved`, `action_rejected`, `action_expired`, `action_execution_succeeded`, `action_execution_failed`, `rule_created`, `rule_revoked`

#### Scenario: Audit event immutability

- **WHEN** an UPDATE or DELETE is attempted on `approval_events`
- **THEN** the database trigger rejects the operation
- **AND** the row remains unchanged

### Requirement: Redaction

Sensitive fields in tool arguments and execution results are redacted before persistence or presentation. Redaction uses sensitivity classification (module metadata, heuristic arg name matching, default).

#### Scenario: Sensitive tool args redaction

- **WHEN** tool args are redacted for safe exposure
- **THEN** args matching sensitive heuristic names (`to`, `recipient`, `email`, `password`, `token`, `secret`, `key`, `api_key`, `auth`, `credential`, `credentials`, `url`, `uri`, `amount`, `price`, `cost`, `account`) are replaced with `***REDACTED***`
- **AND** module-declared `ToolMeta.arg_sensitivities` take precedence over heuristics

#### Scenario: Execution result error redaction

- **WHEN** an execution result contains an `error` field
- **THEN** the error message is replaced with `***REDACTED***` (may contain secrets in stack traces)
- **AND** result values are preserved (controlled by tool implementation)

#### Scenario: Viewer-based redaction for presentation

- **WHEN** approval details are presented to a viewer
- **THEN** only the action owner sees unredacted sensitive details
- **AND** all other viewers see redacted values

### Requirement: Retention Policy

Configurable retention windows control automatic cleanup of approvals data: `pending_actions_retention_days` (default 90), `approval_rules_retention_days` (default 180), `approval_events_retention_days` (default 365).

#### Scenario: Cleanup old actions

- **WHEN** `cleanup_old_actions` runs
- **THEN** only terminal-status actions (`approved`, `rejected`, `expired`, `executed`) older than the retention window are deleted
- **AND** pending actions are never cleaned up automatically
- **AND** a dry-run mode returns counts without deleting

#### Scenario: Cleanup old rules

- **WHEN** `cleanup_old_rules` runs
- **THEN** only inactive rules (`active=false`) older than the retention window are deleted

#### Scenario: Cleanup old events requires privilege

- **WHEN** `cleanup_old_events` is called without `privileged=True`
- **THEN** a `PermissionError` is raised
- **AND** no events are deleted
- **WHEN** `cleanup_old_events` is called with `privileged=True`
- **THEN** events older than the retention window are deleted (bypasses immutability trigger)

### Requirement: MCP Tool Surface (13 Tools)

The module registers exactly 13 stable MCP tools when enabled.

#### Scenario: Queue management tools (7)

- **WHEN** the approvals module is registered
- **THEN** the following 7 queue tools are available: `list_pending_actions`, `show_pending_action`, `approve_action`, `reject_action`, `pending_action_count`, `expire_stale_actions`, `list_executed_actions`

#### Scenario: Rule management tools (6)

- **WHEN** the approvals module is registered
- **THEN** the following 6 rule tools are available: `create_approval_rule`, `create_rule_from_action`, `list_approval_rules`, `show_approval_rule`, `revoke_approval_rule`, `suggest_rule_constraints`

#### Scenario: List executed actions with filters

- **WHEN** `list_executed_actions` is called with optional `tool_name`, `rule_id`, `since`, `limit` filters
- **THEN** only executed actions matching the filters are returned ordered by `decided_at DESC`

### Requirement: Configuration Contract

Module config is declared under `[modules.approvals]` in `butler.toml`.

#### Scenario: Valid config with gated tools

- **WHEN** `[modules.approvals]` is configured with `enabled = true`, `default_expiry_hours`, `default_risk_tier`, and `gated_tools` mapping
- **THEN** the module applies gate wrappers to configured tools with per-tool expiry and risk tier overrides

#### Scenario: Missing or disabled config

- **WHEN** `[modules.approvals]` is absent or `enabled = false`
- **THEN** the module does not wrap any tools and no approval gates are active

### Requirement: Authorization Model

The approvals module is a single-user control surface. Decision-bearing actions require authenticated human identity.

#### Scenario: Decision action with authenticated human actor

- **WHEN** `approve_action`, `reject_action`, `create_approval_rule`, `create_rule_from_action`, or `revoke_approval_rule` is called
- **THEN** the actor context is extracted from the FastMCP `AccessToken`
- **AND** the actor must have `type` of `human` or `user`, `authenticated = true`, and a non-empty `id`
- **AND** if any check fails, a structured error with `error_code: "human_actor_required"` is returned

#### Scenario: Auto-approval via standing rules

- **WHEN** a standing rule auto-approves an action
- **THEN** the action is treated as pre-approval by the human who created the rule
- **AND** the `decided_by` field is set to `rule:<rule_id>`

### Requirement: [TARGET-STATE] Batch Approve/Reject

Batch approval/rejection for homogeneous low-risk items when explicit user intent is clear.

#### Scenario: Bulk approval of homogeneous actions

- **WHEN** multiple pending actions share the same tool and similar arguments
- **THEN** a batch approve operation processes all in one flow

### Requirement: [TARGET-STATE] Rule Blast Radius Preview

Show estimated blast radius before creating broad rules.

#### Scenario: Preview matching actions for a proposed rule

- **WHEN** an operator previews a proposed rule's constraints
- **THEN** the system shows how many historical and pending actions would match

### Requirement: [TARGET-STATE] API Mutation Endpoints

REST API write endpoints for approval decisions, blocked on auth subsystem.

#### Scenario: API approve/reject/create-rule endpoints

- **WHEN** the auth subsystem is implemented
- **THEN** `POST /api/approvals/actions/{actionId}/approve`, `POST /api/approvals/actions/{actionId}/reject`, `POST /api/approvals/rules`, `POST /api/approvals/rules/from-action`, `POST /api/approvals/rules/{ruleId}/revoke` become functional (currently return 501)
