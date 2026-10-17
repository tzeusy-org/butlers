## MODIFIED Requirements

### Requirement: Structured Evidence Payloads
QA findings and investigations SHALL carry structured evidence in addition to any free-form summary text. Evidence for a reasoning failure SHALL identify the failed goal invocation by its ledger identifiers rather than by a session UUID.

The evidence set is bounded by what each discovery source exposes through its
sanctioned access path (RFC 0010).  The current implementation delivers Phase 1
evidence (identifiers and source-specific metadata available without additional
DB migrations).  Richer evidence fields (`request_id`, `trace_id`, `runtime_type`,
`model`, tool-call summaries) are deferred to a future Phase 2 that extends the
`v_qa_recent_failures` view.

Source: RFC 0038 D11 and D17
Scope: v1-mandatory

#### Scenario: Session-records finding includes structured evidence (Phase 1)
- **WHEN** a finding originates from the `session_records` source
- **THEN** `structured_evidence` contains:
  - `source`: `"session_records"`
  - `status`: the invocation failure state (`"failed"` | `"timed_out"`)
  - `invocation_ids`: a list of up to 5 failed goal-invocation identifiers (as strings) that share this fingerprint, drawn from the `v_qa_recent_failures` view
  - `goal_ids`: the distinct `goal_id` values of those invocations
- **AND** the investigation prompt includes a `## Structured Evidence` section listing the available identifiers without embedding raw sensitive payloads

#### Scenario: Log-scanner finding includes structured evidence (Phase 1)
- **WHEN** a finding originates from the `log_scanner` source
- **THEN** `structured_evidence` contains:
  - `source`: `"log_scanner"`
  - `log_file`: the filename (stem) of the log file where the fingerprint was first seen
  - `level`: the log level of the first occurrence (e.g. `"error"`, `"critical"`)
  - `trigger_source`: the `trigger_source` field from the structured JSON log entry if present; omitted if absent
- **AND** the investigation prompt includes a `## Structured Evidence` section listing the available identifiers without embedding raw sensitive payloads

#### Scenario: Large evidence bundle attached out-of-band (Phase 2 — not yet implemented)
- **NOTE** Out-of-band worktree artifact persistence for large evidence bundles is deferred to Phase 2.
- **WHEN** the available diagnostic evidence is too large for the prompt (Phase 2 only)
- **THEN** QA persists a redacted evidence artifact in the worktree
- **AND** the prompt points the agent to that artifact for detailed inspection

### Requirement: QA Self-Recursion Barrier
QA SHALL suppress autonomous investigation of failures originating from QA self-healing sessions. The suppression decision is driven by a `source_session_trigger_source` field carried on every `QaFinding`. Provenance for work done inside a goal SHALL come from the verified invocation lineage, never from spawner-derived session context or a model-supplied argument.

Source: RFC 0038 D7 and D18
Scope: v1-mandatory

#### Scenario: QaFinding carries source session trigger_source
- **WHEN** a discovery source produces a `QaFinding`
- **THEN** the finding MUST include `source_session_trigger_source` (nullable str): the provenance of the work that produced the error — an engineering-executor session's `trigger_source`, or the goal origin of a goal invocation
- **AND** for `session_records` source: `source_session_trigger_source` is the failed invocation's goal origin read from the goal ledger through the view; for an engineering-executor session row, it is that row's `trigger_source` column
- **AND** for `log_scanner` source: `source_session_trigger_source` is extracted from the structured JSON log field `trigger_source` if present; `null` if absent
- **AND** for `butler_reports` source: `source_session_trigger_source` is set by the reporting butler's deterministic relay code — from the active engineering-executor session's `trigger_source` when that session's failure is reported, or from the goal origin of the verified assertion lineage when a goal invocation's call reaches `report_error`; the `report_finding` tool SHALL accept an optional `trigger_source` parameter and include it in the buffered finding
- **AND** `source_session_trigger_source` is stored in the `qa_findings` row for auditing

#### Scenario: QA finding originated from QA self-healing session
- **WHEN** a finding's `source_butler == "qa"` AND `source_session_trigger_source` is in `{"healing", "qa"}` (QA investigation sessions use `trigger_source="qa"`; healing sessions use `trigger_source="healing"`)
- **THEN** normal autonomous investigation is suppressed
- **AND** the finding is routed to a QA meta-review/operator lane (visible at `GET /api/qa/meta-review`)
- **AND** no standard QA investigation workflow is launched for that finding

#### Scenario: QA finding with unknown trigger source
- **WHEN** a finding's `source_butler == "qa"` AND `source_session_trigger_source` is null or not in `{"healing", "qa"}`
- **THEN** the finding is treated as potentially recursive and routed to the meta-review/operator lane as a precaution
- **AND** a log WARNING is emitted: "QA finding from QA butler with unrecognized trigger_source; routing to meta-review"

#### Scenario: Non-QA finding is never suppressed by this barrier
- **WHEN** a finding's `source_butler` is any value other than `"qa"`
- **THEN** the self-recursion barrier does NOT apply regardless of `source_session_trigger_source`
