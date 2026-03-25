## MODIFIED Requirements

### Requirement: MCP Tool Surface (13 Tools)

The module registers exactly 16 stable MCP tools when enabled (13 existing + 3 new autonomy suggestion tools).

#### Scenario: Queue management tools (7)

- **WHEN** the approvals module is registered
- **THEN** the following 7 queue tools are available: `list_pending_actions`, `show_pending_action`, `approve_action`, `reject_action`, `pending_action_count`, `expire_stale_actions`, `list_executed_actions`

#### Scenario: Rule management tools (6)

- **WHEN** the approvals module is registered
- **THEN** the following 6 rule tools are available: `create_approval_rule`, `create_rule_from_action`, `list_approval_rules`, `show_approval_rule`, `revoke_approval_rule`, `suggest_rule_constraints`

#### Scenario: Autonomy suggestion tools (3)

- **WHEN** the approvals module is registered
- **THEN** the following 3 suggestion tools are available: `list_promotion_suggestions`, `confirm_promotion_suggestion`, `dismiss_promotion_suggestion`

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

#### Scenario: Gated tools completeness for outbound butlers

- **WHEN** a butler registers outbound communication tools (send, reply, or delivery tools for any channel)
- **THEN** ALL such tools MUST be listed in the butler's `gated_tools` config
- **AND** omitting any registered outbound tool from `gated_tools` is a spec violation

#### Scenario: Autonomy tracker config keys

- **WHEN** `[modules.approvals]` includes `promotion_threshold`, `velocity_window`, or `suggestion_cooldown_days`
- **THEN** the module MUST pass these values to the autonomy tracker
- **AND** default values MUST apply for any absent keys (threshold=5, velocity_window=10, cooldown_days=30)

## ADDED Requirements

### Requirement: Post-Approval Tracker Hook

After a pending action is manually approved by a human actor (status transitions from `pending` to `approved`), the approvals module MUST invoke the autonomy tracker to record the approval and check for promotion threshold crossings.

#### Scenario: Tracker invoked after manual approval

- **WHEN** `approve_action` successfully transitions an action to `approved`
- **THEN** the autonomy tracker's `record_approval` function MUST be called with the action's `tool_name`, `tool_args`, `action_id`, `requested_at`, and `decided_at`
- **AND** tracker invocation MUST NOT block or delay the approval response to the caller

#### Scenario: Tracker failure does not block approval

- **WHEN** the autonomy tracker raises an exception during `record_approval`
- **THEN** the approval MUST still succeed
- **AND** the tracker error MUST be logged at WARNING level
- **AND** no approval data SHALL be lost

### Requirement: Post-Execution Demotion Hook

After an auto-approved action fails execution (execution_result has `success: false`), the approvals module MUST invoke the autonomy suggestion engine to create a demotion suggestion for the standing rule that auto-approved the action.

#### Scenario: Execution failure triggers demotion check

- **WHEN** `execute_approved_action` completes with `success: false`
- **AND** the action has `approval_rule_id` set (was auto-approved)
- **THEN** the suggestion engine's `create_demotion_suggestion` function MUST be called with the action's details and rule ID

#### Scenario: Manual approval execution failure does not trigger demotion

- **WHEN** `execute_approved_action` completes with `success: false`
- **AND** the action has `approval_rule_id` as `null` (was manually approved)
- **THEN** no demotion suggestion SHALL be created

### Requirement: Rule Creation Supersedes Pending Suggestions

When a new standing rule is created (via `create_approval_rule` or `create_rule_from_action`), the module MUST check for and supersede any pending promotion suggestions that the new rule would cover.

#### Scenario: New rule supersedes matching suggestion

- **WHEN** `create_approval_rule` is called and succeeds
- **THEN** the module MUST query for pending suggestions where `tool_name` matches and `representative_args` would be matched by the new rule's constraints
- **AND** any matching pending suggestions MUST be transitioned to `superseded` status
