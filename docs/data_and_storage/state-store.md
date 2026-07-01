# State Store

> **Purpose:** Document the KV JSONB state store that butlers use to persist data between sessions.
> **Audience:** Module developers, butler authors.
> **Prerequisites:** [Schema Topology](schema-topology.md).

## Overview

![State Store Design](./state-store-design.svg)

Every butler has a `state` table in its schema that provides a key-value store backed by PostgreSQL JSONB. This is the primary mechanism for butlers to remember things between ephemeral LLM CLI sessions. The state store is intentionally simple: string keys mapping to arbitrary JSON-serializable values, with built-in versioning for safe concurrent writes.

## Table Schema

```sql
CREATE TABLE state (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    version    INTEGER NOT NULL DEFAULT 1
);
```

Each row tracks:
- **`key`** -- A unique string identifier (e.g., `"contacts::sync::google"`, `"scheduler::last_tick"`)
- **`value`** -- Any JSON-serializable data stored as JSONB
- **`updated_at`** -- Timestamp of the last write
- **`version`** -- Monotonically increasing integer, incremented on each update

## API

The state store API is defined in `src/butlers/core/state.py` and provides five async functions that operate on an asyncpg pool:

### `state_get(pool, key) -> Any | None`

Returns the JSONB value for a key, or `None` if the key does not exist. Handles double-encoded JSONB values gracefully (a safety net for historical encoding issues).

### `state_set(pool, key, value) -> int`

Upserts a key with a JSON-serializable value. If the key exists, its value, `updated_at`, and `version` are updated. If new, version starts at 1. Returns the new version number.

Uses PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` for atomic upsert:

```sql
INSERT INTO state (key, value, updated_at, version)
VALUES ($1, $2::jsonb, now(), 1)
ON CONFLICT (key) DO UPDATE
    SET value = EXCLUDED.value,
        updated_at = now(),
        version = state.version + 1
RETURNING version
```

### `state_compare_and_set(pool, key, expected_version, new_value) -> int`

Conditionally updates a key only if the current version matches `expected_version`. This provides **optimistic concurrency control** for safe concurrent writes.

If the version does not match, raises `CASConflictError` with details about the expected vs actual version. This pattern prevents lost updates when two sessions read the same key, modify it, and write back -- exactly one will succeed.

```python
try:
    new_ver = await state_compare_and_set(pool, "my_key", expected_version=3, new_value=data)
except CASConflictError as e:
    # e.expected_version, e.actual_version available for retry logic
    pass
```

### `state_delete(pool, key) -> None`

Deletes a key from the state store. No-op if the key does not exist.

### `state_list(pool, prefix=None, keys_only=True) -> list`

Lists state entries, optionally filtered by key prefix (SQL `LIKE prefix%`).

- `keys_only=True` (default): Returns a list of key strings, sorted alphabetically.
- `keys_only=False`: Returns a list of `{"key": ..., "value": ...}` dicts.

## Key Naming Conventions

State keys follow a namespaced convention using `::` separators:

- `contacts::sync::google` -- Google contacts sync state
- `contacts::sync::telegram` -- Telegram contacts sync state
- `scheduler::last_tick` -- Last scheduler tick timestamp
- `module::<name>::<key>` -- Module-specific state

## JSONB Decoding

The `decode_jsonb()` helper handles a subtle asyncpg behavior: JSONB columns are returned as Python strings (text representation) when no custom codec is registered. The function applies `json.loads()` and detects double-encoded values (a JSON string containing JSON text) by applying a second decode pass when needed.

## Concurrency Model

The state store is designed for concurrent access from multiple asyncio tasks within a butler daemon:

- **Simple writes** (`state_set`): Last-writer-wins semantics. Safe when only one writer per key is expected.
- **Coordinated writes** (`state_compare_and_set`): Optimistic locking via version numbers. Use this when multiple writers may contend on the same key.

Since each butler has its own schema and pool, there is no cross-butler contention on the state table.

## Exposed as MCP Tools

The state store is exposed to LLM CLI instances through core MCP tools (`state_get`, `state_set`, `state_list`, `state_delete`), allowing the AI runtime to persist and retrieve data across sessions.

## Verification

To confirm the state store described here matches the running system:

```bash
# 1. State table exists in each butler's schema
psql -h localhost -U butlers -d butlers -c \
  "SELECT key, updated_at, version FROM general.state ORDER BY updated_at DESC LIMIT 5;"
# Expected: rows with namespaced keys (e.g., "contacts::sync::google", "scheduler::last_tick")

# 2. Version increments on each write
# Record the current version for a key, then write to it via the MCP state_set tool,
# then re-query:
psql -h localhost -U butlers -d butlers -c \
  "SELECT key, version FROM general.state WHERE key = 'scheduler::last_tick';"
# Expected: version number increases by 1 on each write

# 3. state_list MCP tool returns keys accessible to the LLM
# Trigger a butler session that calls state_list() and check its output.
# Expected: returns key strings (with keys_only=True), matching what's in the DB above

# 4. CAS conflict detection: state_compare_and_set rejects stale version
# In Python with a running pool, call state_compare_and_set with the wrong version:
#   from butlers.core.state import state_compare_and_set, CASConflictError
#   try:
#       await state_compare_and_set(pool, "scheduler::last_tick", expected_version=0, new_value={})
#   except CASConflictError as e:
#       print(f"Rejected: expected {e.expected_version}, actual {e.actual_version}")
# Expected: CASConflictError raised with correct version values

# 5. State is scoped per-butler (general's state is not visible to health)
psql -h localhost -U butlers -d butlers -c \
  "SELECT key FROM health.state ORDER BY key LIMIT 5;"
# Expected: different keys from general.state, no cross-butler leakage
```

## Related Pages

- [Schema Topology](schema-topology.md) -- Where the state table lives
- [Migration Patterns](migration-patterns.md) -- How the state table is created
