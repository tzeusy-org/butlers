## ADDED Requirements

### Requirement: Truthful Status-Board Summary and Error Composition

The `/butlers` status board SHALL present fleet health from the canonical
server-derived `BoardRow.activity` vocabulary. A healthy count SHALL exclude
every row whose activity is `offline`, `quarantined`, `overdue`, or `unknown`.
The `unknown` aggregate SHALL be derived from canonical row activity, not from
registry `eligibility = unavailable`, which remains a separate availability
diagnostic.

The Page shell SHALL omit status-board header and footer slots only when its
initial board request has failed and no cached rows exist. A normal empty
response and initial loading continue to use the shell’s existing behavior.

#### Scenario: Fleet health excludes every non-healthy activity

- **WHEN** board rows contain one or more `offline`, `quarantined`, `overdue`,
  or `unknown` canonical activity verdicts
- **THEN** the header’s healthy/total pill subtracts all four counts from the
  registered total
- **AND** registry availability alone SHALL NOT change the `unknown` count or
  make a row appear unhealthy without its canonical activity verdict

#### Scenario: Initial failure has no misleading board chrome

- **WHEN** the initial board request fails and no cached rows are available
- **THEN** the Page error region renders the error and retry control
- **AND** the status-board header and footer SHALL NOT render around that error

#### Scenario: Cached refresh failure keeps contextual chrome

- **WHEN** a board refresh fails after one or more cached rows were loaded
- **THEN** the cached rows, status-board header, and footer remain visible
- **AND** the page renders its stale-data warning instead of replacing the
  board with a full-page error

### Requirement: Canonical Status-Board Cadence Labels

The board’s human-facing cadence label SHALL describe only a canonical
interval: exactly one hour is `hourly`, exactly one day is `daily`, and
exactly seven days is `weekly`. A positive interval that is not one of those
canonical values, including two hours, SHALL be labeled `custom`. A butler with
no enabled schedule SHALL retain a null cadence label. The raw
`cadence_seconds` and cadence-overdue calculation remain authoritative and
unchanged.

#### Scenario: Canonical cadence interval has its named label

- **WHEN** a butler’s shortest enabled cron interval is exactly one hour, one
  day, or seven days
- **THEN** its board row exposes `hourly`, `daily`, or `weekly` respectively

#### Scenario: Noncanonical cadence avoids an inaccurate named label

- **WHEN** a butler’s shortest enabled cron interval is two hours or any other
  positive noncanonical duration
- **THEN** its board row exposes `cadence_label = custom`
- **AND** it SHALL NOT label that duration `hourly`, `daily`, or `weekly`
