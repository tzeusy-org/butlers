#!/usr/bin/env bash
# Block Docker containers on the 'egress' network from reaching private subnets.
#
# Containers can still reach the public internet (needed for LLM APIs, Google
# OAuth, Telegram, Gmail), but cannot reach your LAN, Tailscale hosts, or
# other RFC1918 networks.
#
# Usage:
#   sudo ./scripts/egress-firewall.sh          # apply rules
#   sudo ./scripts/egress-firewall.sh --remove  # remove rules
#
# How it works:
#   Docker uses the DOCKER-USER iptables chain for user-defined rules.
#   Rules in DOCKER-USER are evaluated BEFORE Docker's own forwarding rules,
#   so they can block traffic that Docker would otherwise allow.
#
#   We insert DROP rules for private subnet destinations, but only for
#   traffic originating from the egress network's bridge interface.
#
# Prerequisites:
#   - Docker must be running (to resolve the bridge interface name)
#   - Must run as root (iptables requires CAP_NET_ADMIN)

set -euo pipefail

# Resolve the egress network's bridge interface name.
# Docker creates a bridge like "br-<hash>" for each user-defined network.
COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-rig}"
EGRESS_NETWORK="${COMPOSE_PROJECT}_egress"

resolve_bridge() {
  local bridge
  bridge=$(docker network inspect "$EGRESS_NETWORK" \
    --format '{{ .Options.com.docker.network.bridge.name }}' 2>/dev/null) || true

  # If bridge name isn't set in options, look up the interface ID
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
    echo "    docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d" >&2
    exit 1
  fi

  echo "$bridge"
}

# Private subnets to block (RFC1918 + Tailscale CGNAT + link-local)
BLOCKED_SUBNETS=(
  "10.0.0.0/8"        # RFC1918 Class A
  "172.16.0.0/12"     # RFC1918 Class B (excludes Docker's 172.17+)
  "192.168.0.0/16"    # RFC1918 Class C (your LAN)
  "100.64.0.0/10"     # CGNAT / Tailscale
  "169.254.0.0/16"    # Link-local
)

# Marker comment so we can find and remove our rules
COMMENT="butlers-egress-firewall"

apply_rules() {
  local bridge
  bridge=$(resolve_bridge)
  echo "Egress network bridge: $bridge"
  echo "Blocking private subnets for containers on '$EGRESS_NETWORK'..."

  for subnet in "${BLOCKED_SUBNETS[@]}"; do
    # Skip if rule already exists
    if iptables -C DOCKER-USER -i "$bridge" -d "$subnet" -j DROP \
        -m comment --comment "$COMMENT" 2>/dev/null; then
      echo "  $subnet — already blocked"
      continue
    fi
    iptables -I DOCKER-USER -i "$bridge" -d "$subnet" -j DROP \
      -m comment --comment "$COMMENT"
    echo "  $subnet — blocked"
  done

  echo ""
  echo "Done. Containers on '$EGRESS_NETWORK' can reach the internet but not:"
  printf '  %s\n' "${BLOCKED_SUBNETS[@]}"
}

remove_rules() {
  echo "Removing egress firewall rules..."
  # Delete all rules in DOCKER-USER that have our comment marker
  while iptables -D DOCKER-USER -m comment --comment "$COMMENT" -j DROP 2>/dev/null; do
    true
  done
  echo "Done. All '$COMMENT' rules removed from DOCKER-USER chain."
}

case "${1:-}" in
  --remove)
    remove_rules
    ;;
  *)
    apply_rules
    ;;
esac
