# Backup and Restore

> **Purpose:** Document the backup cadence, storage location, and the
> step-by-step restore drill for the Butlers PostgreSQL data plane.
> **Audience:** The owner/operator performing data recovery or verifying
> backup health.
> **Prerequisites:** [Environment Config](environment-config.md),
> [Docker Deployment](docker-deployment.md), `psql`/`createdb`/`dropdb`
> on PATH.

---

## Overview

Butlers stores all personal data in a single PostgreSQL database.  The
backup-cron sidecar (running inside the compose stack) writes a
timestamped, gzip-compressed plain-SQL dump to the `butlers_backups`
Docker volume every night at 02:00 UTC.  The dashboard `/system` page
shows the recency and size of the most-recent backup so you can confirm
the automated job is running.

| What | Where |
|------|-------|
| Backup script | `deploy/backup/pg_dump.sh` (runs inside container) |
| Restore script | `scripts/pg_restore.sh` (run on the host) |
| Verify script | `scripts/pg_verify_restore.sh` (run on the host) |
| Backup volume | `butlers_backups` Docker volume |
| Host-accessible path | `docker volume inspect butlers_backups` → Mountpoint |
| Default schedule | 02:00 UTC daily (`BACKUP_CRON` env var) |
| Default retention | 14 days (`BACKUP_RETAIN_DAYS` env var) |
| Dump format | Plain SQL, gzip-compressed (`butlers_YYYY-MM-DDTHH-MM-SS.sql.gz`) |

---

## How Backups Are Created

The `backup-cron` service in `docker-compose.yml` mounts
`deploy/backup/pg_dump.sh` into a `postgres:17-alpine` container and
runs it on a cron schedule.  The script:

1. Calls `pg_dump --format=plain` against the Postgres host.
2. Streams output through `gzip` to a `.tmp` file so partial dumps are
   never visible.
3. Atomically renames the `.tmp` file to the final timestamped name.
4. Prunes files older than `BACKUP_RETAIN_DAYS`.

The compose volume `butlers_backups` is shared read-only into
`dashboard-api` so the `/api/system/backups` endpoint can surface backup
recency.

### Configuring the backup job

In `.env.dev` / `.env.prod`:

```
# Daily at 02:00 UTC (default)
BACKUP_CRON=0 2 * * *

# Keep 14 days of dumps (default)
BACKUP_RETAIN_DAYS=14
```

You do **not** need to restart the stack to change the schedule — the
cron is re-read on `backup-cron` container restart only.

---

## Triggering a Manual Backup

To produce an on-demand dump without waiting for the scheduled job:

```bash
# Run the backup script inside the backup-cron container
docker compose exec backup-cron sh /backup/pg_dump.sh
```

Or directly on the host (requires `pg_dump` installed locally):

```bash
# Load connection params from your env file
source .env.dev        # or .env.prod for production

TIMESTAMP="$(date -u +%Y-%m-%dT%H-%M-%S)"
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
  --host="$POSTGRES_HOST" \
  --port="${POSTGRES_PORT:-5432}" \
  --username="${POSTGRES_USER:-butlers}" \
  --dbname="${POSTGRES_DB:-butlers}" \
  --format=plain \
  --no-password \
  | gzip > "butlers_${TIMESTAMP}.sql.gz"
```

---

## Locating Backup Files

The `butlers_backups` Docker volume holds all automated backups:

```bash
# List recent backups (sorted, newest last)
docker run --rm \
  -v butlers_backups:/backups:ro \
  busybox:latest \
  find /backups -name "butlers_*.sql.gz" | sort

# Copy the most-recent backup to the current directory
LATEST=$(docker run --rm \
  -v butlers_backups:/backups:ro \
  busybox:latest \
  find /backups -name "butlers_*.sql.gz" | sort | tail -1)

docker run --rm \
  -v butlers_backups:/backups:ro \
  -v "$(pwd):/out" \
  busybox:latest \
  cp "$LATEST" /out/
```

---

## Restore Drill

Run this drill periodically (suggested: monthly) to prove that a backup
can actually be restored and that data is intact.  The drill restores
into a scratch database (`butlers_restore_verify`) so the production
database is **never touched**.

### Prerequisites

- `psql`, `createdb`, `dropdb`, and `gunzip` available on the host.
- The Postgres server is reachable (the live stack can be running).
- A backup file on disk (copy it out of the Docker volume as shown
  above, or use a manual dump from the previous section).

### Step 1 — Obtain the backup file

```bash
# Extract the most-recent backup from the Docker volume
LATEST=$(docker run --rm \
  -v butlers_backups:/backups:ro \
  busybox:latest \
  sh -c 'find /backups -name "butlers_*.sql.gz" | sort | tail -1')

docker run --rm \
  -v butlers_backups:/backups:ro \
  -v "$(pwd):/out" \
  busybox:latest \
  cp "$LATEST" /out/

BACKUP_FILE="$(basename "$LATEST")"
echo "Working with: $BACKUP_FILE"
```

Expected output example:
```
Working with: butlers_2026-06-18T02-00-01.sql.gz
```

Confirm the file is non-empty:
```bash
ls -lh "$BACKUP_FILE"
# Expected: a .sql.gz file of several KB to MB depending on data volume
gunzip -t "$BACKUP_FILE" && echo "gzip integrity: OK"
```

### Step 2 — Restore into a scratch database

```bash
./scripts/pg_restore.sh "$BACKUP_FILE" --drop-existing
```

The script auto-detects `.env.dev` (or `.env.prod`) for connection
parameters.  Pass `--env-file <path>` to use a different env file, or
use `--host`/`--port`/`--user`/`--password` flags directly.

Expected output:
```
[restore] Loading connection params from .env.dev
[restore] backup:    butlers_2026-06-18T02-00-01.sql.gz
[restore] target db: butlers_restore_verify
[restore] host:      butlers-db-dev.your-tailnet.ts.net:5432
[restore] user:      butlers

WARNING: This restore targets 'butlers_restore_verify', NOT the production database.
         The production database is left untouched.

[restore] Dropping existing database 'butlers_restore_verify' (--drop-existing)
[restore] Creating target database 'butlers_restore_verify' (if it does not exist)...
[restore] Restoring butlers_2026-06-18T02-00-01.sql.gz → butlers_restore_verify ...
[restore] (This may take a minute for large databases)
[restore] done — 'butlers_restore_verify' is populated
```

### Step 3 — Verify integrity

```bash
./scripts/pg_verify_restore.sh
```

Expected output (all checks pass):
```
[verify] target database: butlers_restore_verify
[verify] host:            butlers-db-dev.your-tailnet.ts.net:5432
[verify] user:            butlers

── Check 1: Database connectivity ──
  [PASS] Connected to 'butlers_restore_verify'

── Check 2: Core schema presence ──
  [PASS] Schema 'public' present
  [PASS] Schema 'switchboard' present
  [PASS] Schema 'general' present

── Check 3: Core tables present ──
  [PASS] Table 'public.contacts' present
  [PASS] Table 'public.contact_info' present
  [PASS] Table 'public.model_catalog' present

── Check 4: Owner row in public.contacts ──
  [PASS] public.contacts has 1 row(s) (at least owner row present)

── Check 5: model_catalog populated ──
  [PASS] public.model_catalog has 3 row(s)

── Check 6: Row-count parity vs live 'butlers' ──
  [PASS] public.contacts: 1 rows (matches live)
  [WARN] public.contact_info: restored=12, live=14 (backup predates 14 - 12 new rows — expected)

══════════════════════════════════════════════
RESULT: 11 checks — 11 passed, 0 failed  ✓  RESTORE VERIFIED
```

A `[WARN]` on row counts is expected: the backup was taken before new
rows were added to the live system.  It is only a `[FAIL]` if the
restored count *exceeds* the live count (which would indicate corruption
or the wrong database being compared).

The drill is **complete** once you see `RESTORE VERIFIED`.

### Step 4 — Optional: manual inspection

If you want to spot-check data beyond the automated checks:

```bash
source .env.dev
PGPASSWORD="$POSTGRES_PASSWORD" psql \
  -h "$POSTGRES_HOST" -U "${POSTGRES_USER:-butlers}" \
  -d butlers_restore_verify

-- Inside psql:
\dn                           -- list schemas
\dt public.*                  -- list public tables
SELECT id, name FROM public.contacts LIMIT 5;
SELECT type, value FROM public.contact_info LIMIT 10;
\q
```

### Step 5 — Clean up the scratch database

```bash
source .env.dev
PGPASSWORD="$POSTGRES_PASSWORD" dropdb \
  -h "$POSTGRES_HOST" -U "${POSTGRES_USER:-butlers}" \
  butlers_restore_verify
echo "Scratch database dropped"
```

---

## Drill Cadence and Storage

| What | Recommendation |
|------|---------------|
| Drill frequency | Monthly (or after any significant data-volume change) |
| Backup storage | `butlers_backups` Docker volume (on the host running the stack) |
| Off-site copy | Manually copy dumps to an external drive or cloud storage; Butlers does not currently automate off-site transfer |
| Retention | 14 days automated; keep at least one monthly snapshot off-site |

> **Note on off-site backups:** The `butlers_backups` volume lives on the
> same host as the database.  A disk failure that destroys the DB would
> also destroy the volume.  For true disaster recovery, periodically copy
> a dump file to a separate physical location or cloud storage.

---

## Troubleshooting

### `pg_restore.sh` fails with "connection refused"

- Is the Postgres host reachable?  `ping $POSTGRES_HOST`
- Is the port open?  `nc -zv $POSTGRES_HOST 5432`
- Did you source the right env file?  Try `--env-file .env.dev` explicitly.

### `createdb` fails with "permission denied"

The `butlers` user may not have `CREATEDB` privilege.  Run as the
superuser:

```bash
psql -h "$POSTGRES_HOST" -U postgres -c \
  "ALTER USER butlers CREATEDB;"
```

Or create the scratch database manually before running `pg_restore.sh`:

```bash
source .env.dev
PGPASSWORD="$POSTGRES_PASSWORD" psql \
  -h "$POSTGRES_HOST" -U "${POSTGRES_USER:-butlers}" \
  -d postgres -c "CREATE DATABASE butlers_restore_verify;"
```

Then run `pg_restore.sh` *without* `--drop-existing` (so it skips the
createdb step):

```bash
./scripts/pg_restore.sh "$BACKUP_FILE"
```

### `pg_verify_restore.sh` reports schema missing

The restore may have completed but the schemas were not created because
the dump predates `scripts/init-db.sql` being run.  The plain-SQL dump
format includes `CREATE SCHEMA` statements if the schema existed at dump
time.  If the dump is from a correctly provisioned database, all schemas
should be present.

### Backup dashboard shows "no backup recorded"

- Is the `backup-cron` container running?  `docker compose ps backup-cron`
- Check logs: `docker compose logs backup-cron --tail=50`
- Is `BUTLERS_BACKUP_DIR` set in `dashboard-api`?  Check `docker compose config`.
