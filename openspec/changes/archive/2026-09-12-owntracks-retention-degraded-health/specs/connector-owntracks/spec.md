## ADDED Requirements

### Requirement: Retention Purge Degradation Visibility
The OwnTracks connector SHALL maintain a process-local consecutive failure streak for its
retention purge task. A caught purge failure SHALL remain non-fatal and retryable, increment the
streak, and make the existing connector health and heartbeat state `degraded` with a sanitized,
count-based diagnostic. A successful purge SHALL reset the streak and clear retention-derived
degradation. The exposed diagnostic SHALL NOT include raw exception details.

#### Scenario: First and repeated purge failures degrade the connector
- **WHEN** one or more retention purge attempts raise an exception
- **THEN** each failure is logged and the purge loop remains running for its next scheduled retry
- **AND** the process-local failure streak increases once per failed attempt
- **AND** existing health and heartbeat state report `degraded` with only the consecutive-failure count

#### Scenario: Successful purge clears retention degradation
- **WHEN** a retention purge succeeds after one or more failed attempts
- **THEN** the process-local failure streak resets to zero
- **AND** retention-derived health degradation and its diagnostic are cleared

#### Scenario: Existing connector error retains priority
- **WHEN** the connector already has an `error` health condition and the retention failure streak is nonzero
- **THEN** health and heartbeat state continue to report the existing `error` condition rather than retention degradation

#### Scenario: Retention diagnostic is sanitized
- **WHEN** a retention purge raises an exception containing sensitive or implementation-specific text
- **THEN** the exposed health and heartbeat diagnostic contains neither the exception message nor traceback
- **AND** the diagnostic is derived only from the process-local consecutive-failure count

#### Scenario: The streak is not durable
- **WHEN** the OwnTracks connector process restarts
- **THEN** retention failure tracking begins with a zero streak
- **AND** no database migration, durable counter, alert, notification, or new API surface is introduced
