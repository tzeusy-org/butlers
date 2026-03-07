## Why

Butlers currently maintain dedicated CRUD tables for each data domain (health measurements, relationship interactions, finance transactions, home entity snapshots). These tables are invisible to the cross-butler memory graph, cannot be semantically searched, and are not entity-anchored. Every butler that wants to store structured event data must design its own schema, write its own aggregation queries, and manage its own lifecycle.

The memory module's bitemporal SPO facts table — introduced in bu-axm — solves this at the infrastructure level. Facts carry `valid_at` for temporal sequences, `entity_id` for entity anchoring, `metadata` JSONB for structured payload, and participate in semantic search and the entity graph. Migrating domain CRUD tables to facts-as-wrappers eliminates the parallel data silos while keeping the existing tool API surfaces intact.

## What Changes

- **Predicate taxonomy** — 40+ new predicates seeded into `predicate_registry` across four domains (health, relationship, finance, home). Each predicate defines `is_temporal`, `expected_subject_type`, and a metadata schema convention.
- **Entity resolution contract** — all domain facts MUST be anchored to a resolved `entity_id`. The owner entity (from `shared.contacts WHERE roles @> '["owner"]'`) anchors all self-data. Contact entities anchor relationship data. Anonymous placeholder entities anchor unresolved actors. The string `"user"` is never acceptable as a bare subject.
- **Wrapper tool pattern** — each migrated MCP tool is rewritten to call `store_fact` / `memory_search` / `memory_recall` internally. The external tool signature and response shape are preserved for backward compatibility.
- **Aggregation query patterns** — tools like `nutrition_summary`, `spending_summary`, and `trend_report` switch from SQL aggregation on dedicated tables to JSONB extraction on `facts`. GIN and partial predicate indexes support these queries.
- **Data backfill** — idempotent migration scripts backfill existing rows from deprecated tables into facts.
- **Table deprecation** — old tables are retained but no longer written to; a future cleanup epic will drop them.

## Capabilities

### New Capabilities

- `crud-to-spo-migration/predicate-taxonomy`: Full predicate registry for health, relationship, finance, and home domains. Each predicate has `is_temporal`, `expected_subject_type`, `expected_object_type`, and metadata schema.
- `crud-to-spo-migration/entity-resolution-contract`: Entity anchoring rules for self-data (owner entity), contact-data (contact entity), and unresolved actors (anonymous placeholder). Prohibits bare string subjects.
- `crud-to-spo-migration/aggregation-patterns`: JSONB extraction query patterns for `nutrition_summary`, `spending_summary`, and `trend_report`. Index recommendations for high-volume temporal predicates.
- `crud-to-spo-migration/wrapper-api-contract`: Backward-compatible response shape mappings from fact fields to legacy response fields for each migrated tool.

### Modified Capabilities

- `module-memory`: Extended predicate registry seed with all Phase 1-4 domain predicates.
- `butler-health`: Measurement, symptom, medication_dose, medication, condition, and research tools rewritten as fact wrappers. SPO migration section added.
- `butler-relationship`: quick_facts, interaction, life_event, note, gift, loan, task, reminder, and activity tools rewritten as fact wrappers. SPO migration section added.
- `butler-finance`: Transaction, account, subscription, and bill tools rewritten as fact wrappers. SPO migration section added.
- `butler-home`: ha_entity_snapshot rewritten as fact wrapper. SPO migration section added.

## Impact

- **DB migrations**: One migration per butler phase seeding new predicates into `predicate_registry`. Plus bu-ddb.6 adds GIN and partial B-tree indexes on `facts.metadata`.
- **Tool rewrites**: Health (6 tables), Relationship (9 tables), Finance (4 tables), Home (1 table) — 20 total tool implementations rewritten as fact wrappers.
- **Backfill scripts**: 4 phase scripts (bu-ddb.7) to migrate existing rows into facts.
- **No breaking changes**: All tool signatures and response shapes are preserved. Callers see no difference.
- **Existing tables retained**: Deprecated but not dropped. Safe to roll back by reverting the tool implementations.
