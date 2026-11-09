#!/usr/bin/env bash
# The public MCP owner step on a dev deploy (RFC 0039 D3, OD14, OD20).
#
# Usage: scripts/k8s/public-mcp-owner-step.sh <env> <tag> [on|off] [release]
#
# `make deploy-dev` runs this after record-deploy.sh, whatever that returned: the release's
# migrations initContainer ran `butlers public-mcp reconcile`, and the file's own guard decides
# whether this release may act.
#
# The superuser file is rendered here, from the reviewed git tree of <tag> in this checkout
# (`git archive`, then `uv run --project <tree> butlers public-mcp owner-sql`), as bootstrap-db.sh
# reads init-db.sql from git. It is never taken from a running pod: dashboard-api is privileged
# and writable, so code in it could replace the generator. Rendering from the tag's tree, not
# from the working tree, keeps a rollback (`make deploy-dev TAG=<older sha>`) consistent with the
# image whose migrations reconciled. The captured file must pass the psql meta-command allowlist
# (public_mcp_sql_guard.py) before it is piped to the CNPG primary as the local superuser
# (`kubectl exec`, peer authentication). Nothing is written to the checkout and no Secret is read.
#
# A running dashboard-api pod of <release> on exactly <tag> must exist: the release rolled out.
# The third argument is the committed PUBLIC_MCP_OWNER_STEP_<env> switch; `off` skips the step.
# Any other environment prints a reminder and exits 0 (prod is deferred and manual, OD18).
# On any failure it prints `public-mcp owner step FAILED` and exits non-zero; nothing rolls back,
# and the public server stays fail-closed until a later deploy or a rerun of this script.
set -euo pipefail

fail() {
  echo "public-mcp owner step FAILED: $*" >&2
  exit 1
}

[ "$#" -ge 2 ] && [ -n "$1" ] && [ -n "$2" ] || {
  echo "usage: $0 <env> <tag> [on|off] [release]" >&2
  echo "public-mcp owner step FAILED: missing environment or tag" >&2
  exit 2
}
ENV_NAME="$1" TAG="$2" SWITCH="${3-on}" RELEASE="${4-butlers}"

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
# The switch is checked only for an automated environment: other environments pass an empty one.
case "$SWITCH" in
  on) ;;
  off)
    echo "public-mcp owner step: PUBLIC_MCP_OWNER_STEP_$ENV_NAME is off; skipped" >&2
    exit 0
    ;;
  *) fail "switch must be on or off, not '$SWITCH'" ;;
esac

[[ "$RELEASE" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || fail "invalid release name '$RELEASE'"
DB_NAME="${BUTLERS_DB_NAME:-butlers}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# The tag is a commit of this checkout, named by an exact prefix of its id (never a branch).
[[ "$TAG" =~ ^[0-9a-f]{7,40}$ ]] || fail "tag '$TAG' is not a commit id"
commit="$(git -C "$ROOT" rev-parse --verify --quiet "$TAG^{commit}")" \
  || fail "tag $TAG is not a commit in $ROOT (fetch it first)"
[ "${commit:0:${#TAG}}" = "$TAG" ] || fail "tag $TAG resolves to $commit"

# The release on this tag rolled out: a running, not-terminating dashboard-api pod of <release>
# whose image tag is exactly <tag>.
pods="$(kubectl -n "$APP_NAMESPACE" get pods \
  -l "app.kubernetes.io/component=dashboard-api,app.kubernetes.io/instance=$RELEASE" \
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
[ -n "$pod" ] || fail "no running dashboard-api pod of $RELEASE in $APP_NAMESPACE carries tag $TAG"

tree="$(mktemp -d)"
trap 'rm -rf -- "$tree"' EXIT
git -C "$ROOT" archive --format=tar "$commit" | tar -x -C "$tree" \
  || fail "could not export the tree of $TAG"

render() {
  env -u UV_PROJECT_ENVIRONMENT -u VIRTUAL_ENV -u PYTHONPATH -u PYTHONHOME \
    uv run --project "$tree" --frozen --no-dev --quiet butlers "$@"
}

# A tag older than the command lists no exact `owner-sql` entry: a rollback, not a failure.
listed() {  # <help text> <entry>
  printf '%s\n' "$1" | awk -v entry="$2" '$1 == entry { found = 1 } END { exit !found }'
}
help="$(render --help)" || fail "command listing failed for $TAG"
if listed "$help" public-mcp; then
  help="$(render public-mcp --help)" || fail "public-mcp command listing failed for $TAG"
fi
if ! listed "$help" owner-sql; then
  echo "public-mcp owner step: $TAG has no 'butlers public-mcp owner-sql'; skipped" >&2
  exit 0
fi

sql="$(render public-mcp owner-sql)" || fail "owner-sql generation failed for $TAG"
[ -n "$sql" ] || fail "owner-sql printed nothing for $TAG"
printf '%s\n' "$sql" | python3 -I "$ROOT/scripts/k8s/public_mcp_sql_guard.py" \
  || fail "owner-sql for $TAG has a psql meta-command outside the allowlist"

primary="$(kubectl -n "$PG_NAMESPACE" get pod \
  -l "cnpg.io/cluster=$PG_CLUSTER,cnpg.io/instanceRole=primary" -o name)" \
  || fail "could not list the $PG_CLUSTER primary"
[ "$(printf '%s\n' "$primary" | grep -c .)" = 1 ] \
  || fail "expected one primary pod for $PG_CLUSTER, found: ${primary:-none}"

echo "public-mcp owner step: applying owner-sql of $TAG ($pod rolled out) to $DB_NAME on $primary" >&2
printf '%s\n' "$sql" | kubectl -n "$PG_NAMESPACE" exec -i "$primary" -c postgres -- \
  psql -X -q -v ON_ERROR_STOP=1 -d "$DB_NAME" -f - \
  || fail "psql exited non-zero (a guard stop or a failed statement; see the error above)"
echo "public-mcp owner step: applied" >&2
