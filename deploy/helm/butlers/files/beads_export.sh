#!/bin/sh
# Refresh issues.export.jsonl on the beads-export PVC from the Dolt tracker.
#
# Writes to a temp file on the same volume, then renames it into place, so a
# reader never sees a partial file. If `bd export` fails the previous export
# is left untouched (readers age it out via STALE_BEADS_EXPORT_AGE and report
# "unavailable", never an empty queue) and this script exits non-zero.
#
# Env: EXPORT_DIR (default /export), BD_BIN (default bd).
set -eu

EXPORT_DIR="${EXPORT_DIR:-/export}"
BD_BIN="${BD_BIN:-bd}"
FINAL="${EXPORT_DIR}/issues.export.jsonl"
TMP="${EXPORT_DIR}/.issues.export.jsonl.tmp.$$"

trap 'rm -f "${TMP}"' EXIT INT TERM

if ! "${BD_BIN}" export -o "${TMP}"; then
  echo "beads-export: bd export failed; keeping previous export" >&2
  exit 1
fi

if [ ! -s "${TMP}" ]; then
  echo "beads-export: bd export produced an empty file; keeping previous export" >&2
  exit 1
fi

mv -f "${TMP}" "${FINAL}"
trap - EXIT INT TERM
echo "beads-export: wrote ${FINAL}"
