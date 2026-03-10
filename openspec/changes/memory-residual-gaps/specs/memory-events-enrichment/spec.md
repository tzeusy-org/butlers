## ADDED Requirements

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

---

### Requirement: embedding_versions tracking table

The memory module SHALL maintain an `embedding_versions` table to track which embedding model is active. This enables future model migrations where all embeddings need re-computation.

#### Scenario: Table schema

- **WHEN** the migration is applied
- **THEN** the `embedding_versions` table MUST exist with columns: `id` (UUID PK, default gen_random_uuid()), `model_name` (TEXT NOT NULL), `dimensions` (INTEGER NOT NULL), `is_active` (BOOLEAN NOT NULL, default true), `created_at` (TIMESTAMPTZ, default now())

#### Scenario: Initial seed row

- **WHEN** the migration is applied
- **THEN** one row MUST be seeded: `model_name='all-MiniLM-L6-v2'`, `dimensions=384`, `is_active=true`
