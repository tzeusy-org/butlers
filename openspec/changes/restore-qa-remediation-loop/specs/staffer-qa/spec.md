## MODIFIED Requirements

### Requirement: Patrol Loop Lifecycle
The QA Staffer SHALL run a scheduler-driven patrol loop that executes at a configurable interval (default: 10 minutes). Each patrol cycle is a discrete unit of work with its own DB record.

ID: REQ-staffer-qa-011
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Patrol tick fires on schedule
- **WHEN** the patrol interval elapses
- **THEN** the QA Staffer creates a new patrol record in `public.qa_patrols`
- **AND** polls all enabled discovery sources
- **AND** passes combined findings through the triage layer
- **AND** dispatches novel findings for investigation (up to concurrency cap)
- **AND** updates the patrol record with outcome (findings_count, novel_count, dispatched_count, status, sources_polled)

#### Scenario: Patrol cycle completes with no findings
- **WHEN** all enabled discovery sources return complete empty finding sets for their declared coverage
- **THEN** the patrol record is marked `status = "clean"`
- **AND** `findings_count = 0`, `novel_count = 0`, `dispatched_count = 0`

#### Scenario: Patrol cycle discovers issues
- **WHEN** one or more discovery sources return findings
- **THEN** each finding is recorded in `public.qa_findings` with its `source_type`
- **AND** the triage layer filters to novel issues
- **AND** novel issues are dispatched for investigation (up to concurrency cap)
- **AND** the patrol record reflects the full pipeline: `findings_count`, `novel_count`, `dispatched_count`

#### Scenario: Patrol overlap prevention
- **WHEN** a patrol tick fires while the previous patrol cycle is still running
- **THEN** the new tick is skipped with a log WARNING
- **AND** the skip is recorded as a patrol with `status = "skipped_overlap"`

#### Scenario: Patrol crash recovery
- **WHEN** the QA staffer daemon restarts and finds a `qa_patrols` row with `status = "running"` and no `completed_at`
- **THEN** the stale row is updated to `status = "error"`, `completed_at = now()`, `error_detail = "daemon restart during patrol"`
- **AND** any `investigating` healing attempts with `qa_patrol_id` matching that patrol are evaluated by the restart recovery logic in `healing_attempts` (deadline-based timeout or preserve — no special re-dispatch)
- **AND** persisted findings that were novel but not yet dispatched retain pending work; expired leases are reclaimed and acknowledged only with an authoritative disposition
- **NOTE** `dispatch_pending` is not a valid `healing_attempts` status — there is no special recovery path for it

#### Scenario: Reactive findings between patrols
- **WHEN** a butler calls `report_error` between patrol cycles
- **THEN** the finding is relayed to the QA staffer through the Switchboard service-plane relay calling the QA staffer's `report_finding` tool — a deterministic butler-to-staffer forward that starts no goal
- **AND** at the RFC0038 generalist cutover this relay is not LLM-presentable or on the session listener and never uses the deleted generic `route` tool
- **AND** ordinary relay findings retain their existing volatile buffer contract until persisted; durable dashboard reports continue through their separately specified inbox
- **AND** it is picked up on the next patrol tick
- **AND** if the finding's severity is 0 (critical), an immediate mini-patrol is triggered without waiting for the next timer tick
- **AND** that mini-patrol polls every enabled source and records scheduled origin; only complete source coverage qualifies for completed discovery assurance

#### Scenario: Partial source is distinct from failed scheduler
- **WHEN** a source returns bounded partial observations or is unavailable
- **THEN** observed findings remain usable, but that source is not certified complete and cannot prove recovery of a missing condition
- **AND** patrol liveness, source availability and coverage completeness are separate reported facts
- **AND** independently supervised liveness observation and owner-policy admission checks remain in force

## ADDED Requirements

### Requirement: Bounded Discovery Progress and Coverage
Enabled deterministic discovery sources SHALL implement the companion `discover_batch(lookback_minutes: int, checkpoint: SourceCheckpoint | None) -> DiscoveryBatch` method and report a source-generation-bound progress token, covered interval or observation boundary, and complete/partial/unavailable status; durable progress SHALL advance only with persisted findings or explicit durable exclusions.

ID: REQ-staffer-qa-012
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Batch protocol preserves existing inbox authority
- **WHEN** the patrol coordinator polls an enabled source
- **THEN** it calls discover_batch with the trusted durable checkpoint; DiscoveryBatch carries findings, source_generation, next_checkpoint, covered_boundary, coverage_status and gap_reason
- **AND** the existing discover(lookback_minutes) list-return contract remains a projection of the same bounded batch for any surviving caller, never a second scan or inbox
- **AND** only the coordinator commits progress after durable finding disposition; source code cannot self-acknowledge unread data
- **AND** all active dashboard-inbox authentication, canonical identity, receipt lookup, fenced delivery and ordinary-relay volatility requirements remain unchanged

#### Scenario: Catch-up after interruption
- **WHEN** QA resumes after a gap longer than the normal overlap window
- **THEN** it resumes from durable source progress with bounded batches and explicit backlog age
- **AND** overlapping replay and late-arriving records produce idempotent observations rather than duplicate incident admissions
- **AND** progress cannot jump past an unpersisted batch or a failed source read

#### Scenario: Source loss reset or retention boundary
- **WHEN** a source generation changes, data rotates, or retention has removed an unread range
- **THEN** QA reports the coverage gap and resumes from a validated new boundary without claiming the unavailable interval was clean
- **AND** fair continuation prevents a hot source from indefinitely starving another enabled source

#### Scenario: Durable inbox and volatile relay boundaries
- **WHEN** discovery consumes dashboard reports or ordinary butler relays
- **THEN** it preserves the active dashboard-inbox delivery/acknowledgement contract and does not create a competing inbox
- **AND** ordinary relay buffering is explicitly volatile before persistence and never supplies a durable-capture assurance claim
- **AND** source coverage/cursors confer no extra cross-schema or external-provider access
