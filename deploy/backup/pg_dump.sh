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

echo "[backup] start: ${TIMESTAMP}, host=${POSTGRES_HOST}:${POSTGRES_PORT}, db=${POSTGRES_DB}"

# pg_dump writes to stdout; we pipe through gzip into a .tmp file so the
# directory scanner in get_backup_facts() never sees a partial dump.
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
find "${BACKUP_DIR}" -maxdepth 1 -name "butlers_*.sql.gz" \
  -mtime "+${BACKUP_RETAIN_DAYS}" -delete \
  -exec echo "[backup] pruned: {}" \;

echo "[backup] done"
