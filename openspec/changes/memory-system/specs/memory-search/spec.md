## ADDED Requirements

### Requirement: Tenant-bounded retrieval by default

All retrieval operations SHALL be tenant-bounded by default using authenticated request context. Unscoped retrieval may include all scopes within the same tenant. Cross-tenant retrieval SHALL require explicit elevated authorization.

#### Scenario: Unscoped retrieval remains tenant-bounded
- **WHEN** memory_recall is called without a scope parameter
- **THEN** results SHALL include all scopes only within the caller tenant
- **AND** results from other tenants SHALL NOT be returned

### Requirement: Semantic search via pgvector cosine similarity

The system SHALL support semantic search by embedding the query text using MiniLM-L6 and performing cosine similarity search against the `embedding` column using pgvector's `<=>` operator.

#### Scenario: Semantic search returns conceptually similar results
- **WHEN** a semantic search is executed with query "feeling unwell"
- **AND** a fact exists with content "User experiences nausea after dairy"
- **THEN** the fact SHALL appear in results ranked by cosine similarity

#### Scenario: Semantic search respects limit parameter
- **WHEN** a semantic search is executed with `limit=5`
- **THEN** at most 5 results SHALL be returned

### Requirement: Keyword search via PostgreSQL full-text search

The system SHALL support keyword search using PostgreSQL's `tsvector`/`tsquery` full-text search against the `search_vector` column.

#### Scenario: Keyword search matches exact terms
- **WHEN** a keyword search is executed with query "Dr. Smith"
- **AND** a fact exists with content containing "Dr. Smith"
- **THEN** the fact SHALL appear in results

#### Scenario: Keyword search handles stemming
- **WHEN** a keyword search is executed with query "running"
- **AND** a fact exists with content containing "runs"
- **THEN** the fact SHALL appear in results (via tsvector stemming)

### Requirement: Hybrid search via Reciprocal Rank Fusion

The system SHALL support hybrid search that executes both semantic and keyword searches, then fuses results using Reciprocal Rank Fusion: `rrf_score = 1/(k + semantic_rank) + 1/(k + keyword_rank)` where `k=60`. Results appearing in only one list SHALL use a default rank of `limit + 1` for the missing list.

#### Scenario: Hybrid search combines both signals
- **WHEN** a hybrid search is executed with query "lactose intolerant"
- **AND** a fact matches well semantically (rank 1) but not by keyword (rank 15)
- **AND** another fact matches well by keyword (rank 1) but not semantically (rank 10)
- **THEN** both facts SHALL appear in results with RRF scores reflecting their combined ranks

#### Scenario: Hybrid is the default search mode
- **WHEN** a search is executed without specifying a mode
- **THEN** hybrid search SHALL be used

### Requirement: Composite scoring for memory recall

The `memory_recall` tool SHALL score results using a composite of four signals: `final_score = w_relevance × relevance + w_importance × importance + w_recency × recency + w_confidence × effective_confidence`. Default weights SHALL be: relevance=0.4, importance=0.3, recency=0.2, confidence=0.1. Weights SHALL be configurable per butler via `butler.toml`.

#### Scenario: High-importance recent fact ranks above low-importance old fact
- **WHEN** memory_recall is called with a topic
- **AND** a fact with importance=9.0 was referenced 1 day ago
- **AND** a fact with importance=2.0 was referenced 30 days ago
- **AND** both have equal relevance and confidence
- **THEN** the high-importance recent fact SHALL rank higher

#### Scenario: Custom weights from butler config
- **WHEN** a butler's `butler.toml` specifies `score_weights = { relevance = 0.6, importance = 0.1, recency = 0.2, confidence = 0.1 }`
- **THEN** memory_recall for that butler SHALL use those weights

### Requirement: Scope filtering on retrieval

All retrieval operations SHALL support scope filtering. When a scope is specified, facts and rules SHALL be filtered to `scope IN ('global', <specified_scope>)` within the active tenant. Episodes SHALL be filtered by `butler = <specified_scope>` within the active tenant. When no scope is specified, all scopes in the active tenant SHALL be searched.

#### Scenario: Butler-scoped recall returns global and butler-specific memories
- **WHEN** memory_recall is called with `scope='health'`
- **THEN** results SHALL include facts with `scope='global'` and `scope='health'`
- **AND** results SHALL NOT include facts with `scope='general'`

#### Scenario: Unscoped recall returns all memories
- **WHEN** memory_recall is called without a scope parameter
- **THEN** results SHALL include facts from all scopes in the active tenant

### Requirement: Effective confidence filtering

Retrieval operations SHALL compute effective confidence as `confidence × exp(-decay_rate × days_since_last_confirmed)` and exclude results below the retrieval threshold (default 0.2). A `min_confidence` parameter SHALL allow overriding this threshold.

#### Scenario: Fading memory excluded from default recall
- **WHEN** memory_recall is called without min_confidence
- **AND** a fact has effective_confidence of 0.15 (below 0.2 threshold)
- **THEN** the fact SHALL NOT appear in results

#### Scenario: Fading memory included with explicit threshold
- **WHEN** memory_recall is called with `min_confidence=0`
- **AND** a fact has effective_confidence of 0.15
- **THEN** the fact SHALL appear in results

### Requirement: Reference count and timestamp bumping on retrieval

When a memory is returned by `memory_search`, `memory_recall`, or `memory_get`, the system SHALL increment `reference_count` by 1 and update `last_referenced_at` to the current timestamp.

#### Scenario: Recall bumps reference metadata
- **WHEN** a fact with `reference_count=3` is returned by memory_recall
- **THEN** its `reference_count` SHALL be 4
- **AND** its `last_referenced_at` SHALL be updated to the current time

### Requirement: Deterministic ordering under equal score

When multiple candidate memories have equal final score, retrieval order SHALL be deterministic using tie-breakers `created_at DESC`, then `id ASC`.

#### Scenario: Equal-score ties are stable
- **WHEN** two facts have equal composite score for the same query
- **THEN** their output order SHALL follow `created_at DESC`, then `id ASC`
