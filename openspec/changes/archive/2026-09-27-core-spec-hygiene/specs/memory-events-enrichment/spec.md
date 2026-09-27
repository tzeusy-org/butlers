## RENAMED Requirements

- FROM: `### Requirement: embedding_versions tracking table (SUPERSEDED)`
- TO: `### Requirement: Per-row embedding model version tracking`

## MODIFIED Requirements

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
