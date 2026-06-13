# dashboard-api

## MODIFIED Requirements

### Requirement: Memory Endpoints (Cross-Butler Fan-Out)
The memory endpoint surface (`src/butlers/api/routers/memory.py`) SHALL probe
all butler pools for memory tables, gracefully skip pools that lack a memory
schema, and SHALL be extended to back the house-ledger `/memory` redesign with
the following **additive, backward-compatible** read-side deltas and two new
fact lifecycle mutations. Every new field and parameter MUST have a verified
data source; no affordance on the redesigned page may ship without its wire here.

- `GET /api/memory/stats` SHALL additionally return:
  - `last_consolidation_at: str | null` — ISO timestamp of the most recent
    successful consolidation run (sourced from `public.consolidation_runs`).
  - `last_consolidation_facts_produced: int | null` — facts produced by that run.
  - `dead_letter_episodes: int` — count of dead-lettered episodes (default 0).
  These fields are additive; existing `/stats` consumers are not broken.
- `GET /api/memory/episodes` SHALL accept a `status` filter over the
  `consolidation_status` enum `{pending, consolidated, failed, dead_letter}`.
  The legacy `consolidated: bool` parameter SHALL remain accepted; when both are
  supplied, `status` takes precedence.
- `GET /api/memory/facts` SHALL accept a `source_episode_id: str | null` filter
  (facts whose source episode matches) and an `importance_min: float | null`
  filter (facts with importance ≥ the threshold). The response `meta.total`
  reflects the filtered count (the attention rail reads it for the
  "important facts fading" row).
- `GET /api/memory/facts/{id}` SHALL additionally return `superseded_by:
  str | null`, computed by the reverse query `WHERE supersedes_id = $1`
  (the forward `supersedes_id` field already exists).
- `POST /api/memory/facts/{id}/confirm` SHALL be added: body `{}` →
  `ApiResponse<Fact>`, delegating to the storage `confirm_memory()` operation
  (re-inking; updates `last_confirmed_at`). It is the backend for the fact
  detail Confirm commit pill.
- `POST /api/memory/facts/{id}/retract` SHALL be added: body `{}` →
  `ApiResponse<Fact>` with `validity = 'retracted'`, delegating to the storage
  `forget_memory()` operation. It is the backend for the Retract secondary pill.
- `GET /api/memory/inspect` (existing) SHALL back the page's single unified
  search; pagination is **one offset across the union of kinds** for v1 (the
  current handler paginates the union; this is acceptable and is stated here so
  the frontend reads one offset, not per-kind offsets).
- `GET /api/memory/reembed/pending` (existing, per-tier `counts` + `total`)
  SHALL back the embeddings housekeeping surface and the rail's stale-embeddings
  row; no change required.

All new and existing memory endpoints continue to use the cross-butler fan-out
pattern and the `ApiResponse<T>` / `PaginatedResponse<T>` envelopes (RFC 0007);
pools without memory tables are silently skipped.

#### Scenario: Memory fan-out with graceful skip
- **WHEN** a memory endpoint queries across butler pools
- **THEN** pools without memory tables (episodes, facts, rules) are silently skipped
- **AND** results from pools with memory tables are merged and paginated

#### Scenario: Stats carries consolidation fields
- **WHEN** `GET /api/memory/stats` is called
- **THEN** the response includes `last_consolidation_at`,
  `last_consolidation_facts_produced`, and `dead_letter_episodes`
- **AND** a client that ignores those fields observes the pre-change `/stats`
  shape unchanged

#### Scenario: Episodes status filter takes precedence over legacy bool
- **WHEN** `GET /api/memory/episodes?status=dead_letter` is called
- **THEN** only episodes with `consolidation_status = 'dead_letter'` are returned
- **AND** when both `status` and the legacy `consolidated` bool are supplied,
  `status` governs the filter

#### Scenario: Facts source-episode and importance filters
- **WHEN** `GET /api/memory/facts?source_episode_id=<id>` is called
- **THEN** only facts whose source episode equals `<id>` are returned (backing
  the episode detail page's derived-facts list)
- **WHEN** `GET /api/memory/facts?importance_min=8&validity=fading` is called
- **THEN** `meta.total` reflects the count of high-importance fading facts
  (backing the rail's "important facts fading" row)

#### Scenario: Fact detail carries superseded-by
- **WHEN** `GET /api/memory/facts/{id}` is called and another fact has
  `supersedes_id` equal to `{id}`
- **THEN** the response includes `superseded_by` set to that other fact's id
- **WHEN** no fact supersedes it
- **THEN** `superseded_by` is `null`

#### Scenario: Confirm re-inks a fact
- **WHEN** `POST /api/memory/facts/{id}/confirm` is called
- **THEN** the response is `ApiResponse<Fact>` with `last_confirmed_at` updated
- **AND** the operation delegates to the storage `confirm_memory()` path

#### Scenario: Retract sets validity to retracted
- **WHEN** `POST /api/memory/facts/{id}/retract` is called
- **THEN** the response is `ApiResponse<Fact>` with `validity = 'retracted'`
- **AND** the operation delegates to the storage `forget_memory()` path

#### Scenario: Inspect paginates the union with one offset
- **WHEN** `GET /api/memory/inspect?q=<term>&offset=<n>` is called
- **THEN** results across kinds are paginated as a single union with one offset
  (v1 semantics), not per-kind offsets

## ADDED Requirements

### Requirement: Consolidation Run Audit Table (additive-only)
The data plane SHALL gain one new cross-butler table,
`public.consolidation_runs`, written once per successful consolidation run.
This table is **additive-only**: it introduces no change to any existing memory
table (episodes, facts, rules), preserving the redesign's no-storage-migration
intent. It exists because `last_consolidation_facts_produced` (surfaced by
`/api/memory/stats`, the overture, and the rail) is otherwise underivable from
existing tables.

The table SHALL carry at least: `id`, `butler`, `consolidated_at`,
`episodes_processed`, `facts_produced`, `facts_updated`, `rules_created`,
`confirmations_made`, and `errors` — the counts the consolidation pipeline
already computes and returns on each run. The consolidation pipeline SHALL
insert one row on each successful run (write-on-completion). Cross-butler
aggregation for `/api/memory/stats` SHALL follow the established memory fan-out
pattern and MUST NOT breach per-butler schema isolation.

#### Scenario: One row per successful consolidation run
- **WHEN** a butler's consolidation run completes successfully
- **THEN** exactly one row is inserted into `public.consolidation_runs` with the
  run's counts
- **AND** no existing memory table schema is altered by this change

#### Scenario: Stats derives last-write-up from the audit table
- **WHEN** `GET /api/memory/stats` computes `last_consolidation_at` and
  `last_consolidation_facts_produced`
- **THEN** the values are read from the most recent `public.consolidation_runs`
  row (by `consolidated_at`), aggregated across butler pools

## Source References

- RFC 0007 (Dashboard API response envelope)
- Doctrine: read-mostly observability surface, not a uniform information feed —
  `about/heart-and-soul/design-language.md:44-62`
- Binding backend contract delta — `docs/redesigns/2026-06-12-memory-brief.md` §3
