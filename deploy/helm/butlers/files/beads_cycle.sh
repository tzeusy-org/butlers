#!/bin/sh
# One beads CronJob run: apply recorded Decision Desk intents, then refresh the
# export (bu-ckkpz.3). Both steps always run, so an applier failure never
# freezes the export; the run fails if either step failed.
#
# Writes the scratch workspace bd needs in server mode (a .beads/metadata.json
# naming the database; host, port and credential come from BEADS_DOLT_*).
#
# Env: BEADS_DOLT_DATABASE (default butlers), APPLY_DECISIONS (default 1),
#      BEADS_WORKSPACE (default /tmp/beads), BEADS_SCRIPT_DIR (default this
#      script's directory), PYTHON (default python3).
set -u

SCRIPT_DIR="${BEADS_SCRIPT_DIR:-$(dirname "$0")}"
WORKSPACE="${BEADS_WORKSPACE:-/tmp/beads}"
mkdir -p "${WORKSPACE}/.beads"
printf '{"database":"dolt","backend":"dolt","dolt_mode":"server","dolt_database":"%s"}\n' \
  "${BEADS_DOLT_DATABASE:-butlers}" > "${WORKSPACE}/.beads/metadata.json"
export BEADS_DIR="${WORKSPACE}/.beads"

status=0
if [ "${APPLY_DECISIONS:-1}" = "1" ]; then
  if ! "${PYTHON:-python3}" "${SCRIPT_DIR}/beads_decision_applier.py"; then
    echo "beads-cycle: decision applier failed; exporting anyway" >&2
    status=1
  fi
fi
if ! sh "${SCRIPT_DIR}/beads_export.sh"; then
  status=1
fi
exit "${status}"
