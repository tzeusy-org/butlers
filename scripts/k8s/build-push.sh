#!/usr/bin/env bash
# Build and push the Butlers images for Kubernetes (deploy/helm/butlers).
#
# Usage: scripts/k8s/build-push.sh <dev|prod>
#   Run under `bws run` so CARTO_BASEMAP_API_KEY reaches the frontend build.
#   Prints the image tag on the last line of stdout.
#
# Images:
#   $REGISTRY/butlers-app:<sha>             shared by both environments
#   $REGISTRY/butlers-frontend:<sha>-<env>  Vite bakes the base path in
set -euo pipefail

ENV_NAME="${1:?usage: $0 <dev|prod>}"
REGISTRY="${BUTLERS_IMAGE_REGISTRY:-docker-registry.parrot-hen.ts.net}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$PROJECT_DIR"

case "$ENV_NAME" in
  dev)  URL_PREFIX=butlers-dev; API_PREFIX=butlers-dev-api ;;
  prod) URL_PREFIX=butlers;     API_PREFIX=butlers-api ;;
  *) echo "unknown environment: $ENV_NAME" >&2; exit 1 ;;
esac

# Deployed images must map to a commit. ALLOW_DIRTY=1 tags a dirty tree
# distinctly so it is never mistaken for that commit.
TAG="$(git rev-parse --short=12 HEAD)"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  if [ "${ALLOW_DIRTY:-0}" != "1" ]; then
    echo "ERROR: worktree has uncommitted changes; commit or set ALLOW_DIRTY=1" >&2
    exit 1
  fi
  TAG="${TAG}-dirty-$(date +%Y%m%d%H%M%S)"
fi

# ── Base image: same freshness rule as scripts/compose.sh ──────────────
# shellcheck source=../base-image-input-fingerprint.sh
source scripts/base-image-input-fingerprint.sh
BASE_INPUT_SHA=$(butlers_base_image_input_fingerprint \
  Dockerfile.base scripts/runtime_cli_sandbox_init.c scripts/generate_runtime_cli_sandbox_manifest.py)
BASE_DOCKERFILE_SHA=$(sha256sum < Dockerfile.base | awk '{print $1}')
current=$(docker image inspect butlers-base:latest \
  --format '{{ index .Config.Labels "butlers.base.input_sha" }}' 2>/dev/null || true)
if [ "$current" != "$BASE_INPUT_SHA" ]; then
  echo "Building butlers-base (inputs changed or missing)..." >&2
  docker build \
    --label "butlers.base.dockerfile_sha=${BASE_DOCKERFILE_SHA}" \
    --label "butlers.base.input_sha=${BASE_INPUT_SHA}" \
    -f Dockerfile.base -t butlers-base . >&2
fi

# ── App image ─────────────────────────────────────────────────────────
APP_IMAGE="${REGISTRY}/butlers-app:${TAG}"
echo "Building ${APP_IMAGE}..." >&2
docker build --build-arg "GIT_SHA=$(git rev-parse HEAD)" -t "$APP_IMAGE" . >&2
docker push "$APP_IMAGE" >&2

# ── Frontend image ────────────────────────────────────────────────────
FRONTEND_IMAGE="${REGISTRY}/butlers-frontend:${TAG}-${ENV_NAME}"
echo "Building ${FRONTEND_IMAGE}..." >&2
secret_args=()
if [ -n "${CARTO_BASEMAP_API_KEY:-}" ]; then
  secret_args=(--secret id=carto_key,env=CARTO_BASEMAP_API_KEY)
else
  echo "WARNING: CARTO_BASEMAP_API_KEY unset; maps render without basemap tiles" >&2
fi
docker build -f Dockerfile.frontend "${secret_args[@]}" \
  --build-arg "BASE_PATH=/${URL_PREFIX}/" \
  --build-arg "BASE_PATH_NOSLASH=/${URL_PREFIX}" \
  --build-arg "API_URL=/${API_PREFIX}/api" \
  -t "$FRONTEND_IMAGE" . >&2
docker push "$FRONTEND_IMAGE" >&2

echo "$TAG"
