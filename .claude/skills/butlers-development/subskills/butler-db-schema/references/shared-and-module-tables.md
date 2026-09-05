# Cross-Butler, Module, and Butler-Specific Tables

Load when working beyond the core tables: shared `public` tables, module
migration chains (memory, approvals, contacts), or a specific butler's domain
tables — plus the schema-design principles for new domain tables.

## Cross-Butler Tables (in `public`)

Tables in `public` are readable by all butlers but writable only by core
migrations. Created by `core_005` and later core migrations.

### Calendar Projection Tables

The calendar module projects Google Calendar data into these shared tables:

| Table | Purpose |
|---|---|
| `calendar_sources` | Calendar provider sources with lane (user/butler) |
| `calendar_events` | Base events with recurrence rules |
| `calendar_event_instances` | Expanded recurring event occurrences |
| `calendar_sync_cursors` | Incremental sync state per source |
| `calendar_action_log` | Idempotent mutation audit trail |

Key design patterns in calendar tables:
- **GiST indexes on time ranges:** `USING GIST (tstzrange(starts_at, ends_at, '[)'))` for efficient overlap queries
- **Idempotency keys:** `idempotency_key TEXT NOT NULL UNIQUE` on action log
- **Lane-based partitioning:** `lane IN ('user', 'butler')` separates read-only user calendars from writable butler calendars

## Module Tables

Modules create tables in the butler's own schema via module-specific migration
chains.

### Memory Module (`mem_001`)

Uses pgvector for semantic search. Four tables:

| Table | Purpose |
|---|---|
| `episodes` | Session memory snapshots with embeddings |
| `facts` | Persistent structured knowledge (subject/predicate/content) |
| `rules` | Learned behavioral rules with effectiveness tracking |
| `memory_links` | Cross-type relationships between memory entities |

Key design patterns:
- **`vector(384)` embeddings** with IVFFLAT indexes for cosine similarity search
- **`tsvector` columns** with GIN indexes for full-text search
- **Decay model:** `decay_rate`, `reference_count`, `last_referenced_at` for memory aging
- **Permanence levels:** `permanent`, `stable`, `standard`, `volatile`
- **Consolidation pipeline:** `consolidation_status` ('pending', 'done') tracks episode processing

```sql
-- Example: facts table (key columns)
CREATE TABLE facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding vector(384),
    search_vector tsvector,
    importance FLOAT NOT NULL DEFAULT 5.0,
    confidence FLOAT NOT NULL DEFAULT 1.0,
    decay_rate FLOAT NOT NULL DEFAULT 0.008,
    permanence TEXT NOT NULL DEFAULT 'standard',
    validity TEXT NOT NULL DEFAULT 'active',
    scope TEXT NOT NULL DEFAULT 'global',
    reference_count INTEGER NOT NULL DEFAULT 0,
    tags JSONB DEFAULT '[]'::jsonb,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_referenced_at TIMESTAMPTZ
);

CREATE INDEX idx_facts_subject_predicate ON facts (subject, predicate);
CREATE INDEX idx_facts_scope_validity ON facts (scope, validity) WHERE validity = 'active';
CREATE INDEX idx_facts_search ON facts USING gin(search_vector);
CREATE INDEX idx_facts_tags ON facts USING gin(tags);
CREATE INDEX idx_facts_embedding ON facts USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20);
```

### Approvals Module

Two tables for tool-call approval gating:

| Table | Purpose |
|---|---|
| `approval_rules` | Pre-approval rules (tool + arg constraints) |
| `pending_actions` | Actions awaiting approval/execution |

### Contacts Module

Three tables for external contact sync:

| Table | Purpose |
|---|---|
| `contacts_source_accounts` | Registered sync provider accounts |
| `contacts_sync_state` | Per-account incremental sync cursor |
| `contacts_source_links` | External-to-local contact provenance |

## Butler-Specific Tables

Each butler defines its own domain tables via migrations in
`roster/<name>/migrations/`. Examples:

**Health butler** — measurements, medications, medication_doses, conditions, meals, symptoms, research

**Relationship butler** — contacts, relationships, important_dates, notes, interactions, reminders, gifts, loans, groups, group_members, labels, contact_labels, quick_facts, activity_feed

**General butler** — collections, entities (freeform JSONB data)

**Messenger butler** — delivery_requests, delivery_attempts, delivery_receipts, delivery_dead_letter

**Switchboard butler** — butler_registry, routing_log, extraction_queue, extraction_log, message_inbox, and more

## Schema Design Principles

1. **JSONB for flexible/evolving fields.** Use typed columns for things you query on (foreign keys, timestamps, amounts). Use JSONB for metadata, details, and fields that vary across records.
2. **Always include `created_at`.** Every table gets `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`.
3. **Include `updated_at` on mutable tables.** If rows get updated, track when.
4. **Use `UUID` primary keys** for domain tables. Use `BIGINT GENERATED ALWAYS AS IDENTITY` only for high-volume append-only tables.
5. **Use `TEXT` over `VARCHAR`.** PostgreSQL treats them identically. `TEXT` is simpler.
6. **Prefer JSONB arrays for tags** (`JSONB DEFAULT '[]'::jsonb`) over `TEXT[]` — established pattern across the codebase.
7. **Cascade deletes where ownership is clear.** `ON DELETE CASCADE` for child records that have no meaning without their parent.
8. **Use CHECK constraints for enums.** `CHECK (status IN ('pending', 'active', 'done'))` instead of a separate lookup table.
