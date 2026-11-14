#!/usr/bin/env bash
# Upgrade the public MCP chart (deploy/helm/butlers-public-mcp) at the same TAG as the butlers
# release (RFC 0039 D3, task 2.4).
#
# Usage: scripts/k8s/public-mcp-chart-deploy.sh <env> <tag> [on|off]
#
# `make deploy-dev` runs this only after the owner step exited 0 (so the shipped manifest
# digest converges with the replicated one in the same deploy). The third argument is the
# committed PUBLIC_MCP_CHART_<env> switch: `off` (the default until go-live, because the server
# image must exist first) skips with a notice and exits 0. Any environment but dev also skips:
# prod is deferred (OD18).
#
# The release record lives in the butlers namespace, never in butlers-public-<env>: the chart
# creates that namespace, and helm cannot store a release in a namespace it has yet to create.
# Nothing here reads a Secret; values come from the chart and the site env. The server's
# audience and allowed owner subjects are site values that are never committed: set
# BUTLERS_PUBLIC_MCP_AUDIENCE (https URL) and BUTLERS_PUBLIC_MCP_ALLOWED_SUBS (comma-separated);
# the chart refuses to render without them. BUTLERS_PUBLIC_MCP_CLIENT_DOMAINS and
# BUTLERS_PUBLIC_MCP_REGISTERED_CLIENTS are optional.
set -euo pipefail

[ "$#" -ge 2 ] && [ -n "$1" ] && [ -n "$2" ] || {
  echo "usage: $0 <env> <tag> [on|off]" >&2
  exit 2
}
ENV_NAME="$1" TAG="$2" SWITCH="${3-off}"

case "$ENV_NAME" in
  dev) RELEASE_NAMESPACE=butlers-dev ;;
  *)
    echo "public-mcp chart: not automated for '$ENV_NAME' (prod is deferred, OD18); skipped" >&2
    exit 0
    ;;
esac
case "$SWITCH" in
  on) ;;
  off)
    echo "public-mcp chart: PUBLIC_MCP_CHART_$ENV_NAME is off; skipped" >&2
    exit 0
    ;;
  *)
    echo "public-mcp chart FAILED: switch must be on or off, not '$SWITCH'" >&2
    exit 1
    ;;
esac
[[ "$TAG" =~ ^[A-Za-z0-9_.-]+$ ]] || {
  echo "public-mcp chart FAILED: invalid tag '$TAG'" >&2
  exit 1
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHART="$ROOT/deploy/helm/butlers-public-mcp"
. "$ROOT/scripts/site-env.sh"

args=(
  upgrade --install butlers-public-mcp "$CHART"
  -n "$RELEASE_NAMESPACE"
  -f "$CHART/values.yaml" -f "$CHART/values.$ENV_NAME.yaml"
  --set "image.tag=$TAG"
  --wait --timeout 5m
)
[ -z "${BUTLERS_IMAGE_REGISTRY:-}" ] || args+=(--set "image.repository=${BUTLERS_IMAGE_REGISTRY}/butlers-app")
[ -z "${BUTLERS_PUBLIC_HOST:-}" ] || args+=(--set "dashboardBase=https://${BUTLERS_PUBLIC_HOST}/butlers-$ENV_NAME")

# --set-string with escaped commas: subjects and domains are comma-separated lists.
setlist() { args+=(--set-string "$1=${2//,/\\,}"); }
[ -z "${BUTLERS_PUBLIC_MCP_AUDIENCE:-}" ] || setlist audience "$BUTLERS_PUBLIC_MCP_AUDIENCE"
[ -z "${BUTLERS_PUBLIC_MCP_ALLOWED_SUBS:-}" ] || setlist allowedSubs "$BUTLERS_PUBLIC_MCP_ALLOWED_SUBS"
[ -z "${BUTLERS_PUBLIC_MCP_CLIENT_DOMAINS:-}" ] || setlist clientDomains "$BUTLERS_PUBLIC_MCP_CLIENT_DOMAINS"
[ -z "${BUTLERS_PUBLIC_MCP_REGISTERED_CLIENTS:-}" ] || setlist registeredClients "$BUTLERS_PUBLIC_MCP_REGISTERED_CLIENTS"

helm "${args[@]}" || {
  echo "public-mcp chart FAILED: helm upgrade of butlers-public-mcp at $TAG" >&2
  exit 1
}
