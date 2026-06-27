# Memory Events Enrichment

## Purpose

The memory events enrichment spec defines additional columns on the `memory_events` table for structured audit completeness, and per-entity embedding model version tracking to support future embedding model migrations.

Note (reality sync, code authoritative): the standalone `embedding_versions` tracking table was created by migration `mem_001` but later removed by migration `mem_005_drop_embedding_versions` after it was verified to have zero runtime references. Model-version tracking now lives on an `embedding_model_version` TEXT column added to the `episodes`, `facts`, and `rules` tables by migration `mem_004_embedding_model_version`. The `embedding_versions` requirement below is retained for historical context and is superseded by the per-entity column.

## Requirements

### Requirement: Enriched memory_events columns for audit completeness

The `memory_events` table SHALL have additional columns to support structured audit queries: which request triggered the event, what type and ID of memory item was affected, and which butler performed the action. These columns are nullable to preserve backward compatibility with existing rows.

#### Scenario: Enrichment columns added

- **WHEN** the enrichment migration is applied
- **THEN** the `memory_events` table MUST have additional columns: `request_id` (TEXT nullable), `memory_type` (TEXT nullable, one of 'episode', 'fact', 'rule'), `memory_id` (UUID nullable), `actor_butler` (TEXT nullable)
- **AND** existing rows MUST retain NULL values for the new columns

#### Scenario: Consolidation events populate enrichment columns

- **WHEN** a consolidation success or failure event is emitted
- **THEN** the INSERT MUST populate `actor_butler` with the consolidation butler name
- **AND** the INSERT MUST populate `tenant_id` from the episode group
- **AND** the INSERT MUST populate `memory_type='episode'` and `memory_id` with the affected episode
- **AND** the `request_id` column is left NULL by consolidation events (consolidation is scheduler-driven and carries no inbound request id)

---

### Requirement: embedding_versions tracking table (SUPERSEDED)

Status: superseded by migration `mem_005_drop_embedding_versions`. The table described below was dropped after it was found to have zero runtime references. Active model-version tracking is the `embedding_model_version` TEXT column on `episodes`, `facts`, and `rules` (migration `mem_004`). This requirement is retained for historical context and MUST NOT be treated as a build target.

The memory module SHALL maintain an `embedding_versions` table to track which embedding model is active. This enables future model migrations where all embeddings need re-computation.

#### Scenario: Table schema

- **WHEN** the migration is applied
- **THEN** the `embedding_versions` table MUST exist with columns: `id` (UUID PK, default gen_random_uuid()), `model_name` (TEXT NOT NULL), `dimensions` (INTEGER NOT NULL), `is_active` (BOOLEAN NOT NULL, default true), `created_at` (TIMESTAMPTZ, default now())

#### Scenario: Initial seed row

- **WHEN** the migration is applied
- **THEN** one row MUST be seeded: `model_name='all-MiniLM-L6-v2'`, `dimensions=384`, `is_active=true`
