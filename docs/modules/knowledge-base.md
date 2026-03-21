# Knowledge Base

> **Purpose:** Documents the entity-centric data model, SPO fact graph, predicate vocabulary, and domain overlays that form the butler system's knowledge layer.
> **Audience:** Contributors and module developers.
> **Prerequisites:** [Module System](module-system.md), [Memory Module](memory.md).

## Overview

The Butlers knowledge graph uses **entities as the universal identity anchor**. Every piece of knowledge -- facts, relationships, credentials, contact details -- attaches to an entity via `entity_id`. This page describes the entity data model, the predicate vocabulary system, and how domain-specific "overlays" layer onto the shared entity graph.

The knowledge base is not a standalone module. It is the data model that the Memory module reads and writes, and that other modules (Contacts, Health, Finance, Relationship) attach to via foreign keys.

## Core: shared.entities

The `shared.entities` table is the single source of identity across all butlers. Every person, organization, place, or device in the system is an entity.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID PK | Universal identifier |
| `canonical_name` | VARCHAR | Display name |
| `entity_type` | VARCHAR | `person`, `organization`, `place`, `other` |
| `aliases` | TEXT[] | Alternative names for resolution |
| `roles` | TEXT[] | Identity roles (e.g. `['owner']`) |
| `metadata` | JSONB | Extensible; includes lifecycle (`merged_into`, `deleted_at`, `unidentified`) |
| `tenant_id` | TEXT | Multi-tenant isolation |

**Entity resolution** uses a 5-tier waterfall: role match -> exact name -> exact alias -> prefix/substring -> fuzzy (edit distance <= 2). Context boosting from graph neighborhood and domain hints refines scoring.

**Lifecycle**: Entities are never hard-deleted. Merging sets `metadata.merged_into`; soft-delete sets `metadata.deleted_at`. A partial unique index allows name reuse after tombstoning.

## The SPO Fact Graph

Knowledge is stored as **Subject/Predicate/Object** facts in per-butler `facts` tables. The entity is always the subject; the object can be another entity (edge-fact) or a content string (property-fact).

### Three Fact Types

| Type | entity_id | object_entity_id | valid_at | Behavior |
|------|-----------|-------------------|----------|----------|
| **Property** | Set | NULL | NULL | Latest supersedes previous |
| **Edge** | Set | Set | NULL | Links two entities; supersedes by full key |
| **Temporal** | Set | Optional | Set | Coexists with others at different timestamps |

**Uniqueness keys:**

- Property: `(tenant_id, entity_id, scope, predicate)` where `valid_at IS NULL`
- Edge: `(tenant_id, entity_id, object_entity_id, scope, predicate)` where `valid_at IS NULL`
- Temporal: Idempotency key (SHA-256 of canonical tuple) prevents duplicates; no supersession

### Fact Scoping

Facts are namespaced by `scope` to isolate domains:

| Scope | Butler | Example predicates |
|-------|--------|-------------------|
| `health` | Health | `measurement_weight`, `symptom`, `took_dose`, `medication`, `condition` |
| `relationship` | Relationship | `interaction`, `gift`, `loan`, `reminder`, `contact_note` |
| `finance` | Finance | `transaction_debit`, `transaction_credit`, `account`, `subscription` |
| `home` | Home | `ha_state` |
| `global` | Any | `knows`, `parent_of`, `birthday`, `preference` |

## Predicate Vocabulary

The `predicate_registry` table governs which predicates are valid, what constraints they carry, and how they are discovered.

### Registry Schema

| Column | Type | Purpose |
|--------|------|---------|
| `name` | TEXT PK | Canonical predicate identifier |
| `is_edge` | BOOLEAN | Requires `object_entity_id` at write time |
| `is_temporal` | BOOLEAN | Requires `valid_at` at write time |
| `scope` | TEXT | Domain namespace |
| `aliases` | TEXT[] | Synonym list for deterministic resolution |
| `inverse_of` | TEXT FK | Bidirectional pair (e.g. `parent_of` <-> `child_of`) |
| `is_symmetric` | BOOLEAN | Self-inverse (e.g. `knows`, `sibling_of`) |
| `status` | TEXT | `active`, `deprecated`, `proposed` |
| `description_embedding` | vector(384) | Semantic search via MiniLM |
| `search_vector` | tsvector | Full-text search on name + description |
| `usage_count` | INTEGER | Popularity ranking signal |
| `example_json` | JSONB | Sample payload template |

### Predicate Lifecycle

```
proposed -> active -> deprecated (superseded_by -> replacement)
```

Auto-registered predicates start as `proposed` with inferred flags. Migration-seeded predicates are `active` with rich descriptions. Deprecated predicates still accept writes but return warnings.

### Write-Time Enforcement

When a predicate exists in the registry:

- `is_edge = true` -> `object_entity_id` required (else ValueError).
- `is_temporal = true` -> `valid_at` required (else ValueError).

Unregistered predicates are stored freely, then auto-registered as `proposed`.

### Predicate Search

The `memory_predicate_search` MCP tool uses three-signal hybrid retrieval fused via Reciprocal Rank Fusion (RRF):

1. **Trigram** (pg_trgm): Fuzzy name matching -- catches typos.
2. **Full-text** (tsvector): Stemmed description search.
3. **Semantic** (vector cosine): Conceptual matching -- "dad" finds `parent_of`.

### Inverse Predicates

Edge predicates can declare bidirectional pairs. Inverse facts are **materialized at write time** -- when `parent_of(Alice, Bob)` is stored and `parent_of` has `inverse_of = 'child_of'`, a mirrored fact `child_of(Bob, Alice)` is auto-created in the same transaction. This doubles edge-fact storage but means queries work naturally without special read-path logic.

### Domain/Range Type Validation

When a predicate specifies `expected_subject_type` or `expected_object_type`, `store_fact()` checks actual entity types. Mismatches produce **warnings, not errors** -- the fact is still stored. This follows Wikidata's philosophy: constraints are guidance, not enforcement gates.

## Overlays: Sub-Data Models

Overlays are domain-specific data models that attach to entities via foreign keys.

### Contacts Overlay (CRM)

`shared.contacts` is linked via `contacts.entity_id -> entities.id`:

- `shared.contacts` -- name variants, company, job_title, metadata.
- `shared.contact_info` -- multi-channel identifiers: email, phone, telegram, etc.

### Credentials Overlay

`shared.entity_info` stores credentials and identifiers per entity, with `secured = true` for masked values and `is_primary` for preferred identifiers.

### Health/Finance Overlays

Health and finance data attaches to the owner entity. All measurements, conditions, transactions, and subscriptions are facts scoped to their domain.

## Schema Isolation

| Schema | Visibility | Purpose |
|--------|-----------|---------|
| `shared` | All butlers (read); selective write | Entities, contacts, contact_info, entity_info |
| Per-butler | Butler-specific | Facts, episodes, rules, domain tables |

Inter-butler communication is MCP-only through the Switchboard. The shared schema provides identity resolution without violating butler isolation.

## Key Design Principles

1. **Entity-first**: All knowledge attaches to entities, not contacts or bare strings.
2. **Overlays, not monoliths**: Contacts, credentials, facts are separate models linked by `entity_id`.
3. **Predicate governance**: Registry enforces `is_edge`/`is_temporal`, aliases prevent proliferation, inverses enable bidirectional traversal.
4. **Temporal safety**: Temporal predicates require `valid_at` to prevent supersession from destroying historical data.
5. **Soft lifecycle**: Entities and predicates are tombstoned/deprecated, never deleted.
6. **Scope isolation**: Facts are namespaced by domain; predicates are scoped for search relevance.

## Related Pages

- [Memory Module](memory.md) -- the module that reads/writes this data model
- [Contacts Module](contacts.md) -- contacts overlay
- [Module System](module-system.md)
