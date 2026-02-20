# One-DB Data Migration Runbook

Owner issue: `butlers-1003.4`  
Last updated: 2026-02-20

This runbook is the executable procedure for backfill, parity verification, and
rollback rehearsal when migrating from legacy multi-DB stores to one-DB multi-schema.

## 1. Prerequisites

- One-DB target schemas already bootstrapped (`butlers-1003.2`).
- ACL/runtime wiring validated in staging (`butlers-1003.3`, `butlers-1003.5`).
- DSN env vars exported for target and each source DB:
  - `BUTLERS_DATABASE_URL` (target)
  - `BUTLER_<NAME>_DATABASE_URL` for each butler source
  - `BUTLER_SHARED_DATABASE_URL` for shared source

## 2. Staging dry-run (required)

Run a no-write readiness check and archive reports:

```bash
python scripts/one_db_data_migration.py plan \
  --target-env BUTLERS_DATABASE_URL \
  --source-env general=BUTLER_GENERAL_DATABASE_URL \
  --source-env relationship=BUTLER_RELATIONSHIP_DATABASE_URL \
  --source-env health=BUTLER_HEALTH_DATABASE_URL \
  --source-env switchboard=BUTLER_SWITCHBOARD_DATABASE_URL \
  --source-env messenger=BUTLER_MESSENGER_DATABASE_URL \
  --shared-source-env BUTLER_SHARED_DATABASE_URL \
  --report-path .tmp/migration/plan.json

python scripts/one_db_data_migration.py migrate \
  --target-env BUTLERS_DATABASE_URL \
  --source-env general=BUTLER_GENERAL_DATABASE_URL \
  --source-env relationship=BUTLER_RELATIONSHIP_DATABASE_URL \
  --source-env health=BUTLER_HEALTH_DATABASE_URL \
  --source-env switchboard=BUTLER_SWITCHBOARD_DATABASE_URL \
  --source-env messenger=BUTLER_MESSENGER_DATABASE_URL \
  --shared-source-env BUTLER_SHARED_DATABASE_URL \
  --dry-run \
  --report-path .tmp/migration/migrate-dry-run.json
```

Expected result:
- Exit code `0`
- No `status=error` rows in reports

## 3. Execute backfill + parity checks

```bash
python scripts/one_db_data_migration.py run \
  --target-env BUTLERS_DATABASE_URL \
  --source-env general=BUTLER_GENERAL_DATABASE_URL \
  --source-env relationship=BUTLER_RELATIONSHIP_DATABASE_URL \
  --source-env health=BUTLER_HEALTH_DATABASE_URL \
  --source-env switchboard=BUTLER_SWITCHBOARD_DATABASE_URL \
  --source-env messenger=BUTLER_MESSENGER_DATABASE_URL \
  --shared-source-env BUTLER_SHARED_DATABASE_URL \
  --replace-target \
  --report-path .tmp/migration/run.json
```

Default required table coverage:
- Per-butler schema: `state`, `scheduled_tasks`, `sessions`, `route_inbox`
- Shared schema: `butler_secrets`

Failure behavior:
- Any count/checksum mismatch returns non-zero and prints `PARITY CHECK FAILED`.
- Cutover is blocked until a clean rerun report is produced.

## 4. Rollback rehearsal and failed-cutover rollback

If migration validation fails, clear target migrated data and reattempt:

```bash
python scripts/one_db_data_migration.py rollback \
  --target-env BUTLERS_DATABASE_URL \
  --source-env general=BUTLER_GENERAL_DATABASE_URL \
  --source-env relationship=BUTLER_RELATIONSHIP_DATABASE_URL \
  --source-env health=BUTLER_HEALTH_DATABASE_URL \
  --source-env switchboard=BUTLER_SWITCHBOARD_DATABASE_URL \
  --source-env messenger=BUTLER_MESSENGER_DATABASE_URL \
  --shared-source-env BUTLER_SHARED_DATABASE_URL \
  --confirm-rollback ROLLBACK \
  --report-path .tmp/migration/rollback.json
```

Validation:
- Rollback report shows `status=ok` and `target_count_after=0` for affected tables.
- Source DBs remain unchanged and authoritative.

## 5. Production cutover signoff criteria

Before production cutover, record all of the following:

- `plan`, `migrate --dry-run`, and final `run` reports archived.
- Latest `run` report has:
  - `status=ok`
  - `summary.tables_failed=0`
  - no `mismatch` rows
- Rollback rehearsal report archived and clean.
- Operator signoff confirms no unresolved parity failures and source snapshots are retained.
