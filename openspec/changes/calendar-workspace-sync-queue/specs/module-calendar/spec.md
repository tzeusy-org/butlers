## ADDED Requirements

### Requirement: Durable Queued Force-Sync Commands

`calendar_force_sync` SHALL preserve its existing inline behavior by default and SHALL accept a dashboard-requested queued mode. Queued mode SHALL durably record the request in the owning schema's `calendar_action_log`, return acknowledgement before provider I/O, and execute the request through the owning CalendarModule rather than the dashboard process.

#### Scenario: Queue acknowledgement is durable and prompt

- **WHEN** `calendar_force_sync` is called with `queue=true`, a `request_id`, and optional `calendar_id`/`full`
- **THEN** the module records a `calendar_force_sync` action-log row in `pending` status before returning
- **AND** it returns `status="queued"` with the durable request correlation and requested recovery strength
- **AND** it does not perform provider pull or mirror I/O in that acknowledgement path

#### Scenario: Owner drains queued command serially

- **WHEN** a pending queued force-sync command exists in a running CalendarModule
- **THEN** the module atomically claims it as `running` and executes the existing incremental or full force-sync behavior
- **AND** it processes at most one queued force-sync command at a time for that owner
- **AND** it records terminal `applied` when the provider/mirror operation completes without recorded errors, otherwise `failed` with the structured result/error

#### Scenario: Restart resumes interrupted queued command

- **WHEN** the owning CalendarModule starts and finds an interrupted `calendar_force_sync` action-log command in `running` status
- **THEN** it returns that command to `pending` and drains it after provider initialization
- **AND** the command is not silently discarded because the dashboard API process or prior daemon process restarted

#### Scenario: Redundant manual clicks coalesce without weakening recovery

- **WHEN** a compatible queued force-sync command is pending or an incremental command is already running for the owner
- **THEN** an additional incremental request returns a queued/coalesced acknowledgement instead of creating duplicate provider work
- **AND** a `full=true` recovery request upgrades a pending incremental command or creates one pending full successor behind a running incremental command
- **AND** a full recovery request is never acknowledged as satisfied solely by an incremental command

#### Scenario: Direct tool callers retain inline semantics

- **WHEN** `calendar_force_sync` is called without `queue=true`
- **THEN** it retains the existing inline incremental/full behavior and response fields
- **AND** queued-command processing does not require the normal sync poller to be enabled
