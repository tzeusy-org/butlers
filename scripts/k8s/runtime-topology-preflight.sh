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
# A fresh worktree has an empty project venv, so do not rely on it (and do not sync every project
# dependency to read two YAML documents): run in an isolated environment holding the one module the
# script imports. Keep the pin equal to uv.lock (tests/scripts/test_k8s_runtime_topology_preflight.py).
exec uv run --no-project --with pyyaml==6.0.3 python -B \
  "$ROOT/scripts/k8s/runtime_topology_preflight.py" "$@"
