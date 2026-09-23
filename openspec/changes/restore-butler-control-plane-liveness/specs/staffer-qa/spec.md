## MODIFIED Requirements

### Requirement: Patrol Database Schema
Patrol cycles SHALL be recorded in `public.qa_patrols` for observability and dashboard display. The record SHALL distinguish an actual scheduled discovery cycle from an operator-created synthetic validation row and retain the enabled-source configuration and successful-completion evidence used by independent patrol assurance. Legacy rows without this provenance SHALL remain readable but SHALL NOT count as qualifying patrols.

ID: REQ-staffer-qa-008
Source: RFC 0015 §D6; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §5.2; [Observed] src/butlers/modules/qa/__init__.py and src/butlers/api/routers/qa.py synthetic finding path
Scope: v1-mandatory

#### Scenario: Patrol record structure
- **WHEN** a patrol cycle starts
- **THEN** a row is inserted with: `id` (UUIDv7), `started_at` (timestamptz), `completed_at` (nullable timestamptz), `status` (text: running, clean, findings_dispatched, suppressed, error, skipped_overlap), `findings_count` (int), `novel_count` (int), `dispatched_count` (int), `log_lookback_minutes` (int), `sources_polled` (text[], successful source names), `error_detail` (nullable text), plus `origin` (`scheduled` or `operator_synthetic`), `enabled_sources_snapshot` (text[]), `enabled_sources_config_digest` (bounded stable digest), and `discovery_complete` (boolean)
- **AND** migrated legacy rows with no reliable origin/source-completion evidence retain null provenance rather than being backfilled as completed

#### Scenario: Patrol record is updated on completion
- **WHEN** a patrol cycle completes (success or failure)
- **THEN** the row's `completed_at`, `status`, and count fields are updated
- **AND** if the cycle errored, `error_detail` contains a sanitized error summary
- **AND** `discovery_complete=true` only when a scheduled cycle successfully completed every source in its captured enabled set, with `sources_polled` matching that set and no source error

#### Scenario: Genuine suppressed patrol remains healthy discovery
- **WHEN** a scheduled cycle successfully completes every enabled source but its novel findings are filtered by cooldown or severity
- **THEN** it may finish `status='suppressed'` with `origin='scheduled'` and `discovery_complete=true`
- **AND** it qualifies for patrol freshness while investigation dispatch remains suppressed

#### Scenario: Synthetic validation row does not renew assurance
- **WHEN** the dashboard creates an operator validation placeholder patrol
- **THEN** it records `origin='operator_synthetic'`, `discovery_complete=false`, and no successful enabled-source snapshot, regardless of its `status='suppressed'`
- **AND** it cannot renew patrol freshness or resolve an overdue condition

#### Scenario: Changed or missing source configuration fails closed
- **WHEN** the current enabled-source configuration differs from the patrol's recorded digest, or a legacy row lacks reliable provenance
- **THEN** that row does not prove current patrol coverage for readiness or condition resolution
- **AND** a subsequent scheduled complete cycle under the current configuration can establish freshness

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
