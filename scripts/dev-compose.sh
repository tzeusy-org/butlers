#!/usr/bin/env bash
# Launch Butlers dev environment via Docker Compose.
# Replaces scripts/dev.sh (tmux-based) with compose orchestration.
#
# Usage:
#   ./scripts/dev-compose.sh                       # standard mode
#   ./scripts/dev-compose.sh --hotreload           # volume-mount source for live changes
#   ./scripts/dev-compose.sh --skip-oauth-check    # skip OAuth gate
#   ./scripts/dev-compose.sh --skip-tailscale-check  # skip tailscale serve setup
#   ./scripts/dev-compose.sh --audio               # include live-listener (needs /dev/snd)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

# Always include "dev" profile (activates frontend-dev from base compose)
PROFILES=(dev)
COMPOSE_ENV=()
SKIP_TAILSCALE=false

for arg in "$@"; do
  case "$arg" in
    --hotreload)            PROFILES+=(hotreload) ;;
    --audio)                PROFILES+=(audio) ;;
    --skip-oauth-check)     COMPOSE_ENV+=("SKIP_OAUTH_CHECK=true") ;;
    --skip-tailscale-check) SKIP_TAILSCALE=true ;;
    *)                      echo "Unknown flag: $arg" >&2; exit 1 ;;
  esac
done

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
  FRONTEND_PORT="${FRONTEND_PORT:-41173}"

  # Tailscale serve path mappings: "path_prefix|local_target"
  # Each entry creates an HTTPS -> HTTP proxy via tailscale serve.
  SERVE_MAPPINGS=(
    "/butlers|http://localhost:${FRONTEND_PORT}/butlers"       # Dashboard UI
    "/butlers-api|http://localhost:41200"                      # Dashboard API
    "/owntracks|http://localhost:40086/owntracks"              # OwnTracks webhook
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
    export GOOGLE_OAUTH_REDIRECT_URI="${TS_BASE}/butlers-api/api/oauth/google/callback"
    export OWNTRACKS_CONNECTOR_HOST="${TS_HOSTNAME}"
    export OWNTRACKS_CONNECTOR_PORT="${TAILSCALE_HTTPS_PORT}"

    echo ""
    echo "Tailscale serve: ready (${TS_HOSTNAME})"
    echo "  Dashboard:      ${TS_BASE}/butlers/"
    echo "  API:            ${TS_BASE}/butlers-api/api"
    echo "  OwnTracks:      ${TS_BASE}/owntracks/webhook"
    echo "  OAuth callback: ${GOOGLE_OAUTH_REDIRECT_URI}"
  else
    echo "Tailscale serve: mappings applied (could not resolve hostname)"
  fi
  echo ""
fi

# ── Build compose command ─────────────────────────────────────────────
CMD=(docker compose)
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

# ── Ensure base image exists ───────────────────────────────────────────
# The base image (butlers-base) contains system deps, Node.js, LLM CLIs,
# Go binaries, and uv. It changes rarely. Build it once; Dockerfile uses
# it as FROM. Rebuild manually with: docker build -f Dockerfile.base -t butlers-base .
if ! docker image inspect butlers-base:latest &>/dev/null; then
  echo "Building butlers-base image (first time only, ~5-10 min)..."
  docker build -f Dockerfile.base -t butlers-base . || {
    echo "ERROR: Failed to build butlers-base image" >&2
    exit 1
  }
  echo ""
fi

echo "Starting Butlers dev stack..."
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
    butlers-db-dev     # PostgreSQL (future external DB)
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

# ── Build images while existing stack keeps running ────────────────────
# This means zero downtime during rebuilds — old containers serve traffic
# until the new images are ready, then we swap.
echo "Building images (existing stack stays up)..."
"${CMD[@]}" build --scale connector-whatsapp-user=0 2>/dev/null || true

# ── Swap: stop old containers, start new ones ─────────────────────────
# --remove-orphans clears containers from renamed/removed services.
"${CMD[@]}" down --remove-orphans 2>/dev/null || true
"${CMD[@]}" up -d "${SCALE_ARGS[@]}" --scale connector-whatsapp-user=0

# ── Apply egress firewall (blocks private subnet access from containers) ─
if sudo -n true 2>/dev/null; then
  sudo ALLOWED_TAILNET_HOSTS="${ALLOWED_TAILNET_HOSTS:-}" \
    "${SCRIPT_DIR}/egress-firewall.sh" && echo ""
else
  echo "NOTE: Run 'sudo ALLOWED_TAILNET_HOSTS=\"${ALLOWED_TAILNET_HOSTS:-}\" ./scripts/egress-firewall.sh'"
  echo "  to block container access to LAN/Tailscale (sudo requires a password)."
  echo ""
fi

# ── Whatsapp connector (slow Go build, non-blocking) ──────────────────
echo "Building whatsapp connector in background (Go stage, ~5 min first time)..."
"${CMD[@]}" up -d --build connector-whatsapp-user 2>/dev/null &
