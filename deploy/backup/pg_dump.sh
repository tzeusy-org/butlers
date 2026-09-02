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
#
# Every run -- successful or not -- also rewrites BACKUP_DIR/last_run.json, a
# one-line JSON receipt for the MOST RECENT RUN (bu-xrqyu):
#
#   {"result":"failed","reason":"pg_dump_failed","exit_code":1,
#    "finished_at":"2026-08-23T02:00:11Z","artifact":null}
#
# It exists because a failed run publishes no artifact and deletes nothing, so
# the backup directory alone cannot tell "last night's run failed" from "last
# night's run was not due yet": both look like yesterday's good dump sitting
# there. GET /api/system/backups reads this file to answer the first question,
# which artifact freshness was never positioned to answer.
#
# ---------------------------------------------------------------------------
# WHAT THIS BACKUP DOES *NOT* CONTAIN  (bu-e1410 — read before restoring)
# ---------------------------------------------------------------------------
# The dump runs as $POSTGRES_USER: the shared migration/runtime login that
# scripts/init-db.sql deliberately holds at NOSUPERUSER NOCREATEROLE
# NOREPLICATION NOCREATEDB so it cannot cross the isolated-executor boundary.
# That boundary also fences a small set of trusted-bootstrap objects away from
# this login. pg_dump takes LOCK TABLE over every relation in scope before it
# writes a byte, so a single unreadable relation aborts the whole dump and —
# because a failed run is cleaned up rather than published — leaves no file at
# all. Those objects are therefore excluded by name below.
#
# The exclusion set is NOT a convenience: it is the trusted-bootstrap control
# plane, and it is excluded on purpose, for two reasons.
#
#   1. It is not recovered from a dump. scripts/init-db.sql, run by a cluster
#      superuser, is the only supported way to reconstruct these schemas,
#      roles, owners, RLS policies, and definer functions
#      (docs/operations/backup-restore.md, "Bootstrap prerequisite").
#   2. Restoring a dumped copy would be actively harmful. Every excluded object
#      is owned by a fenced role the restoring login is not a member of, so the
#      dump's `ALTER ... OWNER TO` statements would fail and leave the object
#      owned by whoever ran the restore — silently dissolving the fence that
#      init-db.sql exists to build.
#
# What that costs, stated plainly:
#   - restore_drill_executor.*                 restore-drill result ledger:
#         the LEDGER is not backed up; the drill HISTORY is. record_result()
#         writes its public.audit_log projection in the same transaction as the
#         ledger insert, so every outcome also lands in this dump as a
#         restore_drill/restore_drill_result audit row. What is lost in a
#         disaster is the ledger's authority (audit_log is writable by ordinary
#         runtime roles), not the answer to "has a drill ever passed". Measured
#         both ways in tests/scripts/test_restore_drill_evidence_backup.py.
#   - public.dnd_generation_mutations          DND generation mutation audit.
#   - public.user_context                      context-bus signals. Every row
#         carries a hard expires_at (core_042: "no indefinite signals"), so
#         this is expiring, self-regenerating situational state.
#   - public.runtime_attention_outbox
#     public.runtime_attention_delivery_lease
#     public.runtime_attention_producer_control
#         runtime-attention delivery state and its producer control row.
#   - public.expected_signals                 rebuildable liveness-qualified
#         cadence state. Source observations and connector heartbeats remain in
#         the dump; the next detector run recreates this projection.
#   - *_admin schemas                          bootstrap configuration rows
#         (role names) plus the fixed installer/finalizer functions.
#
# No ordinary application data is excluded, and tests/scripts/test_pg_dump_backup.py
# proves that against a real bootstrapped database: it fails if a fenced object
# appears that is not listed here, and equally if anything listed here is in
# fact readable. Do not add an entry to make a red run go green — an entry here
# is a decision that data will not be in the backup.
#
# Do NOT add --enable-row-security to work around a row-level-security fence.
# It turns a loud "permission denied" into a dump that silently omits the rows
# the dump role's policies hide, which is the same class of failure as no
# backup at all.

# NOTE: no `set -o pipefail` here. It is not POSIX, so the shebang above was a
# lie on any host whose /bin/sh is dash — the script died on line 1 of its own
# safety setup. The dump's exit status is captured explicitly below instead,
# which is both portable and stricter than pipefail (it reports the code).
set -eu

POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-butlers}"
POSTGRES_DB="${POSTGRES_DB:-butlers}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-14}"

# Trusted-bootstrap exclusion set. Whitespace-separated; parsed by
# tests/scripts/test_pg_dump_backup.py, so keep the assignments on one line each.
BACKUP_EXCLUDE_SCHEMAS="restore_drill_executor restore_drill_executor_admin dnd_generation_admin runtime_attention_admin"
# public.audit_log is deliberately NOT here: it carries the restore-drill
# evidence projection, and excluding it is the one edit that would silently
# empty that path. Four tests across two files fail if it is added.
BACKUP_EXCLUDE_TABLES="public.dnd_generation_mutations public.user_context public.runtime_attention_outbox public.runtime_attention_delivery_lease public.runtime_attention_producer_control public.expected_signals"

# A gzip stream smaller than this cannot hold a real dump (gzip's own
# header+footer is ~20 bytes). Matches _BACKUP_MIN_SIZE_BYTES in
# src/butlers/core/backup_facts.py, which renders the same verdict on read.
BACKUP_MIN_SIZE_BYTES=256

mkdir -p "${BACKUP_DIR}"

TIMESTAMP="$(date -u +%Y-%m-%dT%H-%M-%S)"
OUTFILE="${BACKUP_DIR}/butlers_${TIMESTAMP}.sql.gz"
TMPFILE="${OUTFILE}.tmp"
STATUSFILE="${OUTFILE}.status"

# Outcome of THIS run, recorded where the dashboard can read it (bu-xrqyu).
# A failed run publishes no artifact, so the directory keeps holding yesterday's
# good dump and artifact freshness alone reads as healthy for another 36 hours.
# This sentinel is the run's own signal, independent of any artifact's age.
RUN_SENTINEL="${BACKUP_DIR}/last_run.json"

# Set immediately before each deliberate `exit 1` below; the EXIT trap reads it
# back. An abort nobody enumerated (any `set -e` failure) leaves it empty and is
# recorded as "unexpected_error" -- never as a success.
FAILURE_REASON=""

# Fixed reason vocabulary, mirrored by _BACKUP_RUN_REASONS in
# src/butlers/core/backup_facts.py: ok, pg_dump_failed, artifact_undersize,
# artifact_corrupt, unexpected_error. Keep the two lists in step.
write_run_sentinel() {
  sentinel_exit="$1"
  sentinel_reason="$2"
  sentinel_artifact="$3"
  sentinel_tmp="${RUN_SENTINEL}.tmp"

  if [ "${sentinel_exit}" -eq 0 ]; then
    sentinel_result="success"
    sentinel_artifact_json="\"${sentinel_artifact}\""
  else
    sentinel_result="failed"
    sentinel_artifact_json="null"
  fi

  # Written to a temp file and mv'd into place so a dashboard poll never reads
  # a half-written sentinel. Every field comes from the fixed vocabulary above,
  # an exit status, or a timestamp -- never from command output, so nothing
  # here can carry a DSN, password, or dump fragment into a file the dashboard
  # renders. A sentinel that cannot be written must not change the run's exit
  # status: this runs from the EXIT trap, and failing a good backup because its
  # receipt was unwritable would be its own bug.
  if printf '{"result":"%s","reason":"%s","exit_code":%s,"finished_at":"%s","artifact":%s}\n' \
      "${sentinel_result}" \
      "${sentinel_reason}" \
      "${sentinel_exit}" \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      "${sentinel_artifact_json}" > "${sentinel_tmp}" 2>/dev/null \
     && mv -f "${sentinel_tmp}" "${RUN_SENTINEL}" 2>/dev/null; then
    return 0
  fi
  rm -f "${sentinel_tmp}" 2>/dev/null || true
  echo "[backup] WARNING: could not record run outcome at ${RUN_SENTINEL}" >&2
  return 0
}

# Remove the temp file on exit so a failed dump never leaves a partial file
# in the backup directory (the directory scanner ignores .tmp files, but this
# keeps the directory clean even if something kills the process mid-run).
#
# A failed run publishes NO file, so its only traces are this log line and the
# run sentinel. Say so unmistakably rather than letting the run end on
# pg_dump's stderr and an exit code nobody reads (bu-e1410), and record the
# outcome from the trap rather than from each failure branch (bu-xrqyu): the
# trap fires on every exit path, including the ones nobody enumerated, so no
# route out of this script can skip the signal it is supposed to leave.
cleanup() {
  status=$?
  rm -f "${TMPFILE}" "${STATUSFILE}"
  if [ "${status}" -eq 0 ]; then
    write_run_sentinel 0 ok "${OUTFILE##*/}"
  else
    write_run_sentinel "${status}" "${FAILURE_REASON:-unexpected_error}" ""
    if [ ! -f "${OUTFILE}" ]; then
      echo "[backup] FAILED: no backup artifact was produced (exit ${status});" \
           "${BACKUP_DIR} still holds only previous runs, if any" >&2
    fi
  fi
}
trap cleanup EXIT

echo "[backup] start: ${TIMESTAMP}, host=${POSTGRES_HOST}:${POSTGRES_PORT}, db=${POSTGRES_DB}"

# Build the exclusion arguments from the declared sets above.
set --
for schema in ${BACKUP_EXCLUDE_SCHEMAS}; do
  set -- "$@" "--exclude-schema=${schema}"
done
for table in ${BACKUP_EXCLUDE_TABLES}; do
  set -- "$@" "--exclude-table=${table}"
done

# pg_dump writes to stdout; we pipe through gzip into a .tmp file so the
# directory scanner in get_backup_facts() never sees a partial dump.  gzip's
# own exit status says nothing about pg_dump's, and the left-hand side of a
# pipeline runs in a subshell, so the dump records its failure in a status file
# the parent shell can read back.  The cleanup trap removes both temp files
# before the mv would make a bad dump permanent.
: > "${STATUSFILE}"
{
  PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump \
    --host="${POSTGRES_HOST}" \
    --port="${POSTGRES_PORT}" \
    --username="${POSTGRES_USER}" \
    --dbname="${POSTGRES_DB}" \
    --format=plain \
    --no-password \
    "$@" \
  || echo "$?" > "${STATUSFILE}"
} | gzip > "${TMPFILE}"

DUMP_STATUS="$(cat "${STATUSFILE}")"
if [ -n "${DUMP_STATUS}" ]; then
  FAILURE_REASON="pg_dump_failed"
  echo "[backup] FAILED: pg_dump exited ${DUMP_STATUS}; not publishing" >&2
  exit 1
fi

# Verify before publishing rather than trusting exit codes alone: a dump that
# is too small to be real, or whose gzip CRC does not check out, must never be
# mv'd into place and then counted as a good backup by the dashboard.
# `wc -c` pads its output with blanks on some implementations; strip them
# so the numeric comparison below cannot trip over leading whitespace.
TMPSIZE="$(wc -c < "${TMPFILE}" | tr -d " ")"
if [ "${TMPSIZE}" -lt "${BACKUP_MIN_SIZE_BYTES}" ]; then
  FAILURE_REASON="artifact_undersize"
  echo "[backup] FAILED: dump is ${TMPSIZE} bytes, below the" \
       "${BACKUP_MIN_SIZE_BYTES}-byte floor; not publishing" >&2
  exit 1
fi
if ! gzip -dc "${TMPFILE}" > /dev/null 2>&1; then
  FAILURE_REASON="artifact_corrupt"
  echo "[backup] FAILED: dump did not decompress cleanly; not publishing" >&2
  exit 1
fi

mv "${TMPFILE}" "${OUTFILE}"
echo "[backup] written: ${OUTFILE} ($(du -h "${OUTFILE}" | cut -f1))"

# Prune files older than BACKUP_RETAIN_DAYS days.
# -exec echo before -delete so the filename is logged before removal.
find "${BACKUP_DIR}" -maxdepth 1 -name "butlers_*.sql.gz" \
  -mtime "+${BACKUP_RETAIN_DAYS}" \
  -exec echo "[backup] pruned: {}" \; -delete

echo "[backup] done"
