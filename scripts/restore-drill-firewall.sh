#!/usr/bin/env bash
# Default-deny all restore-drill executor traffic except its configured
# PostgreSQL IPv4 endpoint and port.
#
# This is intentionally separate from egress-firewall.sh: the ordinary egress
# bridge is a broad application network, while restore_drill_db carries only
# the credentialed recovery executor. scripts/compose.sh creates that service
# without starting it, applies this policy, and only then starts the stack.
#
# Usage:
#   sudo RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST=203.0.113.10 \
#        RESTORE_DRILL_EXECUTOR_DB_PORT=5432 \
#        ./scripts/restore-drill-firewall.sh
#   sudo ./scripts/restore-drill-firewall.sh --remove

set -euo pipefail

COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-butlers-dev}"
RESTORE_DRILL_NETWORK="${COMPOSE_PROJECT}_restore_drill_db"
CHAIN_SUFFIX="$(printf '%s' "$RESTORE_DRILL_NETWORK" | cksum | awk '{print $1}')"
RESTORE_DRILL_CHAIN="BTRL_RD_${CHAIN_SUFFIX}"
JUMP_COMMENT="butlers-restore-drill-default-deny"
ALLOW_COMMENT="butlers-restore-drill-postgres-only"

resolve_bridge() {
    local bridge net_id
    bridge="$(docker network inspect "$RESTORE_DRILL_NETWORK" \
        --format '{{ .Options.com.docker.network.bridge.name }}' 2>/dev/null)" || true

    if [[ -z "$bridge" || "$bridge" == "<no value>" ]]; then
        net_id="$(docker network inspect "$RESTORE_DRILL_NETWORK" --format '{{.Id}}' 2>/dev/null)" || true
        if [[ -n "$net_id" ]]; then
            bridge="br-${net_id:0:12}"
        fi
    fi

    if [[ -z "$bridge" ]] || ! ip link show "$bridge" &>/dev/null; then
        printf "ERROR: Could not resolve bridge interface for network '%s'\n" "$RESTORE_DRILL_NETWORK" >&2
        printf '%s\n' '  Start it only through scripts/compose.sh so the isolated network is created first.' >&2
        exit 1
    fi

    printf '%s\n' "$bridge"
}

is_ipv4() {
    local ip="$1" octet
    local -a octets
    IFS='.' read -r -a octets <<< "$ip"
    [[ ${#octets[@]} -eq 4 ]] || return 1
    for octet in "${octets[@]}"; do
        [[ "$octet" =~ ^[0-9]{1,3}$ ]] || return 1
        ((10#$octet <= 255)) || return 1
    done
}

validate_endpoint() {
    RESTORE_DRILL_DB_HOST="${RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST:?Set RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST to the resolved PostgreSQL IPv4 endpoint}"
    RESTORE_DRILL_DB_PORT="${RESTORE_DRILL_EXECUTOR_DB_PORT:-5432}"

    if ! is_ipv4 "$RESTORE_DRILL_DB_HOST"; then
        printf '%s\n' 'ERROR: RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST must be a resolved IPv4 address.' >&2
        exit 2
    fi
    if [[ ! "$RESTORE_DRILL_DB_PORT" =~ ^[0-9]+$ ]] \
        || ((10#$RESTORE_DRILL_DB_PORT < 1 || 10#$RESTORE_DRILL_DB_PORT > 65535)); then
        printf '%s\n' 'ERROR: RESTORE_DRILL_EXECUTOR_DB_PORT must be in the range 1..65535.' >&2
        exit 2
    fi
}

apply_rules() {
    local bridge
    validate_endpoint
    bridge="$(resolve_bridge)"

    # A project-scoped chain keeps simultaneous dev/prod stacks independent.
    if ! iptables -nL "$RESTORE_DRILL_CHAIN" &>/dev/null; then
        iptables -N "$RESTORE_DRILL_CHAIN"
    fi
    # compose.sh calls this before starting the executor, so rebuilding this
    # dedicated chain cannot create a live egress window.
    iptables -F "$RESTORE_DRILL_CHAIN"
    iptables -A "$RESTORE_DRILL_CHAIN" \
        -p tcp -d "$RESTORE_DRILL_DB_HOST" --dport "$RESTORE_DRILL_DB_PORT" \
        -j ACCEPT -m comment --comment "$ALLOW_COMMENT"
    iptables -A "$RESTORE_DRILL_CHAIN" -j DROP -m comment --comment "$JUMP_COMMENT"

    if ! iptables -C DOCKER-USER -i "$bridge" -j "$RESTORE_DRILL_CHAIN" \
        -m comment --comment "$JUMP_COMMENT" 2>/dev/null; then
        iptables -I DOCKER-USER 1 -i "$bridge" -j "$RESTORE_DRILL_CHAIN" \
            -m comment --comment "$JUMP_COMMENT"
    fi

    printf 'Restore-drill bridge: %s\n' "$bridge"
    printf 'Allowed PostgreSQL endpoint: %s:%s\n' "$RESTORE_DRILL_DB_HOST" "$RESTORE_DRILL_DB_PORT"
    printf '%s\n' 'All other restore-drill outbound traffic is denied.'
}

remove_rules() {
    local bridge
    bridge="$(resolve_bridge)"

    while iptables -D DOCKER-USER -i "$bridge" -j "$RESTORE_DRILL_CHAIN" \
        -m comment --comment "$JUMP_COMMENT" 2>/dev/null; do
        true
    done
    iptables -F "$RESTORE_DRILL_CHAIN" 2>/dev/null || true
    iptables -X "$RESTORE_DRILL_CHAIN" 2>/dev/null || true
    printf 'Removed restore-drill firewall policy for bridge: %s\n' "$bridge"
}

case "${1:-}" in
    --remove)
        remove_rules
        ;;
    "")
        apply_rules
        ;;
    *)
        printf 'Usage: %s [--remove]\n' "$0" >&2
        exit 2
        ;;
esac
