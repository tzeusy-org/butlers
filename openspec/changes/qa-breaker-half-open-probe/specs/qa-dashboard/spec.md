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
