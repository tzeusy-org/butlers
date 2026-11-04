#!/usr/bin/env bash
# Read-only dev topology admission; never bootstrap, scale, delete or deploy.
set -euo pipefail
if [ "$#" -lt 3 ] || [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ]; then
  echo 'namespace, tag and release must be non-empty' >&2
  exit 2
fi
# Preserve existing non-dev behavior and avoid introducing host dependencies there.
[ "$1" = butlers-dev ] || exit 0
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec uv run --no-sync --project "$ROOT" python -B \
  "$ROOT/scripts/k8s/runtime_topology_preflight.py" "$@"
