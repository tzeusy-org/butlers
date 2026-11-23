## ADDED Requirements

### Requirement: Open Breaker Probes Itself Back To Health

An open QA circuit breaker SHALL admit exactly one finding as a half-open probe once its backoff
has elapsed, with no owner action. The backoff SHALL start at 15 minutes after the newest failure
of the streak and double for each failure the streak has gained since the trip, capped at 2 hours.
A probe SHALL be admitted only when no QA investigation is in flight and SHALL be serialized across
dispatchers and daemons by a lock held across its claim. The breaker SHALL keep no state of its own:
its trip time, cause and next probe time SHALL be derived from the launched-execution rows that
admission already shares with the dashboard.

ID: REQ-qa-investigation-dispatch-015
Source: bu-ejow5p.3 (run 25 move 3), slice S3; owner decision on bu-ejow5p.3 approving autonomous probing with a 15 minute to 2 hour backoff
Scope: v1-mandatory

#### Scenario: A probe is admitted after the backoff

- **WHEN** the breaker is open, its backoff has elapsed and no QA investigation is in flight
- **THEN** the first eligible finding is dispatched as the probe and a `breaker_probe` dispatch event is recorded
- **AND** the remaining findings of the patrol are held behind the breaker

#### Scenario: Two dispatchers admit one probe

- **WHEN** two patrols reach the elapsed backoff at the same time
- **THEN** exactly one probe is launched
- **AND** the other patrol holds all of its findings

#### Scenario: A probe's outcome moves the breaker

- **WHEN** the probe investigation succeeds
- **THEN** the failure streak is broken and the breaker is closed
- **AND** when it fails, the next probe waits twice as long, up to 2 hours

#### Scenario: A finding a gate rejects does not spend the probe

- **WHEN** the first finding is rejected by a per-finding gate before any investigation launches
- **THEN** the next finding is considered for the probe

#### Scenario: An unreadable schedule does not probe

- **WHEN** the breaker rows or the active-attempt count cannot be read
- **THEN** no probe is admitted
