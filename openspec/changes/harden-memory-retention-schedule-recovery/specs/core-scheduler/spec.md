## ADDED Requirements

### Requirement: Module-default recovery respects TOML provenance and operator disables

The scheduler SHALL distinguish a removed TOML override from an active TOML
schedule before a module-default registration can recover a row. TOML schedule
synchronization SHALL establish the current TOML membership before module-default
recovery is evaluated. A module-default recovery candidate MUST satisfy both of these conditions:

- a name explicitly registered by the owning module as one of its defaults; and
- the same-named `scheduled_tasks` row with `source='toml'` and `enabled=false`
  after that TOML synchronization.

No registered default is exempt. `memory_episode_cleanup` is recovered like any
other registered default, because its handler deletes only reapable expired
episodes in bounded batches and so cannot perform an unbounded catch-up.

Recovery SHALL set only that candidate's `source` to `db` and `enabled` to
`true`. It MUST preserve the existing cron, dispatch mode, prompt/job name, job
arguments, complexity, and `next_run_at`. A row with `source='db'` MUST NOT be
re-enabled, rewritten, or audited by module-default recovery, regardless of its
`enabled` state. The scheduler SHALL NOT infer module ownership from a name
prefix, a dashboard request, or arbitrary database state.

Recovery is a control-plane transition only (RFC 0038 D2). It SHALL NOT admit
a goal, submit an obligation admission, call a job function, or start a
session. A recovered row's later due occurrences SHALL dispatch through its
preserved dispatch mode under the Dispatch Modes requirement: a job-mode row
calls its deterministic job, and a prompt-mode row submits one obligation
admission for that occurrence. Because `next_run_at` is preserved, recovery
SHALL NOT admit catch-up occurrences.

Recovery confers no historical-drain authority. A recovered cleanup row runs
only its ordinary bounded, consolidation-aware sweep at its preserved cadence;
it is never invoked as an owner-gated historical drain.

ID: REQ-core-scheduler-003
Source: bu-kqnum.13 module-default TOML-orphan recovery; PR #3565; RFC 0038 D2
Scope: v1-mandatory

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

#### Scenario: Recovery admits no goal and dispatches later by preserved mode

- **WHEN** an eligible module default is recovered at daemon startup
- **THEN** the recovery SHALL admit no goal, submit no obligation admission,
  call no job function, and start no session
- **AND** when the recovered row next falls due it SHALL dispatch through its
  preserved dispatch mode, with no additional occurrence admitted for the time
  it was disabled

#### Scenario: Disabled TOML cleanup is recovered like any other default

- **WHEN** a memory-enabled butler has no active TOML declaration for
  `memory_episode_cleanup` and, after TOML synchronization, its same-named row
  is `source='toml'` and `enabled=false`
- **THEN** module-default recovery SHALL set that row to `source='db'` and
  `enabled=true` and append one `scheduler.module_default_recovered` audit entry
- **AND** the row's cron, dispatch payload, complexity, and `next_run_at` SHALL
  remain the values it had before the recovery
- **AND** a same-named `source='db'` cleanup row, enabled or disabled, SHALL be
  left unchanged and unaudited

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
