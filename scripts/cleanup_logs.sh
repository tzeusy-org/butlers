#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

LOGS_DIR="${1:-"$REPO_ROOT/logs"}"
RETENTION_DAYS="${RETENTION_DAYS:-3}"

if ! [[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]]; then
  echo "Error: RETENTION_DAYS must be a non-negative integer" >&2
  exit 1
fi

if [ ! -d "$LOGS_DIR" ]; then
  echo "Error: logs directory not found: $LOGS_DIR" >&2
  exit 1
fi

echo "Deleting files in $LOGS_DIR older than $RETENTION_DAYS day(s)..."
find "$LOGS_DIR" -type f -mtime +"$RETENTION_DAYS" -print -delete

echo "Removing empty folders in $LOGS_DIR..."
find "$LOGS_DIR" -depth -mindepth 1 -type d -empty -print -delete

echo "Log cleanup complete."
