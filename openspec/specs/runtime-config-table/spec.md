# Runtime Config Table

## Purpose
Defines the per-butler `runtime_config` DB table and the `RuntimeConfigAccessor` that provides TTL-cached read/seed access. The table is the runtime source of truth for operational tuning (`core_groups`, concurrency, queue depth), seeded once from `[butler.runtime_seed]` in `butler.toml` on first boot, and managed thereafter via the dashboard. NOTE: migration `core_073` moved `model`, `runtime_type`, `args`, and `session_timeout_s` OFF this table onto `public.model_catalog`; those fields are now resolved per complexity tier by `resolve_model()` and edited via the model-settings surface, not here.

## Requirements

### Requirement: Runtime config table exists per butler schema

Each butler schema SHALL contain a `runtime_config` table with typed columns for all operational config fields. The table holds exactly one row per butler, keyed by `butler_name`.

Source: RFC 0006 §Database Schema, RFC 0001 §Startup Phases
Scope: v1-mandatory

Schema (after migration `core_073` dropped `model`, `runtime_type`, `args`, and `session_timeout_s`):
- `butler_name text PRIMARY KEY`
- `core_groups text[]` (nullable; NULL means all groups enabled)
- `max_concurrent int NOT NULL DEFAULT 3`
- `max_queued int NOT NULL DEFAULT 10`
- `seeded_at timestamptz NOT NULL DEFAULT now()`
- `updated_at timestamptz NOT NULL DEFAULT now()`

The `RuntimeConfig` dataclass (`src/butlers/core/runtime_config.py`) mirrors these columns: `butler_name`, `core_groups`, `max_concurrent`, `max_queued`, `seeded_at`, `updated_at`.

#### Scenario: Table creation via migration
- **WHEN** the Alembic migration runs against a butler database
- **THEN** every butler schema SHALL have a `runtime_config` table with the columns above and appropriate defaults

#### Scenario: Table has at most one row
- **WHEN** the daemon seeds the table
- **THEN** there SHALL be exactly one row keyed by the butler's name

### Requirement: RuntimeConfigAccessor provides cached read access

A `RuntimeConfigAccessor` class SHALL provide TTL-cached read access to the `runtime_config` table. The default TTL is 30 seconds.

Source: RFC 0001 §Startup Phases (phase 10 — spawner creation)
Scope: v1-mandatory

#### Scenario: Cached read within TTL
- **WHEN** `accessor.get()` is called twice within 30 seconds
- **THEN** the second call SHALL return the cached result without a DB query

#### Scenario: Cache expiry triggers DB read
- **WHEN** `accessor.get()` is called after 30 seconds since the last DB read
- **THEN** the accessor SHALL query the DB and update the cache

#### Scenario: Accessor returns typed RuntimeConfig
- **WHEN** `accessor.get()` returns
- **THEN** the result SHALL be a `RuntimeConfig` dataclass with typed fields matching the table schema

#### Scenario: DB unreachable during get — return stale cache
- **WHEN** `accessor.get()` is called after TTL expiry but the DB query fails
- **THEN** the accessor SHALL return the last successfully cached value
- **AND** log a warning with the DB error

#### Scenario: DB unreachable during get — no prior cache
- **WHEN** `accessor.get()` is called with no prior cache and the DB query fails
- **THEN** the accessor SHALL raise the DB exception (fatal — no config available)

### Requirement: Seed-if-empty on first boot

The accessor SHALL provide a `seed_if_empty(seed: RuntimeSeedConfig)` method that inserts a row from the toml seed values only if no row exists.

Source: Doctrine Rule #5 (git seeds identity and operational defaults)
Scope: v1-mandatory

#### Scenario: First boot seeds from toml
- **WHEN** `seed_if_empty()` is called and the `runtime_config` table is empty
- **THEN** a row SHALL be inserted with values from the `RuntimeSeedConfig` and `seeded_at` set to now

#### Scenario: Subsequent boot uses existing row
- **WHEN** `seed_if_empty()` is called and the `runtime_config` table already has a row
- **THEN** the existing row SHALL be returned unchanged (toml seed values are ignored)

#### Scenario: Concurrent daemon starts race on seed
- **WHEN** two daemon instances call `seed_if_empty()` concurrently for the same butler
- **THEN** the INSERT SHALL use `ON CONFLICT DO NOTHING` so exactly one row is created
- **AND** both callers SHALL return the single row

#### Scenario: DB unreachable during seed
- **WHEN** `seed_if_empty()` cannot connect to the database
- **THEN** the daemon SHALL fail startup (fatal — cannot operate without runtime config)

### Requirement: Re-seeding by row deletion

Deleting the `runtime_config` row and restarting the daemon SHALL cause the toml seed values to be applied again.

Source: Design §Re-seeding mechanism
Scope: v1-mandatory

#### Scenario: Re-seed after row deletion
- **WHEN** the `runtime_config` row is deleted and the daemon restarts
- **THEN** `seed_if_empty()` SHALL insert a fresh row from the current toml seed values
