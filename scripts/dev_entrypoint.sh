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

# Read the shared run directory created by log-init (via bind-mounted volume).
# Fall back to a timestamped dir if the marker file doesn't exist yet.
if [ -f /app/logs/.current_run_dir ]; then
  RUN_DIR="$(cat /app/logs/.current_run_dir)"
else
  RUN_DIR="${BUTLERS_LOG_RUN_DIR:-/app/logs/$(date +%Y%m%d_%H%M%S)}"
fi
LOG_DIR="${RUN_DIR}/${LOG_SUBDIR}"
mkdir -p "$LOG_DIR"

LOG_FILE="${LOG_DIR}/output.log"

# Redirect stdout+stderr to both the log file AND docker logs (fd 1).
# Using process substitution preserves the service as PID 1 so Docker
# signals (SIGTERM) reach it directly for graceful shutdown.
exec "$@" > >(tee -a "$LOG_FILE") 2>&1
