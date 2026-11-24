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

. "$(dirname "${BASH_SOURCE[0]}")/../site-env.sh"

ENV_NAME="${1:?usage: $0 <dev|prod>}"
REGISTRY="${BUTLERS_IMAGE_REGISTRY:?set BUTLERS_IMAGE_REGISTRY to your image registry host}"

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

# The shared app retains its production dependency profile in both environments.
# Building the coordinated base is separate from deployment publication.
BASE_REF_PATTERN='^[a-z0-9][a-z0-9.-]*(:[0-9]+)?/[a-z0-9]+([._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}$'
if ! [[ "${BUTLERS_BASE_IMAGE:-}" =~ $BASE_REF_PATTERN ]]; then
  echo "ERROR: BUTLERS_BASE_IMAGE must be a full registry/repository@sha256 reference." >&2
  exit 1
fi
PYTHON_ARTIFACTS_CONTEXT="${BUTLERS_PYTHON_ARTIFACTS:-}"
if [[ -z "$PYTHON_ARTIFACTS_CONTEXT" || ! -d "$PYTHON_ARTIFACTS_CONTEXT" \
  || -L "$PYTHON_ARTIFACTS_CONTEXT" || ! -f "$PYTHON_ARTIFACTS_CONTEXT/provenance.json" \
  || -L "$PYTHON_ARTIFACTS_CONTEXT/provenance.json" ]]; then
  echo "ERROR: Supply a real closed Python artifacts directory with provenance.json." >&2
  exit 1
fi
PYTHON_ARTIFACTS_CONTEXT=$(cd -- "$PYTHON_ARTIFACTS_CONTEXT" && pwd -P)
# The Dockerfile mounts the closed Go module closure too (its `go-artifacts` stage).
GO_ARTIFACTS_CONTEXT="${BUTLERS_GO_ARTIFACTS:-}"
if [[ -z "$GO_ARTIFACTS_CONTEXT" || ! -d "$GO_ARTIFACTS_CONTEXT" \
  || -L "$GO_ARTIFACTS_CONTEXT" || ! -f "$GO_ARTIFACTS_CONTEXT/provenance.json" \
  || -L "$GO_ARTIFACTS_CONTEXT/provenance.json" ]]; then
  echo "ERROR: Supply a real closed Go artifacts directory with provenance.json." >&2
  exit 1
fi
GO_ARTIFACTS_CONTEXT=$(cd -- "$GO_ARTIFACTS_CONTEXT" && pwd -P)

# ── App image ─────────────────────────────────────────────────────────
APP_IMAGE="${REGISTRY}/butlers-app:${TAG}"
echo "Building ${APP_IMAGE}..." >&2
docker build --build-arg "GIT_SHA=$(git rev-parse HEAD)" \
  --build-arg "BUTLERS_BASE_IMAGE=${BUTLERS_BASE_IMAGE}" \
  --build-context "python-artifacts=${PYTHON_ARTIFACTS_CONTEXT}" \
  --build-context "go-artifacts=${GO_ARTIFACTS_CONTEXT}" \
  --target production -t "$APP_IMAGE" . >&2
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
