# healing-session-tracking

## MODIFIED Requirements

### Requirement: Dispatch Decision Tracking

The system SHALL distinguish dispatch decisions from launched execution
attempts. `public.healing_attempts` SHALL remain execution history and SHALL
NOT be used as the durable infrastructure-condition ledger.

#### Scenario: Dispatch decision schema
- **WHEN** the migration creates `public.healing_dispatch_events`
- **THEN** the table contains: `id` (UUID PK), `fingerprint` (TEXT NOT NULL),
  `butler_name` (TEXT NOT NULL), `decision` (TEXT NOT NULL), `reason` (TEXT),
  `attempt_id` (UUID NULL FK to `healing_attempts.id`), `created_at`
  (TIMESTAMPTZ NOT NULL DEFAULT now())

#### Scenario: Gate rejection before launch
- **WHEN** cooldown, concurrency cap, circuit breaker, or no-model prevents a
  healing workflow from launching a runtime session
- **THEN** a `healing_dispatch_events` row is inserted describing the decision
- **AND** no `healing_attempts` row is marked `failed` solely because of that
  gate rejection

#### Scenario: Active infrastructure condition is a decision-only rejection
- **WHEN** QA dispatch determines after normal eligibility and before
  `create_or_join_attempt` that an `infra_state` finding matches an active
  infrastructure condition
- **THEN** it inserts a `healing_dispatch_events` row with
  `decision = infra_condition_open`, a sanitized condition reason, and
  `attempt_id = NULL`
- **AND** the row is a dispatch decision rather than a healing attempt,
  execution failure, runtime session, or worktree record
- **AND** repeated active findings remain separately auditable as decisions
  while no new `healing_attempts` row is created or mutated by this rejection

## Source References
- Non-Negotiable Rule 4 (deterministic daemon infrastructure)
- RFC 0001 (dispatch decision versus launched execution)
- RFC 0005 (recovery decision telemetry)
- `infrastructure-reliability` (active-condition suppression contract)
