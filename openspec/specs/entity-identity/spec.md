## ADDED Requirements

### Requirement: Entities table in shared schema

The `entities` table SHALL reside in the `shared` PostgreSQL schema (`shared.entities`). All butler roles SHALL have `SELECT, INSERT, UPDATE, DELETE` grants on `shared.entities`. The table SHALL be accessible to all butlers through their `search_path` which includes `shared`.

#### Schema

```sql
CREATE TABLE shared.entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    canonical_name VARCHAR NOT NULL,
    entity_type VARCHAR NOT NULL DEFAULT 'other',
    aliases TEXT[] NOT NULL DEFAULT '{}',
    metadata JSONB DEFAULT '{}'::jsonb,
    roles TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_shared_entities_entity_type CHECK (
        entity_type IN ('person', 'organization', 'place', 'other')
    ),
    CONSTRAINT uq_shared_entities_tenant_canonical_type
        UNIQUE (tenant_id, canonical_name, entity_type)
);
```

**Implementation note (core_014):** The `shared.entities` table is created by `core_014_entities_to_shared.py`. It includes supporting indexes:
- `idx_shared_entities_tenant_canonical` on `(tenant_id, canonical_name)`
- `idx_shared_entities_aliases` using GIN on `aliases`
- `idx_shared_entities_metadata` using GIN on `metadata`

**Implementation note (search_path):** The memory module tools (`entity_create`, `entity_get`, `entity_update`, `entity_merge`, `entity_resolve`) address the table as `entities` (unqualified), relying on the session's `search_path` which sets the butler's own schema first, then `shared`, then `public`. For memory butlers (which do not have their own `entities` table post-`core_014`), the unqualified name resolves to `shared.entities` via search_path. The `core_014` migration copies all butler-local entities data into `shared.entities` as part of the migration.

**Grants:** `core_014` grants `SELECT, INSERT, UPDATE, DELETE` on `shared.entities` to: `butler_switchboard_rw`, `butler_general_rw`, `butler_health_rw`, `butler_relationship_rw`, `butler_messenger_rw`. Additional butler roles must be added to `_ALL_BUTLER_ROLES` in `core_014` (or a follow-up migration) as new butlers are created.

#### Scenario: Entities accessible from any butler schema

- **WHEN** any butler daemon queries `SELECT * FROM shared.entities WHERE id = $1`
- **THEN** the query MUST resolve to `shared.entities`
- **AND** the result MUST include all columns including `roles`

#### Scenario: General butler entities table unaffected

- **WHEN** the general butler queries unqualified `entities`
- **THEN** the query MUST resolve to `general.entities` (search_path ordering: general, shared, public)
- **AND** the general butler's collection-item entities table MUST NOT be confused with `shared.entities`

---

### Requirement: Roles column on entities

The `shared.entities` table SHALL have a `roles TEXT[] NOT NULL DEFAULT '{}'` column. Each element in the array represents an identity role. The initial supported role value is `'owner'`. This column is the authoritative source of truth for identity roles, replacing `contacts.roles`.

**Implementation note:** `contacts.roles` is kept for backward compatibility during the transition period. A follow-up `core_015` migration will drop `contacts.roles`. Until then, `contacts.roles` is populated on bootstrap for contacts that lack an entity link (fallback path).

#### Scenario: Owner entity has owner role

- **WHEN** an entity represents the system owner
- **THEN** the entity's `roles` column MUST contain `'owner'`

#### Scenario: Non-owner entity has empty roles

- **WHEN** an entity is created without explicit role assignment
- **THEN** the entity's `roles` column MUST be `'{}'` (empty array)

#### Scenario: Query entities by role

- **WHEN** a system component queries `SELECT * FROM shared.entities WHERE 'owner' = ANY(roles)`
- **THEN** the query MUST return exactly the entities that have `'owner'` in their roles array

---

### Requirement: Owner entity singleton

There SHALL be at most one entity with `'owner'` in `roles` across the entire `shared.entities` table. This is enforced by a partial unique index:

```sql
CREATE UNIQUE INDEX ix_entities_owner_singleton
ON shared.entities ((true))
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

1. Check whether `shared.entities` exists and has a `roles` column; if so, insert into `shared.entities` with `tenant_id='shared'`, `canonical_name='Owner'`, `entity_type='person'`, `roles=['owner']`. Use `ON CONFLICT (tenant_id, canonical_name, entity_type) DO NOTHING` for idempotency. If the INSERT returns no row (conflict), SELECT the existing entity id.
2. Insert into `shared.contacts` with `name='Owner'`, `roles=['owner']`, `entity_id` pointing to the owner entity (if entity was created/found). Use `ON CONFLICT DO NOTHING` against `ix_contacts_owner_singleton`.

**Implementation note:** `_ensure_owner_entity_and_contact()` in `src/butlers/daemon.py` implements this. It guards each step with existence checks (`to_regclass`, `information_schema.columns`) so the function is a no-op when tables or the `roles` column have not yet been migrated in. The `contacts.roles` column is also populated on the contact row (backward compat until `core_015`).

#### Scenario: Fresh bootstrap creates entity then contact

- **WHEN** a butler daemon starts with empty `shared.entities` and `shared.contacts`
- **THEN** it MUST first create the owner entity in `shared.entities`
- **AND** then create the owner contact linked via `entity_id`

#### Scenario: Graceful fallback when entities table missing

- **WHEN** a butler daemon starts and `shared.entities` does not yet exist
- **THEN** the daemon MUST still create the owner contact (without entity link)
- **AND** must NOT fail or raise

#### Scenario: Graceful fallback when roles column missing on entities

- **WHEN** `shared.entities` exists but lacks the `roles` column (pre-`core_014`)
- **THEN** the daemon MUST skip the entity INSERT and fall back to contact-only bootstrap
- **AND** must NOT fail or raise

---

### Requirement: Role lookup via entity JOIN

All code that needs to determine a contact's roles MUST query through the entity relationship:

```sql
SELECT COALESCE(e.roles, '{}') AS roles
FROM shared.contacts c
LEFT JOIN shared.entities e ON e.id = c.entity_id
```

Direct reads of `contacts.roles` are deprecated. The `contacts.roles` column will be dropped in a follow-up migration (`core_015`).

**Implementation note:** `resolve_contact_by_channel()` and `create_temp_contact()` in `src/butlers/identity.py` both use this JOIN pattern. `resolve_owner_contact_info()` and `upsert_owner_contact_info()` in `src/butlers/credential_store.py` use `JOIN shared.entities e ON e.id = c.entity_id WHERE 'owner' = ANY(e.roles)`.

#### Scenario: resolve_contact_by_channel reads roles from entity

- **WHEN** `resolve_contact_by_channel('telegram', '12345')` is called
- **THEN** the query MUST include `LEFT JOIN shared.entities e ON e.id = c.entity_id`
- **AND** roles MUST be read as `COALESCE(e.roles, '{}')`

#### Scenario: Owner credential resolution joins through entity

- **WHEN** `resolve_owner_contact_info(pool, 'telegram')` is called
- **THEN** the query MUST find the owner via `JOIN shared.entities e ON e.id = c.entity_id WHERE 'owner' = ANY(e.roles)`

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

### Requirement: Roles not exposed to runtime MCP tools

The `roles` field on entities MUST NOT be writable by runtime MCP tool callers. The `entity_create` Python function accepts an internal `roles` parameter (for daemon bootstrap), but the MCP tool registration MUST NOT expose it. The `entity_update` MCP tool MUST NOT accept `roles`.

**Implementation note:** The `memory_entity_create` MCP wrapper in `src/butlers/modules/memory/__init__.py` does not include a `roles` parameter — it calls `entity_create(..., roles=None)` implicitly. The `memory_entity_update` MCP wrapper does not accept `roles`. Entity `GET` and `entity_get()` return the `roles` field (read-only, for informational use).

#### Scenario: Runtime entity_create omits roles

- **WHEN** a runtime instance calls the `memory_entity_create` MCP tool
- **THEN** the tool MUST NOT accept a `roles` parameter
- **AND** the created entity MUST have `roles = []`

#### Scenario: Dashboard API can update entity roles

- **WHEN** `PATCH /api/contacts/{id}` with `{"roles": ["owner"]}` is called
- **THEN** the API MUST update `shared.entities.roles` via the contact's `entity_id`

**Implementation note:** `roster/relationship/api/router.py::patch_contact()` handles this. When `request.roles` is provided and the contact has an `entity_id`, it executes `UPDATE shared.entities SET roles = $1 WHERE id = $2`. If the contact has no `entity_id`, the roles update is silently skipped.

---

### Requirement: Facts FK points to shared.entities

The `facts.entity_id` foreign key MUST reference `shared.entities(id)` (not a butler-local entities table). The migration MUST drop the old search-path-resolved FK from `mem_002` and create a new explicit FK:

```sql
ALTER TABLE {schema}.facts
    ADD CONSTRAINT facts_entity_id_shared_fkey
    FOREIGN KEY (entity_id) REFERENCES shared.entities(id)
    ON DELETE RESTRICT;
```

**Implementation note:** `core_014` performs this for the following butler schemas: `general`, `health`, `messenger`, `relationship`, `switchboard`. The migration drops the old `facts_entity_id_fkey` (if it exists) and creates `facts_entity_id_shared_fkey` using `NOT VALID` + `VALIDATE CONSTRAINT` to avoid locking under heavy load.

`core_014` also re-creates the FK from `shared.contacts.entity_id` to `shared.entities(id)` as `contacts_entity_id_shared_fkey` (ON DELETE SET NULL), replacing the old `contacts_entity_id_fkey`.

#### Scenario: Fact references entity in shared schema

- **WHEN** a fact has `entity_id = <uuid>`
- **THEN** that UUID MUST exist in `shared.entities`
- **AND** attempting to delete the entity while facts reference it MUST fail (ON DELETE RESTRICT)
