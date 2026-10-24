## MODIFIED Requirements

### Requirement: Cooldown Awareness
The triage layer SHALL respect the existing per-fingerprint cooldown from the healing dispatch system.

ID: REQ-qa-triage-001
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Recent terminal attempt within cooldown
- **WHEN** a finding's fingerprint has a terminal `healing_attempts` row closed within the cooldown window (default: 60 minutes)
- **THEN** the finding is marked `dedup_reason = "cooldown"`
- **AND** it is excluded from investigation dispatch

#### Scenario: Unchanged incident outcome outlives cooldown
- **WHEN** an inconclusive, blocked, operational, design-required or patch-ready incident has no material evidence/configuration/deployment change
- **THEN** expiry of its ordinary fingerprint cooldown does not by itself admit another reasoning session
- **AND** a higher severity or meaningful new evidence makes it eligible for fresh admission subject to the incident budget, current policy and unresolved publication holds
- **AND** polling timestamps, occurrence-count increments alone and paraphrased summaries are not material evidence changes

### Requirement: Finding Persistence

All findings (novel and deduplicated) SHALL be recorded in `public.qa_findings`
for dashboard visibility, including a finding suppressed by an active
infrastructure condition.

ID: REQ-qa-triage-002
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Finding record structure
- **WHEN** a finding is processed by the triage layer
- **THEN** a row is inserted in `public.qa_findings` with: `id` (UUID, preserving the legacy UUIDv4 default),
  `patrol_id` (FK to qa_patrols), `fingerprint` (str), `source_type` (str,
  e.g., "log_scanner", "session_records", "butler_reports"),
  `source_butler` (str), `severity` (int), `exception_type` (str),
  `event_summary` (str), `call_site` (str), `occurrence_count` (int),
  `first_seen` (timestamptz), `last_seen` (timestamptz), `dedup_reason`
  (nullable text), `healing_attempt_id` (nullable UUID FK preserving the referenced legacy identity),
  `source_session_trigger_source` (nullable text — verified goal origin for goal-invocation failures, or the engineering-executor session/log `trigger_source` otherwise; drives QA self-recursion suppression under RFC0038, never caller-asserted provenance), a pending-work reference when deferred; the new fenced pending-work record is the sole queue authority, replacing dispatch_queued and retiring get_dispatch_queued_findings(),
  `created_at` (timestamptz)
- **AND** when dispatch suppresses an otherwise eligible `infra_state`
  finding for an active condition, it updates the existing row with
  `dedup_reason = infra_condition_open` and leaves `healing_attempt_id` null
- **AND** that explicit suppression remains visible without adding a new QA
  source-type vocabulary

#### Scenario: Acknowledgement is coupled to disposition
- **WHEN** a deferred finding is leased for triage
- **THEN** its pending work is acknowledged only with the durable linked attempt or explicit final suppression reason in one transaction
- **AND** an expired lease is reclaimable with a new fence and an older worker cannot acknowledge the successor claim
- **AND** queue age, capacity deferral, failed-source evidence and denied policy are separately visible

### Requirement: Three-Source Deduplication
The triage layer SHALL check each finding's fingerprint against three sources to determine novelty.

ID: REQ-qa-triage-004
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Check active healing attempts (includes open PRs)
- **WHEN** a finding's fingerprint matches an active row in `public.healing_attempts` (status in: `investigating`, `proposal_ready`, `pr_open`, `held`)
- **THEN** the finding is marked `dedup_reason = "active_investigation"`
- **AND** it is excluded from investigation dispatch
- **AND** the finding's `qa_findings` record is linked to the existing attempt ID
- **AND** if the matched attempt has `status = "pr_open"` with a non-null `pr_url`, the dashboard reads the `pr_url` through the finding's `healing_attempt_id` FK to the attempt row (the `pr_url` is not copied onto `qa_findings`)
- **NOTE** `dispatch_pending` is NOT a valid status and is NOT included here — deferred work is persisted separately from healing-attempt status; authoritative novelty claim and attempt linkage are atomic

#### Scenario: Check local dismissal cache
- **WHEN** a finding's fingerprint exists in the `public.qa_dismissals` table with `dismissed_until > now()`
- **THEN** the finding is marked `dedup_reason = "dismissed"`
- **AND** it is excluded from investigation dispatch

#### Scenario: Novel finding
- **WHEN** a finding's fingerprint does not match any of the three dedup sources
- **THEN** the finding is marked `dedup_reason = null` (novel)
- **AND** it is a candidate for state-aware incident admission, not proof that another reasoning session is warranted

#### Scenario: Incident linkage augments fingerprint deduplication
- **WHEN** trusted occurrence lineage connects distinct source fingerprints to one incident generation
- **THEN** triage retains both source observations and joins the incident's existing attempt or pending disposition
- **AND** unrelated matching text never substitutes for that lineage

### Requirement: Dismissal Management
Operators SHALL be able to dismiss findings via the dashboard API, preventing them from triggering investigations for a configurable duration.

ID: REQ-qa-triage-005
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Dismiss a finding by fingerprint
- **WHEN** `POST /api/qa/known-issues/{fingerprint}/dismiss` is called (fingerprint in the path) with an optional body `{dismissed_until}`; any legacy caller-asserted actor is ignored under the established attribution policy
- **THEN** a row is upserted in `public.qa_dismissals` with `fingerprint`, `dismissed_until` (the supplied timestamp, or a year-9999 sentinel for an indefinite dismissal when omitted), and `dismissed_by` derived from authenticated_principal(), never a caller-supplied actor
- **AND** subsequent triage cycles skip this fingerprint until `dismissed_until` expires

#### Scenario: Dismissal expiry
- **WHEN** `dismissed_until` has passed for a fingerprint
- **THEN** subsequent findings with that fingerprint are reconsidered under all current admission gates, including incident state and remaining budget; expiry alone cannot reset an unchanged terminal outcome

## ADDED Requirements

### Requirement: Incident Correlation and Reasoning Admission
QA SHALL retain source observations independently while correlating them into incident generations by trustworthy shared occurrence/session/request/condition or verified goal/invocation lineage, and SHALL atomically admit reasoning on material state changes within finite persisted budgets.

ID: REQ-qa-triage-003
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Multiple discovery sources observe one failure
- **WHEN** a log observation and session or tool failure share verified occurrence lineage
- **THEN** both evidence references enrich one incident generation with one admitted investigation for that evidence revision
- **AND** equal message text alone never merges unrelated sources, owners or source epochs
- **AND** without trusted linkage separate incidents remain visible rather than asserting a shared root cause

#### Scenario: Concurrent retries and recovery create no duplicate work
- **WHEN** two patrols, restarts or feedback consumers request work for the same incident generation and evidence revision
- **THEN** one fenced persisted admission owns the launch and the others join or defer with an explicit reason
- **AND** a completed recovery followed by a new occurrence opens a distinguishable regression generation without rewriting earlier evidence

#### Scenario: Untrusted caller or unavailable durable store
- **WHEN** a caller supplies another incident owner, arbitrary schema or fabricated observation identity, or persistence is unavailable
- **THEN** admission refuses without launching and no caller-asserted attribution becomes authoritative
- **AND** runtime roles outside QA cannot write QA admission, cursor or evidence-verdict records even after bootstrap grants are replayed

#### Scenario: Publication capability outage is diagnosed once
- **WHEN** an existing trusted publisher receipt reports unavailable repository access
- **THEN** QA records an action-needed capability condition and retains candidate artifacts rather than repeatedly executing identical coding work
- **AND** this condition adds no credential lookup, provider probe or broader permission beyond the existing publisher contract
