#!/usr/bin/env bash
# Helm release of deploy/helm/butlers that leaves one public.deployments row.
#
# Usage (from deploy/helm/butlers): record-deploy.sh <namespace> <tag> <release> -- <helm args...>
#
# Runs `helm upgrade --install <release> .` then waits for the required rollouts, then
# holds until semantic readiness is sustained (`butlers deployments await-ready` in the
# dashboard-api pod; REQ-dashboard-api-064): /ready true beyond the longest liveness TTL
# across two distinct observer cycles and two distinct qualifying QA patrols, bounded by
# a cadence-derived deadline (READINESS_TIMEOUT seconds may raise it, never lower it below
# the derived minimum). Only then it records result=success; if helm, a rollout, or the
# readiness wait failed it records result=failed with --git-sha <tag>. The wait prints a
# content-blind receipt and rolls nothing back. The row is written by
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

# A READINESS_TIMEOUT override below the cadence-derived floor is refused before the
# cluster changes; the in-pod wait revalidates against the released image's roster.
if [ -n "${READINESS_TIMEOUT:-}" ]; then
  root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  uv run --project "$root" butlers deployments await-ready --check-only \
    --timeout "$READINESS_TIMEOUT" --roster-dir "$root/roster" \
    || { echo "READINESS_TIMEOUT refused before helm upgrade; nothing was released" >&2; exit 2; }
fi

# The image's ENTRYPOINT wraps the CLI in `uv run`, which `kubectl exec` skips, and the venv is not
# on PATH. Call the installed console script directly (no sync step); Dockerfile.base sets
# WORKDIR /app and `uv sync` puts the project venv in /app/.venv.
BUTLERS_BIN=/app/.venv/bin/butlers

record() {
  kubectl -n "$ns" exec --pod-running-timeout=30s deploy/dashboard-api -- \
    "$BUTLERS_BIN" deployments record --source deploy --result "$@"
}

status=0
helm upgrade --install "$release" . --create-namespace "$@" || status=$?
[ "$status" -eq 0 ] && { kubectl -n "$ns" rollout status deploy/dashboard-api --timeout=10m || status=$?; }
[ "$status" -eq 0 ] && { kubectl -n "$ns" rollout status deploy/butlers-up --timeout=10m || status=$?; }
# The split control workload is optional in the chart. Do not confuse an API
# error with absence, and do not record success before an enabled control rolls.
if [ "$status" -eq 0 ]; then
  control="$(kubectl -n "$ns" get deployment butlers-control --ignore-not-found -o name)" || status=$?
  if [ "$status" -eq 0 ] && [ -n "$control" ]; then
    kubectl -n "$ns" rollout status deploy/butlers-control --timeout=10m || status=$?
  fi
fi

# Process rollout is not deployment completion: a restarted API can pass /health while
# routing fails minutes later. A readiness exec failure fails closed.
if [ "$status" -eq 0 ]; then
  kubectl -n "$ns" exec --pod-running-timeout=30s deploy/dashboard-api -- \
    "$BUTLERS_BIN" deployments await-ready ${READINESS_TIMEOUT:+--timeout "$READINESS_TIMEOUT"} \
    || status=$?
fi

if [ "$status" -eq 0 ]; then
  record success || echo "warning: release succeeded but no deployment row was recorded" >&2
else
  record failed --git-sha "$tag" \
    || echo "no deployment row recorded: kubectl exec or the ledger write failed" >&2
fi
exit "$status"
