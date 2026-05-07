#!/bin/sh
# deploy/backup/pg_dump.sh
#
# Filesystem pg_dump backup for Butlers.
#
# Writes a timestamped .sql.gz dump to BACKUP_DIR (default: /backups).
# Prunes files older than BACKUP_RETAIN_DAYS (default: 14) days.
# Intended to run as a cron job inside the backup-cron sidecar container
# (see docker-compose.yml) but is also safe to invoke directly.
#
# Environment variables (all optional — defaults shown):
#   POSTGRES_HOST          postgres hostname (default: localhost)
#   POSTGRES_PORT          postgres port (default: 5432)
#   POSTGRES_USER          postgres user (default: butlers)
#   POSTGRES_PASSWORD      postgres password
#   POSTGRES_DB            database name (default: butlers)
#   BACKUP_DIR             directory to write dumps to (default: /backups)
#   BACKUP_RETAIN_DAYS     number of days to keep backups (default: 14)
#
# Output filename format: butlers_YYYY-MM-DDTHH-MM-SS.sql.gz

set -eu
set -o pipefail

POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-butlers}"
POSTGRES_DB="${POSTGRES_DB:-butlers}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-14}"

mkdir -p "${BACKUP_DIR}"

TIMESTAMP="$(date -u +%Y-%m-%dT%H-%M-%S)"
OUTFILE="${BACKUP_DIR}/butlers_${TIMESTAMP}.sql.gz"
TMPFILE="${OUTFILE}.tmp"

# Remove the temp file on exit so a failed dump never leaves a partial file
# in the backup directory (the directory scanner ignores .tmp files, but this
# keeps the directory clean even if something kills the process mid-run).
cleanup() {
  rm -f "${TMPFILE}"
}
trap cleanup EXIT

echo "[backup] start: ${TIMESTAMP}, host=${POSTGRES_HOST}:${POSTGRES_PORT}, db=${POSTGRES_DB}"

# pg_dump writes to stdout; we pipe through gzip into a .tmp file so the
# directory scanner in get_backup_facts() never sees a partial dump.
# pipefail ensures a pg_dump failure propagates through the pipe and the
# cleanup trap removes the temp file before the mv would make it permanent.
PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump \
  --host="${POSTGRES_HOST}" \
  --port="${POSTGRES_PORT}" \
  --username="${POSTGRES_USER}" \
  --dbname="${POSTGRES_DB}" \
  --format=plain \
  --no-password \
  | gzip > "${TMPFILE}"

mv "${TMPFILE}" "${OUTFILE}"
echo "[backup] written: ${OUTFILE} ($(du -h "${OUTFILE}" | cut -f1))"

# Prune files older than BACKUP_RETAIN_DAYS days.
# -exec echo before -delete so the filename is logged before removal.
find "${BACKUP_DIR}" -maxdepth 1 -name "butlers_*.sql.gz" \
  -mtime "+${BACKUP_RETAIN_DAYS}" \
  -exec echo "[backup] pruned: {}" \; -delete

echo "[backup] done"
