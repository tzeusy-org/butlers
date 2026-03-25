#!/usr/bin/env bash
# Block Docker containers on the 'egress' network from reaching private subnets,
# while allowing specific tailnet hosts (e.g. Garage S3, external Postgres).
#
# Containers can reach:
#   - Public internet (LLM APIs, Google OAuth, Telegram, Gmail)
#   - Explicitly allowed tailnet hosts (listed in ALLOWED_TAILNET_HOSTS)
#
# Containers CANNOT reach:
#   - Your LAN (192.168.0.0/16)
#   - Tailscale hosts not in the allow-list
#   - Other RFC1918 networks
#
# Usage:
#   sudo ./scripts/egress-firewall.sh          # apply rules
#   sudo ./scripts/egress-firewall.sh --remove  # remove rules
#
# Configuration:
#   ALLOWED_TAILNET_HOSTS — space-separated list of tailnet IPs to allow.
#   Set in .env or pass as env var:
#     ALLOWED_TAILNET_HOSTS="100.x.x.x 100.y.y.y" sudo ./scripts/egress-firewall.sh
#
# How it works:
#   Docker's DOCKER-USER iptables chain is evaluated BEFORE Docker's own
#   forwarding rules. We insert:
#     1. ACCEPT rules for allowed tailnet hosts (specific IPs, before DROP)
#     2. DROP rules for all private subnets (RFC1918, CGNAT, link-local)
#   Only traffic from the egress network's bridge interface is affected.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Load .env for ALLOWED_TAILNET_HOSTS if not already set ──────────
if [ -z "${ALLOWED_TAILNET_HOSTS:-}" ] && [ -f "${PROJECT_DIR}/.env" ]; then
  val=$(grep -E '^ALLOWED_TAILNET_HOSTS=' "${PROJECT_DIR}/.env" 2>/dev/null | head -1 | cut -d= -f2-) || true
  if [ -n "$val" ]; then
    ALLOWED_TAILNET_HOSTS="$val"
  fi
fi

# Space-separated list of tailnet IPs to allow through the firewall.
# Example: "100.99.218.10 100.99.218.20"
ALLOWED_TAILNET_HOSTS="${ALLOWED_TAILNET_HOSTS:-}"

# Resolve the egress network's bridge interface name.
COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-rig}"
EGRESS_NETWORK="${COMPOSE_PROJECT}_egress"

resolve_bridge() {
  local bridge
  bridge=$(docker network inspect "$EGRESS_NETWORK" \
    --format '{{ .Options.com.docker.network.bridge.name }}' 2>/dev/null) || true

  if [ -z "$bridge" ]; then
    local net_id
    net_id=$(docker network inspect "$EGRESS_NETWORK" --format '{{.Id}}' 2>/dev/null) || true
    if [ -n "$net_id" ]; then
      bridge="br-${net_id:0:12}"
    fi
  fi

  if [ -z "$bridge" ] || ! ip link show "$bridge" &>/dev/null; then
    echo "ERROR: Could not resolve bridge interface for network '$EGRESS_NETWORK'" >&2
    echo "  Make sure Docker is running and the network exists:" >&2
    echo "    docker compose up -d" >&2
    exit 1
  fi

  echo "$bridge"
}

# Private subnets to block (RFC1918 + Tailscale CGNAT + link-local)
BLOCKED_SUBNETS=(
  "10.0.0.0/8"        # RFC1918 Class A
  "172.16.0.0/12"     # RFC1918 Class B
  "192.168.0.0/16"    # RFC1918 Class C (your LAN)
  "100.64.0.0/10"     # CGNAT / Tailscale
  "169.254.0.0/16"    # Link-local
)

# Marker comments for our rules
COMMENT_ALLOW="butlers-egress-allow"
COMMENT_DROP="butlers-egress-firewall"

apply_rules() {
  local bridge
  bridge=$(resolve_bridge)
  echo "Egress network bridge: $bridge"

  # Step 1: Insert ACCEPT rules for allowed tailnet hosts.
  # These must come BEFORE the DROP rules in the chain.
  if [ -n "$ALLOWED_TAILNET_HOSTS" ]; then
    echo "Allowing specific tailnet hosts..."
    for host_ip in $ALLOWED_TAILNET_HOSTS; do
      if iptables -C DOCKER-USER -i "$bridge" -d "$host_ip" -j ACCEPT \
          -m comment --comment "$COMMENT_ALLOW" 2>/dev/null; then
        echo "  $host_ip — already allowed"
        continue
      fi
      iptables -I DOCKER-USER -i "$bridge" -d "$host_ip" -j ACCEPT \
        -m comment --comment "$COMMENT_ALLOW"
      echo "  $host_ip — allowed"
    done
  else
    echo "No ALLOWED_TAILNET_HOSTS set. To allow tailnet services, add to .env:"
    echo "  ALLOWED_TAILNET_HOSTS=\"100.x.x.x 100.y.y.y\""
  fi

  # Step 2: Insert DROP rules for private subnets.
  echo "Blocking private subnets..."
  for subnet in "${BLOCKED_SUBNETS[@]}"; do
    if iptables -C DOCKER-USER -i "$bridge" -d "$subnet" -j DROP \
        -m comment --comment "$COMMENT_DROP" 2>/dev/null; then
      echo "  $subnet — already blocked"
      continue
    fi
    # Append after ACCEPT rules (use -A instead of -I for these)
    iptables -A DOCKER-USER -i "$bridge" -d "$subnet" -j DROP \
      -m comment --comment "$COMMENT_DROP"
    echo "  $subnet — blocked"
  done

  echo ""
  if [ -n "$ALLOWED_TAILNET_HOSTS" ]; then
    echo "Allowed tailnet hosts: $ALLOWED_TAILNET_HOSTS"
  fi
  echo "Blocked subnets:"
  printf '  %s\n' "${BLOCKED_SUBNETS[@]}"
}

remove_rules() {
  echo "Removing egress firewall rules..."
  while iptables -D DOCKER-USER -m comment --comment "$COMMENT_ALLOW" -j ACCEPT 2>/dev/null; do
    true
  done
  while iptables -D DOCKER-USER -m comment --comment "$COMMENT_DROP" -j DROP 2>/dev/null; do
    true
  done
  echo "Done. All butlers egress rules removed from DOCKER-USER chain."
}

case "${1:-}" in
  --remove)
    remove_rules
    ;;
  *)
    apply_rules
    ;;
esac
