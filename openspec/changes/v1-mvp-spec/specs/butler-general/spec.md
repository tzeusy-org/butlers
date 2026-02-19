# General Butler Specification

The General butler is the catch-all butler for requests that do not fit any specialist butler. It provides freeform JSONB storage organized into optional collections, with tagging and text search capabilities. As usage patterns emerge, data stored in the General butler can be exported and migrated to a new specialized butler. The General butler runs no modules and has no scheduled tasks — it is purely reactive, responding only to MCP tool calls.

**Port:** 40101
**Database:** `butler_general`

## Database Schema

```sql
CREATE TABLE collections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    schema_hint JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id UUID REFERENCES collections(id),
    title TEXT,
    data JSONB NOT NULL DEFAULT '{}',
    tags JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_entities_collection_id ON entities (collection_id);
CREATE INDEX idx_entities_tags ON entities USING GIN (tags);
CREATE INDEX idx_entities_data ON entities USING GIN (data);
```

---

## ADDED Requirements

### Requirement: Butler Configuration

The General butler SHALL be configured via `butler.toml` with `name = "general"`, `port = 40101`, and `db.name = "butler_general"`. It SHALL declare no modules and no scheduled tasks.

#### Scenario: General butler starts with minimal config

WHEN the General butler starts with a `butler.toml` containing `[butler]` with `name = "general"` and `port = 40101` and `[butler.db]` with `name = "butler_general"`,
THEN the daemon SHALL start successfully with no modules loaded,
AND no scheduled tasks SHALL be registered,
AND the FastMCP server SHALL listen on port 40101.

---

### Requirement: Butler-Specific Migration Provisioning

The General butler SHALL provision the `collections` and `entities` tables via Alembic revisions in the `general` version chain, applied after the core Alembic chain. GIN indexes SHALL be created on the `entities.tags` and `entities.data` columns for efficient querying, and a B-tree index SHALL be created on `entities.collection_id` for collection lookups.

#### Scenario: Tables created on first startup

WHEN the General butler starts against a freshly provisioned database,
THEN the `collections` table MUST exist with columns `id` (UUID PK), `name` (TEXT UNIQUE NOT NULL), `description` (TEXT), `schema_hint` (JSONB), and `created_at` (TIMESTAMPTZ),
AND the `entities` table MUST exist with columns `id` (UUID PK), `collection_id` (UUID FK to collections), `title` (TEXT), `data` (JSONB NOT NULL DEFAULT '{}'), `tags` (JSONB NOT NULL DEFAULT '[]'), `created_at` (TIMESTAMPTZ), and `updated_at` (TIMESTAMPTZ),
AND GIN indexes MUST exist on `entities.tags` and `entities.data`,
AND a B-tree index MUST exist on `entities.collection_id`.

#### Scenario: Alembic migrations are idempotent on restart

WHEN the General butler restarts and the `collections` and `entities` tables already exist,
THEN Alembic SHALL detect that all revisions are already applied and skip them without error.

---

### Requirement: collection_create creates a new collection

The `collection_create` MCP tool SHALL accept `name` (required), `description` (optional), and `schema_hint` (optional) parameters and insert a new row into the `collections` table. It SHALL return the newly created collection's UUID.

The `schema_hint` field is advisory only — it documents the expected shape of entities in the collection but SHALL NOT be enforced by any tool.

#### Scenario: Creating a collection with all fields

WHEN `collection_create(name="recipes", description="Cooking recipes", schema_hint={"type": "object", "properties": {"ingredients": {"type": "array"}}})` is called,
THEN a new row SHALL be inserted into the `collections` table with the given name, description, and schema_hint,
AND the tool SHALL return the UUID of the created collection.

#### Scenario: Creating a collection with name only

WHEN `collection_create(name="notes")` is called with no description or schema_hint,
THEN a new row SHALL be inserted with `description` as NULL and `schema_hint` as NULL,
AND the tool SHALL return the UUID of the created collection.

#### Scenario: Duplicate collection name is rejected

WHEN `collection_create(name="recipes")` is called and a collection named "recipes" already exists,
THEN the tool SHALL return an error indicating that the collection name is already taken,
AND no new row SHALL be inserted.

---

### Requirement: collection_list returns all collections

The `collection_list` MCP tool SHALL accept no parameters and return a list of all collections in the `collections` table.

#### Scenario: Listing collections when several exist

WHEN three collections exist in the `collections` table and `collection_list()` is called,
THEN the tool SHALL return all three collections with their `id`, `name`, `description`, `schema_hint`, and `created_at` fields.

#### Scenario: Listing collections when none exist

WHEN no collections exist in the `collections` table and `collection_list()` is called,
THEN the tool SHALL return an empty list,
AND it MUST NOT raise an error.

---

### Requirement: collection_get returns a collection with entity count

The `collection_get` MCP tool SHALL accept an `id` parameter (UUID) and return the full collection record along with the count of entities belonging to that collection.

#### Scenario: Getting an existing collection

WHEN `collection_get(id)` is called with the UUID of an existing collection that has 5 entities,
THEN the tool SHALL return the collection's `id`, `name`, `description`, `schema_hint`, `created_at`, and an `entity_count` of 5.

#### Scenario: Getting a collection with no entities

WHEN `collection_get(id)` is called with the UUID of an existing collection that has no entities,
THEN the tool SHALL return the collection record with an `entity_count` of 0.

#### Scenario: Getting a nonexistent collection

WHEN `collection_get(id)` is called with a UUID that does not correspond to any collection,
THEN the tool SHALL return null or an error indicating the collection was not found.

---

### Requirement: entity_create creates a new entity

The `entity_create` MCP tool SHALL accept `data` (required, arbitrary JSONB), `collection_id` (optional UUID), `title` (optional string), and `tags` (optional array of strings) parameters. It SHALL insert a new row into the `entities` table and return the newly created entity's UUID.

Entities MAY exist without a collection — the `collection_id` field is nullable.

#### Scenario: Creating an entity with all fields

WHEN `entity_create(collection_id=<uuid>, title="Pasta Carbonara", data={"type": "recipe", "ingredients": ["pasta", "eggs", "guanciale"]}, tags=["italian", "dinner"])` is called,
THEN a new row SHALL be inserted into the `entities` table with the given collection_id, title, data, and tags,
AND the tool SHALL return the UUID of the created entity.

#### Scenario: Creating an entity with data only

WHEN `entity_create(data={"type": "quick_note", "text": "remember to buy milk"})` is called with no collection_id, title, or tags,
THEN a new row SHALL be inserted with `collection_id` as NULL, `title` as NULL, and `tags` as `'[]'`,
AND the tool SHALL return the UUID of the created entity.

#### Scenario: Creating an entity with nonexistent collection_id

WHEN `entity_create(collection_id=<nonexistent-uuid>, data={"note": "test"})` is called with a collection_id that does not reference an existing collection,
THEN the tool SHALL return an error indicating that the referenced collection does not exist,
AND no row SHALL be inserted.

#### Scenario: Tags stored as JSONB array of strings

WHEN `entity_create(data={"x": 1}, tags=["alpha", "beta", "gamma"])` is called,
THEN the `tags` column SHALL contain the JSONB array `["alpha", "beta", "gamma"]`.

---

### Requirement: entity_get returns a full entity

The `entity_get` MCP tool SHALL accept an `id` parameter (UUID) and return the complete entity record including all fields.

#### Scenario: Getting an existing entity

WHEN `entity_get(id)` is called with the UUID of an existing entity,
THEN the tool SHALL return the entity's `id`, `collection_id`, `title`, `data`, `tags`, `created_at`, and `updated_at`.

#### Scenario: Getting a nonexistent entity

WHEN `entity_get(id)` is called with a UUID that does not correspond to any entity,
THEN the tool SHALL return null or an error indicating the entity was not found.

---

### Requirement: entity_update uses deep merge for data

The `entity_update` MCP tool SHALL accept `id` (required UUID), `title` (optional), `data` (optional JSONB), and `tags` (optional array of strings) parameters. Only provided fields SHALL be updated. The `data` field SHALL use deep merge semantics: the provided data object is recursively merged into the existing data, not replaced wholesale. The `updated_at` timestamp SHALL be set to the current time on every update.

#### Scenario: Updating only the title

WHEN an entity exists with `title = "Old Title"` and `data = {"a": 1}` and `entity_update(id, title="New Title")` is called,
THEN the entity's `title` SHALL be updated to "New Title",
AND the entity's `data` SHALL remain `{"a": 1}` unchanged,
AND `updated_at` SHALL be updated to the current timestamp.

#### Scenario: Deep merge of data field

WHEN an entity exists with `data = {"name": "Carbonara", "ingredients": ["pasta"], "servings": 2}` and `entity_update(id, data={"ingredients": ["pasta", "eggs"], "prep_time": "20min"})` is called,
THEN the entity's `data` SHALL be `{"name": "Carbonara", "ingredients": ["pasta", "eggs"], "servings": 2, "prep_time": "20min"}`,
AND existing keys not present in the update (`name`, `servings`) SHALL be preserved,
AND existing keys present in the update (`ingredients`) SHALL be overwritten with the new value,
AND new keys (`prep_time`) SHALL be added.

#### Scenario: Updating tags replaces the entire tags array

WHEN an entity exists with `tags = ["old"]` and `entity_update(id, tags=["new", "updated"])` is called,
THEN the entity's `tags` SHALL be `["new", "updated"]`,
AND the old tags array SHALL be fully replaced, not merged.

#### Scenario: Updating a nonexistent entity

WHEN `entity_update(id, title="New")` is called with a UUID that does not correspond to any entity,
THEN the tool SHALL return an error indicating the entity was not found.

#### Scenario: Updating with no optional fields provided

WHEN `entity_update(id)` is called with only the required `id` and no optional fields,
THEN the entity SHALL remain unchanged except that `updated_at` SHALL be updated to the current timestamp.

---

### Requirement: entity_search finds entities by collection, tag, or text query

The `entity_search` MCP tool SHALL accept `collection_id` (optional UUID), `tag` (optional string), and `query` (optional string) parameters. All provided parameters act as filters combined with AND semantics. The tool SHALL return a list of matching entities.

The `query` parameter SHALL perform text search on the `title` field and within the JSONB `data` field. The search SHOULD use case-insensitive matching.

#### Scenario: Search by collection_id

WHEN `entity_search(collection_id=<uuid>)` is called and 3 entities belong to that collection,
THEN the tool SHALL return those 3 entities.

#### Scenario: Search by tag

WHEN `entity_search(tag="italian")` is called and 2 entities have "italian" in their tags array,
THEN the tool SHALL return those 2 entities.

#### Scenario: Search by text query on title

WHEN `entity_search(query="Carbonara")` is called and one entity has `title = "Pasta Carbonara"`,
THEN the tool SHALL return that entity in the results.

#### Scenario: Search by text query on data

WHEN `entity_search(query="Kyoto")` is called and one entity has `data = {"destination": "Kyoto", "notes": "cherry blossom season"}`,
THEN the tool SHALL return that entity in the results.

#### Scenario: Combined filters with AND semantics

WHEN `entity_search(collection_id=<uuid>, tag="dinner", query="pasta")` is called,
THEN the tool SHALL return only entities that belong to the specified collection AND have "dinner" in their tags AND match "pasta" in their title or data.

#### Scenario: No filters provided returns all entities

WHEN `entity_search()` is called with no parameters,
THEN the tool SHALL return all entities in the `entities` table.

#### Scenario: No matches found

WHEN `entity_search(tag="nonexistent-tag")` is called and no entities have that tag,
THEN the tool SHALL return an empty list,
AND it MUST NOT raise an error.

---

### Requirement: entity_delete performs hard delete

The `entity_delete` MCP tool SHALL accept an `id` parameter (UUID) and permanently remove the entity from the `entities` table. This is a hard delete — the row is irrecoverably removed.

#### Scenario: Deleting an existing entity

WHEN `entity_delete(id)` is called with the UUID of an existing entity,
THEN the entity row SHALL be removed from the `entities` table,
AND a subsequent `entity_get(id)` SHALL return null or not found.

#### Scenario: Deleting a nonexistent entity

WHEN `entity_delete(id)` is called with a UUID that does not correspond to any entity,
THEN the operation SHALL be a no-op,
AND it MUST NOT raise an error.

---

### Requirement: export_collection exports all entities in a collection

The `export_collection` MCP tool SHALL accept a `collection_id` parameter (UUID) and return all entities belonging to that collection as a JSON array. Each element in the array SHALL contain the complete entity record. This tool exists to support data migration from the General butler to a new specialized butler.

#### Scenario: Exporting a collection with entities

WHEN `export_collection(collection_id=<uuid>)` is called and the collection contains 10 entities,
THEN the tool SHALL return a JSON array of 10 elements,
AND each element SHALL include the entity's `id`, `collection_id`, `title`, `data`, `tags`, `created_at`, and `updated_at`.

#### Scenario: Exporting an empty collection

WHEN `export_collection(collection_id=<uuid>)` is called and the collection contains no entities,
THEN the tool SHALL return an empty JSON array.

#### Scenario: Exporting a nonexistent collection

WHEN `export_collection(collection_id=<nonexistent-uuid>)` is called with a UUID that does not reference an existing collection,
THEN the tool SHALL return an error indicating the collection was not found.

---

### Requirement: export_by_tag exports all entities with a given tag

The `export_by_tag` MCP tool SHALL accept a `tag` parameter (string) and return all entities that contain the specified tag in their `tags` array as a JSON array. Each element SHALL contain the complete entity record. This tool exists to support data migration from the General butler to a new specialized butler.

#### Scenario: Exporting entities by tag

WHEN `export_by_tag(tag="italian")` is called and 4 entities have "italian" in their tags,
THEN the tool SHALL return a JSON array of 4 elements,
AND each element SHALL include the complete entity record.

#### Scenario: No entities match the tag

WHEN `export_by_tag(tag="nonexistent-tag")` is called and no entities have that tag,
THEN the tool SHALL return an empty JSON array,
AND it MUST NOT raise an error.

#### Scenario: Entities from multiple collections included

WHEN `export_by_tag(tag="favorite")` is called and matching entities belong to different collections (or have no collection),
THEN all matching entities SHALL be included in the result regardless of their collection membership.

---

### Requirement: No Modules

The General butler SHALL declare no modules in its `butler.toml`. It operates with core MCP tools plus its butler-specific entity and collection tools. No module loading or module tool registration SHALL occur.

#### Scenario: General butler starts with no modules

WHEN the General butler starts,
THEN the module list SHALL be empty,
AND only core tools (status, tick, trigger, state_*, schedule_*, sessions_*) and General-butler-specific tools (entity_*, collection_*, export_*) SHALL be registered on the MCP server.

---

### Requirement: No Scheduled Tasks

The General butler is purely reactive. It SHALL define no `[[butler.schedule]]` entries in its `butler.toml`. It responds only to direct MCP tool calls, not to cron-based triggers.

#### Scenario: Tick produces no work

WHEN the heartbeat butler calls `tick()` on the General butler,
THEN the tick handler SHALL return with no tasks executed,
AND no runtime sessions SHALL be spawned.

---

### Requirement: Schema Hint Is Advisory Only

The `schema_hint` field on collections SHALL serve as documentation for the expected shape of entities in that collection. It MUST NOT be used to validate entity data on `entity_create` or `entity_update`. Any valid JSONB data SHALL be accepted regardless of whether it conforms to the schema hint.

#### Scenario: Entity created with data not matching schema_hint

WHEN a collection has `schema_hint = {"type": "object", "properties": {"name": {"type": "string"}}}` and `entity_create(collection_id=<uuid>, data={"completely": "different", "structure": 42})` is called,
THEN the entity SHALL be created successfully,
AND no validation error SHALL be raised.

---

### Requirement: Freeform Data Storage

The General butler's entity storage SHALL accept any valid JSONB as the `data` field. There SHALL be no restrictions on the shape, depth, or contents of the data beyond PostgreSQL's native JSONB constraints. This freeform nature is the defining characteristic of the General butler — it stores anything that does not yet have a dedicated specialist butler.

#### Scenario: Storing a recipe

WHEN `entity_create(data={"type": "recipe", "name": "Pasta Carbonara", "ingredients": ["pasta", "eggs", "guanciale", "pecorino", "black pepper"]})` is called,
THEN the entity SHALL be stored with the data intact and retrievable.

#### Scenario: Storing a travel idea

WHEN `entity_create(data={"type": "travel_idea", "destination": "Kyoto", "notes": "cherry blossom season"})` is called,
THEN the entity SHALL be stored with the data intact and retrievable.

#### Scenario: Storing a book note

WHEN `entity_create(data={"type": "book_note", "title": "Thinking Fast and Slow", "highlights": ["System 1 vs System 2", "Anchoring bias"]})` is called,
THEN the entity SHALL be stored with the data intact and retrievable.

#### Scenario: Storing deeply nested data

WHEN `entity_create(data={"level1": {"level2": {"level3": {"level4": "deep value"}}}})` is called,
THEN the entity SHALL be stored with the full nested structure preserved.

---

### Requirement: Updated_at Timestamp Tracking

The `updated_at` column on the `entities` table SHALL always reflect the time of the most recent modification. It SHALL be set to the current timestamp on both insert and update.

#### Scenario: Timestamp set on entity creation

WHEN `entity_create(data={"x": 1})` is called,
THEN the created entity's `updated_at` SHALL be set to the current timestamp,
AND `updated_at` SHALL equal `created_at` for a newly created entity.

#### Scenario: Timestamp updated on entity modification

WHEN `entity_update(id, data={"x": 2})` is called on an existing entity,
THEN the entity's `updated_at` SHALL be updated to the current timestamp,
AND `updated_at` SHALL be greater than or equal to the previous `updated_at` value.

---

### Requirement: Collection Referential Integrity

The `collection_id` foreign key on the `entities` table SHALL enforce referential integrity. Entities MUST NOT reference a collection that does not exist. Deleting a collection that still has entities MUST be prevented or handled explicitly.

#### Scenario: Entity references a valid collection

WHEN `entity_create(collection_id=<existing-uuid>, data={"x": 1})` is called with a collection_id that references an existing collection,
THEN the entity SHALL be created successfully with the collection association.

#### Scenario: Entity references a nonexistent collection

WHEN `entity_create(collection_id=<nonexistent-uuid>, data={"x": 1})` is called with a collection_id that does not exist,
THEN the tool SHALL return an error,
AND no entity row SHALL be inserted.

#### Scenario: Deleting a collection with entities is prevented

WHEN a collection has associated entities and a delete operation is attempted on that collection,
THEN the operation SHALL fail with a referential integrity error,
AND the collection and its entities SHALL remain intact.
