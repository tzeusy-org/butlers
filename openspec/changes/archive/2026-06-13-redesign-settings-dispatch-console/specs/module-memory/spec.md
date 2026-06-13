## ADDED Requirements

### Requirement: Memory Page — Dispatch Fold-In
The existing `/memory` page SHALL fold in the `MemoryExpanded` design from `pr/overview/settings-refactor/settings-expanded.jsx`, with sections for the tier flow, retention policy, compaction log, and memory-inspect search.

#### Scenario: Page structure post-fold-in
- **WHEN** a user navigates to `/memory`
- **THEN** the page renders sections in this order:
  - **§1 Tier flow** — visual hint of `events → mid-term → long-term` with counts and last-compaction timestamps.
  - **§2 Retention policy** — table keyed by `kind` (`event|fact|preference|summary|transcript|embedding`) with editable `ttl_days` and `max_rows` cells; mutations call `PUT /api/memory/retention-policies/{kind}`.
  - **§3 Compaction log** — feed of recent compaction events with `ts`, `kind`, `rows_removed`, `bytes_freed`.
  - **§4 Inspect** — search bar over memory contents (`q`, `kind`) returning paginated hits.

### Requirement: Memory Retention Policies API
The dashboard SHALL expose CRUD over per-kind retention policies.

#### Scenario: Read all policies
- **WHEN** `GET /api/memory/retention-policies` is called
- **THEN** the response is `ApiResponse[RetentionPolicy[]]` with one row per `kind` containing `kind`, `ttl_days: int | null`, `max_rows: bigint | null`, `updated_at`, `updated_by`.

#### Scenario: Update a policy
- **WHEN** `PUT /api/memory/retention-policies/{kind} {ttl_days?, max_rows?}` is called
- **THEN** the row for `kind` is upserted with the supplied fields (null = unlimited)
- **AND** `audit.append("memory.retention", target=kind, note=f"ttl={ttl_days};max={max_rows}")` is invoked.

#### Scenario: Cleanup job consults policy
- **WHEN** the memory cleanup job runs
- **THEN** it loads `memory_retention_policies` and enforces `ttl_days` and `max_rows` per kind
- **AND** each compaction is recorded with `ts, kind, rows_removed, bytes_freed` in the compaction log feed
- **AND** the job runs once daily.

### Requirement: Memory Inspect Search API
The dashboard SHALL expose a search endpoint over memory contents.

#### Scenario: Search hits
- **WHEN** `GET /api/memory/inspect?q=<query>&kind=<kind>&limit=<n>` is called
- **THEN** the response is `PaginatedResponse[MemoryHit]` with `id`, `kind`, `summary`, `created_at`, `validity`, `score` (optional)
- **AND** `q` accepts a plain-text query that is matched against `summary` and other indexed fields per the memory module's existing search semantics.

### Requirement: Compaction Log Feed API
The dashboard SHALL expose a feed of recent compaction events.

#### Scenario: List compaction events
- **WHEN** `GET /api/memory/compaction-log?limit=50` is called
- **THEN** the response is `PaginatedResponse[CompactionEvent]` ordered `ts DESC`, default `limit=50`, max `500`.

## Source References
- PLAN.md §6 Phase 8 — memory fold-in scope.
- `pr/overview/settings-refactor/settings-expanded.jsx :: MemoryExpanded` is the visual reference.
- Reuses `audit.append()` from dashboard-audit-log on policy mutations.
- Existing module-memory requirements (correction-driven retraction, etc.) are unchanged by this delta.
