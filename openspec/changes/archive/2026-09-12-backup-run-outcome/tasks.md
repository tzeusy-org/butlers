## 1. Give a failed run a signal of its own

- [x] 1.1 `deploy/backup/pg_dump.sh` rewrites `BACKUP_DIR/last_run.json` on
  every run from its EXIT trap, with a fixed reason vocabulary (`ok`,
  `pg_dump_failed`, `artifact_undersize`, `artifact_corrupt`,
  `unexpected_error`). An unenumerated abort records `unexpected_error`, never
  a success.
- [x] 1.2 The receipt is written to a temp file and `mv`'d into place, so a
  dashboard poll never reads a half-written receipt, and a receipt that cannot
  be written warns without changing the run's exit status.
- [x] 1.3 Keep the script POSIX under `#!/bin/sh` (no `set -o pipefail`), and
  keep the receipt out of the `butlers_*.sql.gz` glob and the prune sweep.

## 2. Make the dashboard able to see it

- [x] 2.1 `GET /api/system/backups` returns `last_run`
  (`result`/`finished_at`/`exit_code`/`reason`).
- [x] 2.2 A missing, unreadable, or malformed receipt degrades to
  `result="unknown"`; a `reason` outside the fixed vocabulary is reported as
  unrecognized rather than rendered verbatim off a mounted volume.

## 3. Make it reach an operator

- [x] 3.1 `InfraStateSource` raises `BackupRunFailed` when the most recent run
  failed, checked before the staleness checks it precedes, and never invents a
  finding from an absent receipt.

## 4. Prove the previously invisible case

- [x] 4.1 `tests/api/test_system.py` covers fresh artifact + failed run, with
  every artifact-derived field asserted healthy in the same test.
- [x] 4.2 `tests/scripts/test_pg_dump_run_sentinel.py` runs the real script
  (DB-free, stubbed binaries) through each failure mode, proves the surviving
  artifact is byte-identical to the one the successful run published, and pins
  the script's receipt against the endpoint's own reader.
- [x] 4.3 `tests/core/qa/test_infra_state.py` covers the same combination at
  the discovery-source boundary.

## 5. Documentation

- [x] 5.1 `docs/operations/backup-restore.md` documents the receipt, its
  vocabulary, and what "unknown" means.
