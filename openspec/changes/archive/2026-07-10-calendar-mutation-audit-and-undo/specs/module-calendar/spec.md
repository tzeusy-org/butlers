## ADDED Requirements

### Requirement: Reversible Mutation Pre-State Capture

The calendar module SHALL capture the pre-mutation event state of every reversible
user-lane mutation into the recorded `action_result` so an inverse is
reconstructable. For `workspace_user_update` and `workspace_user_delete`, the
captured pre-image (the event state fetched before the write) MUST be stored in
the existing `action_result` JSONB column alongside the existing post-mutation
outcome, with no schema change. The capture MUST reuse the existing pre-write
provider fetch (`existing_event`) where available. This pre-image is the contract
the dashboard undo endpoint reverse-applies.

#### Scenario: Update captures the pre-mutation event state

- **WHEN** `calendar_update_event` resolves an existing event and applies a patch
- **THEN** the finalized `action_result` for the `workspace_user_update` row
  includes the pre-mutation event state (at least title, start_at, end_at,
  timezone, location, description, attendees, recurrence_rule, and the resolved
  calendar id) under a stable key, alongside the existing post-mutation outcome
- **AND** the pre-state is captured from the `existing_event` already fetched
  before the PATCH, adding no extra provider round-trip

#### Scenario: Delete captures the pre-deletion event state

- **WHEN** `calendar_delete_event` removes an existing event
- **THEN** the finalized `action_result` for the `workspace_user_delete` row
  includes the pre-deletion event state (the fields needed to recreate the event)
  under the same stable key
- **AND** the captured pre-image is sufficient for an inverse
  `calendar_create_event` to recreate the event on its home calendar

#### Scenario: Pre-state is absent for non-reversible or non-applied outcomes

- **WHEN** a mutation finalizes with status `failed` or `noop` (e.g. the target
  event was not found), or the mutation is a create (which has no pre-image)
- **THEN** no pre-mutation state is required in `action_result`
- **AND** the undo endpoint treats the absence of pre-state on an otherwise
  reversible action as a fail-fast condition (it does not guess an inverse)

#### Scenario: Idempotent-replay path is unchanged by capture

- **WHEN** a mutation is replayed under the same `request_id` and resolves via
  `_load_projection_action` / `_prepare_workspace_mutation`
- **THEN** the existing replay behavior is preserved (the prior `action_result` is
  returned with `idempotent_replay=true`)
- **AND** capturing pre-state does not alter the `idempotency_key`, the action
  status transitions, or the replay contract
