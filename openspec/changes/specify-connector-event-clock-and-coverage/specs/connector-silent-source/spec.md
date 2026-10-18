# Connector Silent Source

## ADDED Requirements

### Requirement: Silent-source evaluation is a deterministic Switchboard job over registered expected signals

Detection of a silent source SHALL be a deterministic, scheduled evaluation owned by the Switchboard.
It SHALL NOT be an LLM session, a connector-side heuristic, or an RFC 0038 goal obligation. It SHALL
evaluate only sources that have a registered expected signal naming an exact `(connector_type,
endpoint_identity)` pair, a positive expected cadence and a grace period. A source with no declared
cadence SHALL NOT be evaluated. A source SHALL be silent only when its expected signal is `absent`
under the existing rule that liveness precedes absence: the exact endpoint's heartbeat is current
and healthy, the cadence plus grace has elapsed, and no event was observed. A signal that is
`unmeasurable` (dead, stale, missing or unreadable producer evidence) SHALL NOT be silent and SHALL
NOT open a case; connector liveness already owns that condition.

ID: REQ-connector-silent-source-001
Source: bu-h40h2b.12 Slice D; RFC 0029 / expected-signals REQ-expected-signals-001 and "Liveness precedes absence"; RFC 0038 D2 (a deterministic producer is not frozen)
Scope: v1-reserved

#### Scenario: Live source with no events past grace

- **WHEN** an exact endpoint's heartbeat is current and healthy and its cadence plus grace has elapsed with no observed event
- **THEN** the source is silent

#### Scenario: Dead instrument is not a silent source

- **WHEN** the endpoint's heartbeat is stale or offline
- **THEN** the source is not silent
- **AND** no case is opened by this evaluation

#### Scenario: No declared cadence

- **WHEN** a source has no registered cadence
- **THEN** it is not evaluated and no case is opened

#### Scenario: Sibling endpoint does not substitute

- **WHEN** endpoint A is silent and endpoint B of the same connector type is healthy with events
- **THEN** only A is silent

### Requirement: Suppression and false-positive handling are explicit and per source

The evaluation SHALL NOT open a case for a source that is operator-paused, decommissioned,
quarantined, or inside a recorded maintenance window, nor while a backfill or replay for that source
is running. Each source's registered cadence and grace SHALL reflect its real event rhythm, so a
source that is legitimately quiet (an idle location device, a game library not played) declares a
cadence long enough to be meaningful or declares none. A source that leaves a suppressed state SHALL
have its cadence window restart from the exit time, not from its last event.

ID: REQ-connector-silent-source-002
Source: bu-h40h2b.12 Slice D (grace, pause/decommission, maintenance/backfill suppression, source-specific false-positive handling); [Observed] connector-base-spec "Connector Liveness and Eligibility" lists `quarantined` and operator-paused states
Scope: v1-reserved

#### Scenario: Paused source is quiet

- **WHEN** a source is operator-paused and its cadence elapses
- **THEN** no case is opened

#### Scenario: Window restarts after suppression

- **WHEN** a paused source resumes
- **THEN** its cadence window starts at the resume time

#### Scenario: Backfill suppresses

- **WHEN** a backfill or replay for the source is running
- **THEN** no case is opened for it

### Requirement: One case per lapsed source, opened idempotently under a private-safe key

When a source becomes silent the evaluation SHALL open or join exactly one fleet case through the
Switchboard's case authority. The correlation key SHALL be `source-lapse/<connector_type>/<endpoint_ref>`
where `endpoint_ref` is a stable opaque reference derived from the endpoint identity, so the key is
readable by type and never contains the identity string. If a non-closed case exists for the key the
evaluation SHALL contribute to it rather than open another. Each contribution SHALL use evidence
`kind` `source_silent` and a `ref` that identifies the lapse episode, so a rerun is a no-op. The
case SHALL start `open` and `silent` and this evaluation SHALL NOT change its posture.

ID: REQ-connector-silent-source-003
Source: bu-h40h2b.12 Slice D; fleet-case-file REQ-fleet-case-file-003 (single non-closed case per key), REQ-fleet-case-file-004 (idempotent evidence), REQ-fleet-case-file-006 (Switchboard write authority); [Observed] src/butlers/core/fleet_cases.py `find_open_case`, `open_case`, `contribute_evidence`
Scope: v1-reserved

#### Scenario: First lapse opens a case

- **WHEN** a source becomes silent and no non-closed case exists for its key
- **THEN** one case is opened with the key `source-lapse/<connector_type>/<endpoint_ref>`
- **AND** it is `open` and `silent`

#### Scenario: Rerun is idempotent

- **WHEN** the evaluation runs again for the same lapse episode
- **THEN** no second case is opened
- **AND** the repeated evidence is a no-op

#### Scenario: Key hides the identity

- **WHEN** the endpoint identity is an email address
- **THEN** the correlation key does not contain it

### Requirement: Recovery transitions the same case and creating a case causes no other action

When a silent source is observed to produce events again, the evaluation SHALL contribute a
`source_recovered` evidence to the same case and close that case with a recorded outcome, and a
later lapse SHALL open a new case for the key. Creating, contributing to or closing a case under this
contract SHALL NOT send a notification, call a provider, retry a connector, restart a process or
trigger any remediation. Any escalation of posture, owner-facing delivery or remediation SHALL
require its own authority and is not granted here.

ID: REQ-connector-silent-source-004
Source: bu-h40h2b.12 Slice D ("case creation is not external delivery"; recovery resolves the same case); fleet-case-file REQ-fleet-case-file-002 (terminal outcome) and REQ-fleet-case-file-003 (new episode after closure)
Scope: v1-reserved

#### Scenario: Recovery closes the same case

- **WHEN** a source with an open lapse case produces an event again
- **THEN** `source_recovered` evidence is added to that case and the case is closed with an outcome
- **AND** no second case is created

#### Scenario: New lapse after recovery

- **WHEN** a recovered source goes silent again
- **THEN** a new case is opened for the same key

#### Scenario: Case creation has no side effects

- **WHEN** a lapse case is opened
- **THEN** no notification is sent, no provider is called and no connector is retried or restarted

## Source References

- Non-Negotiable Rule 4 (the daemon is deterministic infrastructure) and Rule 7 (transport is connector responsibility)
- RFC 0029 (expected signals), RFC 0032 (fleet case file), RFC 0038 D2
- `expected-signals` and `fleet-case-file` capabilities
