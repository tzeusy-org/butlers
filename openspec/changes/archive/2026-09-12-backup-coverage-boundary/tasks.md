## 1. Make the nightly dump complete

- [x] 1.1 Exclude the trusted-bootstrap control plane by name in
  `deploy/backup/pg_dump.sh`, with the completeness cost stated in the script
  header rather than left implicit.
- [x] 1.2 Keep the dump identity unchanged: no new login, no `pg_read_all_data`,
  no `BYPASSRLS`, no superuser. Refuse `--enable-row-security`, which would
  convert a loud permission error into a silently short dump.

## 2. Stop absence from being silent

- [x] 2.1 Fail loudly: a run that publishes no artifact says `FAILED` and why,
  and a dump that is undersized or does not decompress is never published.
- [x] 2.2 Remove the non-POSIX `set -o pipefail` under a `#!/bin/sh` shebang
  (the script died on its own safety setup under `dash`) and capture the dump's
  exit status explicitly instead.

## 3. Stop the exclusion set from rotting

- [x] 3.1 `tests/scripts/test_pg_dump_backup.py` pins the exclusion set against
  a real `init-db.sql`-bootstrapped, `core@head`-migrated database in both
  directions, and runs the real script in the real sidecar image to prove a
  usable artifact is produced.

## 4. Documentation

- [x] 4.1 State the coverage boundary and its cost in
  `docs/operations/backup-restore.md`.
