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

# D13 adds a non-public durable proof/receipt interface. The original public
# audit above remains intact; its successful exit cannot certify this schema.
# Only a fixed boolean crosses from the dump into the catalog predicate. Read
# the whole stream so pipefail still detects decompression errors. A legacy
# dump may lack this schema; a modern dump must never certify its complete loss.
STEPUP_METADATA=$(gunzip -c "$BACKUP_FILE" | awk '
  /^CREATE SCHEMA (dashboard_stepup|"dashboard_stepup");/ ||
  /^CREATE (TABLE|FUNCTION) (dashboard_stepup|"dashboard_stepup")\./ { required=1 }
  /^COPY dashboard_stepup\.bindings \(.*\) FROM stdin;$/ { copies++; copying=1; next }
  copying && $0 == "\\." { copying=0; next }
  copying { rows++ }
  END { print (required ? "true" : "false"), copies+0, rows+0 }')
read -r STEPUP_EXPECTED STEPUP_COPY_COUNT STEPUP_ROW_COUNT <<< "$STEPUP_METADATA"
# Missing/partial or
# laundered ownership refuses promotion, including when psql continued after a
# failed ALTER OWNER during an otherwise populated restore.
STEPUP_INTACT=$(PGPASSWORD="$PG_PASSWORD" psql \
  --host="$PG_HOST" --port="$PG_PORT" --username="$PG_USER" \
  --dbname="$TARGET_DB" --no-password --no-align --tuples-only \
  --set=ON_ERROR_STOP=1 --quiet -c "
DO \$stepup_stage\$
DECLARE revision_schema text; modern boolean; restored_rows bigint;
BEGIN
 IF to_regnamespace('dashboard_stepup') IS NULL THEN
  IF $STEPUP_EXPECTED THEN
   RAISE EXCEPTION 'required dashboard step-up schema missing';
  END IF;
  FOR revision_schema IN
   SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE c.relname='alembic_version' AND c.relkind='r'
  LOOP
   EXECUTE format('SELECT EXISTS(SELECT FROM %I.alembic_version WHERE CASE
     WHEN version_num ~ ''^core_[0-9]+$'' THEN
      substring(version_num FROM 6)::numeric >= 277 ELSE false END)',revision_schema)
    INTO modern;
   IF modern THEN RAISE EXCEPTION 'recorded dashboard step-up schema missing'; END IF;
  END LOOP;
 END IF;
 IF $STEPUP_EXPECTED THEN
  IF $STEPUP_COPY_COUNT<>1 OR to_regclass('dashboard_stepup.bindings') IS NULL THEN
   RAISE EXCEPTION 'required dashboard step-up receipt data missing';
  END IF;
  EXECUTE 'SELECT count(*) FROM dashboard_stepup.bindings' INTO restored_rows;
  IF restored_rows<>$STEPUP_ROW_COUNT THEN
   RAISE EXCEPTION 'dashboard step-up receipt population differs from dump';
  END IF;
 END IF;
END \$stepup_stage\$;
SELECT CASE WHEN to_regnamespace('dashboard_stepup') IS NULL THEN NOT $STEPUP_EXPECTED ELSE
 EXISTS(SELECT FROM pg_namespace n JOIN pg_roles boot ON boot.oid=n.nspowner
   WHERE n.nspname='dashboard_stepup' AND boot.rolsuper
    AND obj_description(n.oid,'pg_namespace')='butlers:dashboard-stepup:core_277'
    AND EXISTS(SELECT FROM pg_class c JOIN pg_roles own ON own.oid=c.relowner
      WHERE c.relnamespace=n.oid AND c.relname='bindings' AND c.relkind='r'
       AND own.rolname='dashboard_stepup_owner'
       AND NOT(own.rolcanlogin OR own.rolsuper OR own.rolcreaterole OR own.rolcreatedb
        OR own.rolreplication OR own.rolbypassrls)
       AND NOT EXISTS(SELECT FROM pg_auth_members WHERE roleid=own.oid OR member=own.oid)
       AND NOT EXISTS(SELECT FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
        WHERE a.grantee<>own.oid AND (a.grantee=0 OR a.privilege_type<>'SELECT')))
    AND 3=(SELECT count(*) FROM pg_proc p WHERE p.pronamespace=n.oid
      AND p.proname IN('install_interface','finalize_interface','deactivate_interface')
      AND p.proowner=boot.oid AND p.pronargs=0 AND p.prosecdef
      AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[])
    AND 2=(SELECT count(*) FROM pg_proc p JOIN pg_roles own ON own.oid=p.proowner
      WHERE p.pronamespace=n.oid AND own.rolname='dashboard_stepup_owner' AND p.prosecdef
       AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]
       AND ((p.proname='ceremony' AND p.proargtypes='25 3802'::oidvector)
        OR (p.proname='consume' AND p.proargtypes='25 25 25 25 25 25 25 3802'::oidvector)))
    AND NOT EXISTS(SELECT FROM pg_proc p
      CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
      WHERE p.pronamespace=n.oid AND a.grantee=0)) END")
if [[ "$STEPUP_INTACT" != "t" ]]; then
  echo "[restore] SECURITY FAILURE: dashboard step-up ownership/ACL is incomplete; do NOT promote." >&2
  exit 1
fi

# Ordinary owner-creation receipts and the shared owner clause store must not
# disappear from a modern dump. Export only fixed presence/population counts.
ATTENTION_METADATA=$(gunzip -c "$BACKUP_FILE" | awk '
 /^CREATE SCHEMA (dashboard_attention|"dashboard_attention");/ ||
 /^CREATE (TABLE|FUNCTION) (dashboard_attention|"dashboard_attention")\./ { required=1 }
 /^COPY (dashboard_attention\.(tickets|stamps|candidate_causes|final_claims)|public\.break_through_clauses) \(.*\) FROM stdin;$/ {
  key=$2; copies[key]++; copying=key; next
 }
 copying && $0 == "\\." { copying=""; next }
 copying { rows[copying]++ }
 END { print (required ? "true" : "false"),
  copies["dashboard_attention.tickets"]+0,rows["dashboard_attention.tickets"]+0,
  copies["dashboard_attention.stamps"]+0,rows["dashboard_attention.stamps"]+0,
  copies["dashboard_attention.candidate_causes"]+0,rows["dashboard_attention.candidate_causes"]+0,
  copies["dashboard_attention.final_claims"]+0,rows["dashboard_attention.final_claims"]+0,
  copies["public.break_through_clauses"]+0,rows["public.break_through_clauses"]+0 }')
read -r ATTENTION_EXPECTED ATC ATR ASC ASR ACC ACR AFC AFR ABC ABR <<< "$ATTENTION_METADATA"
ATTENTION_INTACT=$(PGPASSWORD="$PG_PASSWORD" psql \
 --host="$PG_HOST" --port="$PG_PORT" --username="$PG_USER" --dbname="$TARGET_DB" \
 --no-password --no-align --tuples-only --set=ON_ERROR_STOP=1 --quiet -c "
DO \$attention_stage\$
DECLARE ns text; modern boolean; item record; actual bigint;
BEGIN
 IF to_regnamespace('dashboard_attention') IS NULL THEN
  IF $ATTENTION_EXPECTED THEN RAISE EXCEPTION 'required owner attention schema missing'; END IF;
  FOR ns IN SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE c.relname='alembic_version' AND c.relkind='r' LOOP
   EXECUTE format('SELECT EXISTS(SELECT FROM %I.alembic_version WHERE CASE WHEN version_num ~ ''^core_[0-9]+$'' THEN substring(version_num FROM 6)::numeric>=278 ELSE false END)',ns) INTO modern;
   IF modern THEN RAISE EXCEPTION 'recorded owner attention schema missing'; END IF;
  END LOOP;
 ELSIF $ATTENTION_EXPECTED THEN
  FOR item IN SELECT * FROM (VALUES
   ('dashboard_attention.tickets',$ATC,$ATR),('dashboard_attention.stamps',$ASC,$ASR),
   ('dashboard_attention.candidate_causes',$ACC,$ACR),('dashboard_attention.final_claims',$AFC,$AFR),('public.break_through_clauses',$ABC,$ABR)
  ) AS t(relation,copies,rows) LOOP
   IF item.copies<>1 OR to_regclass(item.relation) IS NULL THEN
    RAISE EXCEPTION 'owner attention dump population missing'; END IF;
   EXECUTE format('SELECT count(*) FROM %s',item.relation::regclass) INTO actual;
   IF actual<>item.rows THEN RAISE EXCEPTION 'owner attention dump population differs'; END IF;
  END LOOP;
 END IF;
END \$attention_stage\$;
CREATE FUNCTION pg_temp.owner_attention_intact(expected boolean)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER
SET search_path=pg_catalog,pg_temp AS \$attention_catalog\$
BEGIN
 IF to_regnamespace('dashboard_attention') IS NULL THEN
  RETURN NOT expected;
 END IF;
 IF to_regclass('dashboard_attention.bootstrap_configuration') IS NULL THEN
  RETURN false;
 END IF;
 RETURN
 EXISTS(SELECT FROM pg_namespace n JOIN pg_roles boot ON boot.oid=n.nspowner
  WHERE n.nspname='dashboard_attention' AND boot.rolsuper
   AND obj_description(n.oid,'pg_namespace')='butlers:dashboard-attention:core_278'
   AND 3=(SELECT count(*) FROM pg_proc p WHERE p.pronamespace=n.oid AND p.proowner=boot.oid
    AND p.proname IN('install_interface','finalize_interface','deactivate_interface')
    AND p.pronargs=0 AND p.prorettype='void'::regtype AND p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[])
   AND 10=(SELECT count(*) FROM pg_proc p WHERE p.pronamespace=n.oid)
   AND 7=(SELECT count(*) FROM pg_proc p JOIN pg_roles own ON own.oid=p.proowner
    WHERE p.pronamespace=n.oid AND own.rolname='dashboard_attention_owner' AND p.prosecdef AND p.provolatile='v'
     AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]
     AND (p.oid,p.prorettype) IN (VALUES
      (to_regprocedure('dashboard_attention.api(text,jsonb)'),'jsonb'::regtype),
      (to_regprocedure('dashboard_attention.consume(text,text,text,text,text,text,uuid)'),'jsonb'::regtype),
      (to_regprocedure('dashboard_attention.definition_digest(text,uuid)'),'text'::regtype),
      (to_regprocedure('dashboard_attention.evaluate(jsonb)'),'jsonb'::regtype),
      (to_regprocedure('dashboard_attention.final_claim(text,uuid,timestamp with time zone)'),'boolean'::regtype),
      (to_regprocedure('dashboard_attention.remember(uuid,jsonb)'),'boolean'::regtype),
      (to_regprocedure('dashboard_attention.candidate_origin(uuid)'),'jsonb'::regtype)))
   AND 5=(SELECT count(*) FROM pg_class c JOIN pg_roles own ON own.oid=c.relowner
    WHERE c.oid IN(to_regclass('dashboard_attention.tickets'),to_regclass('dashboard_attention.stamps'),
     to_regclass('dashboard_attention.candidate_causes'),to_regclass('dashboard_attention.final_claims'),to_regclass('public.break_through_clauses'))
     AND c.relkind='r' AND own.rolname='dashboard_attention_owner'
     AND (c.oid<>to_regclass('dashboard_attention.final_claims') OR
      (SELECT string_agg(attname,',' ORDER BY attnum) FROM pg_attribute
       WHERE attrelid=c.oid AND attnum>0 AND NOT attisdropped)='namespace,task_id,original_next_run_at,definition_digest,revoked')
     AND NOT(own.rolcanlogin OR own.rolinherit OR own.rolsuper OR own.rolcreaterole OR own.rolcreatedb OR own.rolreplication OR own.rolbypassrls)
     AND NOT EXISTS(SELECT FROM pg_auth_members WHERE roleid=own.oid OR member=own.oid)
     AND NOT EXISTS(SELECT FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
      WHERE a.grantee<>own.oid AND (a.grantee IS DISTINCT FROM
       (SELECT migration_role::text::regrole::oid FROM dashboard_attention.bootstrap_configuration WHERE singleton)
       OR a.privilege_type<>'SELECT' OR a.is_grantable))
     AND NOT EXISTS(SELECT FROM pg_attribute att CROSS JOIN LATERAL aclexplode(att.attacl) a
      WHERE att.attrelid=c.oid AND att.attnum>0 AND a.grantee<>own.oid))
   AND NOT EXISTS(SELECT FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
    WHERE p.pronamespace=n.oid AND a.grantee=0));
END
\$attention_catalog\$;
SELECT pg_temp.owner_attention_intact($ATTENTION_EXPECTED)")
if [[ "$ATTENTION_INTACT" != "t" ]]; then
 echo "[restore] SECURITY FAILURE: owner attention interface is incomplete; do NOT promote." >&2
 exit 1
fi

# Fleet has two artifact shapes: old ordinary evidence COPY (parents excluded),
# and complete three-table scoped history. Counts are from fixed COPY headers,
# never from an observed target catalogue promoted to recovery authority.
FLEET_METADATA=$(gunzip -c "$BACKUP_FILE" | awk '
 /^CREATE TABLE public\.fleet_cases \(/ { dc=1 }
 /^CREATE TABLE public\.fleet_case_evidence \(/ { de=1 }
 /^CREATE TABLE public\.fleet_case_links \(/ { dl=1 }
 /^COPY public\.fleet_case_evidence \(.*\) FROM stdin;$/ { copies++; ordinary=1; next }
 /^COPY butlers_fleet_restore_rows \(ordinal,relation_name,payload_hex\) FROM stdin;$/ { stages++; staged=1; next }
 (ordinary || staged) && $0=="\\." { ordinary=0; staged=0; next }
 ordinary { evidence++ }
 staged {
   n=split($0,a,"\t");
   if(n!=3 || a[3]!~/^[0-9a-f]+$/ || length(a[3])%2) bad++;
   if(a[1]==1 && a[2]=="fleet_cases") cases++;
   else if(a[1]==2 && a[2]=="fleet_case_evidence") evidence++;
   else if(a[1]==3 && a[2]=="fleet_case_links") links++;
   else bad++;
 }
 END { if(ordinary || staged || stages>1 || copies>1 || (stages && copies)) bad++;
       print dc+0,de+0,dl+0,cases+0,evidence+0,links+0,stages+0,bad+0 }')
read -r FDC FDE FDL FCR FER FLR FST FBAD <<< "$FLEET_METADATA"
if [[ "$FBAD" != "0" ]]; then
  echo "[restore] ERROR: unsupported Fleet dump framing; do NOT promote." >&2
  exit 1
fi
PGPASSWORD="$PG_PASSWORD" psql \
 --host="$PG_HOST" --port="$PG_PORT" --username="$PG_USER" --dbname="$TARGET_DB" \
 --no-password --no-align --tuples-only --set=ON_ERROR_STOP=1 --quiet -c "
DO \$fleet_integrity\$
DECLARE item record; actual bigint; parent regclass; child regclass;
BEGIN
 parent:=to_regclass('public.fleet_cases');
 IF parent IS NULL OR to_regclass('public.fleet_case_evidence') IS NULL
     OR to_regclass('public.fleet_case_links') IS NULL THEN
   -- Authentic old zero-history dumps contain only the evidence relation.
   -- This is legacy compatibility, NOT complete Fleet recovery/serving proof.
   IF $FDC<>0 OR $FDL<>0 OR $FST<>0 OR $FCR<>0 OR $FER<>0 OR $FLR<>0
      OR parent IS NOT NULL OR to_regclass('public.fleet_case_links') IS NOT NULL
      OR ($FDE=1 AND to_regclass('public.fleet_case_evidence') IS NULL) THEN
     RAISE EXCEPTION 'Fleet history requires its complete relation family';
   END IF;
   IF to_regclass('public.fleet_case_evidence') IS NOT NULL THEN
     SELECT count(*) INTO actual FROM public.fleet_case_evidence;
     IF actual<>0 THEN RAISE EXCEPTION 'orphan Fleet evidence cannot be certified'; END IF;
   END IF;
   RETURN;
 END IF;
 IF $FDC<>1 OR $FDE<>1 OR $FDL<>1 OR $FST<>1 THEN
   RAISE EXCEPTION 'Fleet target is not the declared complete history artifact';
 END IF;
 FOR item IN SELECT * FROM (VALUES
    ('fleet_cases',$FCR),('fleet_case_evidence',$FER),('fleet_case_links',$FLR)
 ) AS t(relation,expected_rows) LOOP
   EXECUTE format('SELECT count(*) FROM public.%I',item.relation) INTO actual;
   IF actual<>item.expected_rows THEN RAISE EXCEPTION 'Fleet dump population differs'; END IF;
 END LOOP;
 FOR item IN SELECT unnest(ARRAY['fleet_case_evidence','fleet_case_links']) AS relation LOOP
   child:=to_regclass('public.'||item.relation);
   IF NOT EXISTS(SELECT FROM pg_constraint f
      WHERE f.conrelid=child AND f.conname=item.relation||'_case_id_fkey'
       AND f.contype='f' AND f.confrelid=parent AND f.convalidated
       AND NOT f.condeferrable AND f.confdeltype='c'
       AND f.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=child AND attname='case_id')]
       AND f.confkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=parent AND attname='id')]
       AND 4=(SELECT count(*) FROM pg_trigger t WHERE t.tgconstraint=f.oid)
       AND NOT EXISTS(SELECT FROM pg_trigger t WHERE t.tgconstraint=f.oid AND t.tgenabled NOT IN ('O','A'))
   ) THEN RAISE EXCEPTION 'Fleet foreign key is missing, unvalidated or disabled'; END IF;
   EXECUTE format('SELECT count(*) FROM public.%I child LEFT JOIN public.fleet_cases parent '
                  'ON parent.id=child.case_id WHERE parent.id IS NULL',item.relation) INTO actual;
   IF actual<>0 THEN RAISE EXCEPTION 'orphan Fleet history cannot be certified'; END IF;
 END LOOP;
END \$fleet_integrity\$;"

echo "[restore] done — '${TARGET_DB}' is populated"
echo ""
echo "Next step: verify integrity with:"
echo "  ./scripts/pg_verify_restore.sh --target-db '${TARGET_DB}' [connection options]"
