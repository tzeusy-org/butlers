#!/usr/bin/env bash
# Rerun the reviewed privileged bootstrap (scripts/init-db.sql) before a rollout.
#
# Usage: scripts/k8s/bootstrap-db.sh <env> [<commit>]
#
# Online migrations only check the bootstrap profile; they never create it
# (docs/data_and_storage/reviewed-bootstrap.md). A commit whose migrations need
# a newer bootstrap surface (e.g. core_277's dashboard step-up interface) fails
# in dashboard-api's migrations initContainer unless this runs first.
#
# The script runs from <commit> (default HEAD), so a rollback to an older tag
# applies that tag's bootstrap. It executes as the CloudNativePG local superuser
# inside the primary pod (`kubectl exec`), so no superuser password exists or is
# read; the normal connecting login comes from the release's Secret without
# being printed. init-db.sql is idempotent and rerunnable by design.
#
# Only dev is wired: prod's bootstrap stays a manual operator step until its
# privileged identity is configured here (see the reviewed-bootstrap doc).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_NAME="${1:?usage: bootstrap-db.sh <env> [<commit>]}"
COMMIT="${2:-HEAD}"

case "$ENV_NAME" in
  dev)
    APP_NAMESPACE=butlers-dev
    PG_NAMESPACE=postgres-dev
    PG_CLUSTER=postgres-dev
    ;;
  *)
    echo "bootstrap-db: no privileged bootstrap configured for '$ENV_NAME'; run" \
      "scripts/init-db.sql manually per docs/data_and_storage/reviewed-bootstrap.md" >&2
    exit 0
    ;;
esac
DB_NAME="${BUTLERS_DB_NAME:-butlers}"

sql="$(git -C "$ROOT" show "$COMMIT:scripts/init-db.sql")"
primary="$(kubectl -n "$PG_NAMESPACE" get pod \
  -l "cnpg.io/cluster=$PG_CLUSTER,cnpg.io/instanceRole=primary" -o name)"
[ "$(printf '%s\n' "$primary" | grep -c .)" = 1 ] || {
  echo "bootstrap-db: expected one primary pod for $PG_CLUSTER, found: ${primary:-none}" >&2
  exit 1
}
login="$(kubectl -n "$APP_NAMESPACE" get secret butlers-bws \
  -o 'jsonpath={.data.POSTGRES_USER}' | base64 -d)"
[ -n "$login" ] || { echo "bootstrap-db: butlers-bws has no POSTGRES_USER" >&2; exit 1; }

echo "bootstrap-db: applying init-db.sql from $(git -C "$ROOT" rev-parse --short=12 "$COMMIT")" \
  "to $DB_NAME on $primary"
printf '%s\n' "$sql" | kubectl -n "$PG_NAMESPACE" exec -i "$primary" -c postgres -- \
  env PGOPTIONS="-c butlers.connecting_user=$login -c client_min_messages=warning" \
  psql -X -q -v ON_ERROR_STOP=1 -d "$DB_NAME" -f -
