#!/usr/bin/env bash
# Build, push and deploy the current commit to butlers-dev (deploy/helm/butlers).
#
# Usage: scripts/k8s/deploy-dev.sh          build + push images for HEAD, then helm upgrade
#        scripts/k8s/deploy-dev.sh <sha>    redeploy an already-pushed tag (rollback), no build
#
#   BWS_ENV_FILE      BWS env file (BWS_ACCESS_TOKEN, BWS_PROJECT_ID); default ~/.secrets/.bws.dev.env
#   BUTLERS_SITE_ENV  site hosts file read by scripts/site-env.sh; default /secrets/.dev.env
#
# Does not run `make secrets-dev`: butlers-dev takes its Secrets from BWS through
# External Secrets (values.dev.yaml localSecrets.source=bws).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHART="$ROOT/deploy/helm/butlers"
BWS_ENV_FILE="${BWS_ENV_FILE:-$HOME/.secrets/.bws.dev.env}"
TAG="${1:-}"

# Without the site hosts the release falls back to publicUrl.host=butlers.invalid,
# so refuse instead of deploying a broken URL.
. "$ROOT/scripts/site-env.sh"
if [ -z "${BUTLERS_PUBLIC_HOST:-}" ] || [ -z "${BUTLERS_IMAGE_REGISTRY:-}" ]; then
  echo "site env not loaded: set BUTLERS_SITE_ENV (default /secrets/.dev.env) or TAILNET_NAME" >&2
  exit 1
fi

branch="$(git -C "$ROOT" branch --show-current)"
[ "$branch" = main ] || echo "warning: deploying from branch '${branch:-detached}', not main" >&2

cd "$CHART"
if [ -n "$TAG" ]; then
  make deploy-dev TAG="$TAG"
else
  [ -r "$BWS_ENV_FILE" ] || { echo "cannot read BWS env file $BWS_ENV_FILE (set BWS_ENV_FILE)" >&2; exit 1; }
  set -a; . "$BWS_ENV_FILE"; set +a
  # build-push.sh refuses a dirty tree; ship-dev builds HEAD and deploys its tag.
  bws run --project-id "$BWS_PROJECT_ID" -- make ship-dev
fi

make status-dev
