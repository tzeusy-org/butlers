## ADDED Requirements

### Requirement: Circuit Breaker Names Its Cause And Next Probe

The QA circuit-breaker route and the summary's breaker SHALL name why the breaker tripped and when
it next probes. `dominant_cause` SHALL be the one cause every counted failure shares and `mixed`
when they differ, with `cause_counts` carrying the per-cause counts; `opened_at` and
`next_probe_at` SHALL come from the same derivation dispatch admission uses. Each counted attempt
SHALL carry its `reason_class` and the first line of its own error text as `reason_text`. An
unrecognised error text SHALL be an `execution_failure`, never a structural cause. Patrol reads
SHALL carry `halt_reason`, the gate that stopped dispatch when the status is `halted`.

ID: REQ-qa-dashboard-902
Source: bu-ejow5p.3 (run 25 move 3), slice S3; owner decision on bu-ejow5p.3 adopting the bead design
Scope: v1-mandatory

#### Scenario: A uniform structural cause is named

- **WHEN** five consecutive failures all carry `publication_unavailable`
- **THEN** `dominant_cause` is `publisher_unconfigured` and `cause_counts` is `{"publisher_unconfigured": 5}`
- **AND** `next_probe_at` is 15 minutes after the newest failure

#### Scenario: Mixed failures are labelled mixed

- **WHEN** the counted failures have more than one cause
- **THEN** `dominant_cause` is `mixed` and `cause_counts` lists each cause with its count

#### Scenario: A closed breaker names nothing

- **WHEN** the breaker is closed
- **THEN** `dominant_cause` and `next_probe_at` are null

#### Scenario: Failed probes push the next probe out

- **WHEN** the streak holds two failures beyond the threshold
- **THEN** `next_probe_at` is 60 minutes after the newest failure

#### Scenario: A halted patrol names its gate

- **WHEN** a patrol read returns status `halted`
- **THEN** `halt_reason` is `circuit_breaker`, `no_model` or `concurrency_cap`

### Requirement: QA Surfaces Name The Breaker Cause And Halting Gate

The `/qa` verdict opener SHALL name the open breaker's cause and next probe time when the API supplies them, and SHALL link a halted last patrol with the gate that halted it. A patrol's status label SHALL carry the gate for a `halted` status wherever patrol status is shown. The breaker reset dialog SHALL show the dominant cause, each counted attempt's reason text and a forecast of what a reset will do; the forecast SHALL be withheld when the cause is `mixed` or unknown, because a prediction over unlike failures is a guess.

ID: REQ-qa-dashboard-903
Source: bu-ejow5p.3 (run 25 move 3), slice S3b; owner decision on bu-ejow5p.3 adopting the bead design
Scope: v1-mandatory

#### Scenario: The opener names the cause and the next probe

- **WHEN** the breaker is open with a single cause and a next probe time
- **THEN** the opener reads that the breaker is open for that cause with its next probe time

#### Scenario: A halted patrol is linked with its gate

- **WHEN** the last patrol's status is `halted`
- **THEN** the opener links the patrol and names its `halt_reason`

#### Scenario: A mixed cause gets no forecast

- **WHEN** the reset dialog opens while `dominant_cause` is `mixed`
- **THEN** it states that no forecast is available and makes no claim about a reset's outcome
