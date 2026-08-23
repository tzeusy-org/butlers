## Why

`deploy/backup/pg_dump.sh` refuses to publish a bad dump: on a non-zero
`pg_dump` status, an undersized artifact, or one that does not decompress, it
exits non-zero and leaves the previous good file in place. That is the right
failure mode for the artifact — and it means a failed run produces **no signal
of its own**. The directory still holds yesterday's valid backup, so the
dashboard's freshness check stays quiet until that file crosses
`BACKUP_STALE_THRESHOLD_HOURS` (36h).

Up to a day and a half of consecutive failures therefore reads as healthy. The
operator learns of the first failure only when the last *success* goes stale:
the alarm fires late, for the wrong reason, and says nothing about why the runs
failed.

This is the shape bu-e1410 fixed one layer down — a check credited with an
answer it was never positioned to give. Freshness of the newest artifact cannot
answer "did last night's run succeed"; those are different questions, and the
shipped Backup State Facts requirement conflates them.

## What Changes

- Every backup run, successful or not, records its own outcome next to the
  artifacts: `BACKUP_DIR/last_run.json`, written from the script's EXIT trap so
  no exit path — including one nobody enumerated — can skip it.
- `GET /api/system/backups` gains `last_run`, distinguishing "the artifact is
  fresh and the last run succeeded" from "the artifact is fresh and the last run
  failed", with the reason.
- No receipt, or an unparseable one, is reported as `"unknown"` and never as
  success. An older deployment and a first-ever run both land there; absence of
  evidence is not evidence of a successful backup.
- The QA infra-state discovery source raises a failed run as its own finding on
  the night it happens, rather than waiting for the staleness it causes.
- The backup sidecar keeps needing no database connection: the receipt is a file
  in a directory the dashboard already reads, so the signal survives exactly the
  failures — including database failures — that it exists to report.

## Capabilities

### New Capabilities

- `system-overview-page` — Backup Run Outcome: the result of the most recent
  backup *run*, reported independently of the newest artifact's age.

### Modified Capabilities

None. Backup State Facts is unchanged; the run outcome is an additional fact
alongside it, not a redefinition of artifact freshness.
