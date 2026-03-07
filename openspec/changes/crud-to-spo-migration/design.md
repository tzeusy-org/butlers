## Context

The memory module's `facts` table (bu-axm) supports `entity_id`, `valid_at`, `metadata` JSONB, and a `predicate_registry`. Domain butlers (health, relationship, finance, home) maintain separate CRUD tables that duplicate this capability with no cross-butler visibility or semantic search. This design specifies the migration pattern, predicate taxonomy, entity resolution contract, aggregation query patterns, and backward-compatible wrapper API for each phase.

## Goals / Non-Goals

**Goals:**
- Define a complete predicate taxonomy for all Phase 1-4 tables
- Establish an unambiguous entity resolution contract (no bare string subjects)
- Standardize metadata schemas per predicate family
- Document aggregation query patterns with index recommendations
- Guarantee backward-compatible tool response shapes throughout migration

**Non-Goals:**
- Dropping deprecated tables (future cleanup epic)
- Changing memory consolidation engine behavior for temporal facts
- Migrating education, travel, delivery, or approval tables
- Changing the MCP tool signatures visible to callers

---

## Decisions

### D1: Entity anchoring — no bare string subjects

**Decision:** All domain facts MUST carry a resolved `entity_id` UUID. The bare string `"user"` is not acceptable as a `subject` for any migrated fact. The `subject` field is used only for human-readable context when `entity_id` is set.

**Rationale:**
- Bare string subjects prevent cross-butler entity resolution and graph traversal
- The owner entity UUID is available at daemon startup via `shared.contacts WHERE roles @> '["owner"]'`
- Contact entities are available via `shared.contacts.entity_id` FK
- Unresolved actors get an anonymous placeholder entity via `memory_entity_create()`

**Resolution cascade:**
1. Self-data (health, finance): use owner entity_id bootstrapped at startup
2. Contact-data (relationship): resolve `contact_id → shared.contacts → entity_id`; if `entity_id` is NULL, trigger `memory_entity_create()` and backfill `shared.contacts.entity_id`
3. HA device data (home): resolve or create a named entity per HA entity ID (entity_type = 'other', metadata.entity_class = 'ha_device')
4. Unresolved actors in any domain: `memory_entity_create()` with `entity_type='other'`, name = best available string, metadata noting provenance

**Subject field convention when entity_id is set:** the `subject` field SHOULD contain a human-readable identifier (owner name, contact name, HA entity ID string) for readability in search results. It is NOT the uniqueness key when `entity_id` is present.

---

### D2: Temporal vs property fact — migration rule

**Decision:** Use `is_temporal` from `predicate_registry` to determine storage semantics at write time. If the predicate has `is_temporal = true`, always set `valid_at`. If `is_temporal = false`, omit `valid_at` and rely on supersession.

| Fact type | `valid_at` | Supersession | Use case |
|---|---|---|---|
| Temporal | Non-NULL (event timestamp) | None — coexist | Measurements, transactions, interactions |
| Property | NULL | Yes — replaces prior active fact | Medications, accounts, subscriptions |

**Property supersession key:** `(entity_id, scope, predicate)` when `entity_id IS NOT NULL AND object_entity_id IS NULL AND valid_at IS NULL`.

**Multi-valued properties:** For tables where multiple active rows are valid simultaneously (e.g. multiple active medications, multiple accounts), the `content` field acts as a natural differentiator. Two property facts with the same `(entity_id, scope, predicate)` but different `content` values are treated as distinct. Implementers should use content values that are stable identifiers (medication name, institution + last_four, subscription service name).

---

### D3: Metadata schema standardization — typed fields per predicate family

**Decision:** Each predicate family has a documented metadata schema. Keys are snake_case TEXT. Numeric amounts are stored as STRING to preserve NUMERIC(14,2) precision. Timestamps within metadata are ISO 8601 strings.

**Why string amounts:** PostgreSQL JSONB stores numbers as float8, losing NUMERIC precision for amounts like `"1234.99"`. Storing as `"1234.99"` (string) and casting via `(metadata->>'amount')::NUMERIC` in aggregation queries is the correct pattern.

---

### D4: Aggregation queries — JSONB extraction, not dedicated table SQL

**Decision:** After migration, aggregation tools (`nutrition_summary`, `spending_summary`, `trend_report`) use `(metadata->>'key')::TYPE` casts in SQL rather than typed columns. A GIN index on `facts.metadata` and partial predicate indexes on `(predicate, valid_at)` provide acceptable performance for realistic data volumes.

**Index strategy:**
- GIN index on `facts.metadata` for containment queries (`@>`) and key existence (`?`)
- Partial B-tree index on `(predicate, valid_at, entity_id)` WHERE `predicate LIKE 'meal_%' AND validity = 'active'` — meal aggregation
- Partial B-tree index on `(predicate, valid_at, entity_id)` WHERE `predicate LIKE 'transaction_%' AND validity = 'active'` — spending aggregation
- Partial B-tree index on `(predicate, valid_at, entity_id)` WHERE `predicate LIKE 'measurement_%' AND validity = 'active'` — health trends

**Aggregation query pattern (nutrition_summary example):**
```sql
SELECT
  SUM((metadata->>'estimated_calories')::NUMERIC) AS total_calories,
  SUM((metadata->'macros'->>'protein_g')::NUMERIC) AS total_protein_g,
  SUM((metadata->'macros'->>'carbs_g')::NUMERIC) AS total_carbs_g,
  SUM((metadata->'macros'->>'fat_g')::NUMERIC) AS total_fat_g
FROM facts
WHERE entity_id = $1
  AND predicate IN ('meal_breakfast', 'meal_lunch', 'meal_dinner', 'meal_snack')
  AND validity = 'active'
  AND valid_at BETWEEN $2 AND $3
  AND scope = 'health'
```

**Aggregation query pattern (spending_summary example):**
```sql
SELECT
  metadata->>'category' AS category,
  SUM((metadata->>'amount')::NUMERIC) AS total,
  COUNT(*) AS count
FROM facts
WHERE entity_id = $1
  AND predicate IN ('transaction_debit', 'transaction_credit')
  AND validity = 'active'
  AND valid_at BETWEEN $2 AND $3
  AND scope = 'finance'
GROUP BY metadata->>'category'
ORDER BY total DESC
```

---

### D5: Scope field — domain namespacing

**Decision:** Every domain fact uses a domain-specific scope to avoid predicate collisions across butlers.

| Domain | scope value |
|---|---|
| Health butler | `health` |
| Relationship butler | `relationship` |
| Finance butler | `finance` |
| Home butler | `home` |

This is consistent with the `meal_*` predicates already using `scope='health'` in bu-axm.

---

### D6: Backward-compatible response shapes

**Decision:** Each wrapper tool MUST return exactly the same response structure as the original CRUD-table implementation. The mapping from fact fields to legacy response fields is documented per tool in the spec. LLM callers observing tool output MUST see no behavioral difference.

**Mapping pattern:**
- `id` → fact UUID (same type as original CRUD UUID PK)
- Timestamp columns (`measured_at`, `posted_at`, etc.) → `valid_at` from fact
- Payload columns → unpacked from `fact.metadata`
- Computed fields (e.g. `adherence_rate`) → computed in wrapper from fact query results, identical algorithm

---

### D7: Transaction deduplication — metadata-based

**Decision:** Finance transaction deduplication (originally via `uq_transactions_dedupe` partial index on `source_message_id, merchant, amount, posted_at`) is replicated in the `store_fact` wrapper using a pre-insert existence check:

```sql
SELECT id FROM facts
WHERE entity_id = $owner_entity_id
  AND predicate IN ('transaction_debit', 'transaction_credit')
  AND validity = 'active'
  AND scope = 'finance'
  AND metadata->>'source_message_id' = $source_message_id
  AND metadata->>'merchant' = $merchant
  AND metadata->>'amount' = $amount
  AND valid_at = $posted_at
LIMIT 1
```

If a matching fact exists, the insert is skipped. This check is wrapped in a short-circuit return from the tool, preserving idempotency.

---

### D8: HA device entities — entity_type extension

**Decision:** Home Assistant device/sensor entities (non-person, non-org, non-place) use `entity_type = 'other'` from the existing entity type enum, with `metadata.entity_class = 'ha_device'` in the entity row. One entity per distinct HA entity ID string. Entities are created lazily on first snapshot or looked up by `canonical_name = ha_entity_id`.

**Rationale:** Adding a new entity_type would require a migration and enum change across the entity subsystem. The `'other'` bucket with a metadata discriminator is the least invasive path. A future cleanup can introduce a dedicated `'device'` entity type.
