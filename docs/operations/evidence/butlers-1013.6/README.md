# butlers-1013.6 verification evidence

Date: 2026-02-20

## Scope

- Execute destructive reset + migration replay + SQL validation using the
  workflow from `scripts/one_db_migration_reset_workflow.py` (from `butlers-1013.4`).
- Verify schema matrix coverage using `tests/config/test_schema_matrix_migrations.py`
  (from `butlers-1013.5`).
- Verify daemon startup migration wiring with a targeted startup/migration test.

## Environment

- Worktree: `/home/tze/GitHub/butlers/.worktrees/parallel-agents/butlers-1013.6`
- `BUTLERS_DATABASE_URL` was not preset in shell; verification used an isolated
  ephemeral PostgreSQL target (`pgvector/pgvector:pg17`) for destructive reset.
- Ephemeral target metadata: `ephemeral-target.json`

## Reset + rewritten migration workflow evidence

Commands run:

```bash
uv run python scripts/one_db_migration_reset_workflow.py reset \
  --scope managed-schemas \
  --dry-run \
  --report-path docs/operations/evidence/butlers-1013.6/reset-plan.json

uv run python scripts/one_db_migration_reset_workflow.py reset \
  --scope managed-schemas \
  --confirm-destructive-reset RESET \
  --report-path docs/operations/evidence/butlers-1013.6/reset-managed-schemas.json

uv run python scripts/one_db_migration_reset_workflow.py migrate \
  --report-path docs/operations/evidence/butlers-1013.6/migrate.json

uv run python scripts/one_db_migration_reset_workflow.py validate \
  --report-path docs/operations/evidence/butlers-1013.6/validate.json
```

Outcomes:

- Reset dry-run: `status=ok` (`reset-plan.json`)
- Destructive managed-schema reset: `status=ok` (`reset-managed-schemas.json`)
- Rewritten migration replay (`core_001` + `mem_001`): `status=ok` (`migrate.json`)
- SQL validation matrix: `status=ok` with:
  - `schemas_checked=6`
  - `tables_checked=36`
  - `revisions_checked=9`
  - `missing_schemas=0`
  - `missing_tables=0`
  - `missing_revisions=0`

Stdout captures are stored in:

- `reset-plan.stdout.log`
- `reset-managed-schemas.stdout.log`
- `migrate.stdout.log`
- `validate.stdout.log`

## Targeted verification tests

Commands run:

```bash
uv run pytest tests/config/test_schema_matrix_migrations.py::test_one_db_schema_table_matrix_for_core_and_enabled_modules -q
uv run pytest tests/daemon/test_butler_migrations.py::TestButlerSpecificMigrationInDaemon::test_one_db_schema_passed_to_all_migration_runs -q
```

Outcomes:

- Schema matrix test: `1 passed` (`pytest-schema-matrix.log`)
- Daemon startup migration wiring test: `1 passed`
  (`pytest-daemon-startup-migrations.log`)
