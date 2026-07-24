# qa-triage

## MODIFIED Requirements

### Requirement: Source-Agnostic Triage

The triage layer SHALL accept `QaFinding` objects from any discovery source and
apply identical fast deduplication logic regardless of source type. An active
infrastructure-condition check SHALL remain a dispatch-admission decision, not
a new triage source type or an early triage shortcut.

#### Scenario: Mixed-source patrol cycle
- **WHEN** a patrol cycle produces findings from log_scanner, session_records,
  butler_reports, and infra_state
- **THEN** all findings are merged into a single set, deduplicated by
  fingerprint across sources
- **AND** the `source_type` field is preserved for dashboard reporting but
  does not change triage decisions

#### Scenario: InfraState finding reaches ordered dispatch admission
- **WHEN** an `infra_state` finding has no ordinary triage dedup reason
- **THEN** triage persists it as normally eligible for dispatch
- **AND** it does not add a source-type value or preempt the dispatcher's
  active-condition check
- **AND** the dispatcher alone can subsequently record
  `dedup_reason = infra_condition_open` after normal eligibility and before
  the atomic attempt claim

### Requirement: Finding Persistence

All findings (novel and deduplicated) SHALL be recorded in `public.qa_findings`
for dashboard visibility, including a finding suppressed by an active
infrastructure condition.

#### Scenario: Finding record structure
- **WHEN** a finding is processed by the triage layer
- **THEN** a row is inserted in `public.qa_findings` with: `id` (UUIDv7),
  `patrol_id` (FK to qa_patrols), `fingerprint` (str), `source_type` (str,
  e.g., "log_scanner", "session_records", "butler_reports"),
  `source_butler` (str), `severity` (int), `exception_type` (str),
  `event_summary` (str), `call_site` (str), `occurrence_count` (int),
  `first_seen` (timestamptz), `last_seen` (timestamptz), `dedup_reason`
  (nullable text), `healing_attempt_id` (nullable UUIDv7 FK),
  `source_session_trigger_source` (nullable text — the `trigger_source` from
  the session or log entry that produced the error; drives QA self-recursion
  suppression), `dispatch_queued` (bool default FALSE — set to TRUE when the
  finding is skipped due to concurrency cap; the next patrol cycle loads
  queued findings via `get_dispatch_queued_findings()` and retries them),
  `created_at` (timestamptz)
- **AND** when dispatch suppresses an otherwise eligible `infra_state`
  finding for an active condition, it updates the existing row with
  `dedup_reason = infra_condition_open` and leaves `healing_attempt_id` null
- **AND** that explicit suppression remains visible without adding a new QA
  source-type vocabulary

## Source References
- Non-Negotiable Rule 4 (deterministic daemon infrastructure)
- RFC 0001 (admission decisions before launched execution)
- `infrastructure-reliability` (active-condition visibility and suppression)
- `qa-investigation-dispatch` (ordered pre-claim dispatch admission)
