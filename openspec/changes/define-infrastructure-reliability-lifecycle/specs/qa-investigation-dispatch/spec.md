# qa-investigation-dispatch

## MODIFIED Requirements

### Requirement: Gate Sequence Preservation

The QA dispatcher SHALL preserve the existing admission protections for each
novel finding before investigation. Triage performs a fast non-atomic dedup
check to filter obvious duplicates early; dispatch performs the authoritative
atomic claim only after normal eligibility and active-infrastructure-condition
suppression have both been evaluated.

#### Scenario: Gates applied per-finding after triage
- **WHEN** a novel finding passes triage's fast dedup check
- **THEN** the dispatcher applies normal eligibility checks for recursion,
  opt-in, fingerprint, severity, cooldown, concurrency cap, circuit breaker,
  and model resolution
- **AND** after those checks pass but before `create_or_join_attempt`, an
  `infra_state` finding is matched against an active infrastructure condition
  by explicit canonical source and fingerprint
- **AND** only a finding without a matching active condition proceeds to the
  authoritative atomic novelty claim and then to worktree/session launch
- **AND** findings rejected by a normal eligibility gate or active-condition
  suppression are recorded with their explicit rejection reason in
  `qa_findings.dedup_reason`

#### Scenario: Active infrastructure condition is checked before attempt claim
- **WHEN** an otherwise eligible `infra_state` finding matches an `open` or
  `aging` infrastructure-condition episode
- **THEN** the dispatcher writes the decision-only `infra_condition_open`
  dispatch event before calling `create_or_join_attempt`
- **AND** it returns without creating, joining, deleting, or changing a
  `healing_attempt`
- **AND** it invokes no LLM, creates no runtime session, and creates no
  worktree

### Requirement: Gate Rejections Do Not Count as Execution Failures

QA admission-control outcomes SHALL remain distinct from launched
investigation outcomes.

#### Scenario: Circuit breaker or cooldown rejection before launch
- **WHEN** a finding is rejected by cooldown, concurrency cap, circuit
  breaker, or no-model before any QA investigation session launches
- **THEN** no investigation attempt is marked `failed` solely because of that
  rejection
- **AND** the rejection does NOT contribute to the QA circuit-breaker failure
  streak
- **AND** the dashboard exposes it as a dispatch decision rather than a
  failed execution

#### Scenario: Active infrastructure condition rejection before claim
- **WHEN** an otherwise eligible `infra_state` finding is suppressed because
  its canonical condition remains active
- **THEN** `healing_dispatch_events` records `decision = infra_condition_open`
  with null attempt linkage
- **AND** no `healing_attempts` row, worktree, runtime session, or LLM
  invocation exists as a consequence of that suppression
- **AND** the event does NOT contribute to QA circuit-breaker execution
  history

#### Scenario: Infra-condition suppression links back to the suppressing condition (bu-ep4ks.3)
- **WHEN** an `infra_state` finding is rejected because an active standing
  condition (`public.infra_conditions`, same `source`/`fingerprint`
  identity) already covers it
- **THEN** the rejection is recorded as a `healing_dispatch_events` row with
  `decision="infra_condition_open"`, carrying the same `fingerprint` as the
  suppressing condition
- **AND** `GET /api/healing/dispatch-events` accepts a `fingerprint` filter
  (combinable with `decision`) so a dashboard surface can look up every
  dispatch a given standing condition suppressed
- **AND** this suppression is no longer invisible: the Standing Conditions
  panel (see `system-overview-page` spec) surfaces a per-condition count of
  suppressed QA dispatches derived from this join

## Source References
- Non-Negotiable Rule 4 (deterministic daemon infrastructure)
- RFC 0001 (admission decisions precede launched execution)
- RFC 0005 (decision telemetry distinct from execution failures)
- `infrastructure-reliability` (pre-claim infrastructure-condition suppression)
