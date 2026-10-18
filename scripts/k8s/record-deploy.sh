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
shift 3
[ "${1:-}" = "--" ] && shift

record() {
  kubectl -n "$ns" exec --pod-running-timeout=30s deploy/dashboard-api -- \
    butlers deployments record --source deploy --result "$@"
}

status=0
helm upgrade --install "$release" . --create-namespace "$@" || status=$?
[ "$status" -eq 0 ] && { kubectl -n "$ns" rollout status deploy/dashboard-api --timeout=10m || status=$?; }
[ "$status" -eq 0 ] && { kubectl -n "$ns" rollout status deploy/butlers-up --timeout=10m || status=$?; }

if [ "$status" -eq 0 ]; then
  record success || echo "warning: release succeeded but no deployment row was recorded" >&2
else
  record failed --git-sha "$tag" \
    || echo "no deployment row recorded: dashboard-api was unreachable" >&2
fi
exit "$status"
