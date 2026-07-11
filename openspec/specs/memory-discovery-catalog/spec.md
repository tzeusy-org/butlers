# Memory Discovery Catalog

## Purpose

The memory discovery catalog defines the cross-butler searchable index of memory items via `public.memory_catalog`, including the table schema, access grants, write-behind behavior on memory store operations, cross-butler search capabilities, and the exclusion of episodes from the catalog.

## Requirements

### Requirement: Public memory discovery catalog table

A `public.memory_catalog` table SHALL provide a cross-butler searchable index of memory items. The catalog stores searchable summaries with provenance pointers back to the owning butler schema — it is a discovery index, NOT a canonical store. Canonical memory data remains in each butler's local schema.

#### Scenario: Memory catalog table schema

- **WHEN** the shared discovery catalog migration runs
- **THEN** a `public.memory_catalog` table MUST be created with columns:
  - `id` (UUID PK DEFAULT gen_random_uuid())
  - `tenant_id` (TEXT NOT NULL)
  - `source_schema` (TEXT NOT NULL) — the owning butler's schema name
  - `source_table` (TEXT NOT NULL) — 'facts' or 'rules'
  - `source_id` (UUID NOT NULL) — the ID in the source table
  - `source_butler` (TEXT NOT NULL) — butler name that owns this memory
  - `memory_type` (TEXT NOT NULL) — 'fact' or 'rule'
  - `title` (TEXT nullable) — human-readable summary (e.g., subject + predicate for facts)
  - `search_text` (TEXT NOT NULL) — text for full-text search indexing
  - `embedding` (vector(384)) — semantic search vector
  - `search_vector` (tsvector) — PostgreSQL full-text search vector
  - `entity_id` (UUID nullable, FK to public.entities ON DELETE SET NULL)
  - `object_entity_id` (UUID nullable, FK to public.entities ON DELETE SET NULL)
  - `predicate` (TEXT nullable)
  - `scope` (TEXT nullable)
  - `valid_at` (TIMESTAMPTZ nullable)
  - `invalid_at` (TIMESTAMPTZ nullable)
  - `confidence` (DOUBLE PRECISION nullable)
  - `importance` (DOUBLE PRECISION nullable)
  - `retention_class` (TEXT nullable)
  - `sensitivity` (TEXT nullable)
  - `created_at` (TIMESTAMPTZ NOT NULL DEFAULT now())
  - `updated_at` (TIMESTAMPTZ NOT NULL DEFAULT now())
- **AND** a UNIQUE constraint MUST exist on `(source_schema, source_table, source_id)` to prevent duplicate catalog entries
- **AND** indexes MUST exist for: semantic search (IVFFlat on embedding), full-text search (GIN on search_vector), tenant + entity lookups, and tenant + scope/predicate lookups

#### Scenario: Catalog is discovery-only, not canonical

- **WHEN** a cross-butler search is performed via the catalog
- **THEN** the catalog MUST return provenance pointers (`source_schema`, `source_table`, `source_id`)
- **AND** full memory retrieval (with all columns) MUST be performed by querying the owning butler's schema using the provenance pointer
- **AND** the catalog MUST NOT be treated as a replacement for butler-local memory tables

---

### Requirement: Butler roles have narrow grants on public.memory_catalog

Butler database roles SHALL have `INSERT` and `UPDATE` grants on `public.memory_catalog` only (not broader public schema grants). This enables direct catalog writes without routing through Switchboard.

#### Scenario: Butler can insert catalog entries

- **WHEN** a butler stores a new fact or rule
- **THEN** the butler's database role MUST be able to INSERT a corresponding row into `public.memory_catalog`

#### Scenario: Butler can update catalog entries

- **WHEN** a butler's fact is superseded, expired, or retracted
- **THEN** the butler's database role MUST be able to UPDATE the corresponding `public.memory_catalog` row

#### Scenario: Butler cannot delete other butlers' catalog entries

- **WHEN** a butler attempts to DELETE a catalog entry where `source_butler` does not match its own name
- **THEN** the operation SHOULD be prevented by application-level checks (not database-level row-security, to keep the initial implementation simple)

#### Scenario: All existing butler roles receive grants

- **WHEN** the catalog migration runs
- **THEN** all butler roles listed in the migration's role array MUST receive `GRANT INSERT, UPDATE ON public.memory_catalog`
- **AND** the migration MUST follow the same pattern as `core_014` for butler role enumeration

---

### Requirement: Catalog write-behind on memory store

When a fact or rule is stored or updated, the storage layer SHALL write a corresponding entry to `public.memory_catalog`. Catalog writes are best-effort — a failure MUST NOT prevent the canonical memory from being stored.

#### Scenario: Fact stored triggers catalog upsert

- **WHEN** `store_fact` successfully inserts a new fact
- **THEN** a catalog entry MUST be upserted (INSERT ... ON CONFLICT (source_schema, source_table, source_id) DO UPDATE) into `public.memory_catalog` with the fact's searchable fields
- **AND** the `title` MUST be formatted as `"{subject} {predicate}"`
- **AND** the `search_text` MUST be the same searchable text used for the fact's tsvector

#### Scenario: Rule stored triggers catalog upsert

- **WHEN** `store_rule` successfully inserts a new rule
- **THEN** a catalog entry MUST be upserted into `public.memory_catalog`
- **AND** the `title` MUST be the first 100 characters of the rule's content
- **AND** the `search_text` MUST be the rule's content

#### Scenario: Fact supersession updates catalog

- **WHEN** a fact is superseded (validity changed to 'superseded')
- **THEN** the catalog entry for the superseded fact MUST be DELETED or marked as stale (confidence set to 0)
- **AND** the new superseding fact MUST have its own catalog entry

#### Scenario: Catalog write failure does not block canonical store

- **WHEN** the catalog INSERT/UPDATE fails (e.g., public schema unavailable, permission error)
- **THEN** the error MUST be logged as a warning
- **AND** the canonical fact/rule in the butler's local schema MUST still be committed successfully
- **AND** the catalog entry can be reconciled later by a background repair job

---

### Requirement: Cross-butler search via catalog

A search function SHALL query `public.memory_catalog` to discover memory items across all butlers. Results include provenance information for full retrieval from the owning butler's schema.

#### Scenario: Cross-butler semantic search

- **WHEN** a cross-butler search is performed with a query embedding
- **THEN** the search MUST query `public.memory_catalog` using cosine similarity on the `embedding` column
- **AND** results MUST be filtered by `tenant_id`
- **AND** each result MUST include `source_schema`, `source_table`, `source_id`, and `source_butler` for provenance

#### Scenario: Cross-butler keyword search

- **WHEN** a cross-butler search is performed with a text query
- **THEN** the search MUST query `public.memory_catalog` using `search_vector @@ plainto_tsquery`
- **AND** results MUST be filtered by `tenant_id`

#### Scenario: Cross-butler hybrid search

- **WHEN** a cross-butler search is performed in hybrid mode
- **THEN** both semantic and keyword search MUST be executed against `public.memory_catalog`
- **AND** results MUST be fused using Reciprocal Rank Fusion (same algorithm as butler-local search)

#### Scenario: Sensitivity filtering

- **WHEN** a cross-butler search is performed
- **THEN** results with `sensitivity` values that the caller is not authorized to view MUST be excluded
- **AND** the default behavior MUST include only `sensitivity = 'normal'` results unless the caller explicitly requests higher sensitivity levels

#### Scenario: Scope and predicate filtering

- **WHEN** a cross-butler search includes `scope` or `predicate` filters
- **THEN** the catalog query MUST apply these as additional WHERE conditions
- **AND** `source_butler` MAY also be used as a filter to narrow results to specific butlers

---

### Requirement: Episodes are NOT indexed in the discovery catalog

Episodes SHALL NOT be written to `public.memory_catalog`. Only facts and rules — which represent consolidated, durable knowledge — are discoverable cross-butler.

#### Scenario: store_episode does not write to catalog

- **WHEN** `store_episode` is called
- **THEN** no row MUST be written to `public.memory_catalog`

#### Scenario: Catalog memory_type values

- **WHEN** querying `public.memory_catalog`
- **THEN** the `memory_type` column MUST contain only `'fact'` or `'rule'` values
- **AND** `'episode'` MUST NOT appear

### Requirement: Catalog write-behind defaults to enabled

The memory module's `enable_shared_catalog` feature flag SHALL default to
`True`. A butler's `butler.toml` `[modules.memory]` block MAY still set
`enable_shared_catalog = false` to opt that specific butler out of write-behind
(e.g. a deployment with no `public.memory_catalog` table, or a butler whose
facts must never surface cross-butler); toml is an opt-out, not a required
opt-in. When `catalog_source_schema` is not explicitly configured, the
`source_schema` written to catalog rows SHALL be inferred from the module's
database handle (falling back to the butler's own name when the handle
carries no schema), so the default-on behavior produces correctly attributed
catalog rows without requiring per-butler toml configuration.

#### Scenario: A memory-enabled butler catalogs facts/rules with no toml configuration

- **WHEN** a butler has `[modules.memory]` enabled in its `butler.toml` and
  does not set `enable_shared_catalog` or `catalog_source_schema`
- **THEN** `store_fact()` and `store_rule()` calls made through that butler's
  MCP tools MUST write a corresponding `public.memory_catalog` row
- **AND** the row's `source_schema` MUST match the butler's own schema (or its
  butler name, if the database handle carries no schema)

#### Scenario: A butler explicitly opts out via toml

- **WHEN** a butler's `butler.toml` sets `enable_shared_catalog = false`
  under `[modules.memory]`
- **THEN** `store_fact()` and `store_rule()` calls made through that butler's
  MCP tools MUST NOT write to `public.memory_catalog`

---

### Requirement: Backfill drains pre-existing facts/rules into the catalog

A bounded, idempotent backfill mechanism SHALL exist that catalogs facts and
rules written before write-behind was enabled (or before a specific butler
opted in). Each invocation SHALL process at most a fixed batch of rows per
table so that no single run holds a long-lived transaction across the full
backlog, and SHALL be safe to invoke repeatedly — already-cataloged rows
(matched via the existing `UNIQUE(source_schema, source_table, source_id)`
key) MUST be skipped rather than re-inserted or duplicated. The backfill
SHALL run as a module-default scheduled job so the backlog drains without
requiring a copy-pasted `butler.toml` schedule block per butler.

#### Scenario: Backfill catalogs active facts and rules not yet cataloged

- **WHEN** the backfill runs against a butler's schema containing active
  facts and non-forgotten rules with no corresponding `public.memory_catalog`
  row
- **THEN** each such fact/rule MUST receive a `public.memory_catalog` row
  keyed by `(source_schema, source_table, source_id)`
- **AND** the row MUST be discoverable via cross-butler catalog search

#### Scenario: Backfill excludes retracted facts and forgotten rules

- **WHEN** the backfill runs against a butler's schema containing a retracted
  fact (`validity != 'active'`) or a forgotten rule (`metadata.forgotten =
  true`)
- **THEN** neither MUST receive a `public.memory_catalog` row

#### Scenario: Backfill is idempotent across repeated runs

- **WHEN** the backfill is invoked a second time after a prior run already
  cataloged a fact or rule
- **THEN** the second run MUST NOT re-process, duplicate, or error on that
  already-cataloged row

#### Scenario: Backfill runs as a module default, not per-butler toml

- **WHEN** a butler with the memory module enabled boots with no
  `[[butler.schedule]]` block referencing the backfill job
- **THEN** the backfill job MUST still be scheduled and dispatchable for that
  butler

