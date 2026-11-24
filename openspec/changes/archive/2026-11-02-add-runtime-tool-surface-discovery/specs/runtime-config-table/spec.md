## MODIFIED Requirements

### Requirement: Runtime config table exists per butler schema

Each butler schema SHALL contain a `runtime_config` table with typed columns for all operational config fields. The table holds exactly one row per butler, keyed by `butler_name`.

ID: REQ-runtime-config-table-001
Source: RFC 0006 §Database Schema, RFC 0001 §Startup Phases
Scope: v1-mandatory

Schema (after migration `core_073` dropped `model`, `runtime_type`, `args`, and `session_timeout_s`):
- `butler_name text PRIMARY KEY`
- `core_groups text[]` (nullable; NULL means all groups enabled)
- `core_groups_narrowing_reason text` (nullable; non-empty text is required for a DB-owned narrowing to remain effective)
- `catalog_read_sensitivity text NOT NULL DEFAULT 'normal'` (one of `normal`, `internal`, `confidential`)
- `max_concurrent int NOT NULL DEFAULT 3`
- `max_queued int NOT NULL DEFAULT 10`
- `tool_exposure_policy text NOT NULL DEFAULT 'eager_filtered'` (constrained to `eager_filtered` or `auto`)
- `seeded_at timestamptz NOT NULL DEFAULT now()`
- `updated_at timestamptz NOT NULL DEFAULT now()`

The `RuntimeConfig` dataclass (`src/butlers/core/runtime_config.py`) mirrors these columns, including `core_groups_narrowing_reason` and the resolved core-group source/diff metadata used for operator visibility.

#### Scenario: Table creation via migration
- **WHEN** the Alembic migration runs against a butler database
- **THEN** every butler schema SHALL have a `runtime_config` table with the columns above and appropriate defaults

#### Scenario: Table has at most one row
- **WHEN** the daemon seeds the table
- **THEN** there SHALL be exactly one row keyed by the butler's name

#### Scenario: Existing rows receive the conservative policy

- **WHEN** migration `core_224` adds the exposure policy column to an existing runtime-config table
- **THEN** existing rows SHALL receive `eager_filtered`
- **AND** applying the migration SHALL NOT activate native tool search

## Source References

- Non-Negotiable Rule 3 (MCP inter-butler communication)
- Non-Negotiable Rule 5 (DB-persisted operational tuning)
- RFC 0027 (catalog and exposure contract; unlanded runtime work succeeds to RFC 0038)
