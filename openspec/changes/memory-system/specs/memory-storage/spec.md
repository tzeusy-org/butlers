## ADDED Requirements

### Requirement: Episodes table stores raw observations from runtime sessions

The system SHALL store episodes in a PostgreSQL table with columns: `id` (UUID PK), `tenant_id` (TEXT), `butler` (TEXT, source butler name), `session_id` (UUID, nullable), `content` (TEXT), `embedding` (vector(384)), `search_vector` (tsvector), `importance` (FLOAT, default 5.0), `reference_count` (INT, default 0), `consolidated` (BOOLEAN compatibility projection), `consolidation_status` (TEXT), `consolidation_attempts` (INT), `last_consolidation_error` (TEXT, nullable), `next_consolidation_retry_at` (TIMESTAMPTZ, nullable), `created_at` (TIMESTAMPTZ), `last_referenced_at` (TIMESTAMPTZ), `expires_at` (TIMESTAMPTZ, default now + 7 days), `metadata` (JSONB).

#### Scenario: Episode created with defaults
- **WHEN** an episode is inserted with only `tenant_id`, `butler`, and `content`
- **THEN** it SHALL have `importance=5.0`, `consolidation_status='pending'`, `consolidated=false`, `consolidation_attempts=0`, `reference_count=0`, and `expires_at` set to 7 days from now

#### Scenario: Episode embedding generated at write time
- **WHEN** an episode is stored
- **THEN** its `embedding` column SHALL be populated with a 384-dimensional vector from the MiniLM-L6 model
- **AND** its `search_vector` column SHALL be populated with a tsvector for full-text search

### Requirement: Facts table stores distilled subject-predicate knowledge

The system SHALL store facts in a PostgreSQL table with columns: `id` (UUID PK), `tenant_id` (TEXT), `subject` (TEXT), `predicate` (TEXT), `content` (TEXT), `embedding` (vector(384)), `search_vector` (tsvector), `importance` (FLOAT, default 5.0), `confidence` (FLOAT, default 1.0), `decay_rate` (FLOAT, default 0.008), `permanence` (TEXT, default 'standard'), `source_butler` (TEXT, nullable), `source_episode_id` (UUID FK → episodes, nullable), `supersedes_id` (UUID FK → facts, nullable), `validity` (TEXT, default 'active'), `scope` (TEXT, default 'global'), `reference_count` (INT, default 0), `created_at` (TIMESTAMPTZ), `last_referenced_at` (TIMESTAMPTZ), `last_confirmed_at` (TIMESTAMPTZ), `tags` (JSONB, default []), `metadata` (JSONB).

#### Scenario: Fact created with permanence category
- **WHEN** a fact is stored with `permanence='permanent'`
- **THEN** its `decay_rate` SHALL be 0.0

#### Scenario: Fact created with default permanence
- **WHEN** a fact is stored without specifying permanence
- **THEN** its `permanence` SHALL be 'standard' and `decay_rate` SHALL be 0.008

### Requirement: Rules table stores learned behavioral patterns

The system SHALL store rules in a PostgreSQL table with columns: `id` (UUID PK), `tenant_id` (TEXT), `content` (TEXT), `embedding` (vector(384)), `search_vector` (tsvector), `scope` (TEXT, default 'global'), `maturity` (TEXT, default 'candidate'), `confidence` (FLOAT, default 0.5), `decay_rate` (FLOAT, default 0.008), `permanence` (TEXT, default 'standard'), `effectiveness_score` (FLOAT, default 0.0), `applied_count` (INT, default 0), `success_count` (INT, default 0), `harmful_count` (INT, default 0), `source_episode_id` (UUID FK → episodes, nullable), `source_butler` (TEXT, nullable), `created_at` (TIMESTAMPTZ), `last_applied_at` (TIMESTAMPTZ, nullable), `last_evaluated_at` (TIMESTAMPTZ, nullable), `last_confirmed_at` (TIMESTAMPTZ, nullable), `tags` (JSONB, default []), `metadata` (JSONB).

#### Scenario: New rule starts as candidate
- **WHEN** a rule is created
- **THEN** its `maturity` SHALL be 'candidate' and `confidence` SHALL be 0.5

### Requirement: Memory links table tracks provenance and relationships

The system SHALL store memory links in a PostgreSQL table with columns `tenant_id`, `source_type`, `source_id`, `target_type`, `target_id`, `relation`, `created_at`, and composite PK `(tenant_id, source_type, source_id, target_type, target_id)`. Valid relation types SHALL be: `derived_from`, `supports`, `contradicts`, `supersedes`, `related_to`.

#### Scenario: Link created between episode and derived fact
- **WHEN** a fact is extracted from an episode during consolidation
- **THEN** a memory link SHALL be created with `source_type='fact'`, `target_type='episode'`, `relation='derived_from'`

#### Scenario: Duplicate link rejected
- **WHEN** a link with the same `(tenant_id, source_type, source_id, target_type, target_id)` already exists
- **THEN** the insert SHALL be rejected by the primary key constraint

### Requirement: Fact supersession on subject-predicate conflict

The system SHALL check for existing active facts with the same `(tenant_id, scope, subject, predicate)` when storing a new fact. If found, the existing fact's `validity` SHALL be set to `superseded`, and the new fact's `supersedes_id` SHALL reference the old fact.

#### Scenario: New fact supersedes existing fact with same subject and predicate
- **WHEN** a fact with `subject='user'` and `predicate='favorite_color'` and `content='blue'` is stored
- **AND** an active fact with `subject='user'` and `predicate='favorite_color'` and `content='green'` already exists
- **THEN** the old fact's `validity` SHALL be 'superseded'
- **AND** the new fact's `supersedes_id` SHALL reference the old fact's id
- **AND** a memory link with `relation='supersedes'` SHALL be created

#### Scenario: No supersession when subject-predicate pair is unique
- **WHEN** a fact is stored with a subject-predicate pair that has no active match
- **THEN** no existing facts SHALL be modified

### Requirement: Active fact uniqueness is DB-enforced

The system SHALL enforce active-fact uniqueness at the database level via a partial unique index on `(tenant_id, scope, subject, predicate)` where `validity='active'`.

#### Scenario: Concurrent duplicate active facts prevented
- **WHEN** two concurrent writes attempt to create active facts with identical `(tenant_id, scope, subject, predicate)`
- **THEN** at most one active row SHALL be committed

### Requirement: Canonical fact validity states

Facts SHALL use canonical validity states: `active`, `fading`, `superseded`, `expired`, `retracted`.

#### Scenario: Legacy forgotten state accepted as compatibility alias
- **WHEN** an input or legacy row uses fact validity `forgotten`
- **THEN** the system SHALL normalize it to canonical `retracted` for writes and retrieval filtering

### Requirement: memory_events append-only audit table

The system SHALL store memory events in a `memory_events` table with columns: `id` (UUID PK), `tenant_id` (TEXT), `event_type` (TEXT), `entity_type` (TEXT), `entity_id` (UUID), `occurred_at` (TIMESTAMPTZ), `actor` (TEXT, nullable), `request_id` (TEXT, nullable), and `payload` (JSONB). Rows SHALL be append-only.

#### Scenario: Forget operation writes audit event
- **WHEN** `memory_forget` is applied to a fact
- **THEN** a `memory_events` row SHALL be appended for the transition
- **AND** existing memory_events rows SHALL remain immutable

### Requirement: Permanence categories map to decay rates

The system SHALL support five permanence categories with fixed decay rates: `permanent` (λ=0.0, never decays), `stable` (λ=0.002, ~346-day half-life), `standard` (λ=0.008, ~87-day half-life), `volatile` (λ=0.03, ~23-day half-life), `ephemeral` (λ=0.1, ~7-day half-life).

#### Scenario: Permanence-to-decay-rate mapping
- **WHEN** a fact or rule is stored with `permanence='stable'`
- **THEN** its `decay_rate` SHALL be set to 0.002

#### Scenario: Invalid permanence rejected
- **WHEN** a fact is stored with a permanence value not in the valid set
- **THEN** the operation SHALL fail with a validation error

### Requirement: Database extensions and indexes

The system SHALL require PostgreSQL extensions `vector` (pgvector) and `uuid-ossp`. The system SHALL create indexes for: episodes by tenant+butler+created_at, episodes by expires_at (partial where not null), unconsolidated episodes, episode embeddings (IVFFlat, 20 lists), episode search vectors (GIN), facts by tenant+scope+validity (partial where active), facts by tenant+subject+predicate, fact embeddings (IVFFlat, 20 lists), fact search vectors (GIN), fact tags (GIN), rules by tenant+scope+maturity, rule embeddings (IVFFlat, 20 lists), rule search vectors (GIN), memory links by tenant+target, and memory_events by tenant+occurred_at.

#### Scenario: Vector similarity search uses index
- **WHEN** a cosine similarity query is executed against the facts embedding column
- **THEN** the query plan SHALL use the IVFFlat index

### Requirement: Alembic migration chain for memory module tables

The system SHALL provide Alembic migrations (memory chain) that create the episodes, facts, rules, memory_links, and memory_events tables, enable required extensions, and add search vectors and indexes. For each butler with memory module enabled, migrations SHALL run programmatically at that butler's startup against its own database.

#### Scenario: Clean database migration
- **WHEN** a butler starts with memory module enabled against an empty `butler_<name>` database
- **THEN** all memory tables SHALL be created via Alembic upgrade to head
- **AND** pgvector and uuid-ossp extensions SHALL be enabled
