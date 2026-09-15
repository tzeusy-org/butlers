## Why

`deploy/backup/pg_dump.sh` dumps as the shared `POSTGRES_USER` migration/runtime
login, which `scripts/init-db.sql` deliberately fences away from the
trusted-bootstrap control plane. `pg_dump` locks every relation in scope before
writing a byte, so a single unreadable relation aborts the run — and because the
script cleans up rather than publishes a failed dump, the run leaves *no file at
all*. Reproduced against a real bootstrapped database (bu-e1410): the nightly
backup fails with `permission denied for schema restore_drill_executor_admin`
and publishes nothing.

The shipped "Backup And Restore Verification Path" requirement says a backup can
be produced. It says nothing about what a backup must contain, so an exclusion
set added to make the command exit `0` would satisfy it while silently shrinking
the backup — the same class of failure as no backup at all.

## What Changes

- Amend the Backup And Restore Verification Path requirement so producing a
  backup is a stated coverage claim, not merely a zero exit status: the backup
  SHALL cover the application data plane, its exclusions SHALL be limited to
  objects the bootstrap reconstructs, and those exclusions SHALL be documented
  and machine-checked in both directions against a real bootstrapped database.
- No change to the restore drill, the executor boundary, or any role privilege.
  The dump identity is unchanged: the fix hands the dump role nothing.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-hardening` — Backup And Restore Verification Path gains an
  explicit coverage boundary and a drift-detection obligation.
