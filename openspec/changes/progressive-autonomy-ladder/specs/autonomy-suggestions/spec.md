## ADDED Requirements

### Requirement: Promotion Suggestion Data Model

The `autonomy_suggestions` table MUST be created in the butler's schema with columns: `id` (UUID PK), `pattern_fingerprint` (VARCHAR(64), indexed), `tool_name` (TEXT), `representative_args` (JSONB -- the exact tool_args from the most recent approval that triggered the suggestion), `status` (VARCHAR: `pending`, `confirmed`, `dismissed`, `superseded`), `approval_count_at_creation` (INTEGER -- how many approvals existed when suggestion was created), `created_at` (TIMESTAMPTZ), `decided_at` (TIMESTAMPTZ, nullable), `decided_by` (TEXT, nullable), `resulting_rule_id` (UUID, nullable, FK to approval_rules), `cooldown_until` (TIMESTAMPTZ, nullable -- set on dismissal), `dismissal_reason` (TEXT, nullable).

#### Scenario: Table created via migration

- **WHEN** the Alembic migration runs
- **THEN** the `autonomy_suggestions` table MUST exist with all specified columns
- **AND** an index MUST exist on `pattern_fingerprint`
- **AND** an index MUST exist on `(status, created_at)` for listing active suggestions

#### Scenario: Valid status transitions

- **WHEN** a suggestion status changes
- **THEN** only the following transitions SHALL be valid: `pending -> confirmed`, `pending -> dismissed`, `pending -> superseded`
- **AND** `confirmed`, `dismissed`, and `superseded` are terminal states

### Requirement: Suggestion Scope Description

When presenting a promotion suggestion to the user, the system MUST generate a human-readable scope description showing exactly what the proposed standing rule would auto-approve. The description MUST list every `(arg_key, arg_value)` pair that would be pinned with `exact` match type.

#### Scenario: Scope description for telegram send

- **WHEN** a suggestion is generated for `tool_name="send_telegram"` with `representative_args={"chat_id": "mom_123", "text": "Good morning"}`
- **THEN** the scope description MUST read similar to: "Auto-approve send_telegram when chat_id = 'mom_123' AND text = 'Good morning'"
- **AND** every argument MUST be listed -- none omitted

#### Scenario: Scope description for notify tool

- **WHEN** a suggestion is generated for `tool_name="notify"` with `representative_args={"channel": "email", "to": "mom@example.com", "subject": "Weekly update"}`
- **THEN** the scope description MUST include all three arguments with their exact values

### Requirement: Confirm Promotion Suggestion

The system SHALL provide a `confirm_promotion_suggestion` MCP tool that accepts a `suggestion_id` and an authenticated human actor context. On confirmation, the system MUST create a standing approval rule with `match_type: "exact"` constraints for ALL arguments in `representative_args`, transition the suggestion to `confirmed` status, and record the `resulting_rule_id`.

#### Scenario: Confirm creates exact standing rule

- **WHEN** `confirm_promotion_suggestion` is called with a valid `suggestion_id` and authenticated human actor
- **THEN** a new standing rule MUST be created with `tool_name` from the suggestion
- **AND** `arg_constraints` MUST contain `{"type": "exact", "value": <original_value>}` for every key in `representative_args`
- **AND** the suggestion status MUST transition to `confirmed`
- **AND** `resulting_rule_id` MUST be set to the new rule's ID
- **AND** a `promotion_confirmed` audit event MUST be recorded

#### Scenario: Confirm requires human authentication

- **WHEN** `confirm_promotion_suggestion` is called without authenticated human actor context
- **THEN** the system MUST return a structured error with `error_code: "human_actor_required"`

#### Scenario: Confirm already-decided suggestion

- **WHEN** `confirm_promotion_suggestion` is called for a suggestion with status `confirmed` or `dismissed`
- **THEN** the system MUST return a structured error indicating the suggestion has already been decided

### Requirement: Dismiss Promotion Suggestion

The system SHALL provide a `dismiss_promotion_suggestion` MCP tool that accepts a `suggestion_id`, an optional `reason`, and an authenticated human actor context. On dismissal, the suggestion MUST transition to `dismissed` status with `cooldown_until` set to `now() + suggestion_cooldown_days`.

#### Scenario: Dismiss with cooldown

- **WHEN** `dismiss_promotion_suggestion` is called with a valid `suggestion_id`
- **THEN** the suggestion status MUST transition to `dismissed`
- **AND** `cooldown_until` MUST be set to `now() + 30 days` (or configured cooldown)
- **AND** `dismissal_reason` MUST be set if provided
- **AND** a `promotion_dismissed` audit event MUST be recorded

#### Scenario: Dismiss requires human authentication

- **WHEN** `dismiss_promotion_suggestion` is called without authenticated human actor context
- **THEN** the system MUST return a structured error with `error_code: "human_actor_required"`

### Requirement: List Promotion Suggestions

The system SHALL provide a `list_promotion_suggestions` MCP tool that returns promotion suggestions with optional status filter (default: `pending`) and limit (default: 20).

#### Scenario: List pending suggestions

- **WHEN** `list_promotion_suggestions` is called with no filters
- **THEN** only suggestions with status `pending` MUST be returned
- **AND** results MUST be ordered by `created_at DESC`
- **AND** each result MUST include the human-readable scope description

#### Scenario: List all suggestions

- **WHEN** `list_promotion_suggestions` is called with `status="all"`
- **THEN** suggestions of all statuses MUST be returned

#### Scenario: No pending suggestions

- **WHEN** `list_promotion_suggestions` is called and no pending suggestions exist
- **THEN** an empty list MUST be returned

### Requirement: Supersede Stale Suggestions

When a standing rule is manually created by the user that covers a pattern for which a `pending` promotion suggestion exists, the system MUST automatically transition that suggestion to `superseded` status.

#### Scenario: Manual rule creation supersedes matching suggestion

- **WHEN** a user creates a standing rule via `create_approval_rule` or `create_rule_from_action`
- **AND** a `pending` suggestion exists with the same `tool_name` and matching `representative_args`
- **THEN** the suggestion MUST transition to `superseded`
- **AND** a `promotion_superseded` audit event MUST be recorded

### Requirement: Demotion Suggestion on Execution Failure

When an auto-approved action (matched by a standing rule) fails execution, the system SHALL create a demotion suggestion advising the user to review the standing rule. The demotion suggestion MUST include the failed action details, the rule ID, and the execution error.

#### Scenario: Execution failure creates demotion suggestion

- **WHEN** `execute_approved_action` completes with `success: false` for an auto-approved action
- **AND** the action has an `approval_rule_id` set
- **THEN** a new `autonomy_suggestion` row MUST be created with a special `suggestion_type: "demotion"` and status `pending`
- **AND** the suggestion MUST reference the `approval_rule_id` and include the error details in metadata

#### Scenario: Confirm demotion revokes the rule

- **WHEN** `confirm_promotion_suggestion` is called on a demotion suggestion
- **THEN** the referenced standing rule MUST be revoked (active = false)
- **AND** a `demotion_confirmed` audit event MUST be recorded

#### Scenario: Dismiss demotion keeps the rule active

- **WHEN** `dismiss_promotion_suggestion` is called on a demotion suggestion
- **THEN** the standing rule MUST remain active
- **AND** a `demotion_dismissed` audit event MUST be recorded

### Requirement: Promotion Audit Events

All promotion lifecycle transitions MUST be recorded as immutable audit events in the `approval_events` table using the following new event types: `promotion_suggested`, `promotion_confirmed`, `promotion_dismissed`, `promotion_superseded`, `demotion_suggested`, `demotion_confirmed`, `demotion_dismissed`.

#### Scenario: Suggestion creation records audit event

- **WHEN** a new promotion suggestion is created
- **THEN** a `promotion_suggested` event MUST be recorded with `event_metadata` containing `pattern_fingerprint`, `tool_name`, `approval_count`, and `scope_description`

#### Scenario: All promotion event types are tracked

- **WHEN** any suggestion state transition occurs
- **THEN** the corresponding event type from the defined set MUST be used
- **AND** the event MUST include `actor`, `rule_id` (if applicable), and `reason` (if applicable)

### Requirement: MCP Tool Surface (Suggestions)

The autonomy suggestions feature SHALL add exactly 3 new MCP tools to the approvals module: `list_promotion_suggestions`, `confirm_promotion_suggestion`, `dismiss_promotion_suggestion`.

#### Scenario: New tools are registered

- **WHEN** the approvals module is registered with autonomy features enabled
- **THEN** the 3 suggestion tools MUST be available alongside the existing 13 approval tools
- **AND** the total tool count MUST be 16

### Requirement: Suggestion Type Field

The `autonomy_suggestions` table MUST include a `suggestion_type` column (VARCHAR, default: `"promotion"`) to distinguish promotion suggestions from demotion suggestions. Valid values are `"promotion"` and `"demotion"`.

#### Scenario: Promotion suggestion type

- **WHEN** a suggestion is created because a pattern crossed the promotion threshold
- **THEN** `suggestion_type` MUST be `"promotion"`

#### Scenario: Demotion suggestion type

- **WHEN** a suggestion is created because an auto-approved action failed
- **THEN** `suggestion_type` MUST be `"demotion"`
