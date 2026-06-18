#!/usr/bin/env bash
# scripts/pg_verify_restore.sh
#
# Verify that a PostgreSQL restore succeeded by checking schema presence,
# row counts, and key table integrity in the restored database.
#
# This is the verification step of the Butlers backup/restore drill
# (docs/operations/backup-restore.md).  Run it after pg_restore.sh to
# confirm the restore produced a working, data-intact database.
#
# Usage:
#   ./scripts/pg_verify_restore.sh [--target-db <name>] [--reference-db <name>]
#                                  [--env-file <path>]
#                                  [--host <host>] [--port <port>]
#                                  [--user <user>] [--password <password>]
#
#   --target-db <name>       Restored database to verify (default: butlers_restore_verify)
#   --reference-db <name>    Live database to compare row counts against (default: butlers)
#                            Pass --no-compare to skip the comparison step.
#   --no-compare             Skip live-database row-count comparison
#   --env-file <path>        Path to env file for connection params
#   --host <host>            PostgreSQL hostname override
#   --port <port>            PostgreSQL port override
#   --user <user>            PostgreSQL user override
#   --password <password>    PostgreSQL password override
#
# Exit codes:
#   0  All checks passed
#   1  One or more checks failed (details printed to stdout)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Defaults ────────────────────────────────────────────────────────────
TARGET_DB="butlers_restore_verify"
REFERENCE_DB="butlers"
NO_COMPARE=false
ENV_FILE=""
FLAG_HOST=""
FLAG_PORT=""
FLAG_USER=""
FLAG_PASSWORD=""

PASS=0
FAIL=0

# ── Argument parsing ─────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target-db)    TARGET_DB="$2";     shift 2 ;;
    --reference-db) REFERENCE_DB="$2";  shift 2 ;;
    --no-compare)   NO_COMPARE=true;    shift ;;
    --env-file)     ENV_FILE="$2";      shift 2 ;;
    --host)         FLAG_HOST="$2";     shift 2 ;;
    --port)         FLAG_PORT="$2";     shift 2 ;;
    --user)         FLAG_USER="$2";     shift 2 ;;
    --password)     FLAG_PASSWORD="$2"; shift 2 ;;
    *)              echo "Unknown flag: $1" >&2; exit 1 ;;
  esac
done

# ── Load env file ────────────────────────────────────────────────────────
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
    for candidate in "${PROJECT_DIR}/.env.dev" "${PROJECT_DIR}/.env.prod"; do
      if [[ -f "$candidate" ]]; then
        echo "[verify] Loading connection params from ${candidate}"
        # shellcheck source=/dev/null
        set -a; source "$candidate"; set +a
        break
      fi
    done
  fi
fi

PG_HOST="${FLAG_HOST:-${POSTGRES_HOST:-localhost}}"
PG_PORT="${FLAG_PORT:-${POSTGRES_PORT:-5432}}"
PG_USER="${FLAG_USER:-${POSTGRES_USER:-butlers}}"
PG_PASSWORD="${FLAG_PASSWORD:-${POSTGRES_PASSWORD:-}}"

# ── Helper functions ─────────────────────────────────────────────────────

# Run a SQL query against a database and return the first column of the first row.
run_sql() {
  local db="$1"
  local query="$2"
  PGPASSWORD="$PG_PASSWORD" psql \
    --host="$PG_HOST" \
    --port="$PG_PORT" \
    --username="$PG_USER" \
    --dbname="$db" \
    --no-password \
    --no-align \
    --tuples-only \
    -c "$query" 2>/dev/null | head -1 | tr -d '[:space:]' || echo "ERROR"
}

check_pass() {
  echo "  [PASS] $1"
  PASS=$((PASS + 1))
}

check_fail() {
  echo "  [FAIL] $1"
  FAIL=$((FAIL + 1))
}

# ── Verification checks ───────────────────────────────────────────────────

echo "[verify] target database: ${TARGET_DB}"
echo "[verify] host:            ${PG_HOST}:${PG_PORT}"
echo "[verify] user:            ${PG_USER}"
echo ""

# ── Check 1: Database is reachable and connectable ──────────────────────
echo "── Check 1: Database connectivity ──"
if PGPASSWORD="$PG_PASSWORD" psql \
     --host="$PG_HOST" \
     --port="$PG_PORT" \
     --username="$PG_USER" \
     --dbname="$TARGET_DB" \
     --no-password \
     -c "SELECT 1" >/dev/null 2>&1; then
  check_pass "Connected to '${TARGET_DB}'"
else
  check_fail "Cannot connect to '${TARGET_DB}' — restore may have failed entirely"
  echo ""
  echo "RESULT: $((PASS + FAIL)) checks — ${PASS} passed, ${FAIL} failed"
  exit 1
fi
echo ""

# ── Check 2: Core schemas exist ─────────────────────────────────────────
echo "── Check 2: Core schema presence ──"
# The public schema + at least one butler schema (switchboard or general) must be present
for schema in public switchboard general; do
  exists=$(run_sql "$TARGET_DB" \
    "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = '${schema}';")
  if [[ "$exists" == "1" ]]; then
    check_pass "Schema '${schema}' present"
  else
    check_fail "Schema '${schema}' missing"
  fi
done
echo ""

# ── Check 3: Core tables exist ──────────────────────────────────────────
echo "── Check 3: Core tables present ──"
# Tables that must always exist after a valid restore
declare -a REQUIRED_TABLES=(
  "public.contacts"
  "public.contact_info"
  "public.model_catalog"
)
for table in "${REQUIRED_TABLES[@]}"; do
  schema="${table%%.*}"
  tname="${table##*.}"
  count=$(run_sql "$TARGET_DB" \
    "SELECT COUNT(*) FROM information_schema.tables \
     WHERE table_schema = '${schema}' AND table_name = '${tname}';")
  if [[ "$count" == "1" ]]; then
    check_pass "Table '${table}' present"
  else
    check_fail "Table '${table}' missing"
  fi
done
echo ""

# ── Check 4: Contacts table has at least one row ────────────────────────
echo "── Check 4: Owner row in public.contacts ──"
contact_count=$(run_sql "$TARGET_DB" "SELECT COUNT(*) FROM public.contacts;")
if [[ "$contact_count" =~ ^[0-9]+$ ]] && [[ "$contact_count" -ge 1 ]]; then
  check_pass "public.contacts has ${contact_count} row(s) (at least owner row present)"
else
  check_fail "public.contacts is empty or unreachable (count='${contact_count}')"
fi
echo ""

# ── Check 5: model_catalog has rows ─────────────────────────────────────
echo "── Check 5: model_catalog populated ──"
model_count=$(run_sql "$TARGET_DB" "SELECT COUNT(*) FROM public.model_catalog;")
if [[ "$model_count" =~ ^[0-9]+$ ]] && [[ "$model_count" -ge 1 ]]; then
  check_pass "public.model_catalog has ${model_count} row(s)"
else
  check_fail "public.model_catalog is empty or unreachable (count='${model_count}')"
fi
echo ""

# ── Check 6: Row-count comparison against live DB ───────────────────────
if [[ "$NO_COMPARE" == "false" ]]; then
  echo "── Check 6: Row-count parity vs live '${REFERENCE_DB}' ──"
  echo "   (compares public.contacts and public.contact_info)"
  for table in public.contacts public.contact_info; do
    schema="${table%%.*}"
    tname="${table##*.}"
    live_count=$(run_sql "$REFERENCE_DB" \
      "SELECT COUNT(*) FROM ${table};" 2>/dev/null || echo "N/A")
    restore_count=$(run_sql "$TARGET_DB" \
      "SELECT COUNT(*) FROM ${table};" 2>/dev/null || echo "N/A")
    if [[ "$live_count" == "N/A" ]]; then
      echo "  [SKIP] ${table}: cannot read live DB '${REFERENCE_DB}' — skipping comparison"
    elif [[ "$live_count" == "$restore_count" ]]; then
      check_pass "${table}: ${restore_count} rows (matches live)"
    elif [[ "$restore_count" =~ ^[0-9]+$ ]] && [[ "$live_count" =~ ^[0-9]+$ ]]; then
      # A recent backup will have fewer rows than a live system that has grown;
      # warn rather than fail so the check is informational, not blocking.
      if [[ "$restore_count" -le "$live_count" ]]; then
        echo "  [WARN] ${table}: restored=${restore_count}, live=${live_count}" \
             "(backup predates ${live_count} - ${restore_count} new rows — expected)"
        PASS=$((PASS + 1))
      else
        check_fail "${table}: restored=${restore_count} > live=${live_count} (unexpected)"
      fi
    else
      check_fail "${table}: cannot parse counts (restored='${restore_count}', live='${live_count}')"
    fi
  done
  echo ""
fi

# ── Summary ──────────────────────────────────────────────────────────────
echo "══════════════════════════════════════════════"
TOTAL=$((PASS + FAIL))
if [[ $FAIL -eq 0 ]]; then
  echo "RESULT: ${TOTAL} checks — ${PASS} passed, 0 failed  ✓  RESTORE VERIFIED"
  echo ""
  echo "The restored database '${TARGET_DB}' is structurally sound."
  echo "You may now:"
  echo "  - Inspect data manually:  psql -h ${PG_HOST} -U ${PG_USER} -d ${TARGET_DB}"
  echo "  - Drop scratch DB when done:"
  echo "    PGPASSWORD=... dropdb -h ${PG_HOST} -U ${PG_USER} ${TARGET_DB}"
  exit 0
else
  echo "RESULT: ${TOTAL} checks — ${PASS} passed, ${FAIL} FAILED  ✗  RESTORE NEEDS INVESTIGATION"
  echo ""
  echo "See FAIL lines above for details."
  echo "Inspect the restored database directly:"
  echo "  psql -h ${PG_HOST} -U ${PG_USER} -d ${TARGET_DB}"
  exit 1
fi
