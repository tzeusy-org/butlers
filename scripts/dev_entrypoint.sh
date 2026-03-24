#!/usr/bin/env bash
# Wrapper entrypoint for dev services that redirects stdout/stderr
# to the structured log directory while still streaming to docker logs.
#
# Usage (in compose): entrypoint: ["/app/scripts/dev_entrypoint.sh", "connectors/telegram_bot"]
#   $1 = log subdirectory path (e.g. "connectors/telegram_bot")
#   remaining args = the actual command to run
set -euo pipefail

LOG_SUBDIR="${1:?log subdirectory required}"
shift

# BUTLERS_LOG_RUN_DIR is set by the log-init service and shared via env/volume.
# Fall back to a timestamped dir if not set.
RUN_DIR="${BUTLERS_LOG_RUN_DIR:-/app/logs/$(date +%Y%m%d_%H%M%S)}"
LOG_DIR="${RUN_DIR}/${LOG_SUBDIR}"
mkdir -p "$LOG_DIR"

LOG_FILE="${LOG_DIR}/output.log"

# Tee to both docker stdout AND the log file.
exec "$@" 2>&1 | tee -a "$LOG_FILE"
