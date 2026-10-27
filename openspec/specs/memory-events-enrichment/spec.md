# Memory Events Enrichment

## Purpose

The memory events enrichment spec defines additional columns on the `memory_events` table for structured audit completeness, and per-row embedding model version tracking that lets stored embeddings be re-computed when the embedding model changes.

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

### Requirement: Per-row embedding model version tracking

The `episodes`, `facts`, and `rules` tables SHALL each carry an `embedding_model_version` TEXT column recording the embedding model that produced the row's stored embedding, so rows embedded by a superseded model can be found and re-computed. There is no standalone `embedding_versions` tracking table.

#### Scenario: Column exists on every embedded tier
- **WHEN** the memory schema is migrated
- **THEN** `episodes`, `facts`, and `rules` MUST each have an `embedding_model_version` TEXT column
- **AND** no `embedding_versions` table MUST exist

#### Scenario: Stale embeddings are counted against the configured model
- **WHEN** the configured embedding model differs from a row's `embedding_model_version` and the row has a stored embedding
- **THEN** `memory_reembed_pending_count` MUST count that row as pending re-embedding
- **AND** `memory_reembed` MUST re-compute its embedding and set `embedding_model_version` to the configured model

