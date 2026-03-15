# Memory Catalog Schema

## Purpose

The memory catalog schema defines the `shared.memory_catalog` table, a cross-butler discovery index that enables searching memory items across all butlers. The catalog stores summary pointers to canonical memory rows in per-butler schemas, created via a memory module migration with schema-qualified DDL.

## Requirements

### Requirement: shared.memory_catalog table exists with migration

The `shared.memory_catalog` table SHALL be created by a memory module migration using schema-qualified DDL. This table serves as a discovery-only cross-butler search index — it contains summary pointers to canonical memory rows in per-butler schemas. The table MUST match the columns already referenced by runtime code in `_upsert_catalog()` and `memory_catalog_search()`.

#### Scenario: Table schema

- **WHEN** the migration is applied
- **THEN** the `shared.memory_catalog` table MUST exist with columns: `id` (UUID PK, default gen_random_uuid()), `source_schema` (TEXT NOT NULL), `source_table` (TEXT NOT NULL), `source_id` (UUID NOT NULL), `source_butler` (TEXT NOT NULL), `tenant_id` (TEXT NOT NULL, default 'owner'), `entity_id` (UUID nullable), `object_entity_id` (UUID nullable), `summary` (TEXT NOT NULL), `title` (TEXT nullable), `predicate` (TEXT nullable), `scope` (TEXT nullable), `valid_at` (TIMESTAMPTZ nullable), `memory_type` (TEXT NOT NULL), `embedding` (vector(384) nullable), `search_vector` (tsvector nullable), `confidence` (FLOAT nullable), `importance` (FLOAT nullable), `retention_class` (TEXT nullable), `sensitivity` (TEXT nullable), `created_at` (TIMESTAMPTZ, default now()), `updated_at` (TIMESTAMPTZ, default now())
- **AND** a unique constraint MUST exist on `(source_schema, source_table, source_id)`

#### Scenario: Idempotent creation

- **WHEN** the migration is applied and the table already exists
- **THEN** the migration MUST use `IF NOT EXISTS` and succeed without error

#### Scenario: Indexes for search

- **WHEN** the migration is applied
- **THEN** a GIN index MUST exist on `search_vector` for full-text search
- **AND** an ivfflat or HNSW index SHOULD exist on `embedding` for vector search (if row count warrants it, otherwise deferred)
