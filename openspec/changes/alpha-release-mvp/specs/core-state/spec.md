# State Store

## Purpose
Provides a per-butler key-value store backed by PostgreSQL JSONB, supporting get/set/delete/list operations with version tracking for optimistic concurrency control. Each butler's state is isolated within its own database schema.

## ADDED Requirements

### Requirement: State Get
Retrieve a JSONB value by key from the `state` table. Returns `None` if the key does not exist. Handles both decoded Python objects and raw JSON strings from asyncpg.

#### Scenario: Key exists
- **WHEN** `state_get(pool, key)` is called with an existing key
- **THEN** it returns the deserialized JSONB value (any JSON-serializable type)

#### Scenario: Key does not exist
- **WHEN** `state_get(pool, key)` is called with a non-existent key
- **THEN** it returns `None`

### Requirement: State Set (Upsert)
Upsert a key with any JSON-serializable value. If the key exists, its value, `updated_at` timestamp, and `version` are updated (version incremented). If the key does not exist, a new row is inserted with `version=1`. Returns the new version number.

#### Scenario: Insert new key
- **WHEN** `state_set(pool, key, value)` is called for a non-existent key
- **THEN** a new row is inserted with `version=1` and `updated_at=now()`
- **AND** the returned version is `1`

#### Scenario: Update existing key
- **WHEN** `state_set(pool, key, value)` is called for an existing key
- **THEN** the row's value, `updated_at`, and `version` (incremented by 1) are updated
- **AND** the returned version reflects the increment

### Requirement: State Compare-and-Set (CAS)
Conditionally update a key only if its current version matches the expected version. Provides safe concurrent KV writes.

#### Scenario: Version matches
- **WHEN** `state_compare_and_set(pool, key, expected_version, new_value)` is called and the stored version equals `expected_version`
- **THEN** the value is updated, version is incremented, and the new version is returned

#### Scenario: Version mismatch
- **WHEN** `state_compare_and_set(pool, key, expected_version, new_value)` is called and the stored version does not match `expected_version`
- **THEN** a `CASConflictError` is raised with the key, expected version, and actual version

#### Scenario: Key does not exist
- **WHEN** `state_compare_and_set(pool, key, expected_version, new_value)` is called for a non-existent key
- **THEN** a `CASConflictError` is raised with `actual_version=None`

### Requirement: State Delete
Delete a key from the state store. No-op if the key does not exist.

#### Scenario: Delete existing key
- **WHEN** `state_delete(pool, key)` is called for an existing key
- **THEN** the row is removed from the `state` table

#### Scenario: Delete non-existent key
- **WHEN** `state_delete(pool, key)` is called for a non-existent key
- **THEN** no error is raised (no-op)

### Requirement: State List
Return state entries optionally filtered by key prefix. Supports two modes: keys-only (default) and full key-value pairs.

#### Scenario: List all keys
- **WHEN** `state_list(pool)` is called without a prefix
- **THEN** it returns a list of all key strings ordered alphabetically

#### Scenario: List keys by prefix
- **WHEN** `state_list(pool, prefix="some_prefix")` is called
- **THEN** it returns only keys starting with `"some_prefix"` (via SQL `LIKE prefix%`)

#### Scenario: List with values
- **WHEN** `state_list(pool, keys_only=False)` is called
- **THEN** it returns a list of `{"key": ..., "value": ...}` dicts

### Requirement: Per-Butler Schema Isolation
Each butler operates against its own PostgreSQL schema. The `state` table is a core table required in every butler's database. Direct cross-butler schema access is prohibited.

#### Scenario: Schema isolation
- **WHEN** two butlers operate on the same PostgreSQL instance
- **THEN** each butler's state table is in its own schema
- **AND** state operations for butler A cannot see or modify butler B's data
