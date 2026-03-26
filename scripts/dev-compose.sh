#!/usr/bin/env bash
# Launch Butlers dev environment via Docker Compose.
# Replaces scripts/dev.sh (tmux-based) with compose orchestration.
#
# Usage:
#   ./scripts/dev-compose.sh                       # standard mode
#   ./scripts/dev-compose.sh --hotreload           # volume-mount source for live changes
#   ./scripts/dev-compose.sh --skip-oauth-check    # skip OAuth gate
#   ./scripts/dev-compose.sh --skip-tailscale-check
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

# ── Tailscale prerequisite check ──────────────────────────────────────
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
  echo "Tailscale: OK (state: ${ts_state})"
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
