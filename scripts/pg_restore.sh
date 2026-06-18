#!/usr/bin/env bash
# scripts/pg_restore.sh
#
# Restore a Butlers PostgreSQL backup from a pg_dump .sql.gz file.
#
# Designed for the operator restore drill (docs/operations/backup-restore.md).
# Restores to a scratch target database so the production DB is never touched
# until you explicitly promote — making this drill safe to run alongside a
# live stack.
#
# Usage:
#   ./scripts/pg_restore.sh <backup-file.sql.gz> [--target-db <name>] [--env-file <path>]
#
#   <backup-file.sql.gz>     Path to a dump produced by deploy/backup/pg_dump.sh
#   --target-db <name>       Target database for restore (default: butlers_restore_verify)
#   --env-file <path>        Path to env file for connection params
#                            (default: .env.dev, then .env.prod)
#   --host <host>            Override POSTGRES_HOST (skips env file lookup)
#   --port <port>            Override POSTGRES_PORT
#   --user <user>            Override POSTGRES_USER
#   --password <password>    Override POSTGRES_PASSWORD
#   --drop-existing          Drop the target DB before restore if it exists
#
# Connection parameters (resolved in priority order):
#   1. CLI flags (--host, --port, --user, --password)
#   2. Environment variables (POSTGRES_HOST, POSTGRES_PORT, etc.)
#   3. Env file (--env-file, or .env.dev / .env.prod auto-detection)
#   4. Compiled defaults (localhost:5432, user=butlers)
#
# The script never touches POSTGRES_DB — restore always goes to --target-db.
#
# After restore, run scripts/pg_verify_restore.sh to confirm integrity.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Defaults ────────────────────────────────────────────────────────────
TARGET_DB="butlers_restore_verify"
DROP_EXISTING=false
ENV_FILE=""
BACKUP_FILE=""

# CLI-flag overrides for connection params (empty = use env / file)
FLAG_HOST=""
FLAG_PORT=""
FLAG_USER=""
FLAG_PASSWORD=""

# ── Argument parsing ─────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target-db)   TARGET_DB="$2";       shift 2 ;;
    --env-file)    ENV_FILE="$2";        shift 2 ;;
    --host)        FLAG_HOST="$2";       shift 2 ;;
    --port)        FLAG_PORT="$2";       shift 2 ;;
    --user)        FLAG_USER="$2";       shift 2 ;;
    --password)    FLAG_PASSWORD="$2";   shift 2 ;;
    --drop-existing) DROP_EXISTING=true; shift ;;
    -*)            echo "Unknown flag: $1" >&2; exit 1 ;;
    *)
      if [[ -z "$BACKUP_FILE" ]]; then
        BACKUP_FILE="$1"
      else
        echo "Unexpected positional argument: $1" >&2
        exit 1
      fi
      shift ;;
  esac
done

if [[ -z "$BACKUP_FILE" ]]; then
  echo "ERROR: backup file argument is required" >&2
  echo "Usage: $0 <backup-file.sql.gz> [options]" >&2
  exit 1
fi

if [[ ! -f "$BACKUP_FILE" ]]; then
  echo "ERROR: backup file not found: $BACKUP_FILE" >&2
  exit 1
fi

# ── Load env file for connection params ──────────────────────────────────
# Env file is only sourced if no explicit CLI flags override everything.
# Priority: explicit flags > env vars already set > env file defaults.
if [[ -z "$FLAG_HOST" ]] || [[ -z "$FLAG_PORT" ]] || \
   [[ -z "$FLAG_USER" ]] || [[ -z "$FLAG_PASSWORD" ]]; then
  if [[ -n "$ENV_FILE" ]]; then
    if [[ ! -f "$ENV_FILE" ]]; then
      echo "ERROR: specified --env-file not found: $ENV_FILE" >&2
      exit 1
    fi
    # shellcheck source=/dev/null
    set -a; source "$ENV_FILE"; set +a
  else
    # Auto-detect: prefer .env.dev (live system) over .env.prod
    for candidate in "${PROJECT_DIR}/.env.dev" "${PROJECT_DIR}/.env.prod"; do
      if [[ -f "$candidate" ]]; then
        echo "[restore] Loading connection params from ${candidate}"
        # shellcheck source=/dev/null
        set -a; source "$candidate"; set +a
        break
      fi
    done
  fi
fi

# ── Resolve final connection params ─────────────────────────────────────
PG_HOST="${FLAG_HOST:-${POSTGRES_HOST:-localhost}}"
PG_PORT="${FLAG_PORT:-${POSTGRES_PORT:-5432}}"
PG_USER="${FLAG_USER:-${POSTGRES_USER:-butlers}}"
PG_PASSWORD="${FLAG_PASSWORD:-${POSTGRES_PASSWORD:-}}"

echo "[restore] backup:    ${BACKUP_FILE}"
echo "[restore] target db: ${TARGET_DB}"
echo "[restore] host:      ${PG_HOST}:${PG_PORT}"
echo "[restore] user:      ${PG_USER}"
echo ""
echo "WARNING: This restore targets '${TARGET_DB}', NOT the production database."
echo "         The production database is left untouched."
echo ""

# ── Drop existing target if requested ───────────────────────────────────
if [[ "$DROP_EXISTING" == "true" ]]; then
  echo "[restore] Dropping existing database '${TARGET_DB}' (--drop-existing)"
  PGPASSWORD="$PG_PASSWORD" psql \
    --host="$PG_HOST" \
    --port="$PG_PORT" \
    --username="$PG_USER" \
    --dbname=postgres \
    --no-password \
    -c "DROP DATABASE IF EXISTS \"${TARGET_DB}\" WITH (FORCE);" \
    2>&1 | sed 's/^/  /'
fi

# ── Create target database ───────────────────────────────────────────────
echo "[restore] Creating target database '${TARGET_DB}' (if it does not exist)..."
# createdb returns exit code 1 if DB already exists; suppress that so the
# script is idempotent without --drop-existing.
PGPASSWORD="$PG_PASSWORD" createdb \
  --host="$PG_HOST" \
  --port="$PG_PORT" \
  --username="$PG_USER" \
  --no-password \
  "$TARGET_DB" 2>&1 | sed 's/^/  /' || true

# ── Restore ─────────────────────────────────────────────────────────────
echo "[restore] Restoring ${BACKUP_FILE} → ${TARGET_DB} ..."
echo "[restore] (This may take a minute for large databases)"

PGPASSWORD="$PG_PASSWORD" gunzip -c "$BACKUP_FILE" | psql \
  --host="$PG_HOST" \
  --port="$PG_PORT" \
  --username="$PG_USER" \
  --dbname="$TARGET_DB" \
  --no-password \
  --quiet \
  2>&1 | sed -e '/^$/d' -e 's/^/  /' || {
    echo "[restore] ERROR: psql restore failed" >&2
    exit 1
  }

echo "[restore] done — '${TARGET_DB}' is populated"
echo ""
echo "Next step: verify integrity with:"
echo "  ./scripts/pg_verify_restore.sh --target-db '${TARGET_DB}' [connection options]"
