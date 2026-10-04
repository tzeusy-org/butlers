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
CAPTURE_EPOCH_FILE="${CAPTURE_EPOCH_FILE:-}"

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
    --capture-epoch-file) CAPTURE_EPOCH_FILE="$2"; shift 2 ;;
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

# Capture recovery authority is outside the restored database. A capture dump
# requires an explicit persistent host path; it cannot reconstruct its epoch from
# old rows. Rotate before importing, even if the dump lacks a newer receipt.
HAS_CAPTURE=false
if gunzip -c "$BACKUP_FILE" | awk '/CREATE TABLE public.capture_service_control/ { found=1 } END { exit !found }'; then
  HAS_CAPTURE=true
fi
if [[ "$HAS_CAPTURE" == true ]]; then
  if [[ "$CAPTURE_EPOCH_FILE" != /* ]]; then
    echo "[restore] ERROR: capture restore requires --capture-epoch-file outside DB/repo" >&2
    exit 1
  fi

fi

# ── Drop existing target if requested ───────────────────────────────────
if [[ "$DROP_EXISTING" == "true" ]]; then
  echo "[restore] Dropping existing database '${TARGET_DB}' (--drop-existing)"
  # No ON_ERROR_STOP here on purpose, and none is needed: psql exits non-zero
  # when its single `-c` statement fails, so `set -euo pipefail` already aborts
  # the script. That is *not* true of the `-f`-style restore below, where psql
  # keeps going after an error and still exits 0 — see the audit after it.
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

# Disable an existing target before import. The fixed dump importer also forces
# both flags false and recovery_required=true, so dumped enablement never wins.
if [[ "$HAS_CAPTURE" == true ]]; then
  PGPASSWORD="$PG_PASSWORD" psql --host="$PG_HOST" --port="$PG_PORT" \
    --username="$PG_USER" --dbname="$TARGET_DB" --no-password --set=ON_ERROR_STOP=1 \
    -c "DO \$\$ BEGIN
      IF to_regclass('public.capture_service_control') IS NOT NULL THEN
        SET LOCAL ROLE butler_general_rw;
        UPDATE public.capture_service_control SET admission_enabled=false,
          dispatch_enabled=false,recovery_required=true;
      END IF;
    END \$\$;" >/dev/null
fi

if [[ "$HAS_CAPTURE" == true ]]; then
  mkdir -p "$(dirname "$CAPTURE_EPOCH_FILE")"
  CAPTURE_EPOCH_TMP="$(mktemp "${CAPTURE_EPOCH_FILE}.tmp.XXXXXX")"
  if ! PGPASSWORD="$PG_PASSWORD" psql --host="$PG_HOST" --port="$PG_PORT" \
      --username="$PG_USER" --dbname=postgres --no-password --no-align --tuples-only \
      --set=ON_ERROR_STOP=1 -c "SELECT json_build_object('generation',gen_random_uuid(),
          'not_before',clock_timestamp());" > "$CAPTURE_EPOCH_TMP"; then
    rm -f "$CAPTURE_EPOCH_TMP"
    exit 1
  fi
  sync
  mv -f "$CAPTURE_EPOCH_TMP" "$CAPTURE_EPOCH_FILE"
  sync
fi

# ── Restore ─────────────────────────────────────────────────────────────
echo "[restore] Restoring ${BACKUP_FILE} → ${TARGET_DB} ..."
echo "[restore] (This may take a minute for large databases)"

# The assignment belongs on `psql`, not on the pipeline: a prefix assignment
# applies only to the command it prefixes, so putting it on `gunzip` left psql
# with no password at all and the restore died at the connection.
gunzip -c "$BACKUP_FILE" | PGPASSWORD="$PG_PASSWORD" psql \
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

# ── Ownership audit: the fence must not invert (bu-zbybd) ───────────────
#
# psql does not stop on error while reading a script, and it exits 0 anyway. A
# plain dump carries `CREATE FUNCTION` immediately followed by
# `ALTER FUNCTION ... OWNER TO <role>`, so a failed ALTER still leaves the
# function behind — owned by whoever ran the restore. For a SECURITY DEFINER
# function that is not a lost fence but an inverted one: a body meant to run
# with a constrained NOLOGIN owner's privileges now runs with the restorer's,
# and the restore reports success.
#
# Neither obvious fix works, and both were measured on a real dump of a
# database bootstrapped by scripts/init-db.sql before this audit was written:
#
#   - ON_ERROR_STOP=1 does not work. The first ownership assignment in that
#     dump is on line 29 of 17291 — `ALTER SCHEMA ... OWNER TO` for the
#     migration login, before a single table exists. Stopping there left the
#     target with one schema and zero tables. That trades a silent privilege
#     escalation for a disaster-recovery path that recovers nothing, which is
#     not a fix.
#   - Pre-creating the fenced roles on the target does not work either.
#     Assigning ownership to a role requires membership in it, and *not* being
#     a member is precisely what the fence is, so every one of those ALTERs
#     still failed with `must be able to SET ROLE "..."` and every fenced
#     function still landed on the restoring login.
#
# So the restore is allowed to complete, and is then audited. The audit does
# not repair anything: a disaster-recovery path must not quietly rewrite the
# database it just produced, because the operator's next move may well be to
# create the roles and reassign ownership deliberately. Refusing to certify is
# the guard. See docs/operations/backup-restore.md, "Ownership precondition".
echo "[restore] Auditing SECURITY DEFINER ownership ..."

AUDIT_DIR="$(mktemp -d)"
trap 'rm -rf "$AUDIT_DIR"' EXIT

# What the dump says the owner should be, for every function in `public` it
# hands to someone other than the restoring login. Read from the dump rather
# than a hard-coded role list, so a newly fenced function is covered the day it
# is added. Written as awk rather than sed because the identifier may be
# quoted: cutting at the first `(` keeps a name containing a space intact,
# where cutting at the first space would drop it and leave a security guard
# with a silent blind spot. A quoted name containing `(` itself, or a comma, is
# the acknowledged boundary — no such name exists in this schema.
gunzip -c "$BACKUP_FILE" \
  | awk -v me="$PG_USER" '
      /^ALTER FUNCTION public\./ {
        line = $0
        sub(/;[ \t]*$/, "", line)

        # Split on the LAST " OWNER TO ": an argument list cannot contain it,
        # but taking the first match would be wrong if one ever did.
        sep = " OWNER TO "
        start = 1; pos = 0
        while ((k = index(substr(line, start), sep)) > 0) {
          pos = start + k - 1
          start = pos + length(sep)
        }
        if (pos == 0) next

        owner = substr(line, pos + length(sep))
        gsub(/"/, "", owner)
        if (owner == me) next

        head = substr(line, 1, pos - 1)
        rest = substr(head, length("ALTER FUNCTION public.") + 1)
        paren = index(rest, "(")
        if (paren == 0) next
        name = substr(rest, 1, paren - 1)
        gsub(/"/, "", name)

        print name "\t" owner
      }' \
  | LC_ALL=C sort -u > "$AUDIT_DIR/declared"

# What the target actually ended up with. No value from the dump reaches this
# query — the two sides are intersected below with `join`, in the shell — so
# there is no interpolation for a function name out of a backup file to abuse.
PGPASSWORD="$PG_PASSWORD" psql \
  --host="$PG_HOST" \
  --port="$PG_PORT" \
  --username="$PG_USER" \
  --dbname="$TARGET_DB" \
  --no-password \
  --no-align \
  --tuples-only \
  --set=ON_ERROR_STOP=1 \
  -c "SELECT DISTINCT p.proname
        FROM pg_proc AS p
        JOIN pg_namespace AS n ON n.oid = p.pronamespace
       WHERE n.nspname = 'public'
         AND p.prosecdef
         AND pg_get_userbyid(p.proowner) = current_user" \
  | LC_ALL=C sort -u > "$AUDIT_DIR/owned_by_restorer"

# An inverted fence is a function the dump assigns elsewhere that the restore
# nonetheless left on the restoring login. Matching is by name, which
# over-matches an overloaded name — deliberately, since the conservative
# direction for a security guard is to report.
#
# No `|| true`: an empty intersection is exit 0 already, so the only thing it
# could swallow is join genuinely failing (unsorted input, unreadable file),
# and a security guard that treats its own breakage as "nothing found" fails
# open. `set -e` aborts the restore instead. The tab delimiter is what makes
# the sort above valid for join: tab is below every printable character, so
# ordering "name<TAB>owner" by whole line agrees with ordering by name.
LC_ALL=C join -t "$(printf '\t')" \
  "$AUDIT_DIR/declared" "$AUDIT_DIR/owned_by_restorer" > "$AUDIT_DIR/inverted"

if [[ -s "$AUDIT_DIR/inverted" ]]; then
  {
    echo ""
    echo "[restore] SECURITY FAILURE: SECURITY DEFINER ownership was not preserved."
    echo ""
    echo "  The data restored, but these SECURITY DEFINER functions in 'public' are"
    echo "  now owned by the restoring login '${PG_USER}' instead of the owner the"
    echo "  backup names for them:"
    echo ""
    awk -F'\t' '{ printf "    public.%s  (backup says: %s)\n", $1, $2 }' "$AUDIT_DIR/inverted"
    echo ""
    echo "  Each one now runs its body with '${PG_USER}' privileges. Do NOT promote"
    echo "  '${TARGET_DB}' or expose it to application roles in this state."
    echo ""
    echo "  A dump cannot rebuild these fences by itself: assigning ownership to a"
    echo "  fenced role requires membership in it, and the restoring login is fenced"
    echo "  away from those roles by design — so pre-creating the roles on the target"
    echo "  does not help either. Restore onto a target a cluster superuser has"
    echo "  already bootstrapped with scripts/init-db.sql, as a login that can assume"
    echo "  those owners; or re-run scripts/init-db.sql against '${TARGET_DB}' as a"
    echo "  cluster superuser to rebuild them. See docs/operations/backup-restore.md,"
    echo "  'Ownership precondition'."
  } >&2
  exit 1
fi
echo "[restore]   no SECURITY DEFINER function in 'public' fell to '${PG_USER}'"

if [[ "$HAS_CAPTURE" == true ]]; then
  PGPASSWORD="$PG_PASSWORD" psql --host="$PG_HOST" --port="$PG_PORT" \
    --username="$PG_USER" --dbname="$TARGET_DB" --no-password --set=ON_ERROR_STOP=1 \
    -c "BEGIN; SET LOCAL ROLE butler_general_rw;
      DO \$\$ BEGIN
        IF EXISTS (SELECT 1 FROM public.capture_service_control WHERE admission_enabled
                    OR dispatch_enabled OR NOT recovery_required) OR
           EXISTS (SELECT 1 FROM public.captures AS c
             LEFT JOIN public.capture_operations AS o ON o.id=c.operation_id
             WHERE c.operation_id IS NOT NULL AND
               (o.id IS NULL OR o.capture_id<>c.id OR o.service_epoch<>c.service_epoch
                OR o.payload_digest<>c.payload_digest OR
                (c.disposition='routed' AND (o.stage<>'routed' OR o.receipt<>c.receipt)))) OR
           (SELECT count(*) FROM pg_class WHERE oid IN
             ('public.captures'::regclass,'public.capture_operations'::regclass,
              'public.capture_service_control'::regclass)
             AND relrowsecurity AND relforcerowsecurity) <> 3 OR
           (SELECT count(*) FROM pg_trigger WHERE tgrelid IN
             ('public.captures'::regclass,'public.capture_operations'::regclass,
              'public.capture_service_control'::regclass)
             AND tgname IN ('capture_preserve_evidence','capture_no_truncate')
             AND tgenabled='O') <> 6 THEN
          RAISE EXCEPTION 'capture restore controls or evidence invalid';
        END IF;
      END \$\$; COMMIT;" >/dev/null
fi

echo "[restore] done — '${TARGET_DB}' is populated"
echo ""
echo "Next step: verify integrity with:"
echo "  ./scripts/pg_verify_restore.sh --target-db '${TARGET_DB}' [connection options]"
