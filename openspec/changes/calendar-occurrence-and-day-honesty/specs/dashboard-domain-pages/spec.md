## ADDED Requirements

### Requirement: Calendar Workspace Shows Recurring Occurrence Identity

The Calendar workspace SHALL render each provider occurrence as its own entry and, for an occurrence whose metadata carries a series id, SHALL show a series affordance (a recurring marker with the series identity available in the entry detail). The workspace SHALL NOT collapse occurrences of one series into one entry, and a moved occurrence SHALL render at its current window as the same entry. A source whose coverage is `partial` or `stale` SHALL show that state in the existing source freshness indicators, including `coverage_through`, so a missing future occurrence is not read as absence.

ID: REQ-dashboard-domain-pages-050
Source: bu-h40h2b.6 acceptance 5; bu-vpn5ij
Scope: v1-mandatory

#### Scenario: Occurrences render separately with a series marker

- **WHEN** the window holds three occurrences of one series
- **THEN** three entries render, each with the recurring marker

#### Scenario: Partial coverage is visible on the source

- **WHEN** a source reports `coverage = "partial"` with `coverage_through` before the viewed range
- **THEN** its freshness indicator states the partial coverage and the date it reaches

### Requirement: Calendar Day Load Line With Truthful State

The Calendar day and week headers SHALL render, for each visible day, a day-load line from `ConflictScanResponse.day_load`: meeting hours and, when nonzero, uncategorized hours. A day whose `coverage` is `stale` SHALL render its last known load figures marked stale. A day whose `coverage` is `partial` or `unavailable` SHALL render that named state with no load figure. No non-`complete` day SHALL render as quiet, free or all-clear. A source or read failure SHALL never produce an empty or zero-hours line. The line SHALL be an additive element and SHALL leave the existing radar banner behavior (hidden when `issues_available` is false) unchanged.

ID: REQ-dashboard-domain-pages-051
Source: bu-h40h2b.6 acceptance 5; bu-vpn5ij
Scope: v1-mandatory

#### Scenario: Complete day shows hours

- **WHEN** a day has `coverage = "complete"`, 3 meeting hours and 1 uncategorized hour
- **THEN** the header shows both figures with the uncategorized part labelled

#### Scenario: Unavailable day is not rendered as free

- **WHEN** a day has `coverage = "unavailable"`
- **THEN** the header shows an unavailable state
- **AND** it does not show "0 h" or any all-clear wording

#### Scenario: Stale day names staleness

- **WHEN** a day has `coverage = "stale"`
- **THEN** the header shows the last known load figures marked stale

#### Scenario: Partial day shows the state and no figure

- **WHEN** a day has `coverage = "partial"`
- **THEN** the header shows the partial state and no load figure

### Requirement: Settled-Day Reconciliation Header Line

For a local day that is settled (strictly before the owner's current local date and past the Chronicler settling delay), the Calendar day header SHALL render a reconciliation line from the Chronicler reconciliation read contract: the number of scheduled blocks that were contradicted by evidence, the number whose outcome is unknown, its freshness (`computed_at`) and its coverage. The line SHALL be absent for the current day and every future day, and absent when no reconciliation run exists for the day. The line SHALL NOT state or imply that any block was attended; wording is limited to "contradicted by evidence" and "no evidence either way". A run with incomplete coverage SHALL say so and SHALL NOT read as "nothing contradicted". Calendar intent is not attendance.

ID: REQ-dashboard-domain-pages-052
Source: bu-h40h2b.6 acceptance 5 and 6; bu-vpn5ij; decision A1 (bu-2o1dn8); butler-chronicler Calendar Scheduled Blocks Are Not Attendance Assertions
Scope: v1-mandatory

#### Scenario: Future and current days have no line

- **WHEN** the viewed day is today or later
- **THEN** no reconciliation line renders

#### Scenario: Settled day with run shows contradictions and unknowns only

- **WHEN** a settled day has a run with 2 contradicted blocks and 5 unknown
- **THEN** the line shows those counts and the run's freshness
- **AND** no attended count or "attended" wording appears

#### Scenario: Incomplete coverage is stated

- **WHEN** the run's coverage is partial
- **THEN** the line states partial coverage and does not read as all-clear

#### Scenario: Settled day without a run shows no line

- **WHEN** no run exists
- **THEN** no reconciliation line renders and no placeholder implies completeness
