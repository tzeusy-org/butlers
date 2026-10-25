## MODIFIED Requirements

### Requirement: Event Chain CRUD Tools
The module SHALL register MCP tools: `event_chain_create`, `event_chain_update`, `event_chain_list`, `event_chain_delete`. Creating, updating, pausing, resuming, or deleting a chain changes schedules and, for `prompt` actions, creates obligations at runtime, so those tools SHALL be `administer` capabilities that are never granted to a depth-1 child; `event_chain_list` is a `read` capability. A chain SHALL record a capability set no larger than its creator's grant at creation, and a chain created under exposure SHALL admit every goal it fires exposed.

ID: REQ-event-chains-005
Source: RFC 0038 D4, D5, D8
Scope: v1-mandatory

#### Scenario: List chains by trigger type
- **WHEN** `event_chain_list(trigger_type="calendar_event_end")` is called
- **THEN** only historical chains with `trigger_type='calendar_event_end'` for this butler are returned, without making the trigger writable or runnable
- **AND** these historical rows remain inert; new or explicit updated calendar-end triggers are refused before storage access

#### Scenario: Update chain actions
- **WHEN** `event_chain_update(id, actions=[...])` is called
- **THEN** the chain's actions are replaced
- **AND** if the chain has `status='fired'`, the status resets to `active`

#### Scenario: Delete chain
- **WHEN** `event_chain_delete(id)` is called
- **THEN** the chain row is removed
- **AND** any pending one-shot tasks materialized from this chain are NOT affected (they complete or expire independently)

#### Scenario: Pause and resume chain
- **WHEN** `event_chain_update(id, status="paused")` is called
- **THEN** the chain's status transitions to `paused`
- **AND** `event_chain_update(id, status="active")` resumes it

#### Scenario: A child cannot create a chain
- **WHEN** a depth-1 child calls `event_chain_create` or `event_chain_update`
- **THEN** the broker refuses the call because no `administer` capability is in a child's grant
- **AND** no `event_chains` row is written or changed

### Requirement: Event Chain Action Materialization

When a chain fires, each action SHALL be materialized as a one-shot `scheduled_task` with `source='chain'`, a unique name derived from the chain name and action index, `until_at` set to auto-disable after firing, and `trigger_source` set to `chain:<chain-name>`. A due `prompt` action SHALL submit an obligation admission whose capability set is the chain's recorded set and whose goal input is the recorded prompt, and SHALL NOT spawn a session.

Source: RFC 0038 D2, D5
Scope: v1-mandatory

#### Scenario: Materialized tasks have chain lineage
- **WHEN** chain "post-dentist" with 2 actions fires
- **THEN** two `scheduled_tasks` rows are created:
  - `name="chain:post-dentist:0"`, `source='chain'`, `next_run_at=now()`
  - `name="chain:post-dentist:1"`, `source='chain'`, `next_run_at=now() + delay_minutes`
- **AND** each has `until_at` set to `next_run_at + 1 minute` for auto-disable after firing

#### Scenario: Materialized tasks dispatch normally
- **WHEN** the tick loop encounters a materialized chain task that is due
- **THEN** a `job` action dispatches via the standard job dispatch path, and a `prompt` action submits an obligation admission under the chain's recorded capability set
- **AND** `trigger_source` is set to `chain:<chain-name>`, and the admission's occurrence key is the materialized task name
