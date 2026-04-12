#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'EOF'
Use repo-root .env.dev values to run psql against the dev database.

Usage:
  ./.claude/skills/butler-dev-debug/scripts/dev-psql.sh [psql args...]

Examples:
  ./.claude/skills/butler-dev-debug/scripts/dev-psql.sh -c "SELECT 1"
  ./.claude/skills/butler-dev-debug/scripts/dev-psql.sh -t -A -c "SELECT now()"
EOF
  exit 0
fi

repo_root="$(git rev-parse --show-toplevel)"
env_file="${repo_root}/.env.dev"

if [[ ! -f "${env_file}" ]]; then
  echo "Missing ${env_file}" >&2
  exit 1
fi

get_env() {
  local key="$1"
  grep "^${key}=" "${env_file}" | cut -d= -f2-
}

host="$(get_env POSTGRES_HOST)"
port="$(get_env POSTGRES_PORT)"
user="$(get_env POSTGRES_USER)"
password="$(get_env POSTGRES_PASSWORD)"
db_name="$(get_env POSTGRES_DB || true)"
db_name="${db_name:-butlers}"

if [[ -z "${host}" || -z "${port}" || -z "${user}" || -z "${password}" ]]; then
  echo "Missing one of POSTGRES_HOST/PORT/USER/PASSWORD in ${env_file}" >&2
  exit 1
fi

PGPASSWORD="${password}" exec psql -h "${host}" -p "${port}" -U "${user}" -d "${db_name}" "$@"
