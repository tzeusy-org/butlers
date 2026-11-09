#!/usr/bin/env bash
# The public MCP owner step on a dev deploy (RFC 0039 D3, OD14, OD20).
#
# Usage: scripts/k8s/public-mcp-owner-step.sh <env> <tag> [on|off]
#
# `make deploy-dev` runs this after record-deploy.sh, whatever that returned: the release's
# migrations initContainer ran `butlers public-mcp reconcile`, and the file's own guard decides
# whether this release may act. The file comes from a running dashboard-api pod on <tag>, the
# image whose migrations reconciled, so the file and the publication share one manifest, also on
# a rollback. It is captured whole first, then piped to the CNPG primary as the local superuser
# (`kubectl exec`, peer authentication), so a generator that dies midway feeds psql nothing.
# The file holds no secret, nothing is written to disk, and no Secret is read.
#
# The third argument is the committed PUBLIC_MCP_OWNER_STEP_<env> switch; `off` skips the step.
# Any other environment prints a reminder and exits 0 (prod is deferred and manual, OD18).
# On any failure it prints `public-mcp owner step FAILED` and exits non-zero; nothing rolls back,
# and the public server stays fail-closed until a later deploy or a rerun of this script.
set -euo pipefail

[ "$#" -ge 2 ] && [ -n "$1" ] && [ -n "$2" ] || {
  echo "usage: $0 <env> <tag> [on|off]" >&2
  echo "public-mcp owner step FAILED: missing environment or tag" >&2
  exit 2
}
ENV_NAME="$1" TAG="$2" SWITCH="${3:-on}"

fail() {
  echo "public-mcp owner step FAILED: $*" >&2
  exit 1
}

case "$SWITCH" in
  on) ;;
  off)
    echo "public-mcp owner step: PUBLIC_MCP_OWNER_STEP_$ENV_NAME is off; skipped" >&2
    exit 0
    ;;
  *) fail "switch must be on or off, not '$SWITCH'" ;;
esac

case "$ENV_NAME" in
  dev)
    APP_NAMESPACE=butlers-dev
    PG_NAMESPACE=postgres-dev
    PG_CLUSTER=postgres-dev
    ;;
  *)
    echo "public-mcp owner step: not automated for '$ENV_NAME'; run it manually per" \
      "docs/operations/public-mcp-read-surface.md" >&2
    exit 0
    ;;
esac
DB_NAME="${BUTLERS_DB_NAME:-butlers}"
# kubectl exec skips the image's `uv run` entrypoint; call the venv's console script.
BUTLERS_BIN=/app/.venv/bin/butlers

# A running, not-terminating dashboard-api pod whose image tag is exactly <tag>.
pods="$(kubectl -n "$APP_NAMESPACE" get pods -l app.kubernetes.io/component=dashboard-api \
  --field-selector=status.phase=Running --no-headers \
  -o 'custom-columns=NAME:.metadata.name,DELETING:.metadata.deletionTimestamp,IMAGE:.spec.containers[?(@.name=="dashboard-api")].image')" \
  || fail "could not list dashboard-api pods in $APP_NAMESPACE"
pod=""
while read -r name deleting image; do
  [ -n "${name:-}" ] || continue
  # custom-columns prints <none> for an unset deletion timestamp; skip terminating pods.
  [ "$deleting" = "<none>" ] || continue
  [ "${image##*:}" = "$TAG" ] && [ "$image" != "$TAG" ] || continue
  pod="$name"
  break
done <<< "$pods"
[ -n "$pod" ] || fail "no running dashboard-api pod in $APP_NAMESPACE carries tag $TAG"

in_pod() {
  kubectl -n "$APP_NAMESPACE" exec "$pod" -c dashboard-api -- "$BUTLERS_BIN" "$@"
}

# An image older than the command lists no exact `owner-sql` entry: a rollback, not a failure.
listed() {  # <help text> <entry>
  printf '%s\n' "$1" | awk -v entry="$2" '$1 == entry { found = 1 } END { exit !found }'
}
help="$(in_pod --help)" || fail "command listing failed in $pod"
if listed "$help" public-mcp; then
  help="$(in_pod public-mcp --help)" || fail "public-mcp command listing failed in $pod"
fi
if ! listed "$help" owner-sql; then
  echo "public-mcp owner step: image $TAG has no 'butlers public-mcp owner-sql'; skipped" >&2
  exit 0
fi

sql="$(in_pod public-mcp owner-sql)" || fail "owner-sql generation failed in $pod"
[ -n "$sql" ] || fail "owner-sql printed nothing in $pod"

primary="$(kubectl -n "$PG_NAMESPACE" get pod \
  -l "cnpg.io/cluster=$PG_CLUSTER,cnpg.io/instanceRole=primary" -o name)" \
  || fail "could not list the $PG_CLUSTER primary"
[ "$(printf '%s\n' "$primary" | grep -c .)" = 1 ] \
  || fail "expected one primary pod for $PG_CLUSTER, found: ${primary:-none}"

echo "public-mcp owner step: applying owner-sql from $pod ($TAG) to $DB_NAME on $primary" >&2
printf '%s\n' "$sql" | kubectl -n "$PG_NAMESPACE" exec -i "$primary" -c postgres -- \
  psql -X -q -v ON_ERROR_STOP=1 -d "$DB_NAME" -f - \
  || fail "psql exited non-zero (a guard stop or a failed statement; see the error above)"
echo "public-mcp owner step: applied" >&2
