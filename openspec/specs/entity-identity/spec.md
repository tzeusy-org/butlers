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

#### Scenario: Attempt to create duplicate owner entity

- **WHEN** an entity with `roles = ['owner']` already exists
- **AND** an attempt is made to insert another entity with `roles = ['owner']`
- **THEN** the insert MUST fail with a unique constraint violation

---

### Requirement: Entity-first owner bootstrap

When any butler daemon starts, it SHALL create the owner entity before the owner contact:

1. Insert into `shared.entities` with `tenant_id='shared'`, `canonical_name='Owner'`, `entity_type='person'`, `roles=['owner']`. Use `ON CONFLICT (tenant_id, canonical_name, entity_type) DO NOTHING` for idempotency.
2. Insert into `shared.contacts` with `name='Owner'`, `entity_id` pointing to the owner entity. Use `ON CONFLICT DO NOTHING` against `ix_contacts_owner_singleton`.

#### Scenario: Fresh bootstrap creates entity then contact

- **WHEN** a butler daemon starts with empty `shared.entities` and `shared.contacts`
- **THEN** it MUST first create the owner entity in `shared.entities`
- **AND** then create the owner contact linked via `entity_id`

#### Scenario: Graceful fallback when entities table missing

- **WHEN** a butler daemon starts and `shared.entities` does not yet exist
- **THEN** the daemon MUST still create the owner contact (without entity link)
- **AND** must NOT fail or raise

---

### Requirement: Role lookup via entity JOIN

All code that needs to determine a contact's roles MUST query through the entity relationship:

```sql
SELECT COALESCE(e.roles, '{}') AS roles
FROM shared.contacts c
LEFT JOIN shared.entities e ON e.id = c.entity_id
```

Direct reads of `contacts.roles` are deprecated. The `contacts.roles` column will be dropped in a follow-up migration.

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

#### Scenario: Merge source with roles into target

- **WHEN** source entity has `roles = ['trusted']` and target has `roles = ['owner']`
- **THEN** after merge, target MUST have `roles = ['owner', 'trusted']`

#### Scenario: Merge entities with no roles

- **WHEN** both source and target have `roles = []`
- **THEN** after merge, target MUST have `roles = []`

---

### Requirement: Roles not exposed to runtime MCP tools

The `roles` field on entities MUST NOT be writable by runtime MCP tool callers. The `entity_create` Python function accepts an internal `roles` parameter (for daemon bootstrap), but the MCP tool registration MUST NOT expose it. The `entity_update` MCP tool MUST NOT accept `roles`.

#### Scenario: Runtime entity_create omits roles

- **WHEN** a runtime instance calls the `entity_create` MCP tool
- **THEN** the tool MUST NOT accept a `roles` parameter
- **AND** the created entity MUST have `roles = []`

#### Scenario: Dashboard API can update entity roles

- **WHEN** `PATCH /api/contacts/{id}` with `{"roles": ["owner"]}` is called
- **THEN** the API MUST update `shared.entities.roles` via the contact's `entity_id`

---

### Requirement: Facts FK points to shared.entities

The `facts.entity_id` foreign key MUST reference `shared.entities(id)` (not a butler-local entities table). The migration MUST drop the old search-path-resolved FK from `mem_002` and create a new explicit FK:

```sql
ALTER TABLE {schema}.facts
    ADD CONSTRAINT facts_entity_id_shared_fkey
    FOREIGN KEY (entity_id) REFERENCES shared.entities(id)
    ON DELETE RESTRICT;
```

#### Scenario: Fact references entity in shared schema

- **WHEN** a fact has `entity_id = <uuid>`
- **THEN** that UUID MUST exist in `shared.entities`
- **AND** attempting to delete the entity while facts reference it MUST fail (ON DELETE RESTRICT)
