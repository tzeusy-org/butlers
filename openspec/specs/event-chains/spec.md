# Event Chains

## Purpose
Provides post-event trigger sequences that fire workflows after calendar events complete, deadlines pass, or deadline thresholds are crossed. Event chains are stored in a dedicated `event_chains` table and materialized into one-shot `scheduled_tasks` entries when triggered. Actions execute in array order with cumulative delays.

## ADDED Requirements

### Requirement: Event Chain Definition
An event chain is a named sequence of actions triggered by a temporal event. Chains are stored in an `event_chains` table with fields: `id` (UUID), `name` (unique per butler), `trigger_type` (enum: `calendar_event_end`, `deadline_passed`, `deadline_threshold`), `trigger_reference` (string -- calendar event_id or deadline task UUID), `actions` (JSONB array), `status` (enum: `active`, `paused`, `fired`, `failed`), `butler_name`, `created_at`, `updated_at`.

#### Scenario: Create event chain triggered by calendar event end
- **WHEN** `event_chain_create(name="post-dentist", trigger_type="calendar_event_end", trigger_reference="google-event-123", actions=[{action_type: "prompt", delay_minutes: 0, prompt: "Log the dentist visit and update next appointment"}, {action_type: "prompt", delay_minutes: 1440, prompt: "Send follow-up care reminder"}])` is called
- **THEN** an `event_chains` row is inserted with `status='active'`
- **AND** the chain's UUID is returned

#### Scenario: Create event chain triggered by deadline passing
- **WHEN** `event_chain_create(name="post-tax-filing", trigger_type="deadline_passed", trigger_reference="<deadline-task-uuid>", actions=[{action_type: "job", delay_minutes: 60, job_name: "archive_tax_documents"}])` is called
- **THEN** an `event_chains` row is inserted with `status='active'`

#### Scenario: Duplicate chain name rejected
- **WHEN** `event_chain_create(name="post-dentist", ...)` is called and a chain with that name already exists for this butler
- **THEN** a `ValueError` is raised

### Requirement: Event Chain Action Schema
Each action in the `actions` array SHALL have: `action_type` (enum: `prompt`, `job`), `delay_minutes` (integer >= 0, delay after trigger or previous action), `prompt` (string, required for prompt type), `job_name` (string, required for job type), `job_args` (object, optional for job type). Actions are executed in array order with cumulative delays.

#### Scenario: Prompt action with no delay
- **WHEN** a chain fires and the first action has `{action_type: "prompt", delay_minutes: 0, prompt: "Log visit"}`
- **THEN** a one-shot `scheduled_task` is created with `dispatch_mode='prompt'`, `prompt="Log visit"`, and `next_run_at=now()`

#### Scenario: Job action with delay
- **WHEN** a chain fires and an action has `{action_type: "job", delay_minutes: 60, job_name: "archive_docs"}`
- **THEN** a one-shot `scheduled_task` is created with `dispatch_mode='job'`, `job_name="archive_docs"`, and `next_run_at=now() + 60 minutes`

#### Scenario: Invalid action type rejected
- **WHEN** an action has `action_type="webhook"`
- **THEN** a `ValueError` is raised listing valid action types

### Requirement: Event Chain Trigger Detection
The scheduler's `tick()` function SHALL detect chain triggers during each evaluation cycle.

#### Scenario: Calendar event end triggers chain
- **WHEN** `tick()` runs and a calendar projection event with `event_id` matching a chain's `trigger_reference` has `end_at < now()`
- **AND** the chain has `status='active'` and `trigger_type='calendar_event_end'`
- **AND** the chain has not previously fired for this event occurrence
- **THEN** the chain's actions are materialized as one-shot scheduled_tasks
- **AND** the chain's `status` transitions to `fired`

#### Scenario: Deadline passed triggers chain
- **WHEN** a deadline task transitions to `deadline_status='expired'` or `deadline_status='completed'`
- **AND** a chain with `trigger_type='deadline_passed'` references that deadline's UUID
- **THEN** the chain's actions are materialized as one-shot scheduled_tasks
- **AND** the chain's `status` transitions to `fired`

#### Scenario: Deadline threshold triggers chain
- **WHEN** a deadline threshold fires with a specific severity
- **AND** a chain with `trigger_type='deadline_threshold'` references that deadline's UUID
- **AND** the chain's `trigger_reference` includes the threshold severity (format: `<deadline-uuid>:<severity>`)
- **THEN** the chain's actions are materialized as one-shot scheduled_tasks

#### Scenario: Paused chain does not fire
- **WHEN** a trigger condition is met for a chain with `status='paused'`
- **THEN** the chain does NOT fire
- **AND** the trigger event is not consumed (chain can fire if resumed before next occurrence)

### Requirement: Event Chain Depth Limit
Event chains SHALL NOT cascade beyond 3 levels. A chain action that creates a new deadline or event SHALL NOT itself define a new event chain. The system SHALL track chain depth and reject chain creation that would exceed the limit.

#### Scenario: Direct chain fires normally
- **WHEN** a chain fires at depth 0 (directly triggered by a calendar event or deadline)
- **THEN** all actions execute normally

#### Scenario: Chain depth exceeded
- **WHEN** a chain action at depth 3 attempts to trigger another chain
- **THEN** the nested chain is skipped
- **AND** a warning is logged with the chain name and depth

### Requirement: Event Chain CRUD Tools
The module SHALL register MCP tools: `event_chain_create`, `event_chain_update`, `event_chain_list`, `event_chain_delete`.

#### Scenario: List chains by trigger type
- **WHEN** `event_chain_list(trigger_type="calendar_event_end")` is called
- **THEN** only chains with `trigger_type='calendar_event_end'` for this butler are returned

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

### Requirement: Event Chain Action Materialization
When a chain fires, each action is materialized as a one-shot `scheduled_task` with `source='chain'`, a unique name derived from the chain name and action index, `until_at` set to auto-disable after firing, and `trigger_source` set to `chain:<chain-name>`.

#### Scenario: Materialized tasks have chain lineage
- **WHEN** chain "post-dentist" with 2 actions fires
- **THEN** two `scheduled_tasks` rows are created:
  - `name="chain:post-dentist:0"`, `source='chain'`, `next_run_at=now()`
  - `name="chain:post-dentist:1"`, `source='chain'`, `next_run_at=now() + delay_minutes`
- **AND** each has `until_at` set to `next_run_at + 1 minute` for auto-disable after firing

#### Scenario: Materialized tasks dispatch normally
- **WHEN** the tick loop encounters a materialized chain task that is due
- **THEN** it dispatches via the standard prompt or job dispatch path
- **AND** `trigger_source` is set to `chain:<chain-name>`
