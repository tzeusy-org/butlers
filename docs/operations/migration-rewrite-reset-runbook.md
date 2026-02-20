# Migration Rewrite Destructive Reset Runbook

Owner issue: `butlers-1013.4`  
Last updated: 2026-02-21

This runbook is the operator procedure for destructive reset rollout of rewritten
schema-scoped migrations in local/dev/staging.

## 1. Safety and Scope

This workflow is destructive by design.

- Never run it against production without an explicit maintenance plan and backups.
- Default reset scope is `managed-schemas` (safer than dropping the full database).
- Full database reset is supported for ephemeral/staging rehearsals.
- All destructive execution requires explicit confirmation:
  - `--confirm-destructive-reset RESET`

## 2. Prerequisites

- `BUTLERS_DATABASE_URL` exported and pointing to the target one-db instance.
- Python environment available via `uv run`.
- Rewritten migrations already present in this checkout:
  - core baseline `core_001`
  - memory baseline `mem_001`

Optional safety override:
- If target DB name contains `prod`, command will fail by default.
- To bypass after manual verification, pass `--allow-production-db-name`.

## 3. Precheck (Required)

Run a dry-run first and archive the report:

```bash
uv run python scripts/one_db_migration_reset_workflow.py reset \
  --scope managed-schemas \
  --dry-run \
  --report-path .tmp/migration-rewrite/reset-plan.json
```

Expected result:
- exit code `0`
- report status `ok`
- planned target schemas listed for reset

## 4. Destructive Reset

Choose one scope.

### 4.1 Managed-schemas reset (recommended for dev/staging)

Drops and recreates:
- `shared`
- `general`
- `health`
- `messenger`
- `relationship`
- `switchboard`

```bash
uv run python scripts/one_db_migration_reset_workflow.py reset \
  --scope managed-schemas \
  --confirm-destructive-reset RESET \
  --report-path .tmp/migration-rewrite/reset-managed-schemas.json
```

### 4.2 Full database reset (ephemeral environments only)

Drops and recreates the database from `BUTLERS_DATABASE_URL`.

```bash
uv run python scripts/one_db_migration_reset_workflow.py reset \
  --scope database \
  --confirm-destructive-reset RESET \
  --report-path .tmp/migration-rewrite/reset-database.json
```

## 5. Replay Rewritten Migrations

Run schema-scoped baseline migrations:

- Core chain (`core`) on all butler schemas.
- Memory chain (`memory`) on memory-enabled schemas:
  - `general`, `health`, `relationship`, `switchboard`

```bash
uv run python scripts/one_db_migration_reset_workflow.py migrate \
  --report-path .tmp/migration-rewrite/migrate.json
```

## 6. Post-Migration SQL Validation (Required)

Validate expected schema/table/revision matrix and archive evidence:

```bash
uv run python scripts/one_db_migration_reset_workflow.py validate \
  --report-path .tmp/migration-rewrite/validate.json
```

Validation checks include:
- Managed schema existence (`shared` + all butler schemas)
- Core tables in each butler schema:
  - `state`, `scheduled_tasks`, `sessions`, `route_inbox`
- Memory tables in memory-enabled schemas:
  - `episodes`, `facts`, `rules`, `memory_links`
- Alembic revision presence:
  - `core_001` in each butler schema `alembic_version`
  - `mem_001` in each memory-enabled schema `alembic_version`

Expected result:
- exit code `0`
- `status=ok`
- no missing schema/table/revision entries

If validation fails:
- treat as rollout failure
- do not proceed to daemon startup/cutover
- retain the failed report artifact for diagnosis

## 7. Single-Command Rehearsal

For a fully automated reset+migrate+validate run:

```bash
uv run python scripts/one_db_migration_reset_workflow.py run \
  --scope managed-schemas \
  --confirm-destructive-reset RESET \
  --report-path .tmp/migration-rewrite/run.json
```

Use `--dry-run` on `run` to preview reset only (no migrate/validate execution).

## 8. Environment Profiles

### 8.1 Local

- Prefer `--scope managed-schemas` to keep DB-level grants/roles intact.
- Use the `run` command for quick repetition while iterating on migrations.

### 8.2 Shared Dev

- Always archive `reset`, `migrate`, and `validate` reports under `.tmp/`.
- Coordinate reset windows to avoid interrupting other engineers.

### 8.3 Staging

- Prefer database reset for clean rehearsal parity with release procedure.
- Store report artifacts with deployment records.
- Treat any validation mismatch as release-blocking until resolved.
