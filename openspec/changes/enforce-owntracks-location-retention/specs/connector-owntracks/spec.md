## MODIFIED Requirements

### Requirement: Data Retention

- Location events SHALL be automatically purged after a configurable retention period.
- The existing six-hour public.ingestion_events audit purge/default/positive ENV branches SHALL remain. A separate canonical protected raw policy SHALL enforce default30days and owner-shorter1..30 on connectors.owntracks_points plus Chronicler location point events, only for genuinely committed causal projection/copy coverage. The actual native connector worker SHALL implement its own raw DELETE+tombstone+receipt transaction; Chronicler SHALL retain only its approved raw SELECT and own writes. Projection/copy lag SHALL preserve raw and raise a real owner condition; the scope-specific declaration SHALL name these conditional expiry semantics rather than imply a deletion timestamp guarantee.

ID: REQ-connector-owntracks-012
Source: bu-s11n0s.7 original S1 and Behavior matrix; about/heart-and-soul/security.md Sensitive Data Categories; openspec/specs/connector-owntracks/spec.md; proposed location-retention amendment
Scope: v1-mandatory

#### Scenario: Retention purge schedule
- **WHEN** the connector is running
- **THEN** a background task runs every 6 hours to delete expired location events
- **AND** the task deletes rows from `public.ingestion_events` where `source_channel = 'owntracks'` AND `received_at < NOW() - (<retention_days> * INTERVAL '1 day')`

#### Scenario: Default retention period
- **WHEN** `OWNTRACKS_RETENTION_DAYS` is not set
- **THEN** the default retention period is 30 days

#### Scenario: Configurable retention
- **WHEN** `OWNTRACKS_RETENTION_DAYS` is set to a positive integer
- **THEN** the retention period is that many days
- **AND** the minimum allowed value is 1 day (setting 0 or negative values causes a startup error)

#### Scenario: Purge logging
- **WHEN** the retention purge task runs
- **THEN** it logs the number of deleted rows at INFO level
- **AND** purge failures are logged at WARNING level but do NOT crash the connector

#### Scenario: Data Retention preserves genuine OwnTracks retention

- **WHEN** an actual OwnTracks source reaches the declared policy/coverage/purge branch for this owning contract
- **THEN** The existing six-hour public.ingestion_events audit purge/default/positive ENV branches SHALL remain. A separate canonical protected raw policy SHALL enforce default30days and owner-shorter1..30 on connectors.owntracks_points plus Chronicler location point events, only for genuinely committed causal projection/copy coverage. The actual native connector worker SHALL implement its own raw DELETE+tombstone+receipt transaction; Chronicler SHALL retain only its approved raw SELECT and own writes. Projection/copy lag SHALL preserve raw and raise a real owner condition; the scope-specific declaration SHALL name these conditional expiry semantics rather than imply a deletion timestamp guarantee.
- **AND** no planning/source presence, caller status, unrelated episode or UI-only age filter SHALL substitute for genuine owning runtime proof

### Requirement: Retention Purge Degradation Visibility

- The OwnTracks connector SHALL maintain a process-local consecutive failure streak for its retention purge task. A caught purge failure SHALL remain non-fatal and retryable, increment the streak, and make the existing connector health and heartbeat state `degraded` with a sanitized, count-based diagnostic. A successful purge SHALL reset the streak and clear retention-derived degradation. The exposed diagnostic SHALL NOT include raw exception details.
- OwnTracks raw retention SHALL additionally report current attempt/completion/unknown/lag state from committed receipts. A new raw success SHALL clear only its own failure streak, not old audit failure, unrelated error or incomplete projection/copy condition. Last success SHALL NOT conceal a later failed/cancelled/stale attempt. Diagnostics SHALL use bounded counts/status codes with no raw exception, coordinate, SSID or endpoint label. Existing connector error precedence SHALL remain.

ID: REQ-connector-owntracks-018
Source: bu-s11n0s.7 original S1 and Behavior matrix; about/heart-and-soul/security.md Sensitive Data Categories; openspec/specs/connector-owntracks/spec.md; proposed location-retention amendment
Scope: v1-mandatory

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

#### Scenario: Retention Purge Degradation Visibility preserves genuine OwnTracks retention

- **WHEN** an actual OwnTracks source reaches the declared policy/coverage/purge branch for this owning contract
- **THEN** OwnTracks raw retention SHALL additionally report current attempt/completion/unknown/lag state from committed receipts. A new raw success SHALL clear only its own failure streak, not old audit failure, unrelated error or incomplete projection/copy condition. Last success SHALL NOT conceal a later failed/cancelled/stale attempt. Diagnostics SHALL use bounded counts/status codes with no raw exception, coordinate, SSID or endpoint label. Existing connector error precedence SHALL remain.
- **AND** no planning/source presence, caller status, unrelated episode or UI-only age filter SHALL substitute for genuine owning runtime proof
