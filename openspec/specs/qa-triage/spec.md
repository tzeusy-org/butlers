# QA Triage

## Purpose

Deduplication and triage layer that cross-references findings from all discovery sources against active investigations, open GitHub PRs, cooldown windows, and a local dismissal cache. Determines which findings are novel and warrant investigation dispatch. Source-agnostic — works identically whether findings come from log scanning, session records, reactive butler reports, or future sources.

## Requirements

### Requirement: Source-Agnostic Triage
The triage layer SHALL accept `QaFinding` objects from any discovery source and apply identical deduplication logic regardless of source type.

#### Scenario: Mixed-source patrol cycle
- **WHEN** a patrol cycle produces findings from log_scanner, session_records, and butler_reports
- **THEN** all findings are merged into a single set, deduplicated by fingerprint across sources
- **AND** the `source_type` field is preserved for dashboard reporting but does not affect triage decisions

### Requirement: Three-Source Deduplication
The triage layer SHALL check each finding's fingerprint against three sources to determine novelty.

#### Scenario: Check active healing attempts (includes open PRs)
- **WHEN** a finding's fingerprint matches an active row in `public.healing_attempts` (status in: `investigating`, `pr_open`)
- **THEN** the finding is marked `dedup_reason = "active_investigation"`
- **AND** it is excluded from investigation dispatch
- **AND** the finding's `qa_findings` record is linked to the existing attempt ID
- **AND** if the matched attempt has `status = "pr_open"` with a non-null `pr_url`, the dashboard reads the `pr_url` through the finding's `healing_attempt_id` FK to the attempt row (the `pr_url` is not copied onto `qa_findings`)
- **NOTE** `dispatch_pending` is NOT a valid status and is NOT included here — there is no deferred pre-launch state; novelty claim and row insertion are atomic

#### Scenario: Check local dismissal cache
- **WHEN** a finding's fingerprint exists in the `public.qa_dismissals` table with `dismissed_until > now()`
- **THEN** the finding is marked `dedup_reason = "dismissed"`
- **AND** it is excluded from investigation dispatch

#### Scenario: Novel finding
- **WHEN** a finding's fingerprint does not match any of the three dedup sources
- **THEN** the finding is marked `dedup_reason = null` (novel)
- **AND** it is eligible for investigation dispatch

### Requirement: Cooldown Awareness
The triage layer SHALL respect the existing per-fingerprint cooldown from the healing dispatch system.

#### Scenario: Recent terminal attempt within cooldown
- **WHEN** a finding's fingerprint has a terminal `healing_attempts` row closed within the cooldown window (default: 60 minutes)
- **THEN** the finding is marked `dedup_reason = "cooldown"`
- **AND** it is excluded from investigation dispatch

### Requirement: Finding Persistence
All findings (novel and deduplicated) SHALL be recorded in `public.qa_findings` for dashboard visibility.

#### Scenario: Finding record structure
- **WHEN** a finding is processed by the triage layer
- **THEN** a row is inserted in `public.qa_findings` with: `id` (UUIDv7), `patrol_id` (FK to qa_patrols), `fingerprint` (str), `source_type` (str, e.g., "log_scanner", "session_records", "butler_reports"), `source_butler` (str), `severity` (int), `exception_type` (str), `event_summary` (str), `call_site` (str), `occurrence_count` (int), `first_seen` (timestamptz), `last_seen` (timestamptz), `dedup_reason` (nullable text), `healing_attempt_id` (nullable UUIDv7 FK), `source_session_trigger_source` (nullable text — the `trigger_source` from the session or log entry that produced the error; drives QA self-recursion suppression), `dispatch_queued` (bool default FALSE — set to TRUE when the finding is skipped due to concurrency cap; the next patrol cycle loads queued findings via `get_dispatch_queued_findings()` and retries them), `created_at` (timestamptz)

### Requirement: Dismissal Management
Operators SHALL be able to dismiss findings via the dashboard API, preventing them from triggering investigations for a configurable duration.

#### Scenario: Dismiss a finding by fingerprint
- **WHEN** `POST /api/qa/known-issues/{fingerprint}/dismiss` is called (fingerprint in the path) with an optional body `{dismissed_until, dismissed_by}`
- **THEN** a row is upserted in `public.qa_dismissals` with `fingerprint`, `dismissed_until` (the supplied timestamp, or a year-9999 sentinel for an indefinite dismissal when omitted), and `dismissed_by` (defaults to `"dashboard_user"`)
- **AND** subsequent triage cycles skip this fingerprint until `dismissed_until` expires

#### Scenario: Dismissal expiry
- **WHEN** `dismissed_until` has passed for a fingerprint
- **THEN** subsequent findings with that fingerprint are treated as novel again

### Requirement: Severity-Based Prioritization
Novel findings SHALL be ordered by severity for investigation dispatch, with more severe findings dispatched first.

#### Scenario: Dispatch ordering
- **WHEN** multiple novel findings are eligible for dispatch
- **THEN** they are sorted by severity ascending (0=critical first)
- **AND** within the same severity, by occurrence_count descending (most frequent first)
- **AND** the dispatch loop processes them in this order up to the concurrency cap

### Requirement: Journal Event Emission, Triage
The dispatch layer SHALL emit a `flagged` journal event into `public.qa_investigation_events` whenever a novel finding becomes the head of a new investigation, immediately after the atomic novelty claim that inserts the `healing_attempts` row (the triage module classifies novelty; the dispatch layer performs the emission). Triage MAY emit `sampled` and `cross-checked` events when it performs multi-source corroboration; v1 implementations are permitted to omit these.

#### Scenario: flagged event on novel finding dispatch
- **WHEN** triage persists a `qa_findings` row with `dedup_reason = null` AND the dispatcher proceeds to insert a new `healing_attempts` row for that finding (novelty gate atomic claim succeeds)
- **THEN** the dispatch layer inserts a `qa_investigation_events` row with `step = 'flagged'`, `attempt_id = <new attempt id>`, `finding_id = <the qa_findings id>`, `text` summarizing the trigger (e.g. `"patrol cycle <N> · failure_streak crossed <K>"` or `"novel finding from <source_type>"`), and a `detail` with the source butler, fingerprint prefix, and severity heuristic label
- **AND** the event's `ts` matches the patrol's `started_at` to within one second

#### Scenario: No flagged event for deduplicated findings
- **WHEN** triage persists a `qa_findings` row with a non-null `dedup_reason` (active_investigation, dismissed, cooldown, concurrency_cap, etc.)
- **THEN** no `flagged` journal event is emitted
- **AND** the row is still persisted for dashboard visibility per the existing Finding Persistence requirement

#### Scenario: Optional sampled event for cross-source corroboration
- **WHEN** triage observes the same fingerprint across two or more discovery sources within a single patrol cycle
- **THEN** triage MAY insert a `qa_investigation_events` row with `step = 'sampled'`, `text` describing the corroboration (`"corroborated across <N> sources"`), and a `detail` listing the source types
- **AND** v1 implementations are explicitly permitted to omit this emission

#### Scenario: Optional cross-checked event for dispatch-history corroboration
- **WHEN** triage observes a fingerprint that has had at least one terminal `healing_attempts` row in the prior 24 hours (regardless of cooldown state)
- **THEN** triage MAY insert a `qa_investigation_events` row with `step = 'cross-checked'`, `text` describing the prior context (`"prior attempts: <N> in 24h"`), and a `detail` summarizing their statuses
- **AND** v1 implementations are explicitly permitted to omit this emission
