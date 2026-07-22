## ADDED Requirements

### Requirement: Module-default recovery respects TOML provenance and operator disables

The scheduler SHALL distinguish a removed TOML override from an active TOML
schedule before a module-default registration can recover a row. TOML schedule
synchronization SHALL establish the current TOML membership before module-default
recovery is evaluated. A module-default recovery candidate MUST satisfy all of these conditions:

- a name explicitly registered by the owning module as one of its defaults; and
- the same-named `scheduled_tasks` row with `source='toml'` and `enabled=false`
  after that TOML synchronization; and
- a name eligible for automatic recovery. `memory_episode_cleanup` MUST NOT be
  eligible for automatic recovery of an existing disabled TOML-owned row, even
  when it is a registered module default. This exclusion applies only to
  reclaiming an existing row; ordinary missing-default creation remains governed
  by the missing-default contract below.

Recovery SHALL set only that candidate's `source` to `db` and `enabled` to
`true`. It MUST preserve the existing cron, dispatch mode, prompt/job name, job
arguments, complexity, and `next_run_at`. A row with `source='db'` MUST NOT be
re-enabled, rewritten, or audited by module-default recovery, regardless of its
`enabled` state. The scheduler SHALL NOT infer module ownership from a name
prefix, a dashboard request, or arbitrary database state.

A stale `next_run_at`, expired episode history, or other retained data MUST NOT
override the `memory_episode_cleanup` recovery exclusion. Any future cleanup
recovery policy requires a separate provenance- and owner-gated capability; it
is not generic module-default recovery.

#### Scenario: Active TOML default remains TOML-owned

- **WHEN** a registered module default is also present in the active
  `[[butler.schedule]]` configuration
- **THEN** TOML synchronization SHALL own its cadence and enablement
- **AND** module-default recovery SHALL not change its `source` or emit a
  recovery audit entry

#### Scenario: Removed TOML default is recovered after synchronization

- **WHEN** a registered module default has been removed from TOML and the
  synchronized same-named row is `source='toml'` and `enabled=false`
- **THEN** module-default recovery SHALL set that row to `source='db'` and
  `enabled=true`
- **AND** the row's cron, dispatch payload, complexity, and `next_run_at` SHALL
  remain the values it had before the recovery

#### Scenario: Disabled TOML cleanup cannot be automatically reclaimed or dispatched

- **WHEN** a memory-enabled butler has no active TOML declaration for
  `memory_episode_cleanup`, its same-named row is `source='toml'`,
  `enabled=false`, and has a `next_run_at` in the past
- **AND** that butler's memory schema contains one or more episodes matching
  `expires_at < now()`
- **WHEN** TOML synchronization, generic module-default registration, and a
  due-schedule evaluation run
- **THEN** the cleanup row SHALL remain TOML-owned and disabled with its stored
  `next_run_at` unchanged
- **AND** no `scheduler.module_default_recovered` audit entry SHALL be appended
- **AND** no `memory_episode_cleanup` dispatch SHALL occur
- **AND** no episode SHALL be deleted

#### Scenario: Disabled DB-owned schedule remains operator-owned

- **WHEN** a same-named registered default has `source='db'` and `enabled=false`
- **THEN** module-default registration SHALL leave every column unchanged
- **AND** it SHALL not emit a module-default recovery audit entry

#### Scenario: Missing default schedule is created normally

- **WHEN** no same-named row exists for a registered module default after TOML
  synchronization
- **THEN** registration SHALL insert the ordinary enabled `source='db'` default
  using its registered definition
- **AND** the insertion SHALL not be reported as a reclaimed TOML schedule

### Requirement: Module-default recovery and audit are atomic and idempotent

Each successful module-default recovery SHALL append exactly one canonical
`public.audit_log` entry in the same SQL transaction as its conditional schedule
transition. The audit entry MUST use
`action='scheduler.module_default_recovered'`, target `schedule:<name>`, and
control-plane-only metadata containing the prior source, prior enabled state,
registered default name, `owner_butler`, and `owner_schema`. `owner_butler` and
`owner_schema` MUST be non-empty strings from the recovering scheduler's
configured identity; `schedule:<name>` alone is not an unambiguous identity in
the shared audit log. The metadata MUST NOT include prompt text, job arguments,
episode content, or any other runtime payload.

The conditional state transition's returned row is the only authority to append
this audit record. If no row transitions, no recovery audit is written. If the
audit insert fails, the transaction SHALL roll back the schedule transition.
Concurrent recovery attempts SHALL result in at most one transitioned row and
at most one committed audit record. A later registration pass after a committed
recovery SHALL be a no-op.

#### Scenario: Recovered row has one durable audit entry

- **WHEN** one eligible TOML-owned disabled module default is recovered
- **THEN** the committed `scheduled_tasks` row SHALL be DB-owned and enabled
- **AND** exactly one committed `public.audit_log` row with
  `action='scheduler.module_default_recovered'` and target `schedule:<name>`
  SHALL describe that transition with its `owner_butler` and `owner_schema`
  metadata

#### Scenario: Same-named recovery is attributable across two butlers

- **WHEN** the `general` butler in schema `general` and the `relationship`
  butler in schema `relationship` each recover an eligible TOML orphan named
  `memory_consolidation`
- **THEN** each recovery SHALL append exactly one committed audit row with
  target `schedule:memory_consolidation`
- **AND** the `general` row's metadata SHALL include
  `owner_butler='general'` and `owner_schema='general'`
- **AND** the `relationship` row's metadata SHALL include
  `owner_butler='relationship'` and `owner_schema='relationship'`
- **AND** the two rows SHALL remain distinguishable by that ownership metadata

#### Scenario: Audit failure rolls back recovery

- **WHEN** the eligible schedule transition is attempted and the canonical
  audit append fails
- **THEN** the transaction SHALL roll back
- **AND** the schedule SHALL retain its pre-transition source and enabled state
- **AND** no `scheduler.module_default_recovered` audit row SHALL exist

#### Scenario: Concurrent recovery attempts produce one transition

- **WHEN** two startup paths concurrently attempt to recover the same eligible
  schedule
- **THEN** at most one attempt SHALL receive the transitioned row
- **AND** at most one recovery audit entry SHALL commit
- **AND** the losing attempt SHALL be a no-op rather than an error or duplicate
  audit event

#### Scenario: Restart after committed recovery is idempotent

- **WHEN** a daemon restarts after a module-default recovery has committed
- **THEN** the now DB-owned enabled row SHALL remain unchanged
- **AND** no additional recovery audit entry SHALL be appended
