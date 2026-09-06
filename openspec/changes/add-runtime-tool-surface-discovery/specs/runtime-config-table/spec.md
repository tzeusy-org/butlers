## MODIFIED Requirements

### Requirement: Runtime config table exists per butler schema

Each butler schema SHALL contain a `runtime_config` table with typed columns for all operational config fields. The table holds exactly one row per butler, keyed by `butler_name`. Its columns SHALL be `butler_name`, nullable `core_groups`, `catalog_read_sensitivity` constrained to `normal|internal|confidential`, `max_concurrent`, `max_queued`, `tool_exposure_policy` constrained to `eager_filtered|auto`, `seeded_at`, and `updated_at`, with the defaults detailed below.
- Schema (after migration `core_073` dropped `model`, `runtime_type`, `args`, and `session_timeout_s`):
- `butler_name text PRIMARY KEY`
- `core_groups text[]` (nullable; NULL means all groups enabled)
- `catalog_read_sensitivity text NOT NULL DEFAULT 'normal'` (one of `normal`, `internal`, `confidential`)
- `max_concurrent int NOT NULL DEFAULT 3`
- `max_queued int NOT NULL DEFAULT 10`
- `tool_exposure_policy text NOT NULL DEFAULT 'eager_filtered'` constrained to `eager_filtered` or `auto`
- `seeded_at timestamptz NOT NULL DEFAULT now()`
- `updated_at timestamptz NOT NULL DEFAULT now()`
- The `RuntimeConfig` dataclass (`src/butlers/core/runtime_config.py`) mirrors these columns: `butler_name`, `core_groups`, `catalog_read_sensitivity`, `max_concurrent`, `max_queued`, `seeded_at`, `updated_at`.
- It SHALL additionally expose the typed `tool_exposure_policy` value so the per-invocation runtime plan reads the DB-backed operational authority.

ID: REQ-runtime-config-table-001
Source: RFC 0006 §Database Schema, RFC 0001 §Startup Phases
Scope: v1-mandatory

#### Scenario: Table creation via migration
- **WHEN** the Alembic migration runs against a butler database
- **THEN** every butler schema SHALL have a `runtime_config` table with the columns above and appropriate defaults

#### Scenario: Table has at most one row
- **WHEN** the daemon seeds the table
- **THEN** there SHALL be exactly one row keyed by the butler's name

#### Scenario: Existing row receives conservative policy

- **WHEN** the additive migration runs against an existing `runtime_config` row
- **THEN** `tool_exposure_policy` is populated as `eager_filtered`
- **AND** accepting or deploying the schema change does not activate native discovery
