## MODIFIED Requirements

### Requirement: Workflow Deadline Authority

The implementation SHALL provide the behavior described by this requirement.
The `workflow_deadline_at` field is the authoritative deadline reference for multi-session recovery workflows.

#### Scenario: Deadline is set at row creation
- **WHEN** a `healing_attempts` row is inserted for a launched workflow
- **THEN** `workflow_deadline_at` is set to `now() + configured_workflow_hard_limit` (default: 60 minutes)
- **AND** this value is never updated after row creation — it is an immutable deadline, not a rolling timeout

#### Scenario: Deadline field is null for legacy rows
- **WHEN** a `healing_attempts` row was created before the `workflow_deadline_at` column was added
- **THEN** `workflow_deadline_at` is NULL
- **AND** restart recovery falls back to the `updated_at + timeout_minutes` heuristic for those rows only

#### Scenario: Deadline authority over updated_at heuristic
- **WHEN** restart recovery evaluates a `healing_attempts` row with `workflow_deadline_at IS NOT NULL`
- **THEN** the `workflow_deadline_at` field is the sole authority for timeout decisions
- **AND** the `updated_at` heuristic is NOT applied to that row
