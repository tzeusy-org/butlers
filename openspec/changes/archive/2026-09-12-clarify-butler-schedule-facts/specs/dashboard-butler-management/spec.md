## ADDED Requirements

### Requirement: Butler detail header schedule facts are truthful

`ButlerDetailHeader` SHALL derive schedule facts only from enabled schedule
rows with a parseable finite `next_run_at` instant. The header SHALL use the
existing read-only schedule query and SHALL NOT write schedules, alter
scheduler calculations, or derive status-board activity. A scheduled instant
at or before the current wall clock is overdue; an instant strictly after it
is future-next. The header SHALL recompute that classification on schedule
polling and at least once per minute while mounted.

#### Scenario: Earliest future schedule is shown as next

- **WHEN** one or more enabled schedules have parseable `next_run_at` values
  strictly after the current wall clock
- **THEN** the header SHALL render the earliest such timestamp as its `next`
  fact using the shared `<Time>` primitive
- **AND** no later future schedule SHALL replace that fact

#### Scenario: Stale schedule is shown as an actionable overdue fact

- **WHEN** one or more enabled schedules have parseable `next_run_at` values
  at or before the current wall clock
- **THEN** the header SHALL render the oldest such timestamp as a visibly
  named `overdue` fact with the schedule name and a deterministic relative age
- **AND** the fact SHALL use the established amber foreground token and an
  accessible link name that does not rely on color alone
- **AND** the fact link SHALL target
  `/butlers/:name?tab=system&section=schedules`
- **AND** the stale timestamp SHALL NOT render as a literal `next` fact

#### Scenario: Overdue and future facts coexist

- **WHEN** enabled schedules include both overdue and future parseable
  `next_run_at` values
- **THEN** the header SHALL keep the most-overdue named fact visible
- **AND** the earliest independently truthful future-next fact SHALL remain
  visible

#### Scenario: Unusable timestamps do not fabricate certainty

- **WHEN** a schedule is disabled, has a null timestamp, has a malformed
  timestamp, or has an unparsable timestamp
- **THEN** that row SHALL contribute neither an overdue nor a future-next fact
- **AND** the header SHALL render no fabricated schedule age or future time for
  that row

#### Scenario: Equal schedule instants select a stable named fact

- **WHEN** multiple enabled parseable schedules tie for the selected overdue
  or future timestamp
- **THEN** the header SHALL select the fact deterministically by schedule name
  and then schedule id
