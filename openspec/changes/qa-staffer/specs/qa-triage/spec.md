# QA Triage

## Purpose

Deduplication and triage layer that cross-references findings from all discovery sources against active investigations, open GitHub PRs, cooldown windows, and a local dismissal cache. Determines which findings are novel and warrant investigation dispatch. Source-agnostic — works identically whether findings come from log scanning, session records, reactive butler reports, or future sources.

## ADDED Requirements

### Requirement: Source-Agnostic Triage
The triage layer SHALL accept `QaFinding` objects from any discovery source and apply identical deduplication logic regardless of source type.

#### Scenario: Mixed-source patrol cycle
- **WHEN** a patrol cycle produces findings from log_scanner, session_records, and butler_reports
- **THEN** all findings are merged into a single set, deduplicated by fingerprint across sources
- **AND** the `source_type` field is preserved for dashboard reporting but does not affect triage decisions

### Requirement: Three-Source Deduplication
The triage layer SHALL check each finding's fingerprint against three sources to determine novelty.

#### Scenario: Check active healing attempts (includes open PRs)
- **WHEN** a finding's fingerprint matches an active row in `public.healing_attempts` (status in: `dispatch_pending`, `investigating`, `pr_open`)
- **THEN** the finding is marked `dedup_reason = "active_investigation"`
- **AND** it is excluded from investigation dispatch
- **AND** the finding's `qa_findings` record is linked to the existing attempt ID
- **AND** if the matched attempt has `status = "pr_open"` with a non-null `pr_url`, the finding's record also stores the `pr_url` for dashboard display

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
- **THEN** a row is inserted in `public.qa_findings` with: `id` (UUIDv7), `patrol_id` (FK to qa_patrols), `fingerprint` (str), `source_type` (str, e.g., "log_scanner", "session_records", "butler_reports"), `source_butler` (str), `severity` (int), `exception_type` (str), `event_summary` (str), `call_site` (str), `occurrence_count` (int), `first_seen` (timestamptz), `last_seen` (timestamptz), `dedup_reason` (nullable text), `healing_attempt_id` (nullable UUIDv7 FK), `created_at` (timestamptz)

### Requirement: Dismissal Management
Operators SHALL be able to dismiss findings via the dashboard API, preventing them from triggering investigations for a configurable duration.

#### Scenario: Dismiss a finding by fingerprint
- **WHEN** `POST /api/qa/dismiss` is called with `fingerprint` and `duration_hours` (default: 24)
- **THEN** a row is upserted in `public.qa_dismissals` with `fingerprint`, `dismissed_until = now() + duration`, `dismissed_by = "dashboard"`
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
