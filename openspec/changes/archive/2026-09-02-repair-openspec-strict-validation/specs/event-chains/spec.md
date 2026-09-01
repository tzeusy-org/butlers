## MODIFIED Requirements

### Requirement: Event Chain Definition

The implementation SHALL provide the behavior described by this requirement.
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

### Requirement: Event Chain Action Materialization

The implementation SHALL provide the behavior described by this requirement.
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
