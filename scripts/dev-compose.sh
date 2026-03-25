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
CMD=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)
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

echo "Starting Butlers dev stack..."
echo "  Profiles: ${PROFILES[*]:-default}"
echo "  Compose:  ${CMD[*]} up"
echo ""

# ── Apply egress firewall (blocks private subnet access from containers) ─
# Needs the egress network to exist, so start infra services first.
"${CMD[@]}" up -d postgres
if sudo -n true 2>/dev/null; then
  sudo "${SCRIPT_DIR}/egress-firewall.sh" && echo ""
else
  echo "NOTE: Run 'sudo ./scripts/egress-firewall.sh' to block container access to LAN/Tailscale."
  echo "  (Skipped — sudo requires a password. Containers have unrestricted outbound access.)"
  echo ""
fi

"${CMD[@]}" up --build "${SCALE_ARGS[@]}"
