## ADDED Requirements

### Requirement: Protected deterministic job admission

The scheduler SHALL provide a scheduler-level protected-job registry for
deterministic jobs whose design contract prohibits interactive invocation.  The
registry is an authorization gate, not merely a handler lookup.  For this change,
it SHALL reserve exactly the normalized Relationship identity
`email_correspondence_enrichment`: `source='toml'`,
`name="email-correspondence-enrichment"`, `cron="35 6 * * *"`,
`dispatch_mode="job"`, `job_name="email_correspondence_enrichment"`, and the
configuration-declared job arguments.

Before dispatch, generic `schedule_trigger` SHALL reject that protected identity.
Before persistence, generic `schedule_create` SHALL reject a runtime copy or
alias using the protected job name, and generic `schedule_update` SHALL reject a
protected-row mutation or conversion of another row into that identity.  The
checks SHALL cover name, cron, dispatch mode, job name, and job arguments; no
interactive caller may use a near-match or alias to reach the handler.  The fixed
TOML schedule remains runnable only through trusted configuration
synchronization and the due system tick.

The registry is source-controlled and configuration-owned.  It SHALL not create a
generic writable allowlist or a manual override.  If implementation requires
durable enforcement metadata or a database constraint, that representation SHALL
be migration-managed, reversible, and auditable.  A rejected path returns an
auditable rejection category, emits a bounded content-free metric and security
audit event, and does not reveal job arguments, entity IDs, account references,
or other protected data.

#### Scenario: Manual trigger cannot run a protected job

- **WHEN** a generic caller invokes `schedule_trigger` for the canonical
  `email_correspondence_enrichment` row
- **THEN** the scheduler returns the bounded protected-job rejection before
  dispatching the deterministic handler
- **AND** it does not update the task execution state or expose protected job
  arguments

#### Scenario: Runtime CRUD cannot create or mutate a protected identity

- **WHEN** a generic caller invokes `schedule_create` with the protected job
  name, a runtime alias, or a noncanonical cron/mode/argument combination
- **THEN** the scheduler rejects it before persistence
- **AND WHEN** a generic caller invokes `schedule_update` on the protected row
  or changes another row to use the protected job identity
- **THEN** the scheduler rejects it before persistence

#### Scenario: The configuration-owned fixed batch still runs

- **WHEN** trusted TOML synchronization creates or reconciles the exact protected
  `source='toml'` row and its fixed cron becomes due
- **THEN** the scheduler dispatches the registered deterministic handler through
  the fixed TOML schedule
- **AND** it does not permit an interactive substitute schedule or prompt-mode
  invocation

#### Scenario: Protected-job rejections are observable without data exposure

- **WHEN** the scheduler rejects a generic protected-job path
- **THEN** it emits the auditable rejection category and a dedicated metric and
  security audit event
- **AND** the result, metric labels, and audit payload contain no job arguments,
  entity IDs, account references, peers, or correspondence data
