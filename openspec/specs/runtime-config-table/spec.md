# Runtime Config Table

## Purpose
Defines the per-butler `runtime_config` DB table and the `RuntimeConfigAccessor` that provides TTL-cached read/seed access. The table is the runtime source of truth for operational tuning such as catalog read authority, concurrency, and queue depth. Git-owned `[butler.runtime_seed].core_groups` is authoritative for capability presence; the DB may only narrow that declaration when it carries an explicit `core_groups_narrowing_reason`. NOTE: migration `core_073` moved `model`, `runtime_type`, `args`, and `session_timeout_s` OFF this table onto `public.model_catalog`; those fields are now resolved per complexity tier by `resolve_model()` and edited via the model-settings surface, not here.

## Requirements

### Requirement: Runtime config table exists per butler schema

Each butler schema SHALL contain a `runtime_config` table with typed columns for all operational config fields. The table holds exactly one row per butler, keyed by `butler_name`.

Source: RFC 0006 §Database Schema, RFC 0001 §Startup Phases
Scope: v1-mandatory

Schema (after migration `core_073` dropped `model`, `runtime_type`, `args`, and `session_timeout_s`):
- `butler_name text PRIMARY KEY`
- `core_groups text[]` (nullable; NULL means all groups enabled)
- `core_groups_narrowing_reason text` (nullable; non-empty text is required for a DB-owned narrowing to remain effective)
- `catalog_read_sensitivity text NOT NULL DEFAULT 'normal'` (one of `normal`, `internal`, `confidential`)
- `max_concurrent int NOT NULL DEFAULT 3`
- `max_queued int NOT NULL DEFAULT 10`
- `seeded_at timestamptz NOT NULL DEFAULT now()`
- `updated_at timestamptz NOT NULL DEFAULT now()`

The `RuntimeConfig` dataclass (`src/butlers/core/runtime_config.py`) mirrors these columns, including `core_groups_narrowing_reason` and the resolved core-group source/diff metadata used for operator visibility.

#### Scenario: Table creation via migration
- **WHEN** the Alembic migration runs against a butler database
- **THEN** every butler schema SHALL have a `runtime_config` table with the columns above and appropriate defaults

#### Scenario: Table has at most one row
- **WHEN** the daemon seeds the table
- **THEN** there SHALL be exactly one row keyed by the butler's name

### Requirement: RuntimeConfigAccessor provides cached read access

A `RuntimeConfigAccessor` class SHALL provide TTL-cached read access to the `runtime_config` table. The default TTL is 30 seconds.

Source: RFC 0001 §Startup Phases (phase 9 — runtime config resolution)
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

The accessor SHALL provide a `seed_if_empty(seed: RuntimeSeedConfig)` method that inserts a row from the toml seed values only if no row exists. After that race-safe seed, the same call SHALL reconcile an existing unreasoned `core_groups` value to the current Git declaration. Other runtime-config fields remain DB-owned and SHALL NOT be overwritten by this reconciliation.

Source: Doctrine Rule #5 (git seeds identity and operational defaults)
Scope: v1-mandatory

#### Scenario: First boot seeds from toml
- **WHEN** `seed_if_empty()` is called and the `runtime_config` table is empty
- **THEN** a row SHALL be inserted with values from the `RuntimeSeedConfig` and `seeded_at` set to now

#### Scenario: Subsequent boot uses existing row
- **WHEN** `seed_if_empty()` is called and the `runtime_config` table already has a row
- **THEN** the existing row SHALL be returned unchanged (toml seed values are ignored) for DB-owned operational fields
- **AND** `core_groups` SHALL follow the reconciliation scenarios below

#### Scenario: Unreasoned stale groups reconcile to Git
- **WHEN** `seed_if_empty()` is called, the row's `core_groups` differs from `[butler.runtime_seed].core_groups`, and `core_groups_narrowing_reason` is null or blank
- **THEN** the row's `core_groups` SHALL be transactionally reconciled to the Git declaration
- **AND** the effective source SHALL be reported as Git without changing unrelated runtime-config fields
- **AND** one `core_groups_reconciled` audit record SHALL describe the non-empty before/after diff without exposing sensitive runtime data

#### Scenario: Explicit narrowing remains effective
- **WHEN** the stored `core_groups` is a subset of the Git declaration and `core_groups_narrowing_reason` is non-empty
- **THEN** the stored subset SHALL remain the effective core-group allowlist
- **AND** the reason and DB source SHALL be visible through the runtime-config read surface

#### Scenario: Repeated boot is idempotent
- **WHEN** one or more daemons reconcile the same butler and Git declaration repeatedly or concurrently
- **THEN** they SHALL converge on the same core-group row
- **AND** the audit identity keyed by butler and TOML digest SHALL prevent duplicate reconciliation records

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
