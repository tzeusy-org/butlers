#!/usr/bin/env bash
# Launch Butlers via Docker Compose (dev by default, --prod for production DB).
#
# Usage:
#   ./scripts/compose.sh                           # dev database, hotreload on (default)
#   ./scripts/compose.sh --prod                    # production database, baked image
#   ./scripts/compose.sh --no-hotreload            # dev mode without source volume-mount
#   ./scripts/compose.sh --hotreload               # explicit (already on for dev; no-op)
#   ./scripts/compose.sh --skip-oauth-check        # skip OAuth gate
#   ./scripts/compose.sh --skip-tailscale-check    # skip tailscale serve setup
#   ./scripts/compose.sh --audio                   # include live-listener (needs /dev/snd)
#   ./scripts/compose.sh --observability           # enable observability stack (Prometheus, Grafana, Tempo)
#   ./scripts/compose.sh --hardened                # opt into hardened posture (disables Grafana anon viewer)
#
# DEPLOYMENT POSTURE:
#   Default posture is "dev" (anonymous Grafana viewer enabled when --observability is set).
#   To opt into hardened posture, pass --hardened or set BUTLERS_POSTURE=hardened in the environment.
#   See docs/operations/deployment-posture.md for the full posture reference.
#
# Dev defaults to hotreload because the baked image only re-bakes when this
# script rebuilds it -- editing src/ on the host has no effect on a baked
# dashboard-api or butlers-up container until rebuild + restart. Hotreload
# variants volume-mount src/ and pick up edits immediately. Pass
# --no-hotreload to reproduce the prod-style baked-image path in dev.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

# Always include the dev frontend and the restore-drill executor profiles. The
# latter is selected only by this launcher, after it has installed the
# executor's default-deny firewall below.
PROFILES=(dev restore-drill)
COMPOSE_ENV=()
SKIP_TAILSCALE=false
OBSERVABILITY=false
BUTLERS_MODE=dev
# Hotreload defaults to on for dev, off for prod; resolved after arg parsing.
# Tri-state: empty = use mode default; true/false = user opted in/out.
HOTRELOAD_OPT=""

# Deployment posture: "dev" (default when unset) or "hardened" (explicit opt-in).
# Governs security-gated toggles such as Grafana anonymous viewer access.
# Inherits from the environment; can also be set via --hardened flag.
BUTLERS_POSTURE="${BUTLERS_POSTURE:-dev}"

for arg in "$@"; do
  case "$arg" in
    --prod)                 BUTLERS_MODE=prod ;;
    --hardened)             BUTLERS_POSTURE=hardened ;;
    --hotreload)            HOTRELOAD_OPT=true ;;
    --no-hotreload)         HOTRELOAD_OPT=false ;;
    --audio)                PROFILES+=(audio) ;;
    --observability)        OBSERVABILITY=true ;;
    --skip-oauth-check)     COMPOSE_ENV+=("SKIP_OAUTH_CHECK=true") ;;
    --skip-tailscale-check) SKIP_TAILSCALE=true ;;
    *)                      echo "Unknown flag: $arg" >&2; exit 1 ;;
  esac
done

# Resolve hotreload default: dev mode opts in unless --no-hotreload is set;
# prod mode opts out unless --hotreload is set (rarely useful but allowed).
if [ -z "$HOTRELOAD_OPT" ]; then
  if [ "$BUTLERS_MODE" = "dev" ]; then
    HOTRELOAD_OPT=true
  else
    HOTRELOAD_OPT=false
  fi
fi
if [ "$HOTRELOAD_OPT" = "true" ]; then
  PROFILES+=(hotreload)
fi

# ── Load environment-specific database config ──────────────────────────
ENV_FILE="${PROJECT_DIR}/.env.${BUTLERS_MODE}"
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: Missing ${ENV_FILE}" >&2
  echo "  Create it with POSTGRES_HOST, POSTGRES_PASSWORD, etc." >&2
  exit 1
fi
set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a
echo "Database: ${BUTLERS_MODE} (${POSTGRES_HOST}:${POSTGRES_PORT:-5432})"

# The restore-drill executor has a distinct, default-deny bridge. Resolve its
# firewall endpoint on the host before Compose creates the container so the
# executor never needs DNS or any non-PostgreSQL egress. Keep a DNS connection
# host intact, though: verify-full needs it to check the PostgreSQL certificate
# identity, while Compose's extra_hosts maps it to the resolved IPv4 locally.
_restore_drill_is_ipv4() {
  local ip="$1" octet
  local -a octets
  IFS='.' read -r -a octets <<< "$ip"
  [ "${#octets[@]}" -eq 4 ] || return 1
  for octet in "${octets[@]}"; do
    [[ "$octet" =~ ^[0-9]{1,3}$ ]] || return 1
    ((10#$octet <= 255)) || return 1
  done
}

_restore_drill_is_dns_name() {
  local host="$1"
  [[ "$host" =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*$ ]]
}

_restore_drill_source_host="${RESTORE_DRILL_EXECUTOR_DB_HOST:-${POSTGRES_HOST:?Set POSTGRES_HOST in ${ENV_FILE}}}"
if ! _restore_drill_is_ipv4 "$_restore_drill_source_host" \
  && ! _restore_drill_is_dns_name "$_restore_drill_source_host"; then
  echo "ERROR: Restore-drill PostgreSQL host must be a DNS hostname or IPv4 address." >&2
  exit 1
fi
RESTORE_DRILL_EXECUTOR_DB_HOST="$_restore_drill_source_host"
if [ -n "${RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST:-}" ]; then
  if ! _restore_drill_is_ipv4 "$RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST"; then
    echo "ERROR: RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST must be a resolved PostgreSQL IPv4 endpoint." >&2
    exit 1
  fi
elif _restore_drill_is_ipv4 "$_restore_drill_source_host"; then
  RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST="$_restore_drill_source_host"
else
  RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST="$(getent ahostsv4 "$_restore_drill_source_host" | awk 'NR == 1 {print $1}')"
  if ! _restore_drill_is_ipv4 "$RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST"; then
    echo "ERROR: Could not resolve a PostgreSQL IPv4 endpoint for restore-drill executor: $_restore_drill_source_host" >&2
    exit 1
  fi
fi
RESTORE_DRILL_EXECUTOR_DB_PORT="${RESTORE_DRILL_EXECUTOR_DB_PORT:-${POSTGRES_PORT:-5432}}"
export RESTORE_DRILL_EXECUTOR_DB_HOST RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST RESTORE_DRILL_EXECUTOR_DB_PORT
echo "Restore-drill endpoint: ${RESTORE_DRILL_EXECUTOR_DB_HOST}:${RESTORE_DRILL_EXECUTOR_DB_PORT} (TLS identity; firewall IPv4 ${RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST}, default-deny bridge)"

# ── Mode-dependent configuration ────────────────────────────────────────
# Prod and dev use different URL prefixes, host ports, and project names
# so both can run simultaneously on the same machine.
if [ "$BUTLERS_MODE" = "prod" ]; then
  URL_PREFIX="butlers"
  API_PREFIX="butlers-api"
  OWNTRACKS_PREFIX="owntracks"
  export COMPOSE_PROJECT_NAME="butlers"
  export SWITCHBOARD_HOST_PORT=41100
  export DASHBOARD_HOST_PORT=41200
  export FRONTEND_HOST_PORT=41173
  export OWNTRACKS_HOST_PORT=40086
else
  URL_PREFIX="butlers-dev"
  API_PREFIX="butlers-dev-api"
  OWNTRACKS_PREFIX="owntracks-dev"
  export COMPOSE_PROJECT_NAME="butlers-dev"
  export SWITCHBOARD_HOST_PORT=42100
  export DASHBOARD_HOST_PORT=42200
  export FRONTEND_HOST_PORT=42173
  export OWNTRACKS_HOST_PORT=42086
fi
export FRONTEND_BASE_PATH="/${URL_PREFIX}/"
export VITE_API_URL="/${API_PREFIX}/api"
export OWNTRACKS_WEBHOOK_BASE_PATH="/${OWNTRACKS_PREFIX}"

# ── Tailscale serve configuration ─────────────────────────────────────
# Configure tailscale serve to expose all externally-accessible services
# with TLS termination. Required for Google OAuth (HTTPS redirect URIs)
# and for mobile app connectivity (OwnTracks).
if [ "$SKIP_TAILSCALE" = "false" ]; then
  if ! command -v tailscale &>/dev/null; then
    echo "ERROR: tailscale CLI not found. Install from https://tailscale.com/download" >&2
    echo "  Or skip: $0 --skip-tailscale-check" >&2
    exit 1
  fi
  ts_state=$(tailscale status --json 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('BackendState','Unknown'))" \
    2>/dev/null || echo "Unknown")
  if [ "$ts_state" = "NeedsLogin" ] || [ "$ts_state" = "Stopped" ]; then
    echo "ERROR: tailscale not authenticated (state: ${ts_state}). Run: tailscale up" >&2
    exit 1
  fi

  TAILSCALE_HTTPS_PORT="${TAILSCALE_HTTPS_PORT:-443}"

  # Tailscale serve path mappings: "path_prefix|local_target"
  # Each entry creates an HTTPS -> HTTP proxy via tailscale serve.
  SERVE_MAPPINGS=(
    "/${URL_PREFIX}|http://localhost:${FRONTEND_HOST_PORT}/${URL_PREFIX}"       # Dashboard UI
    "/${API_PREFIX}|http://localhost:${DASHBOARD_HOST_PORT}"                    # Dashboard API
    "/${OWNTRACKS_PREFIX}|http://localhost:${OWNTRACKS_HOST_PORT}/owntracks"    # OwnTracks webhook
  )

  # ── Helper: apply a single tailscale serve mapping ──────────────────
  _ts_run_serve() {
    local path_prefix="$1" target="$2"
    local out="" rc=0
    if [ "$path_prefix" = "/" ]; then
      out=$(tailscale serve --yes --bg --https="${TAILSCALE_HTTPS_PORT}" "$target" 2>&1) || rc=$?
    else
      out=$(tailscale serve --yes --bg --https="${TAILSCALE_HTTPS_PORT}" --set-path "$path_prefix" "$target" 2>&1) || rc=$?
    fi
    # Fallback for older tailscale CLI syntax
    if [ "$rc" -ne 0 ] && echo "$out" | grep -Eqi "(invalid argument format|unknown flag|usage)"; then
      rc=0
      if [ "$path_prefix" = "/" ]; then
        out=$(tailscale serve "https:${TAILSCALE_HTTPS_PORT}" "$target" 2>&1) || rc=$?
      else
        out=$(tailscale serve "https:${TAILSCALE_HTTPS_PORT}" "$path_prefix" "$target" 2>&1) || rc=$?
      fi
    fi
    [ -n "$out" ] && echo "    $out"
    return "$rc"
  }

  # ── Helper: check if a mapping already exists ──────────────────────
  _ts_check_mapping() {
    local target="$1" path_prefix="$2" status_json="$3"
    SERVE_STATUS_JSON="$status_json" python3 - "$target" "$path_prefix" "$TAILSCALE_HTTPS_PORT" <<'PY'
import json, os, sys
target, path_prefix, wanted_port = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.loads(os.environ.get("SERVE_STATUS_JSON", "{}"))
for hostport, cfg in (data.get("Web") or {}).items():
    for hp, handler in ((cfg or {}).get("Handlers") or {}).items():
        if hp == path_prefix and isinstance(handler, dict) and handler.get("Proxy") == target:
            try:
                port = hostport.rsplit(":", 1)[1]
            except Exception:
                port = "443"
            if port == wanted_port:
                raise SystemExit(0)
raise SystemExit(1)
PY
  }

  # ── Apply mappings ─────────────────────────────────────────────────
  echo "Tailscale serve: configuring HTTPS mappings (port ${TAILSCALE_HTTPS_PORT})..."
  serve_status=$(tailscale serve status --json 2>/dev/null || echo "{}")
  ts_serve_ok=true
  for mapping in "${SERVE_MAPPINGS[@]}"; do
    IFS='|' read -r path_prefix target <<< "$mapping"
    if _ts_check_mapping "$target" "$path_prefix" "$serve_status" 2>/dev/null; then
      echo "  ${path_prefix} -> ${target} (ok)"
    else
      echo "  ${path_prefix} -> ${target} (configuring...)"
      if ! _ts_run_serve "$path_prefix" "$target"; then
        echo "  ERROR: failed to configure ${path_prefix}" >&2
        ts_serve_ok=false
      fi
    fi
  done

  if [ "$ts_serve_ok" = "false" ]; then
    echo "" >&2
    echo "ERROR: Some tailscale serve mappings failed." >&2
    echo "  If 'Access denied', run: sudo tailscale set --operator=$USER" >&2
    echo "  To skip: $0 --skip-tailscale-check" >&2
    exit 1
  fi

  # ── Export computed URLs for docker-compose interpolation ───────────
  TS_HOSTNAME=$(tailscale status --json 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Self',{}).get('DNSName','').rstrip('.'))" \
    2>/dev/null || echo "")

  if [ -n "$TS_HOSTNAME" ]; then
    if [ "$TAILSCALE_HTTPS_PORT" = "443" ]; then
      TS_BASE="https://${TS_HOSTNAME}"
    else
      TS_BASE="https://${TS_HOSTNAME}:${TAILSCALE_HTTPS_PORT}"
    fi
    export GOOGLE_OAUTH_REDIRECT_URI="${TS_BASE}/${API_PREFIX}/api/oauth/google/callback"
    export SPOTIFY_OAUTH_REDIRECT_URI="${TS_BASE}/${API_PREFIX}/api/connectors/spotify/oauth/callback"
    export OWNTRACKS_CONNECTOR_HOST="${TS_HOSTNAME}"
    export OWNTRACKS_CONNECTOR_PORT="${TAILSCALE_HTTPS_PORT}"

    echo ""
    echo "Tailscale serve: ready (${TS_HOSTNAME})"
    echo "  Dashboard:      ${TS_BASE}/${URL_PREFIX}/"
    echo "  API:            ${TS_BASE}/${API_PREFIX}/api"
    echo "  OwnTracks:      ${TS_BASE}/${OWNTRACKS_PREFIX}/webhook"
    echo "  OAuth (Google):  ${GOOGLE_OAUTH_REDIRECT_URI}"
    echo "  OAuth (Spotify): ${SPOTIFY_OAUTH_REDIRECT_URI}"
  else
    echo "Tailscale serve: mappings applied (could not resolve hostname)"
  fi
  echo ""
fi

# ── Observability stack configuration ────────────────────────────────
if [ "$OBSERVABILITY" = "true" ]; then
  PROFILES+=(observability)
  export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318

  # Gate Grafana anonymous viewer on deployment posture.
  # dev (default when unset): anon viewer on — convenient for local iteration.
  # hardened: anon viewer off — login required (admin/admin or overridden creds).
  if [ "$BUTLERS_POSTURE" = "hardened" ]; then
    export GF_AUTH_ANONYMOUS_ENABLED=false
    echo "Observability stack enabled: Grafana at http://localhost:3000 (posture=hardened, login required)"
  else
    export GF_AUTH_ANONYMOUS_ENABLED=true
    echo "Observability stack enabled: Grafana at http://localhost:3000 (posture=dev, anonymous viewer on)"
  fi
  echo ""
fi

# ── Build compose command ─────────────────────────────────────────────
# The protected fragment is intentionally absent from bare Compose. This
# launcher stops/creates the executor, installs its firewall, then starts the
# merged service set below, so it is the only supported dev/prod command that
# includes the credentialed executor contract.
CMD=(docker compose -f docker-compose.yml -f docker-compose.restore-drill.yml)
for p in "${PROFILES[@]}"; do
  CMD+=(--profile "$p")
done

# Export env overrides
for e in "${COMPOSE_ENV[@]}"; do
  export "${e?}"
done

# ── Handle hotreload: scale down base services that hotreload replaces ─
SCALE_ARGS=()
for p in "${PROFILES[@]}"; do
  if [ "$p" = "hotreload" ]; then
    SCALE_ARGS+=(--scale butlers-up=0 --scale dashboard-api=0)
    echo "Hotreload: scaling down butlers-up and dashboard-api (replaced by *-hotreload variants)"
  fi
done

# ── Ensure base image is current ───────────────────────────────────────
# The base image (butlers-base) contains system deps, Node.js, LLM CLIs,
# and uv. Rebuild it when Dockerfile.base changes, including pinned LLM CLI
# version bumps, so app rebuilds cannot silently inherit a stale toolchain.
if command -v sha256sum &>/dev/null; then
  _hasher() { sha256sum; }
else
  _hasher() { shasum -a 256; }
fi

BASE_DOCKERFILE_SHA=$(_hasher < Dockerfile.base | awk '{print $1}')

BASE_IMAGE_SHA=$(
  docker image inspect butlers-base:latest     --format '{{ index .Config.Labels "butlers.base.dockerfile_sha" }}'     2>/dev/null || true
)

if [ -z "$BASE_IMAGE_SHA" ]; then
  echo "Building butlers-base image (~5-10 min)..."
  docker build     --label "butlers.base.dockerfile_sha=${BASE_DOCKERFILE_SHA}"     -f Dockerfile.base     -t butlers-base . || {
    echo "ERROR: Failed to build butlers-base image" >&2
    exit 1
  }
  echo ""
elif [ "$BASE_IMAGE_SHA" != "$BASE_DOCKERFILE_SHA" ]; then
  echo "Rebuilding butlers-base image because Dockerfile.base or pinned runtime CLI versions changed..."
  docker build     --label "butlers.base.dockerfile_sha=${BASE_DOCKERFILE_SHA}"     -f Dockerfile.base     -t butlers-base . || {
    echo "ERROR: Failed to rebuild butlers-base image" >&2
    exit 1
  }
  echo ""
fi

echo "Starting Butlers stack (${BUTLERS_MODE})..."
echo "  Profiles: ${PROFILES[*]:-default}"
echo "  Compose:  ${CMD[*]} up"
echo ""

# ── Resolve tailnet hosts for egress firewall allowlist ───────────────
# Butlers needs these tailnet services. Resolve IPs dynamically so the
# firewall stays correct even if tailscale reassigns addresses.
if [ -z "${ALLOWED_TAILNET_HOSTS:-}" ] && command -v tailscale &>/dev/null; then
  # Tailnet services Butlers needs to reach. Uses DNS names (the stable
  # identifiers in tailscale) to resolve current IPs.
  TAILNET_SERVICES=(
    otel               # OpenTelemetry collector (tracing)
    butlers-db-dev     # PostgreSQL for .env.dev (default target; despite "dev", the LIVE host with real data)
    butlers-db         # PostgreSQL for .env.prod (despite "prod", NOT the live host)
    ollama             # Local LLM inference
    tzehouse-synology  # Garage S3 storage
    homeassistant      # Home Assistant (home + health butler modules)
  )
  resolved=()
  ts_domain=$(tailscale status --json 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('MagicDNSSuffix',''))" \
    2>/dev/null) || true
  for host in "${TAILNET_SERVICES[@]}"; do
    ip=$(tailscale status --json 2>/dev/null \
      | python3 -c "
import sys, json
target_dns = '${host}.${ts_domain}.'
peers = json.load(sys.stdin).get('Peer', {})
for p in peers.values():
    if p.get('DNSName','') == target_dns:
        addrs = p.get('TailscaleIPs', [])
        if addrs:
            print(addrs[0])
            break
" 2>/dev/null) || true
    if [ -n "$ip" ]; then
      resolved+=("$ip")
    else
      echo "  WARN: tailnet host '$host' not found (skipped)"
    fi
  done
  if [ ${#resolved[@]} -gt 0 ]; then
    export ALLOWED_TAILNET_HOSTS="${resolved[*]}"
    echo "Tailnet allowlist: ${ALLOWED_TAILNET_HOSTS}"
  fi
fi

# ── Cap build cache to prevent unbounded growth ──────────────────────
# Docker build cache grows ~500MB+ per rebuild across all services.
# Without a cap, it consumed 717GB. Keep it under 20GB.
docker builder prune --keep-storage=20g -f 2>/dev/null || true

# ── Build shared app image (used by all services) ────────────────────
# All services reference butlers-app:${BUTLERS_APP_TAG:-latest} — includes
# Go whatsapp-bridge binary and whatsapp extra (just qrcode). One image
# for everything.
#
# Override BUTLERS_APP_TAG to pin to a specific build (e.g. a git SHA):
#   BUTLERS_APP_TAG=$(git rev-parse --short HEAD) ./scripts/compose.sh
# See docs/operations/image-bump-procedure.md for the full bump process.
BUTLERS_APP_TAG="${BUTLERS_APP_TAG:-latest}"
export BUTLERS_APP_TAG

# GIT_SHA: baked into the image (Dockerfile ARG/ENV) so the running process
# can record its own provenance in public.deployments (bu-9r3hd.2 deployments
# ledger; see src/butlers/core/deployments.py). Falls back to "unknown" when
# not run from a git checkout.
GIT_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
export GIT_SHA

echo "Building butlers-app image (tag: ${BUTLERS_APP_TAG}, sha: ${GIT_SHA})..."
DOCKER_BUILDKIT=1 docker build --build-arg GIT_SHA="${GIT_SHA}" \
  -t "butlers-app:${BUTLERS_APP_TAG}" . || {
  echo "ERROR: Failed to build butlers-app image" >&2
  exit 1
}

# Build profile-specific images (live-listener if audio profile active)
if [[ " ${PROFILES[*]} " == *" audio "* ]]; then
  echo "Building butlers-app-audio image (tag: ${BUTLERS_APP_TAG})..."
  DOCKER_BUILDKIT=1 docker build --build-arg EXTRAS=live-listener --build-arg GIT_SHA="${GIT_SHA}" \
    -t "butlers-app-audio:${BUTLERS_APP_TAG}" . || {
    echo "ERROR: Failed to build butlers-app-audio image" >&2
    exit 1
  }
fi

# ── Ensure the beads export file exists before compose mounts it ──────
# bu-hmdqz.6: docker-compose.yml bind-mounts ./.beads/issues.export.jsonl:ro
# into dashboard-api(-hotreload)/butlers-up(-hotreload). If that host path
# doesn't exist yet (fresh clone, or a worktree that's never run `bd
# export`), Docker creates a *directory* there to satisfy the mount --
# permanently breaking `bd export -o` afterwards with IsADirectoryError.
# This dev flow doesn't go through `butlers deploy`
# (src/butlers/core/deploy.py::materialize_beads_export is the prod-deploy
# equivalent of this same guard), so it needs its own. Prefer a real export
# when `bd` is available and reachable; fall back to an empty placeholder
# file otherwise -- either way, a regular file exists before `compose up`.
mkdir -p .beads
if [ ! -f .beads/issues.export.jsonl ]; then
  bd export -o .beads/issues.export.jsonl 2>/dev/null || touch .beads/issues.export.jsonl
fi

# ── Swap: stop old containers, start new ones ─────────────────────────
# --remove-orphans clears containers from renamed/removed services.
if ! "${CMD[@]}" down --remove-orphans; then
  echo "ERROR: restore-drill executor remains stopped because the prior Compose stack could not be stopped." >&2
  exit 1
fi

# Create the executor and its dedicated network without starting it. The
# default-deny PostgreSQL-only policy must exist before the privileged
# credentialed process receives a network namespace.
"${CMD[@]}" create restore-drill-executor
if ! sudo -n /usr/local/libexec/butlers-restore-drill-firewall \
  --project "${COMPOSE_PROJECT_NAME}" \
  --db-host "${RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST}" \
  --db-port "${RESTORE_DRILL_EXECUTOR_DB_PORT}"; then
  echo "ERROR: restore-drill executor remains stopped because its required root-owned firewall wrapper could not be applied." >&2
  echo "  Install /usr/local/libexec/butlers-restore-drill-firewall through the reviewed root procedure and allow only that fixed wrapper in sudoers, then rerun scripts/compose.sh." >&2
  exit 1
fi

"${CMD[@]}" up -d "${SCALE_ARGS[@]}"

# ── Apply egress firewall (blocks private subnet access from containers) ─
if sudo -n true 2>/dev/null; then
  sudo ALLOWED_TAILNET_HOSTS="${ALLOWED_TAILNET_HOSTS:-}" \
    "${SCRIPT_DIR}/egress-firewall.sh" && echo ""
else
  echo "NOTE: Run 'sudo ALLOWED_TAILNET_HOSTS=\"${ALLOWED_TAILNET_HOSTS:-}\" ./scripts/egress-firewall.sh'"
  echo "  to block container access to LAN/Tailscale (sudo requires a password)."
  echo ""
fi
