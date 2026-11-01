## MODIFIED Requirements

### Requirement: Rule application audit tracking

A `rule_applications` table SHALL record each time a rule is applied during a goal invocation, including the outcome and the invocation's lineage. Lineage SHALL be taken only from the verified broker assertion of the recording call (`goal_id`, `invocation_id`), never from call arguments or spawner context; the legacy `session_id` column remains nullable for rows written before cutover. This provides a learning loop for rule effectiveness beyond simple counter increments.

Source: RFC 0038 D7, D17
Scope: v1-mandatory

#### Scenario: Rule applications table schema

- **WHEN** the retention policy migration runs
- **THEN** a `rule_applications` table MUST be created with columns: `id` (UUID PK DEFAULT gen_random_uuid()), `tenant_id` (TEXT NOT NULL), `rule_id` (UUID NOT NULL, FK to rules ON DELETE CASCADE), `session_id` (UUID nullable), `request_id` (TEXT nullable), `outcome` (TEXT NOT NULL — one of 'helpful', 'harmful', 'neutral', 'skipped'), `notes` (JSONB NOT NULL DEFAULT '{}'), `created_at` (TIMESTAMPTZ NOT NULL DEFAULT now())
- **AND** an additive migration MUST add nullable `goal_id` (UUID) and `invocation_id` (UUID) columns

#### Scenario: Recording a rule application

- **WHEN** `memory_mark_helpful` or `memory_mark_harmful` is called
- **THEN** a `rule_applications` row MUST be inserted with the rule_id, outcome ('helpful' or 'harmful'), and the `goal_id` and `invocation_id` from the call's verified assertion (null when the call carries none)
- **AND** the existing counter increment logic MUST continue to operate as before (rule_applications is additive audit, not a replacement)

#### Scenario: Querying rule application history

- **WHEN** a dashboard or diagnostic tool queries rule applications
- **THEN** the query MUST be filterable by `tenant_id`, `rule_id`, `outcome`, and time range (`created_at`)
- **AND** results MUST be ordered by `created_at DESC`
