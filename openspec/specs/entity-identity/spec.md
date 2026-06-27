## ADDED Requirements

### Requirement: Entities table in public schema

The `entities` table SHALL reside in the `public` PostgreSQL schema (`public.entities`). All butler roles SHALL have `SELECT, INSERT, UPDATE, DELETE` grants on `public.entities`. The table SHALL be accessible to all butlers through their `search_path` which includes `public`.

#### Schema

```sql
CREATE TABLE public.entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name VARCHAR NOT NULL,
    entity_type VARCHAR NOT NULL DEFAULT 'other',
    aliases TEXT[] NOT NULL DEFAULT '{}',
    metadata JSONB DEFAULT '{}'::jsonb,
    roles TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_entities_entity_type CHECK (
        entity_type IN ('person', 'organization', 'place', 'other')
    ),
    CONSTRAINT uq_entities_canonical_type
        UNIQUE (canonical_name, entity_type)
);
```

**Implementation note (core_014):** The `public.entities` table is created by `core_014_entities_to_shared.py`. It includes supporting indexes:
- `idx_entities_canonical` on `(canonical_name)`
- `uq_entities_canonical_type_live` partial unique on `(canonical_name, entity_type)` WHERE merged_into IS NULL AND deleted_at IS NULL
- `idx_entities_aliases` using GIN on `aliases`
- `idx_entities_metadata` using GIN on `metadata`

**Implementation note (search_path):** The memory module tools (`entity_create`, `entity_get`, `entity_update`, `entity_merge`, `entity_resolve`) address the table as `entities` (unqualified), relying on the session's `search_path` which sets the butler's own schema first, then `public`. For memory butlers (which do not have their own `entities` table post-`core_014`), the unqualified name resolves to `public.entities` via search_path. The `core_014` migration copies all butler-local entities data into `public.entities` as part of the migration.

**Grants:** `core_014` grants `SELECT, INSERT, UPDATE, DELETE` on `public.entities` to: `butler_switchboard_rw`, `butler_general_rw`, `butler_health_rw`, `butler_relationship_rw`, `butler_messenger_rw`. Additional butler roles must be added to `_ALL_BUTLER_ROLES` in `core_014` (or a follow-up migration) as new butlers are created.

#### Scenario: Entities accessible from any butler schema

- **WHEN** any butler daemon queries `SELECT * FROM public.entities WHERE id = $1`
- **THEN** the query MUST resolve to `public.entities`
- **AND** the result MUST include all columns including `roles`

#### Scenario: General butler entities table unaffected

- **WHEN** the general butler queries unqualified `entities`
- **THEN** the query MUST resolve to `general.entities` (search_path ordering: general, shared, public)
- **AND** the general butler's collection-item entities table MUST NOT be confused with `public.entities`

---

### Requirement: [WITHDRAWN] Entity tenant_id

**Withdrawn (core_062).** The tenant_id column was removed from public.entities. Entities live in a single shared namespace — uniqueness is enforced on (canonical_name, entity_type). Schema-based isolation (per-butler PostgreSQL schemas) provides the actual boundary between butlers.

---

### Requirement: Entity-first data model

The canonical data model hierarchy is **Entity → Contact → Contact Details**.

- **Entity** (`public.entities`) is the top-level identity anchor. Facts, relationships, and knowledge graph edges attach to entities. Every known person, organization, or place is an entity.
- **Contact** (`public.contacts`) is a child of entity. A CRM record with name fields, linked to exactly one entity via `entity_id`. Created when reachable contact details (phone, email, address) are known.
- **Contact Details** (`public.contact_info`, addresses, etc.) attach to contacts.

An entity may exist without a contact (e.g., a person known from memory/conversation but no contact details). A contact MUST NOT exist without an entity. Facts MUST always be anchored to an entity via `entity_id`.

The relationship butler exposes entity tools (`entity_resolve`, `entity_get`, `entity_update`, `entity_neighbors`) as MCP tools so that LLM sessions can adopt the entity-first workflow: resolve entity first, then create contact only when genuinely needed.

#### Scenario: Entity without contact

- **WHEN** a person is mentioned in a conversation but no contact details are known
- **THEN** an entity SHOULD be created (possibly with `metadata.unidentified = true`)
- **AND** a contact MUST NOT be created until contact details (phone, email, etc.) are available

#### Scenario: Contact always linked to entity

- **WHEN** `contact_create()` is called
- **THEN** an entity MUST be resolved or created BEFORE the contact INSERT
- **AND** `entity_id` MUST be included in the INSERT payload (never NULL)

---

### Requirement: Roles column on entities

The `public.entities` table SHALL have a `roles TEXT[] NOT NULL DEFAULT '{}'` column. Each element in the array represents an identity role. The initial supported role value is `'owner'`. This column is the authoritative source of truth for identity roles, replacing `contacts.roles`.

**Implementation note:** `contacts.roles` is kept for backward compatibility during the transition period. A follow-up `core_015` migration will drop `contacts.roles`. Until then, `contacts.roles` is populated on bootstrap for contacts that lack an entity link (fallback path).

#### Scenario: Owner entity has owner role

- **WHEN** an entity represents the system owner
- **THEN** the entity's `roles` column MUST contain `'owner'`

#### Scenario: Non-owner entity has empty roles

- **WHEN** an entity is created without explicit role assignment
- **THEN** the entity's `roles` column MUST be `'{}'` (empty array)

#### Scenario: Query entities by role

- **WHEN** a system component queries `SELECT * FROM public.entities WHERE 'owner' = ANY(roles)`
- **THEN** the query MUST return exactly the entities that have `'owner'` in their roles array

---

### Requirement: Owner entity singleton

There SHALL be at most one entity with `'owner'` in `roles` across the entire `public.entities` table. This is enforced by a partial unique index:

```sql
CREATE UNIQUE INDEX ix_entities_owner_singleton
ON public.entities ((true))
WHERE 'owner' = ANY(roles);
```

**Implementation note:** The index is created in `core_014`. The `contacts` table retains its own `ix_contacts_owner_singleton` during the transition period (until `contacts.roles` is dropped in `core_015`).

#### Scenario: Attempt to create duplicate owner entity

- **WHEN** an entity with `roles = ['owner']` already exists
- **AND** an attempt is made to insert another entity with `roles = ['owner']`
- **THEN** the insert MUST fail with a unique constraint violation

---

### Requirement: Entity-first owner bootstrap

When any butler daemon starts, it SHALL create the owner entity before the owner contact:

1. Check whether `public.entities` exists and has a `roles` column; if so, insert into `public.entities` with `canonical_name='Owner'`, `entity_type='person'`, `roles=['owner']`. Use `ON CONFLICT (canonical_name, entity_type) DO NOTHING` for idempotency. If the INSERT returns no row (conflict), SELECT the existing entity id.
2. Insert into `public.contacts` with `name='Owner'`, `roles=['owner']`, `entity_id` pointing to the owner entity (if entity was created/found). Use `ON CONFLICT DO NOTHING` against `ix_contacts_owner_singleton`.

**Implementation note:** `_ensure_owner_entity_and_contact()` in `src/butlers/daemon.py` implements this. It guards each step with existence checks (`to_regclass`, `information_schema.columns`) so the function is a no-op when tables or the `roles` column have not yet been migrated in. The `contacts.roles` column is also populated on the contact row (backward compat until `core_015`).

#### Scenario: Fresh bootstrap creates entity then contact

- **WHEN** a butler daemon starts with empty `public.entities` and `public.contacts`
- **THEN** it MUST first create the owner entity in `public.entities`
- **AND** then create the owner contact linked via `entity_id`

#### Scenario: Graceful fallback when entities table missing

- **WHEN** a butler daemon starts and `public.entities` does not yet exist
- **THEN** the daemon MUST still create the owner contact (without entity link)
- **AND** must NOT fail or raise

#### Scenario: Graceful fallback when roles column missing on entities

- **WHEN** `public.entities` exists but lacks the `roles` column (pre-`core_014`)
- **THEN** the daemon MUST skip the entity INSERT and fall back to contact-only bootstrap
- **AND** must NOT fail or raise

---

### Requirement: Role lookup via entity JOIN

All code that needs to determine a contact's roles MUST query through the entity relationship:

```sql
SELECT COALESCE(e.roles, '{}') AS roles
FROM public.contacts c
LEFT JOIN public.entities e ON e.id = c.entity_id
```

Roles live on `public.entities.roles` and are read directly from the entity. The former `public.contacts` table (which carried its own `roles` array) is dropped (`core_134`).

**Implementation note:** `resolve_contact_by_channel()` and `create_temp_contact()` in `src/butlers/identity.py` both use this JOIN pattern. `resolve_owner_contact_info()` and `upsert_owner_contact_info()` in `src/butlers/credential_store.py` use `JOIN public.entities e ON e.id = c.entity_id WHERE 'owner' = ANY(e.roles)`.

#### Scenario: resolve_contact_by_channel reads roles from entity

- **WHEN** `resolve_contact_by_channel('telegram', '12345')` is called
- **THEN** the query MUST include `LEFT JOIN public.entities e ON e.id = c.entity_id`
- **AND** roles MUST be read as `COALESCE(e.roles, '{}')`

#### Scenario: Owner credential resolution joins through entity

- **WHEN** `resolve_owner_contact_info(pool, 'telegram')` is called
- **THEN** the query MUST find the owner via `JOIN public.entities e ON e.id = c.entity_id WHERE 'owner' = ANY(e.roles)`

---

### Requirement: Entity merge preserves roles

When two entities are merged via `entity_merge()`, the target entity MUST inherit all roles from the source entity (union, deduplicated).

**Implementation note:** `entity_merge()` in `src/butlers/modules/memory/tools/entities.py` implements role union in step 3b of the merge transaction.

#### Scenario: Merge source with roles into target

- **WHEN** source entity has `roles = ['trusted']` and target has `roles = ['owner']`
- **THEN** after merge, target MUST have `roles = ['owner', 'trusted']`

#### Scenario: Merge entities with no roles

- **WHEN** both source and target have `roles = []`
- **THEN** after merge, target MUST have `roles = []`

---

### Requirement: Calendar event entity junction table

A `calendar_event_entities` junction table SHALL exist in each butler's calendar-owning schema to link calendar events to entities. This table enables any calendar event (reminder, scheduled task, or provider-synced event) to be associated with zero or more entities in `public.entities`.

```sql
CREATE TABLE {schema}.calendar_event_entities (
    event_id UUID NOT NULL REFERENCES {schema}.calendar_events(id) ON DELETE CASCADE,
    entity_id UUID NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, entity_id)
);

CREATE INDEX idx_calendar_event_entities_entity
ON {schema}.calendar_event_entities(entity_id);
```

Both foreign keys use `ON DELETE CASCADE` — deleting an event removes all its entity associations, and deleting an entity removes all its event associations without deleting the events themselves.

**Implementation note (core_076):** `alembic/versions/core/core_076_calendar_event_columns_and_entities.py` creates this table in every butler schema that has a `calendar_events` table. The junction is read on `calendar_get_event`/`calendar_list_events` and written by `calendar_create_event`/`calendar_update_event`/`reminder_create`.

#### Scenario: Entity deletion preserves calendar events

- **WHEN** an entity is deleted from `public.entities`
- **THEN** all rows in `calendar_event_entities` referencing that entity SHALL be deleted via CASCADE
- **AND** the `calendar_events` rows themselves SHALL NOT be deleted — they lose the association but remain as events

#### Scenario: Entity merge updates event associations

- **WHEN** entity A is merged into entity B via `entity_merge(source=A, target=B)`
- **THEN** `calendar_event_entities` rows with `entity_id = A` SHALL be re-pointed to `entity_id = B`
- **AND** duplicate `(event_id, entity_id)` pairs SHALL be deduplicated (if event was already linked to both A and B, keep one row)

**Implementation note:** `_repoint_calendar_event_entities()` in `src/butlers/modules/memory/tools/entities.py` performs the re-point inside the merge transaction, using `ON CONFLICT (event_id, entity_id) DO NOTHING` for deduplication.

#### Scenario: Dashboard entity detail shows associated events

- **WHEN** the dashboard entity detail page is rendered for entity X
- **THEN** a query joining `calendar_event_entities` with `calendar_events` filtered by `entity_id = X` SHALL return all calendar events associated with that entity
- **AND** events SHALL be displayed with title, start time, recurrence status, and source butler

---

### Requirement: Roles not exposed to runtime MCP tools

The `roles` field on entities MUST NOT be writable by runtime MCP tool callers. The `entity_create` Python function accepts an internal `roles` parameter (for daemon bootstrap), but the MCP tool registration MUST NOT expose it. The `entity_update` MCP tool MUST NOT accept `roles`.

**Implementation note:** The `memory_entity_create` MCP wrapper in `src/butlers/modules/memory/__init__.py` does not include a `roles` parameter — it calls `entity_create(..., roles=None)` implicitly. The `memory_entity_update` MCP wrapper does not accept `roles`. Entity `GET` and `entity_get()` return the `roles` field (read-only, for informational use).

#### Scenario: Runtime entity_create omits roles

- **WHEN** a runtime instance calls the `memory_entity_create` MCP tool
- **THEN** the tool MUST NOT accept a `roles` parameter
- **AND** the created entity MUST have `roles = []`

#### Scenario: Dashboard API can update entity roles

- **WHEN** `PATCH /api/contacts/{id}` with `{"roles": ["owner"]}` is called
- **THEN** the API MUST update `public.entities.roles` via the contact's `entity_id`

**Implementation note:** `roster/relationship/api/router.py::patch_contact()` handles this. When `request.roles` is provided and the contact has an `entity_id`, it executes `UPDATE public.entities SET roles = $1 WHERE id = $2`. If the contact has no `entity_id`, the roles update is silently skipped.

---

### Requirement: [WITHDRAWN] MCP entity tools default tenant_id

**Withdrawn (core_062).** Entity tools no longer accept a tenant_id parameter. See above.

---

### Requirement: Facts FK points to public.entities

The `facts.entity_id` foreign key MUST reference `public.entities(id)` (not a butler-local entities table). The migration MUST drop the old search-path-resolved FK from `mem_002` and create a new explicit FK:

```sql
ALTER TABLE {schema}.facts
    ADD CONSTRAINT facts_entity_id_shared_fkey
    FOREIGN KEY (entity_id) REFERENCES public.entities(id)
    ON DELETE RESTRICT;
```

**Implementation note:** `core_014` performs this for the following butler schemas: `general`, `health`, `messenger`, `relationship`, `switchboard`. The migration drops the old `facts_entity_id_fkey` (if it exists) and creates `facts_entity_id_shared_fkey` using `NOT VALID` + `VALIDATE CONSTRAINT` to avoid locking under heavy load.

`core_014` also re-creates the FK from `public.contacts.entity_id` to `public.entities(id)` as `contacts_entity_id_shared_fkey` (ON DELETE SET NULL), replacing the old `contacts_entity_id_fkey`.

#### Scenario: Fact references entity in public schema

- **WHEN** a fact has `entity_id = <uuid>`
- **THEN** that UUID MUST exist in `public.entities`
- **AND** attempting to delete the entity while facts reference it MUST fail (ON DELETE RESTRICT)

---

### Requirement: Dual-mode facts (property facts and edge facts)

Facts SHALL operate in two modes determined by the presence of `object_entity_id`:

1. **Property fact** (`object_entity_id IS NULL`): Describes an attribute of a single entity. The fact is anchored by `entity_id` (the subject entity) and expresses `predicate → content` about that entity. This is the existing behavior.
2. **Edge fact** (`object_entity_id IS NOT NULL`): Represents a typed, directed relationship between two entities. The fact is anchored by `entity_id` (the subject/source entity) and `object_entity_id` (the object/target entity), with `predicate` naming the relationship type and `content` providing optional descriptive detail.

Both modes use the same `facts` table row structure. The `object_entity_id` column is nullable — existing facts with `object_entity_id = NULL` remain valid property facts with no migration required.

**Implementation note:** The `object_entity_id` column is added by a memory module migration (see module-memory spec). It is a nullable UUID FK to `public.entities(id)` with `ON DELETE RESTRICT`.

#### Scenario: Property fact has no object entity

- **WHEN** a fact describes an attribute of an entity (e.g., "Alice's birthday is March 5")
- **THEN** `entity_id` MUST reference the subject entity (Alice)
- **AND** `object_entity_id` MUST be `NULL`
- **AND** the fact MUST behave identically to pre-KG facts

#### Scenario: Edge fact links two entities

- **WHEN** a fact represents a relationship between two entities (e.g., "Alice works at Acme Corp")
- **THEN** `entity_id` MUST reference the subject entity (Alice)
- **AND** `object_entity_id` MUST reference the object entity (Acme Corp)
- **AND** `predicate` MUST name the relationship type (e.g., `works_at`)
- **AND** `content` MAY provide additional detail (e.g., "Senior Engineer since 2024")

#### Scenario: Edge fact backward compatibility

- **WHEN** code queries facts without filtering on `object_entity_id`
- **THEN** both property facts and edge facts MUST be returned
- **AND** existing queries that do not reference `object_entity_id` MUST continue to work unchanged

---

### Requirement: Typed relationships between entities via edge facts

Edge facts SHALL enable typed, directed relationships between entities. The relationship type is encoded in the `predicate` field. Common relationship predicates include but are not limited to: `knows`, `works_at`, `lives_with`, `manages`, `parent_of`, `sibling_of`, `lives_in`, `member_of`.

No formal ontology or schema enforcement is applied to predicates — they are free-form text strings, consistent with the existing fact model. An optional `predicate_registry` table (see module-memory spec) MAY guide consistent predicate usage but does not enforce it.

#### Scenario: Create a relationship between two people

- **GIVEN** entity "Alice" (person) and entity "Bob" (person) exist
- **WHEN** `store_fact` is called with `entity_id=Alice.id`, `object_entity_id=Bob.id`, `predicate='knows'`, `content='Met at university in 2020'`
- **THEN** an edge fact MUST be created linking Alice → Bob with predicate `knows`

#### Scenario: Create an employment relationship

- **GIVEN** entity "Alice" (person) and entity "Acme Corp" (organization) exist
- **WHEN** `store_fact` is called with `entity_id=Alice.id`, `object_entity_id=AcmeCorp.id`, `predicate='works_at'`, `content='Senior Engineer, started 2024'`
- **THEN** an edge fact MUST be created linking Alice → Acme Corp with predicate `works_at`

#### Scenario: Relationship directionality

- **WHEN** an edge fact exists with `entity_id=A` and `object_entity_id=B` and `predicate='manages'`
- **THEN** the relationship is directed: A manages B
- **AND** this does NOT imply B manages A
- **AND** to express the reverse, a separate edge fact with `entity_id=B` and `object_entity_id=A` MUST be created

---

### Requirement: Entity neighbors traversal via edge facts

An `entity_neighbors` tool SHALL provide multi-hop graph traversal starting from a given entity, following edge facts (facts where `object_entity_id IS NOT NULL`). Traversal uses recursive CTEs on PostgreSQL — no external graph database is required.

#### Scenario: Single-hop neighbor discovery

- **WHEN** `entity_neighbors` is called with `entity_id=X` and `max_depth=1`
- **THEN** all entities directly connected to X via edge facts MUST be returned
- **AND** both outgoing edges (X as `entity_id`) and incoming edges (X as `object_entity_id`) MUST be included by default
- **AND** each result MUST include: the neighbor entity's `id`, `canonical_name`, `entity_type`, the connecting `predicate`, the edge `direction` (`outgoing` or `incoming`), and the edge fact's `content`

#### Scenario: Multi-hop traversal with depth limit

- **WHEN** `entity_neighbors` is called with `entity_id=X` and `max_depth=3`
- **THEN** traversal MUST follow edge facts up to 3 hops from X
- **AND** each result MUST include a `depth` field (1-indexed) indicating the hop distance from X
- **AND** cycles MUST be detected and broken (an entity already visited at a shallower depth MUST NOT be revisited)
- **AND** results MUST be ordered by `depth ASC`, then `canonical_name ASC`

#### Scenario: Predicate filter narrows traversal

- **WHEN** `entity_neighbors` is called with `predicate_filter=['works_at', 'manages']`
- **THEN** only edge facts whose `predicate` is in the filter list MUST be traversed
- **AND** edge facts with other predicates MUST be ignored during traversal

#### Scenario: Direction filter controls edge traversal

- **WHEN** `entity_neighbors` is called with `direction='outgoing'`
- **THEN** only edges where the current entity is `entity_id` (subject) MUST be followed
- **AND** `direction='incoming'` MUST follow only edges where the current entity is `object_entity_id`
- **AND** `direction='both'` (default) MUST follow edges in both directions

#### Scenario: Empty neighborhood

- **WHEN** `entity_neighbors` is called for an entity with no edge facts
- **THEN** an empty list MUST be returned

#### Scenario: Traversal respects fact validity

- **WHEN** `entity_neighbors` traverses edge facts
- **THEN** only facts with `validity = 'active'` MUST be followed
- **AND** superseded, expired, and retracted edge facts MUST be excluded

---

### Requirement: Entity info table for per-entity properties and credentials

The `public.entity_info` table SHALL store typed key-value properties for entities, including credentials consumed by backend modules. Each `(entity_id, type)` pair is unique. Entries with `secured = true` are masked in API responses.

#### Schema

```sql
CREATE TABLE public.entity_info (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
    type VARCHAR NOT NULL,
    value TEXT NOT NULL,
    label VARCHAR,
    is_primary BOOLEAN DEFAULT false,
    secured BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_entity_info_entity_type UNIQUE (entity_id, type)
);
```

---

### Requirement: Entity resolve case-insensitive exact tier with fact-count promotion

`memory_entity_resolve(name, entity_type=...)` SHALL treat case-insensitive matches against `canonical_name` and against any element of `aliases` as a single tier called the **exact tier**. Within the exact tier, candidates SHALL be ranked by `fact_count`, defined as the number of rows in the memory butler's `facts` table where `(entity_id = e.id OR object_entity_id = e.id) AND validity = 'active' AND invalid_at IS NULL`. Candidates whose `fact_count` equals the maximum `fact_count` in the exact tier SHALL be returned with `score = 100`. All other exact-tier candidates SHALL be returned with `score = 80`.

The returned `name_match` field for any exact-tier candidate SHALL be the literal string `"exact"`. The values `"canonical"` and `"alias"` SHALL NOT appear in the returned `name_match` field.

Each returned record SHALL include a `fact_count` integer field reflecting the count described above.

Lower tiers (`prefix`, `fuzzy`, `role`) SHALL retain their existing scoring (`prefix = 50`, `fuzzy = 20`, `role = 120`) and SHALL NOT participate in fact-count promotion.

#### Scenario: Single canonical-name match returns score 100

- **WHEN** `entity_resolve("Acme Corp", entity_type="organization")` is called
- **AND** exactly one entity exists with `canonical_name = "Acme Corp"` and no other entity has `"Acme Corp"` as either a canonical name or an alias (case-insensitive)
- **THEN** the call SHALL return one result with `score = 100`, `name_match = "exact"`, and `fact_count` populated

#### Scenario: Single alias-only match returns score 100

- **WHEN** `entity_resolve("Chloe", entity_type="person")` is called
- **AND** exactly one entity exists whose `canonical_name = "Chloe Wong"` and whose `aliases` array contains `"Chloe"` (case-insensitive)
- **AND** no other entity has `"Chloe"` as canonical name or alias
- **THEN** the call SHALL return one result with `score = 100`, `name_match = "exact"`, `canonical_name = "Chloe Wong"`, and `aliases` containing `"Chloe"`

#### Scenario: Two exact-tier candidates with unequal fact counts

- **WHEN** `entity_resolve("Sam", entity_type="person")` is called
- **AND** entity A has `canonical_name = "Sam"` with `fact_count = 12`
- **AND** entity B has `canonical_name = "Samuel Lin"` with alias `"Sam"` and `fact_count = 47`
- **THEN** the call SHALL return entity B with `score = 100, name_match = "exact"`
- **AND** entity A SHALL be returned with `score = 80, name_match = "exact"`
- **AND** results SHALL be sorted by score descending

#### Scenario: Tied fact counts return multiple results at score 100

- **WHEN** `entity_resolve("Alex", entity_type="person")` is called
- **AND** two entities each have `"Alex"` as canonical name or alias and each have `fact_count = 8`
- **THEN** both SHALL be returned with `score = 100`
- **AND** the runtime LLM is expected to surface this ambiguity to the user (via the `butler-memory` skill disambiguation rule)

#### Scenario: Zero fact counts still return at score 100

- **WHEN** `entity_resolve("NewPerson", entity_type="person")` is called
- **AND** an entity with `canonical_name = "NewPerson"` exists but has `fact_count = 0`
- **THEN** the call SHALL return that entity with `score = 100, fact_count = 0`
- **AND** the candidate SHALL NOT be filtered out solely on account of having zero facts

#### Scenario: Case-insensitive equivalence between canonical and alias

- **WHEN** `entity_resolve("chloe", entity_type="person")` is called
- **AND** entity C has `canonical_name = "Chloe"` (mixed case) with `fact_count = 5`
- **AND** entity D has alias `"CHLOE"` (uppercase) on `canonical_name = "Chloe Wong"` with `fact_count = 5`
- **THEN** both C and D SHALL appear in the exact tier
- **AND** because their `fact_count` is tied at the max, both SHALL be returned with `score = 100, name_match = "exact"`

#### Scenario: Retracted facts do not contribute to fact_count

- **WHEN** `entity_resolve("Riley", entity_type="person")` is called
- **AND** an entity matches in the exact tier and has 10 facts referencing it, of which 4 have `validity != 'active'` or `invalid_at IS NOT NULL`
- **THEN** the returned `fact_count` SHALL be 6, not 10

---

### Requirement: Google Account entity role

Entities with `'google_account'` in their `roles` array SHALL be treated as companion entities for Google account credential storage. They are infrastructure entities, not identity entities.

#### Scenario: Google account role recognized

- **WHEN** an entity has `roles = ['google_account']`
- **THEN** it SHALL be recognized as a Google account companion entity
- **AND** it SHALL anchor `entity_info` rows (type `google_oauth_refresh`) for that account's credentials

#### Scenario: Google account entities excluded from identity resolution

- **WHEN** `entity_resolve()` searches for entities by name
- **THEN** entities with `'google_account' = ANY(roles)` SHALL be excluded from candidate results
- **AND** they SHALL NOT appear in any resolution tier (role, exact, prefix, or fuzzy)

#### Scenario: Google account entities excluded from graph traversal defaults

- **WHEN** `entity_neighbors()` traverses the entity graph with default parameters
- **THEN** entities with `'google_account' = ANY(roles)` SHALL be excluded from traversal results
- **AND** edge facts pointing to/from google_account entities SHALL NOT be followed

#### Scenario: Google account entities excluded from dashboard entity lists

- **WHEN** the dashboard fetches entities for display (entity list, unidentified entities)
- **THEN** entities with `'google_account' = ANY(roles)` SHALL be filtered out
- **AND** they SHALL NOT appear in entity count statistics

### Requirement: Entity info supports multiple Google accounts

The `public.entity_info` table's `UNIQUE(entity_id, type)` constraint SHALL naturally support multiple `google_oauth_refresh` rows — one per Google account companion entity. No constraint change is needed.

#### Scenario: Two accounts with independent refresh tokens

- **WHEN** Google account A has companion entity E1 and account B has companion entity E2
- **THEN** `entity_info` SHALL contain two rows: `(E1, 'google_oauth_refresh', token_A)` and `(E2, 'google_oauth_refresh', token_B)`
- **AND** the `UNIQUE(entity_id, type)` constraint is satisfied because `E1 != E2`

#### Scenario: Owner entity no longer stores Google refresh token

- **WHEN** the multi-account migration completes
- **THEN** the owner entity SHALL NOT have a `google_oauth_refresh` row in `entity_info`
- **AND** Google refresh tokens SHALL only exist on companion entities referenced by `google_accounts.entity_id`

---

### Requirement: Entity info type registry (frontend ↔ backend coupling)

The entity detail page (`/butlers/entities/:id`) provides an "Add property" form with a type dropdown. **This dropdown is the sole UI for provisioning credentials that backend modules resolve at startup.** If a credential type is missing from the dropdown, users cannot configure it through the dashboard.

The frontend `ENTITY_INFO_TYPES` array and the backend module credential lookups (via `resolve_owner_entity_info(pool, info_type)` or `resolve_google_account_entity(pool, email)`) form a tight coupling: every `info_type` that a module resolves MUST be present in the frontend dropdown, and the frontend MUST mark credential types as secured.

#### Canonical type registry

| Type | Label | Secured | Consumed by |
|---|---|---|---|
| `email` | Email | no | Identity / contact info |
| `telegram` | Telegram Handle | no | Identity / contact info |
| `telegram_chat_id` | Telegram Chat ID | no | Identity / Switchboard routing |
| `api_key` | API Key | yes | (generic) |
| `api_secret` | API Secret | yes | (generic) |
| `token` | Token | yes | (generic) |
| `password` | Password | yes | (generic) |
| `username` | Username | no | (generic) |
| `url` | URL | no | (generic) |
| `telegram_api_id` | Telegram API ID | no | Contacts module (`on_startup`) |
| `telegram_api_hash` | Telegram API Hash | yes | Contacts module (`on_startup`) |
| `telegram_user_session` | Telegram User Session | yes | Contacts module (`on_startup`) |
| `home_assistant_url` | Home Assistant URL | no | Home module (`on_startup`) |
| `home_assistant_token` | Home Assistant Token | yes | Home module (`on_startup`) |
| `google_oauth_refresh` | Google OAuth Refresh | yes | Google account registry (companion entities only) |
| `email_password` | Email Password | yes | Email module |
| `other` | Other | no | (generic) |

**Change note:** `google_oauth_refresh` is now consumed by the Google account registry on companion entities, not directly by modules via `resolve_owner_entity_info()`. The type remains in the registry for visibility but manual editing of `google_oauth_refresh` rows on the owner entity is no longer meaningful — Google OAuth tokens are managed exclusively through the `/api/oauth/google/*` endpoints.

**Maintenance rule:** When a new module introduces a credential dependency via `resolve_owner_entity_info()`, the developer MUST add the corresponding type to:
1. The frontend `ENTITY_INFO_TYPES` array in `frontend/src/pages/EntityDetailPage.tsx`
2. The `SECURED_TYPES` set (if the value is a secret)
3. The `entityInfoTypeLabel()` switch for a human-readable label
4. This spec's canonical type registry table

#### Scenario: Module credential type missing from frontend dropdown

- **WHEN** a backend module calls `resolve_owner_entity_info(pool, 'new_credential_type')` at startup
- **AND** `'new_credential_type'` is NOT in the frontend `ENTITY_INFO_TYPES` array
- **THEN** users CANNOT configure this credential through the dashboard entity detail page
- **AND** the module will fail to start or degrade (depending on its error handling)
- **AND** this is considered a bug — the type MUST be added to the frontend

#### Scenario: All module credential types are present in the dropdown

- **WHEN** a user navigates to the entity detail page for the owner entity
- **THEN** the type dropdown MUST include all credential types listed in the canonical type registry
- **AND** selecting a secured type MUST use a password input field and auto-set `secured = true`

#### Scenario: Adding a new module with credential dependency

- **WHEN** a developer creates a new module that resolves credentials via `resolve_owner_entity_info()`
- **THEN** the module's credential types MUST be added to the frontend dropdown before the module is deployed
- **AND** the canonical type registry in this spec MUST be updated

#### Scenario: Google OAuth refresh token not editable on owner entity

- **WHEN** a user views the owner entity's entity_info on the dashboard
- **THEN** `google_oauth_refresh` rows SHALL NOT appear (they live on companion entities)
- **AND** the dashboard SHALL direct users to the Google Accounts management page for OAuth management

---

### Requirement: Transitory entity convention via metadata.unidentified

Entities with `metadata->>'unidentified' = 'true'` SHALL be treated as **transitory entities** — pending user approval for promotion to confirmed entities. This convention is the canonical mechanism for surfacing auto-discovered entities in the dashboard for review.

Transitory entities are full `public.entities` rows — they have a valid UUID, can be referenced by `entity_id` in facts and contacts, and participate in entity resolution and graph traversal. The only distinction is the metadata flag, which controls dashboard presentation (shown in the "Unidentified Entities" section with a visual badge).

#### Scenario: Transitory entity created by contacts system

- **WHEN** a message arrives from an unknown sender and `create_temp_contact()` is called
- **THEN** the auto-created entity MUST have `metadata` containing `{"unidentified": true, "source_channel": "<type>", "source_value": "<value>"}`
- **AND** the entity MUST appear in the dashboard "Unidentified Entities" section

#### Scenario: Transitory entity created by memory fact storage

- **WHEN** an agent stores a fact about an entity not found via `memory_entity_resolve`
- **THEN** the auto-created entity MUST have `metadata` containing `{"unidentified": true, "source": "fact_storage", "source_butler": "<butler>", "source_scope": "<scope>"}`
- **AND** the entity MUST appear in the dashboard "Unidentified Entities" section

#### Scenario: Transitory entity participates in entity resolution

- **WHEN** `memory_entity_resolve` is called with a name matching a transitory entity's `canonical_name`
- **THEN** the transitory entity MUST be returned as a candidate (same scoring as any other entity)
- **AND** the `metadata.unidentified` flag MUST NOT exclude it from resolution results

#### Scenario: Promoting a transitory entity

- **WHEN** the owner edits a transitory entity via the dashboard and removes the `unidentified` flag from metadata (or the system clears it upon confirmation)
- **THEN** the entity MUST no longer appear in the "Unidentified Entities" section
- **AND** the entity MUST continue to be a valid, confirmed entity with all its linked facts intact

#### Scenario: Merging a transitory entity into a confirmed entity

- **WHEN** the owner merges transitory entity T into confirmed entity C via `memory_entity_merge(source=T, target=C)`
- **THEN** all facts with `entity_id = T` MUST be re-pointed to `entity_id = C`
- **AND** entity T MUST be tombstoned (`metadata.merged_into = C`)
- **AND** the transitory entity MUST no longer appear in the "Unidentified Entities" section

#### Scenario: Deleting a transitory entity

- **WHEN** the owner deletes a transitory entity via the dashboard
- **THEN** the entity cannot be deleted while facts reference it, as enforced by the `ON DELETE RESTRICT` foreign key constraint. Facts must be retracted or re-pointed first.

#### Scenario: Dashboard query for unidentified entities

- **WHEN** the dashboard fetches entities for the "Unidentified Entities" section
- **THEN** the query MUST filter on `metadata->>'unidentified' = 'true'`
- **AND** results MUST include entities created by both the contacts system and the memory fact storage system
- **AND** tombstoned entities (`metadata->>'merged_into' IS NOT NULL`) MUST be excluded
