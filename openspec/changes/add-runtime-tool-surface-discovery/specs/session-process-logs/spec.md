## MODIFIED Requirements

### Requirement: Process log table schema
The system SHALL maintain a `session_process_logs` table per butler schema with columns: `session_id` (UUID PK, FK → sessions with CASCADE delete), `pid` (INTEGER, nullable), `exit_code` (INTEGER, nullable), `command` (TEXT, nullable), `stderr` (TEXT, nullable), `runtime_type` (TEXT, nullable), `created_at` (TIMESTAMPTZ, default now()), `expires_at` (TIMESTAMPTZ, default now() + 14 days). An index on `expires_at` SHALL exist for efficient cleanup queries.
The table SHALL additionally contain `tool_surface_attempts` (JSONB, non-null,
default empty array) for bounded content-blind receipts governed by the same row
TTL and session cascade.

ID: REQ-session-process-logs-001
Source: [Observed] session-process-logs table schema; RFC 0027 §Observability and Privacy
Scope: v1-mandatory

#### Scenario: Table created by migration
- **WHEN** migrations run from `core_022` through the additive tool-surface migration on a fresh butler schema
- **THEN** `core_022` creates the base `session_process_logs` table and expiry index
- **AND** the later migration adds non-null `tool_surface_attempts` with an empty-array default, yielding the specified current schema without rewriting `core_022`

#### Scenario: CASCADE delete on session removal
- **WHEN** a session row is deleted from the `sessions` table
- **THEN** the corresponding `session_process_logs` row is automatically deleted

#### Scenario: Existing process log receives an empty receipt array

- **WHEN** the additive tool-surface migration runs against an existing process-log row
- **THEN** `tool_surface_attempts` is populated as an empty JSON array
- **AND** existing subprocess diagnostics and expiry remain unchanged

## ADDED Requirements

### Requirement: Bounded Tool Surface Attempt Receipts

The process-log subsystem SHALL append or replace one content-blind tool-surface
receipt per model/runtime candidate attempt, keyed by zero-based candidate
index, and SHALL retain at most the spawner's configured candidate-attempt cap.
Each candidate receipt SHALL contain one initial presentation subattempt and at
most one replay-safe eager fallback subattempt, ordered by zero-based
presentation index. Reading a non-expired process log SHALL return the nested
ordered receipt array together with existing subprocess diagnostics.

ID: REQ-session-process-logs-002
Source: RFC 0027 §Observability and Privacy; core-tool-discovery REQ-core-tool-discovery-008
Scope: v1-mandatory

#### Scenario: Failover attempts remain individually visible

- **WHEN** one logical session performs multiple candidate attempts or one candidate performs a presentation fallback
- **THEN** the process log preserves every candidate and its one-or-two ordered presentation subattempts
- **AND** a later upsert does not erase or collapse earlier candidate or presentation evidence

#### Scenario: Receipt contains no content payload

- **WHEN** a tool-surface receipt is persisted
- **THEN** it contains only the closed fields specified by `core-tool-discovery`
- **AND** it excludes prompts, search queries, schemas, descriptions, tool inputs/results, credentials, and raw exception text

#### Scenario: Receipt expires with its process log

- **WHEN** process-log TTL cleanup deletes the owning row
- **THEN** every tool-surface attempt receipt in that row is deleted in the same operation
- **AND** no separate discovery-history record remains

## Source References

- Non-Negotiable Rule 4 (deterministic daemon and ephemeral intelligence)
- RFC 0005 (observability and telemetry)
- RFC 0027 (runtime tool surface discovery and exposure)
