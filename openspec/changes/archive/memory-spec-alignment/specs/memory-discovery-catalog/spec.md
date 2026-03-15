## ADDED Requirements

### Requirement: Shared memory discovery catalog table

A `shared.memory_catalog` table SHALL provide a cross-butler searchable index of memory items. The catalog stores searchable summaries with provenance pointers back to the owning butler schema — it is a discovery index, NOT a canonical store. Canonical memory data remains in each butler's local schema.

#### Scenario: Memory catalog table schema

- **WHEN** the shared discovery catalog migration runs
- **THEN** a `shared.memory_catalog` table MUST be created with columns:
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
  - `entity_id` (UUID nullable, FK to shared.entities ON DELETE SET NULL)
  - `object_entity_id` (UUID nullable, FK to shared.entities ON DELETE SET NULL)
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

### Requirement: Butler roles have narrow grants on shared.memory_catalog

Butler database roles SHALL have `INSERT` and `UPDATE` grants on `shared.memory_catalog` only (not broader shared schema grants). This enables direct catalog writes without routing through Switchboard.

#### Scenario: Butler can insert catalog entries

- **WHEN** a butler stores a new fact or rule
- **THEN** the butler's database role MUST be able to INSERT a corresponding row into `shared.memory_catalog`

#### Scenario: Butler can update catalog entries

- **WHEN** a butler's fact is superseded, expired, or retracted
- **THEN** the butler's database role MUST be able to UPDATE the corresponding `shared.memory_catalog` row

#### Scenario: Butler cannot delete other butlers' catalog entries

- **WHEN** a butler attempts to DELETE a catalog entry where `source_butler` does not match its own name
- **THEN** the operation SHOULD be prevented by application-level checks (not database-level row-security, to keep the initial implementation simple)

#### Scenario: All existing butler roles receive grants

- **WHEN** the catalog migration runs
- **THEN** all butler roles listed in the migration's role array MUST receive `GRANT INSERT, UPDATE ON shared.memory_catalog`
- **AND** the migration MUST follow the same pattern as `core_014` for butler role enumeration

---

### Requirement: Catalog write-behind on memory store

When a fact or rule is stored or updated, the storage layer SHALL write a corresponding entry to `shared.memory_catalog`. Catalog writes are best-effort — a failure MUST NOT prevent the canonical memory from being stored.

#### Scenario: Fact stored triggers catalog upsert

- **WHEN** `store_fact` successfully inserts a new fact
- **THEN** a catalog entry MUST be upserted (INSERT ... ON CONFLICT (source_schema, source_table, source_id) DO UPDATE) into `shared.memory_catalog` with the fact's searchable fields
- **AND** the `title` MUST be formatted as `"{subject} {predicate}"`
- **AND** the `search_text` MUST be the same searchable text used for the fact's tsvector

#### Scenario: Rule stored triggers catalog upsert

- **WHEN** `store_rule` successfully inserts a new rule
- **THEN** a catalog entry MUST be upserted into `shared.memory_catalog`
- **AND** the `title` MUST be the first 100 characters of the rule's content
- **AND** the `search_text` MUST be the rule's content

#### Scenario: Fact supersession updates catalog

- **WHEN** a fact is superseded (validity changed to 'superseded')
- **THEN** the catalog entry for the superseded fact MUST be DELETED or marked as stale (confidence set to 0)
- **AND** the new superseding fact MUST have its own catalog entry

#### Scenario: Catalog write failure does not block canonical store

- **WHEN** the catalog INSERT/UPDATE fails (e.g., shared schema unavailable, permission error)
- **THEN** the error MUST be logged as a warning
- **AND** the canonical fact/rule in the butler's local schema MUST still be committed successfully
- **AND** the catalog entry can be reconciled later by a background repair job

---

### Requirement: Cross-butler search via catalog

A search function SHALL query `shared.memory_catalog` to discover memory items across all butlers. Results include provenance information for full retrieval from the owning butler's schema.

#### Scenario: Cross-butler semantic search

- **WHEN** a cross-butler search is performed with a query embedding
- **THEN** the search MUST query `shared.memory_catalog` using cosine similarity on the `embedding` column
- **AND** results MUST be filtered by `tenant_id`
- **AND** each result MUST include `source_schema`, `source_table`, `source_id`, and `source_butler` for provenance

#### Scenario: Cross-butler keyword search

- **WHEN** a cross-butler search is performed with a text query
- **THEN** the search MUST query `shared.memory_catalog` using `search_vector @@ plainto_tsquery`
- **AND** results MUST be filtered by `tenant_id`

#### Scenario: Cross-butler hybrid search

- **WHEN** a cross-butler search is performed in hybrid mode
- **THEN** both semantic and keyword search MUST be executed against `shared.memory_catalog`
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

Episodes SHALL NOT be written to `shared.memory_catalog`. Only facts and rules — which represent consolidated, durable knowledge — are discoverable cross-butler.

#### Scenario: store_episode does not write to catalog

- **WHEN** `store_episode` is called
- **THEN** no row MUST be written to `shared.memory_catalog`

#### Scenario: Catalog memory_type values

- **WHEN** querying `shared.memory_catalog`
- **THEN** the `memory_type` column MUST contain only `'fact'` or `'rule'` values
- **AND** `'episode'` MUST NOT appear
