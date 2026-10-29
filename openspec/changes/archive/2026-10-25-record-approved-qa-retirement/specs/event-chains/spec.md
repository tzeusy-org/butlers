## MODIFIED Requirements

### Requirement: Event Chain Definition

An event chain SHALL be a named sequence of actions triggered by a temporal event. New writes accept only `deadline_passed` and `deadline_threshold`. The stored historical enum is retained without data rewrite. Chains are stored in an `event_chains` table with fields: `id` (UUID), `name` (unique per butler), `trigger_type` (enum: `calendar_event_end`, `deadline_passed`, `deadline_threshold`), `trigger_reference` (string -- deadline task UUID, or retained historical calendar event_id), `actions` (JSONB array), `status` (enum: `active`, `paused`, `fired`, `failed`), `butler_name`, `created_at`, `updated_at`.

#### Scenario: Calendar-end writes are refused before storage access
- **WHEN** `event_chain_create` or an explicit `event_chain_update` requests `trigger_type="calendar_event_end"`
- **THEN** validation refuses before any database read or write
- **AND** no replacement trigger, row cleanup, data rewrite or replay is performed

#### Scenario: Create event chain triggered by deadline passing
- **WHEN** `event_chain_create(name="post-tax-filing", trigger_type="deadline_passed", trigger_reference="<deadline-task-uuid>", actions=[{action_type: "job", delay_minutes: 60, job_name: "archive_tax_documents"}])` is called
- **THEN** an `event_chains` row is inserted with `status='active'`

#### Scenario: Duplicate chain name rejected
- **WHEN** `event_chain_create(name="post-dentist", ...)` is called and a chain with that name already exists for this butler
- **THEN** a `ValueError` is raised
