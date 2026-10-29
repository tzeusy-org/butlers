## MODIFIED Requirements

### Requirement: Cron Evaluation and next_run_at Computation

All cron expressions SHALL use 5-field format (minute hour day month day-of-week).
During daemon startup synchronization, `sync_schedules()` SHALL calculate
`next_run_at` for a new or changed TOML schedule using the startup-resolved
owner general timezone, unless the TOML entry declares its own `timezone` (an IANA
name), which `sync_schedules()` SHALL use instead and store on the row. Startup
synchronization does not read the stored per-row `timezone` value of an existing row
when computing the replacement `next_run_at`, and an otherwise unchanged TOML row
retains its existing `next_run_at` even if the owner timezone has changed. An unknown
TOML `timezone` is a configuration error.

During `tick()` evaluation, cron fields SHALL use an effective timezone: the
scheduler loop's resolved owner general timezone is the default; a stored
`timezone` of `UTC`, `NULL`, or empty is a default sentinel that follows that
default; and a non-UTC `timezone` is an explicit per-schedule override. Because the
literal `UTC` is that sentinel, a schedule pins real UTC with the alias `Etc/UTC`.
If the owner default or effective timezone is invalid, the scheduler SHALL fall
back to UTC. The `croniter` library validates and computes the next occurrence
in the applicable timezone; the scheduler SHALL convert the occurrence to UTC
for `next_run_at` and then apply deterministic staggering as specified below.

#### Scenario: Valid cron expression
- **WHEN** `sync_schedules()` creates or updates a TOML schedule with a valid
  5-field cron expression
- **THEN** `croniter.is_valid(cron)` passes and `next_run_at` is computed from
  the next occurrence in the startup-resolved owner-general timezone, converted to UTC,
  and subject to deterministic staggering
- **AND WHEN** `tick()` computes a subsequent `next_run_at`
- **THEN** it uses that stored row's effective timezone, converted to UTC and
  subject to deterministic staggering

#### Scenario: Startup synchronization uses the owner default
- **WHEN** a daemon creates or updates a TOML schedule during
  `sync_schedules()`
- **THEN** the cron fields SHALL be interpreted in the startup-resolved
  owner-general timezone unless the TOML entry declares `timezone`
- **AND** startup synchronization SHALL NOT inspect a retained per-row
  `timezone` override when computing that row's replacement `next_run_at`
- **AND** if the owner timezone cannot be resolved, the cron fields SHALL be
  interpreted in UTC

#### Scenario: Unchanged TOML rows retain their prior startup computation
- **WHEN** a TOML schedule's synced fields are unchanged on a later daemon
  startup, including after an owner timezone change
- **THEN** `sync_schedules()` SHALL leave that row's existing `next_run_at`
  unchanged

#### Scenario: Tick-time default schedule timezone follows the owner
- **WHEN** `tick()` evaluates a schedule whose stored `timezone` is `UTC`,
  `NULL`, or empty
- **THEN** the cron fields SHALL be interpreted in the scheduler loop's
  resolved owner-general timezone
- **AND** if that timezone cannot be resolved, they SHALL be interpreted in UTC

#### Scenario: A running scheduler loop retains its resolved owner default
- **WHEN** the owner general timezone changes while a scheduler loop is running
- **THEN** subsequent `tick()` calls in that loop SHALL continue using the
  owner-general default resolved when the loop started
- **AND** the changed owner timezone SHALL take effect after the next daemon
  restart starts a new scheduler loop

#### Scenario: Explicit schedule timezone overrides the owner default
- **WHEN** `tick()` evaluates a schedule with a non-UTC stored `timezone`
- **THEN** the cron fields SHALL be interpreted in that timezone rather than the
  owner's configured general timezone

#### Scenario: Invalid cron expression
- **WHEN** a schedule is created or updated with an invalid cron expression
- **THEN** a `ValueError` is raised with a descriptive message

#### Scenario: A TOML timezone pins the schedule to that zone
- **WHEN** a TOML schedule declares `timezone = "Etc/UTC"` and the owner timezone is `Asia/Singapore`
- **THEN** `sync_schedules()` SHALL compute `next_run_at` from the cron fields in `Etc/UTC` and store `Etc/UTC` on the row
- **AND** `tick()` SHALL keep interpreting the cron fields in `Etc/UTC` because it is an explicit override

#### Scenario: Roster schedules fire at the time they state
- **WHEN** a roster schedule's own prompt or comment names an owner-local time
- **THEN** the schedule SHALL fire at that local hour, either as an owner-local cron with that hour or as a cron pinned to a zone in which it reaches the same instant
- **AND** a roster schedule commented with a UTC time SHALL declare `timezone = "Etc/UTC"`

## ADDED Requirements

### Requirement: Always-Quiet Schedule Warning
When `sync_schedules()` is given a usable owner quiet-window policy, it SHALL log one
warning per prompt-mode schedule that references `notify` and whose every firing in the
next seven days, at both its nominal instant and the latest instant the schedule stagger can
move it to, falls inside the quiet window, because its notifications would always be held or
dropped.
The check SHALL be warn-only: it SHALL NOT reject a schedule or stop synchronization, and it
SHALL NOT warn when the policy is missing or invalid, the window is empty, the cron is
invalid or fires irregularly often, or the schedule is job-mode or does not reference
`notify`.

#### Scenario: A schedule that only fires while quiet is warned about once
- **WHEN** the quiet window is 22:00 to 07:00 in the owner zone and a notifying prompt-mode schedule fires only at 23:00
- **THEN** sync logs exactly one warning naming the schedule and still writes its row

#### Scenario: A schedule with a firing outside the window is not warned about
- **WHEN** at least one firing in the week falls outside the quiet window
- **THEN** sync logs no quiet-window warning for that schedule

#### Scenario: Uncertain cases stay silent
- **WHEN** no owner policy is configured, or the schedule is job-mode
- **THEN** sync logs no quiet-window warning

#### Scenario: A firing that only staggers into the window is not warned about
- **WHEN** the quiet window starts at 22:00 and a notifying schedule fires at 21:50 with a 15 minute stagger
- **THEN** sync logs no warning, because the nominal firing is outside the window

### Requirement: TOML Schedule Timezone Key
A `[[butler.schedule]]` entry MAY set `timezone` to an IANA zone name. The config parser
and `sync_schedules()` SHALL refuse the follow-the-owner sentinel values, the literal `UTC`
in any case and an empty or blank string, with an error that names `Etc/UTC`, because the
scheduler would otherwise silently re-resolve them to the owner timezone; real UTC is
pinned with `Etc/UTC`. An unknown zone or a non-string value SHALL be a configuration
error. When only the declared `timezone` of an existing TOML row differs, `sync_schedules()`
SHALL update that row, recompute `next_run_at`, and store the new zone.

#### Scenario: The UTC sentinel is refused with a pointer to Etc/UTC
- **WHEN** a TOML schedule sets `timezone = "UTC"`, `"utc"` or `""`
- **THEN** loading the configuration fails with an error naming `Etc/UTC`
- **AND** `sync_schedules()` given the same value raises a `ValueError` naming `Etc/UTC`

#### Scenario: A timezone-only change updates the existing row
- **WHEN** an existing TOML row matches the schedule in every field except that the TOML now declares `timezone = "Etc/UTC"`
- **THEN** `sync_schedules()` updates the row, recomputes `next_run_at` in `Etc/UTC`, and stores `Etc/UTC`
- **AND** a row whose stored zone already equals the declared zone is not updated
