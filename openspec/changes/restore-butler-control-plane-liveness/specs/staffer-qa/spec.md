## ADDED Requirements

### Requirement: [TARGET-STATE] Patrol continues through derived remote staleness
The QA Staffer's deterministic local patrol and recovery schedules SHALL remain runnable when its remote registry observation is stale. Only explicit authorized administrative pause or quarantine may stop local schedules. Investigation dispatch MAY be suppressed by its separately defined admission gates while discovery evidence remains durable.

ID: REQ-staffer-qa-006
Source: heart-and-soul/vision.md:51-53,80-84; RFC 0015 §D6; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §4 QA circular gate
Scope: v1-mandatory

#### Scenario: QA observes its own registry failure
- **WHEN** QA's daemon and database remain operational but its remote registry row becomes stale
- **THEN** the next due local QA patrol still runs and records its result
- **AND** an independent controller can separately report patrol age if that patrol never completes

#### Scenario: Explicit administrative stop remains effective
- **WHEN** an authorized owner explicitly pauses or quarantines QA
- **THEN** local schedules respect that policy and the absence of patrols remains visible as an intentional stop

### Requirement: [TARGET-STATE] Fleet findings correlate to the control-plane condition
QA SHALL associate related per-butler liveness findings with the active fleet-level condition and preserve source evidence without dispatching a separate investigation for each affected daemon.

ID: REQ-staffer-qa-007
Source: RFC 0015 §D1-4; openspec/changes/define-infrastructure-reliability-lifecycle/specs/infrastructure-reliability/spec.md §Infrastructure-condition QA suppression; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §5.2
Scope: v1-mandatory

#### Scenario: One fleet failure is visible without case explosion
- **WHEN** the independent controller has an active fleet condition covering several stale daemons
- **THEN** QA preserves affected-daemon evidence and records one condition-linked suppression decision for duplicate investigations
- **AND** no condition absence is inferred from a failed or partial scan
