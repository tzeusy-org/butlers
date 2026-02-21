# Scripts

Utility scripts for repository maintenance and fixes.

## dev.sh

Bootstraps the full local Butlers development stack in `tmux` (dashboard, frontend, connectors, backend, OAuth gate, and postgres preflight).

### Usage

```bash
# Preferred compatibility entrypoint
./dev.sh

# Direct script path
./scripts/dev.sh
```

## clear-processes.sh

Kills processes currently listening on the expected local dev ports.

Default ports:
- `POSTGRES_PORT` (default `54320`)
- `FRONTEND_PORT` (default `40173`)
- `DASHBOARD_PORT` (default `40200`)

You can override with `EXPECTED_PORTS` (comma/space separated), for example:

```bash
EXPECTED_PORTS="54320,40173,40200" ./scripts/clear-processes.sh
```

## fix_beads_dependency_timestamps.py

Detects and fixes dependency records with zero timestamps (`created_at="0001-01-01T00:00:00Z"`) in `.beads/issues.jsonl`.

### Background

Due to a bug in the `bd` CLI when running in no-daemon worktree flows, dependency records created via `bd dep add` may have their `created_at` timestamp set to the zero timestamp instead of a real timestamp. This breaks downstream auditing and timeline reasoning.

### Usage

```bash
# Dry-run mode (shows what would be fixed without making changes)
python scripts/fix_beads_dependency_timestamps.py --dry-run

# Apply fixes
python scripts/fix_beads_dependency_timestamps.py

# Specify custom path
python scripts/fix_beads_dependency_timestamps.py --jsonl-path /path/to/issues.jsonl
```

### How it works

1. Scans all issues in `issues.jsonl`
2. Finds dependency records with `created_at="0001-01-01T00:00:00Z"`
3. Replaces the zero timestamp with the parent issue's `updated_at` timestamp (or current time as fallback)
4. Writes the corrected records back to the file

### Example output

```
Fixing issue butlers-2bq.7:
  - Dependency butlers-2bq.7 -> butlers-886 (type: blocks): 0001-01-01T00:00:00Z -> 2026-02-15T02:15:24.686020053+08:00

Summary: scanned 746 issues, modified 9 issues, fixed 9 dependencies
```

## one_db_data_migration.py

Backfill and parity utility for the one-DB multi-schema migration (`butlers-1003.4`).

### What it provides

- Deterministic source -> target data copy with upsert semantics (`migrate`/`run`)
- Staged dry-run support (`plan` and `migrate --dry-run`)
- Strict parity verification over required tables (`verify`)
- JSON report artifacts for migration records (`--report-path`)
- Rollback helper that truncates migrated target tables (`rollback`)

### Required DSN environment variables

- Target one-DB DSN (default env key): `BUTLERS_DATABASE_URL`
- Source per-butler DSNs (provided via `--source-env`, e.g. `general=BUTLER_GENERAL_DATABASE_URL`)
- Shared source DSN (default env key): `BUTLER_SHARED_DATABASE_URL`

### Example commands

```bash
# 1) Staging dry-run (no writes)
python scripts/one_db_data_migration.py plan \
  --target-env BUTLERS_DATABASE_URL \
  --source-env general=BUTLER_GENERAL_DATABASE_URL \
  --source-env relationship=BUTLER_RELATIONSHIP_DATABASE_URL \
  --shared-source-env BUTLER_SHARED_DATABASE_URL \
  --report-path .tmp/migration/plan.json

python scripts/one_db_data_migration.py migrate \
  --target-env BUTLERS_DATABASE_URL \
  --source-env general=BUTLER_GENERAL_DATABASE_URL \
  --source-env relationship=BUTLER_RELATIONSHIP_DATABASE_URL \
  --shared-source-env BUTLER_SHARED_DATABASE_URL \
  --dry-run \
  --report-path .tmp/migration/migrate-dry-run.json

# 2) Execute backfill + parity checks
python scripts/one_db_data_migration.py run \
  --target-env BUTLERS_DATABASE_URL \
  --source-env general=BUTLER_GENERAL_DATABASE_URL \
  --source-env relationship=BUTLER_RELATIONSHIP_DATABASE_URL \
  --shared-source-env BUTLER_SHARED_DATABASE_URL \
  --replace-target \
  --report-path .tmp/migration/run.json

# 3) If cutover attempt fails, clear migrated target data
python scripts/one_db_data_migration.py rollback \
  --target-env BUTLERS_DATABASE_URL \
  --source-env general=BUTLER_GENERAL_DATABASE_URL \
  --source-env relationship=BUTLER_RELATIONSHIP_DATABASE_URL \
  --shared-source-env BUTLER_SHARED_DATABASE_URL \
  --confirm-rollback ROLLBACK \
  --report-path .tmp/migration/rollback.json
```

## one_db_migration_reset_workflow.py

Destructive reset and validation workflow for migration rewrite rollout (`butlers-1013.4`).

### What it provides

- Explicit destructive reset steps with confirmation guard (`RESET`)
- Reset scope options:
  - `managed-schemas` (drop/recreate `shared` + butler schemas)
  - `database` (drop/recreate full target DB)
- Replays rewritten baseline migrations per schema (`core_001`, `mem_001`)
- SQL-based schema/table/revision matrix validation with JSON artifacts

### Example commands

```bash
# 1) Precheck destructive reset plan
python scripts/one_db_migration_reset_workflow.py reset \
  --scope managed-schemas \
  --dry-run \
  --report-path .tmp/migration-rewrite/reset-plan.json

# 2) Execute destructive reset
python scripts/one_db_migration_reset_workflow.py reset \
  --scope managed-schemas \
  --confirm-destructive-reset RESET \
  --report-path .tmp/migration-rewrite/reset.json

# 3) Replay rewritten migrations
python scripts/one_db_migration_reset_workflow.py migrate \
  --report-path .tmp/migration-rewrite/migrate.json

# 4) Validate schema/table/revision matrix
python scripts/one_db_migration_reset_workflow.py validate \
  --report-path .tmp/migration-rewrite/validate.json

# Optional end-to-end command
python scripts/one_db_migration_reset_workflow.py run \
  --scope managed-schemas \
  --confirm-destructive-reset RESET \
  --report-path .tmp/migration-rewrite/run.json
```
