#!/usr/bin/env bash
# Read-only inspection of the protected restore-drill Compose topology.
#
# This intentionally names the protected fragment but refuses lifecycle verbs.
# Use scripts/compose.sh or `butlers deploy` for the stop/create/firewall/up
# sequence; never replace that prepared launch contract with a direct Compose
# invocation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

usage() {
    cat >&2 <<'EOF'
Usage:
  scripts/restore-drill-compose-inspect.sh config [COMPOSE_CONFIG_OPTIONS...]
  scripts/restore-drill-compose-inspect.sh ps [COMPOSE_PS_OPTIONS...]
  scripts/restore-drill-compose-inspect.sh logs [COMPOSE_LOG_OPTIONS...] [SERVICE...]

This helper is read-only. It never accepts `up`, `start`, `create`, `run`,
`restart`, `down`, or any other lifecycle command. Use scripts/compose.sh or
`butlers deploy` to prepare the firewall before starting the protected services.
EOF
}

case "${1:-}" in
    config|ps|logs)
        ;;
    ""|-h|--help)
        usage
        exit 2
        ;;
    *)
        printf 'ERROR: only read-only config, ps, and logs commands are allowed.\n' >&2
        usage
        exit 2
        ;;
esac

exec docker compose -f docker-compose.yml -f docker-compose.restore-drill.yml "$@"
