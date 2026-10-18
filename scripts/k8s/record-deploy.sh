#!/usr/bin/env bash
# Helm release of deploy/helm/butlers that leaves one public.deployments row.
#
# Usage (from deploy/helm/butlers): record-deploy.sh <namespace> <tag> <release> -- <helm args...>
#
# Runs `helm upgrade --install <release> .` then waits for both rollouts. Once
# they have finished it records result=success; if helm or a rollout failed it
# records result=failed with --git-sha <tag>. The row is written by
# `butlers deployments record` inside the dashboard-api pod (kubectl exec), so no
# database credential leaves the cluster. Always exits with the release status:
# a failed record only warns, so it never turns a failed release into a pass nor
# a good release into a failure.
set -uo pipefail

[ "$#" -ge 3 ] || { echo "usage: $0 <namespace> <tag> <release> -- <helm args...>" >&2; exit 2; }
ns="$1" tag="$2" release="$3"
# An empty argument (e.g. `make deploy-dev TAG=`) would shift the rest and run a wrong helm command.
{ [ -n "$ns" ] && [ -n "$tag" ] && [ -n "$release" ]; } \
  || { echo "namespace, tag and release must be non-empty" >&2; exit 2; }
shift 3
[ "${1:-}" = "--" ] && shift

# `kubectl exec` skips the image's `uv run` ENTRYPOINT, and images built before bu-ejcixc lack
# /app/.venv/bin on PATH. Call the installed console script by absolute path (no sync step);
# Dockerfile.base sets WORKDIR /app and `uv sync` puts the project venv in /app/.venv.
BUTLERS_BIN=/app/.venv/bin/butlers

record() {
  kubectl -n "$ns" exec --pod-running-timeout=30s deploy/dashboard-api -- \
    "$BUTLERS_BIN" deployments record --source deploy --result "$@"
}

status=0
helm upgrade --install "$release" . --create-namespace "$@" || status=$?
[ "$status" -eq 0 ] && { kubectl -n "$ns" rollout status deploy/dashboard-api --timeout=10m || status=$?; }
[ "$status" -eq 0 ] && { kubectl -n "$ns" rollout status deploy/butlers-up --timeout=10m || status=$?; }

if [ "$status" -eq 0 ]; then
  record success || echo "warning: release succeeded but no deployment row was recorded" >&2
else
  record failed --git-sha "$tag" \
    || echo "no deployment row recorded: kubectl exec or the ledger write failed" >&2
fi
exit "$status"
