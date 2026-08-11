#!/bin/bash
# Install the restore-drill relay's default-deny egress network policy.
#
# This checked-in source is an installation artifact only. Supported launchers
# invoke the root-owned copy at /usr/local/libexec/butlers-restore-drill-firewall
# after stopping/creating the relay and executor and before allowing either to
# start. Never grant sudo for this checkout path: see
# install_restore_drill_firewall_wrapper.sh.
#
# The policy has two hooks because Docker's DOCKER-USER chain covers forwarded
# bridge traffic but not packets addressed to the Docker host/bridge gateway.
# The INPUT hook therefore default-denies that second path too. The credentialed
# executor uses a separate internal-only bridge and can reach only the
# uncredentialed relay on this egress bridge.

set -euo pipefail

PATH=/usr/sbin:/usr/bin:/sbin:/bin
readonly PATH

readonly JUMP_COMMENT="butlers-restore-drill-default-deny"
readonly ALLOW_COMMENT="butlers-restore-drill-postgres-only"

COMPOSE_PROJECT=""
RESTORE_DRILL_DB_HOST=""
RESTORE_DRILL_DB_PORT=""
DRY_RUN=false
DRY_RUN_BRIDGE=""
REMOVE=false

usage() {
    cat >&2 <<'EOF'
Usage:
  butlers-restore-drill-firewall --project <compose-project> --db-host <IPv4> --db-port <1..65535>
  butlers-restore-drill-firewall --remove --project <compose-project>

The root-owned wrapper accepts only validated literal values. --dry-run and
--bridge are test-only, unprivileged planning options and are intentionally not
included in the sudo policy.
EOF
}

die() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 2
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

is_project_name() {
    [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$ ]]
}

is_bridge_name() {
    [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_.:-]{0,62}$ ]]
}

parse_args() {
    while (($#)); do
        case "$1" in
            --project)
                (($# >= 2)) || die "--project requires a value"
                COMPOSE_PROJECT="$2"
                shift 2
                ;;
            --db-host)
                (($# >= 2)) || die "--db-host requires an IPv4 value"
                RESTORE_DRILL_DB_HOST="$2"
                shift 2
                ;;
            --db-port)
                (($# >= 2)) || die "--db-port requires a value"
                RESTORE_DRILL_DB_PORT="$2"
                shift 2
                ;;
            --remove)
                REMOVE=true
                shift
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            --bridge)
                (($# >= 2)) || die "--bridge requires a value"
                DRY_RUN_BRIDGE="$2"
                shift 2
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                usage
                die "unrecognized argument: $1"
                ;;
        esac
    done

    [[ -n "$COMPOSE_PROJECT" ]] || die "--project is required"
    is_project_name "$COMPOSE_PROJECT" || die "--project contains unsupported characters"

    if [[ "$DRY_RUN" == true ]]; then
        [[ -n "$DRY_RUN_BRIDGE" ]] || die "--dry-run requires --bridge"
        is_bridge_name "$DRY_RUN_BRIDGE" || die "--bridge contains unsupported characters"
    elif [[ -n "$DRY_RUN_BRIDGE" ]]; then
        die "--bridge is valid only with --dry-run"
    fi
    if [[ "$REMOVE" == true && "$DRY_RUN" == true ]]; then
        die "--dry-run cannot be combined with --remove"
    fi

    if [[ "$REMOVE" == false ]]; then
        [[ -n "$RESTORE_DRILL_DB_HOST" ]] || die "--db-host is required"
        [[ -n "$RESTORE_DRILL_DB_PORT" ]] || die "--db-port is required"
        is_ipv4 "$RESTORE_DRILL_DB_HOST" || die "--db-host must be a resolved IPv4 address"
        if [[ ! "$RESTORE_DRILL_DB_PORT" =~ ^[0-9]+$ ]] \
            || ((10#$RESTORE_DRILL_DB_PORT < 1 || 10#$RESTORE_DRILL_DB_PORT > 65535)); then
            die "--db-port must be in the range 1..65535"
        fi
    elif [[ -n "$RESTORE_DRILL_DB_HOST" || -n "$RESTORE_DRILL_DB_PORT" ]]; then
        die "--remove accepts only --project"
    fi
}

network_name() {
    printf '%s_restore_drill_db\n' "$COMPOSE_PROJECT"
}

chain_suffix() {
    network_name | cksum | awk '{print $1}'
}

resolve_bridge() {
    if [[ "$DRY_RUN" == true ]]; then
        printf '%s\n' "$DRY_RUN_BRIDGE"
        return
    fi

    local bridge net_id network
    network="$(network_name)"
    bridge="$(docker network inspect "$network" \
        --format '{{ .Options.com.docker.network.bridge.name }}' 2>/dev/null)" || true

    if [[ -z "$bridge" || "$bridge" == "<no value>" ]]; then
        net_id="$(docker network inspect "$network" --format '{{.Id}}' 2>/dev/null)" || true
        if [[ -n "$net_id" ]]; then
            bridge="br-${net_id:0:12}"
        fi
    fi

    if [[ -z "$bridge" ]] || ! ip link show "$bridge" &>/dev/null; then
        printf "ERROR: Could not resolve bridge interface for network '%s'\n" "$network" >&2
        printf '%s\n' '  The supported launcher must create the isolated network before this policy.' >&2
        exit 1
    fi

    printf '%s\n' "$bridge"
}

emit_iptables() {
    printf 'iptables'
    printf ' %s' "$@"
    printf '\n'
}

run_iptables() {
    if [[ "$DRY_RUN" == true ]]; then
        emit_iptables "$@"
    else
        iptables "$@"
    fi
}

chain_exists() {
    [[ "$DRY_RUN" == false ]] && iptables -nL "$1" &>/dev/null
}

jump_exists() {
    [[ "$DRY_RUN" == false ]] && iptables -C "$@" &>/dev/null
}

ensure_chain() {
    local chain="$1"
    if ! chain_exists "$chain"; then
        run_iptables -N "$chain"
    fi
    run_iptables -F "$chain"
}

ensure_jump() {
    local hook="$1" bridge="$2" chain="$3"
    if ! jump_exists "$hook" -i "$bridge" -j "$chain" -m comment --comment "$JUMP_COMMENT"; then
        run_iptables -I "$hook" 1 -i "$bridge" -j "$chain" -m comment --comment "$JUMP_COMMENT"
    fi
}

apply_rules() {
    local bridge suffix forward_chain input_chain
    bridge="$(resolve_bridge)"
    suffix="$(chain_suffix)"
    forward_chain="BTRL_RDF_${suffix}"
    input_chain="BTRL_RDI_${suffix}"

    ensure_chain "$forward_chain"
    run_iptables -A "$forward_chain" \
        -p tcp -d "$RESTORE_DRILL_DB_HOST" --dport "$RESTORE_DRILL_DB_PORT" \
        -j ACCEPT -m comment --comment "$ALLOW_COMMENT"
    run_iptables -A "$forward_chain" -j DROP -m comment --comment "$JUMP_COMMENT"
    # DOCKER-USER is Docker's earliest FORWARD hook for bridge packets.
    ensure_jump DOCKER-USER "$bridge" "$forward_chain"

    # Packets addressed to the host or bridge gateway never traverse FORWARD.
    ensure_chain "$input_chain"
    run_iptables -A "$input_chain" \
        -p tcp -d "$RESTORE_DRILL_DB_HOST" --dport "$RESTORE_DRILL_DB_PORT" \
        -j ACCEPT -m comment --comment "$ALLOW_COMMENT"
    run_iptables -A "$input_chain" -j DROP -m comment --comment "$JUMP_COMMENT"
    ensure_jump INPUT "$bridge" "$input_chain"

    if [[ "$DRY_RUN" == false ]]; then
        printf 'Restore-drill relay egress bridge: %s\n' "$bridge"
        printf 'Allowed PostgreSQL endpoint: %s:%s\n' "$RESTORE_DRILL_DB_HOST" "$RESTORE_DRILL_DB_PORT"
        printf '%s\n' 'All other forwarded and host/gateway restore-drill traffic is denied.'
    fi
}

remove_chain() {
    local hook="$1" bridge="$2" chain="$3"
    while iptables -D "$hook" -i "$bridge" -j "$chain" \
        -m comment --comment "$JUMP_COMMENT" 2>/dev/null; do
        true
    done
    iptables -F "$chain" 2>/dev/null || true
    iptables -X "$chain" 2>/dev/null || true
}

remove_rules() {
    local bridge suffix
    bridge="$(resolve_bridge)"
    suffix="$(chain_suffix)"
    remove_chain DOCKER-USER "$bridge" "BTRL_RDF_${suffix}"
    remove_chain INPUT "$bridge" "BTRL_RDI_${suffix}"
    printf 'Removed restore-drill firewall policy for bridge: %s\n' "$bridge"
}

parse_args "$@"
if [[ "$REMOVE" == true ]]; then
    remove_rules
else
    apply_rules
fi
