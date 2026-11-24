#!/bin/sh
# deploy/backup/restic_offsite_copy.sh
#
# Off-node copy of the pg_dump directory (bu-ivuf9u).
#
# deploy/backup/pg_dump.sh writes dumps to a volume on the same single-node
# local-path storage as the database. If that storage is lost, the data and its
# dumps go together. This script copies the dump directory into an encrypted
# restic repository on the owner's second always-on tailnet device, over SFTP,
# then applies a snapshot retention policy there.
#
# It runs from the Helm CronJob `<release>-offsite-copy` (offsiteCopy.* values,
# disabled by default). Runbook: docs/operations/offsite-backup-copy.md.
#
# Required environment (the chart supplies all of it):
#   OFFSITE_HOST               device host or tailnet IP (owner-supplied)
#   OFFSITE_USER               SSH user on the device
#   OFFSITE_REPO_PATH          repository path on the device
#   RESTIC_PASSWORD_FILE       file holding the repository password (Secret)
#   OFFSITE_SSH_KEY_FILE       private key for OFFSITE_USER (Secret)
#   OFFSITE_KNOWN_HOSTS_FILE   pinned host key line(s) for OFFSITE_HOST (Secret)
# Optional:
#   BACKUP_DIR                 dump directory (default: /backups)
#   OFFSITE_PORT               SSH port (default: 22)
#   OFFSITE_SOURCE_HOST        restic --host label (default: butlers)
#   OFFSITE_KEEP_DAILY/WEEKLY/MONTHLY   retention (defaults: 14/8/6)
#   OFFSITE_INIT_REPOSITORY    "true" initializes a missing repository; any
#                              other value fails instead (owner-gated step)
#
# Every run rewrites BACKUP_DIR/offsite_last_run.json, read by
# GET /api/system/backups (offsite_copy) and the System verdict banner:
#
#   {"result":"failed","reason":"repo_unreachable","exit_code":1,
#    "finished_at":"2026-11-02T03:30:12Z","snapshot_id":null}
#
# Every field comes from a fixed vocabulary, an exit status, a timestamp, or
# the hex snapshot id -- never from command output -- so nothing secret or
# host-identifying can reach a file the dashboard renders. Restic and ssh
# output goes to the job log only. Restic never prints the repository password.

set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RECEIPT="${BACKUP_DIR}/offsite_last_run.json"
OFFSITE_PORT="${OFFSITE_PORT:-22}"
OFFSITE_SOURCE_HOST="${OFFSITE_SOURCE_HOST:-butlers}"
OFFSITE_TAG="butlers-backup-dumps"

# Fixed reason vocabulary, mirrored by _OFFSITE_COPY_REASONS in
# src/butlers/core/backup_facts.py: ok, config_missing, no_dumps,
# repo_unreachable, repo_init_failed, backup_failed, retention_failed,
# unexpected_error. Keep the two lists in step.
FAILURE_REASON=""
SNAPSHOT_ID=""

write_receipt() {
  receipt_exit="$1"
  receipt_reason="$2"
  receipt_tmp="${RECEIPT}.tmp"
  if [ "${receipt_exit}" -eq 0 ]; then
    receipt_result="success"
  else
    receipt_result="failed"
  fi
  if [ -n "${SNAPSHOT_ID}" ]; then
    receipt_snapshot="\"${SNAPSHOT_ID}\""
  else
    receipt_snapshot="null"
  fi
  # Temp file plus mv so a dashboard poll never reads half a receipt. An
  # unwritable receipt never changes the run's exit status.
  if printf '{"result":"%s","reason":"%s","exit_code":%s,"finished_at":"%s","snapshot_id":%s}\n' \
      "${receipt_result}" "${receipt_reason}" "${receipt_exit}" \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${receipt_snapshot}" > "${receipt_tmp}" 2>/dev/null \
     && mv -f "${receipt_tmp}" "${RECEIPT}" 2>/dev/null; then
    return 0
  fi
  rm -f "${receipt_tmp}" 2>/dev/null || true
  echo "[offsite] WARNING: could not record copy outcome at ${RECEIPT}" >&2
  return 0
}

# The EXIT trap records every route out, including ones nobody enumerated.
cleanup() {
  status=$?
  if [ "${status}" -eq 0 ]; then
    write_receipt 0 ok
    echo "[offsite] done: snapshot ${SNAPSHOT_ID:-unknown}"
  else
    write_receipt "${status}" "${FAILURE_REASON:-unexpected_error}"
    echo "[offsite] FAILED (${FAILURE_REASON:-unexpected_error}, exit ${status})" >&2
  fi
}
trap cleanup EXIT

fail() {
  FAILURE_REASON="$1"
  shift
  echo "[offsite] $*" >&2
  exit 1
}

for var in OFFSITE_HOST OFFSITE_USER OFFSITE_REPO_PATH; do
  eval "value=\${${var}:-}"
  [ -n "${value}" ] || fail config_missing "${var} is not set"
done
for var in RESTIC_PASSWORD_FILE OFFSITE_SSH_KEY_FILE OFFSITE_KNOWN_HOSTS_FILE; do
  eval "value=\${${var}:-}"
  [ -n "${value}" ] && [ -r "${value}" ] || fail config_missing "${var} is not a readable file"
done

set -- "${BACKUP_DIR}"/butlers_*.sql.gz
[ -e "$1" ] || fail no_dumps "no butlers_*.sql.gz in ${BACKUP_DIR}; nothing to copy"

export RESTIC_PASSWORD_FILE
export RESTIC_REPOSITORY="sftp:${OFFSITE_USER}@${OFFSITE_HOST}:${OFFSITE_REPO_PATH}"
# BatchMode never prompts; StrictHostKeyChecking=yes with a pinned
# known_hosts refuses any host key the owner did not record.
SFTP_COMMAND="ssh -i ${OFFSITE_SSH_KEY_FILE} -p ${OFFSITE_PORT} -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=${OFFSITE_KNOWN_HOSTS_FILE} -o ConnectTimeout=30 -o ServerAliveInterval=30 ${OFFSITE_USER}@${OFFSITE_HOST} -s sftp"

echo "[offsite] start: $(date -u +%Y-%m-%dT%H:%M:%SZ), source=${BACKUP_DIR}"

if ! restic -o sftp.command="${SFTP_COMMAND}" cat config > /dev/null; then
  if [ "${OFFSITE_INIT_REPOSITORY:-false}" = "true" ]; then
    echo "[offsite] repository not readable; initializing (OFFSITE_INIT_REPOSITORY=true)"
    restic -o sftp.command="${SFTP_COMMAND}" init \
      || fail repo_init_failed "restic init failed"
  else
    fail repo_unreachable "repository unreachable or not initialized; see the runbook"
  fi
fi

# Back up the directory itself, not a file list, so restic finds the parent
# snapshot and only reads the new dump.
backup_json="$(restic -o sftp.command="${SFTP_COMMAND}" backup \
    --host "${OFFSITE_SOURCE_HOST}" --tag "${OFFSITE_TAG}" --json \
    --exclude "${RECEIPT}" --exclude "${RECEIPT}.tmp" --exclude '*.tmp' \
    "${BACKUP_DIR}")" || fail backup_failed "restic backup failed"

# Keep only a hex prefix: the receipt never carries raw command output.
SNAPSHOT_ID="$(printf '%s\n' "${backup_json}" \
  | sed -n 's/.*"snapshot_id":"\([0-9a-f]\{8\}\)[0-9a-f]*".*/\1/p' | tail -n 1)"

restic -o sftp.command="${SFTP_COMMAND}" forget \
    --host "${OFFSITE_SOURCE_HOST}" --tag "${OFFSITE_TAG}" \
    --keep-daily "${OFFSITE_KEEP_DAILY:-14}" \
    --keep-weekly "${OFFSITE_KEEP_WEEKLY:-8}" \
    --keep-monthly "${OFFSITE_KEEP_MONTHLY:-6}" \
    --prune || fail retention_failed "restic forget --prune failed"
