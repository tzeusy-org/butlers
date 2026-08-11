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
# executor uses a separate internal-only bridge. This wrapper also fences that
# bridge: the credentialed process can reach only the created uncredentialed
# relay, never a host or bridge gateway.

set -euo pipefail

PATH=/usr/sbin:/usr/bin:/sbin:/bin
readonly PATH

readonly JUMP_COMMENT="butlers-restore-drill-default-deny"
readonly ALLOW_COMMENT="butlers-restore-drill-postgres-only"
readonly RELAY_ALLOW_COMMENT="butlers-restore-drill-relay-only"
readonly EXECUTOR_CAPABILITY_DIRECTORY="/run/butlers/restore-drill-firewall"
readonly EXECUTOR_CAPABILITY_VERSION="butlers-restore-drill-firewall-v1"
readonly EXECUTOR_PREPARATION_VERSION="butlers-restore-drill-firewall-prepare-v1"
readonly EXECUTOR_CAPABILITY_SUFFIX=".executor-capability-v1"
readonly EXECUTOR_PREPARATION_SUFFIX=".executor-preparation-v1"

COMPOSE_PROJECT=""
RESTORE_DRILL_DB_HOST=""
RESTORE_DRILL_DB_PORT=""
DRY_RUN=false
DRY_RUN_BRIDGE=""
DRY_RUN_EXECUTOR_BRIDGE=""
DRY_RUN_RELAY_IP=""
PREPARE_EXECUTOR_CAPABILITY=false
REQUIRE_EXECUTOR_CAPABILITY=false
REMOVE=false

# These values come only from Docker inspection after `compose create`, then
# are committed atomically into the root-owned marker after both firewall
# policies succeed. They deliberately are not caller-provided arguments.
PREPARED_CAPABILITY_NONCE=""
PREPARED_EXECUTOR_CONTAINER_ID=""
PREPARED_EXECUTOR_IP=""
PREPARED_EXECUTOR_GATEWAY=""
PREPARED_RELAY_IP=""

usage() {
    cat >&2 <<'EOF'
Usage:
  butlers-restore-drill-firewall --prepare-executor-capability-v1 --project <compose-project>
  butlers-restore-drill-firewall --project <compose-project> --db-host <IPv4> --db-port <1..65535> --require-executor-capability-v1
  butlers-restore-drill-firewall --remove --project <compose-project>

The root-owned wrapper accepts only validated literal values. --dry-run and
--bridge/--executor-bridge/--relay-ip are test-only, unprivileged planning
options and are intentionally not included in the sudo policy.
EOF
}

die() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 2
}

is_ipv4() {
    local ip="$1" octet
    local -a octets
    [[ "$ip" =~ ^[0-9]+(\.[0-9]+){3}$ ]] || return 1
    IFS='.' read -r -a octets <<< "$ip"
    [[ ${#octets[@]} -eq 4 ]] || return 1
    for octet in "${octets[@]}"; do
        # Reject octal-looking spellings before iptables can normalize them to
        # a different destination than the reviewed endpoint policy accepted.
        [[ "$octet" =~ ^(0|[1-9][0-9]{0,2})$ ]] || return 1
        ((10#$octet <= 255)) || return 1
    done
}

is_remote_ipv4() {
    local ip="$1" first_octet second_octet third_octet
    is_ipv4 "$ip" || return 1
    IFS='.' read -r first_octet second_octet third_octet _ <<< "$ip"
    first_octet=$((10#$first_octet))
    second_octet=$((10#$second_octet))
    third_octet=$((10#$third_octet))

    # This must remain in parity with the launcher and proxy policy. Reject
    # local, documentation, benchmark, and special-purpose addresses while
    # allowing RFC1918, tailnet/CGNAT, and valid public unicast targets.
    ((first_octet != 0 && first_octet != 127 && first_octet < 224)) \
        && ! ((first_octet == 169 && second_octet == 254)) \
        && ! ((first_octet == 192 && second_octet == 0 && third_octet == 0)) \
        && ! ((first_octet == 192 && second_octet == 0 && third_octet == 2)) \
        && ! ((first_octet == 192 && second_octet == 31 && third_octet == 196)) \
        && ! ((first_octet == 192 && second_octet == 52 && third_octet == 193)) \
        && ! ((first_octet == 192 && second_octet == 88 && third_octet == 99)) \
        && ! ((first_octet == 192 && second_octet == 175 && third_octet == 48)) \
        && ! ((first_octet == 198 && (second_octet == 18 || second_octet == 19))) \
        && ! ((first_octet == 198 && second_octet == 51 && third_octet == 100)) \
        && ! ((first_octet == 203 && second_octet == 0 && third_octet == 113))
}

is_relay_peer_ipv4() {
    local ip="$1" first_octet second_octet
    is_ipv4 "$ip" || return 1
    IFS='.' read -r first_octet second_octet _ _ <<< "$ip"
    first_octet=$((10#$first_octet))
    second_octet=$((10#$second_octet))

    # The peer is discovered from the Docker internal bridge rather than a
    # caller-controlled endpoint. Still reject addresses that could name a
    # host-local, unspecified, multicast, or link-local target.
    ((first_octet != 0 && first_octet != 127 && first_octet < 224)) \
        && ! ((first_octet == 169 && second_octet == 254))
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
            --executor-bridge)
                (($# >= 2)) || die "--executor-bridge requires a value"
                DRY_RUN_EXECUTOR_BRIDGE="$2"
                shift 2
                ;;
            --relay-ip)
                (($# >= 2)) || die "--relay-ip requires an IPv4 value"
                DRY_RUN_RELAY_IP="$2"
                shift 2
                ;;
            --prepare-executor-capability-v1)
                PREPARE_EXECUTOR_CAPABILITY=true
                shift
                ;;
            --require-executor-capability-v1)
                REQUIRE_EXECUTOR_CAPABILITY=true
                shift
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

    if [[ "$PREPARE_EXECUTOR_CAPABILITY" == true ]]; then
        [[ "$REMOVE" == false && "$DRY_RUN" == false && "$REQUIRE_EXECUTOR_CAPABILITY" == false ]] \
            || die "--prepare-executor-capability-v1 cannot be combined with other modes"
        [[ -z "$RESTORE_DRILL_DB_HOST" && -z "$RESTORE_DRILL_DB_PORT" ]] \
            || die "--prepare-executor-capability-v1 accepts only --project"
        return
    fi

    if [[ "$DRY_RUN" == true ]]; then
        [[ -n "$DRY_RUN_BRIDGE" ]] || die "--dry-run requires --bridge"
        is_bridge_name "$DRY_RUN_BRIDGE" || die "--bridge contains unsupported characters"
        [[ -n "$DRY_RUN_EXECUTOR_BRIDGE" ]] || die "--dry-run requires --executor-bridge"
        is_bridge_name "$DRY_RUN_EXECUTOR_BRIDGE" \
            || die "--executor-bridge contains unsupported characters"
        [[ -n "$DRY_RUN_RELAY_IP" ]] || die "--dry-run requires --relay-ip"
        is_relay_peer_ipv4 "$DRY_RUN_RELAY_IP" \
            || die "--relay-ip must be a non-local IPv4 address"
    elif [[ -n "$DRY_RUN_BRIDGE" || -n "$DRY_RUN_EXECUTOR_BRIDGE" || -n "$DRY_RUN_RELAY_IP" ]]; then
        die "--bridge, --executor-bridge, and --relay-ip are valid only with --dry-run"
    fi
    if [[ "$REMOVE" == true && "$DRY_RUN" == true ]]; then
        die "--dry-run cannot be combined with --remove"
    fi
    if [[ "$REMOVE" == true && "$REQUIRE_EXECUTOR_CAPABILITY" == true ]]; then
        die "--remove cannot require an executor capability"
    fi

    if [[ "$REMOVE" == false ]]; then
        [[ -n "$RESTORE_DRILL_DB_HOST" ]] || die "--db-host is required"
        [[ -n "$RESTORE_DRILL_DB_PORT" ]] || die "--db-port is required"
        is_remote_ipv4 "$RESTORE_DRILL_DB_HOST" || die "--db-host must be a remote IPv4 address"
        if [[ ! "$RESTORE_DRILL_DB_PORT" =~ ^[1-9][0-9]{0,4}$ ]] \
            || ((10#$RESTORE_DRILL_DB_PORT < 1 || 10#$RESTORE_DRILL_DB_PORT > 65535)); then
            die "--db-port must use canonical decimal 1..65535"
        fi
        if [[ "$DRY_RUN" == false && "$REQUIRE_EXECUTOR_CAPABILITY" == false ]]; then
            die "--require-executor-capability-v1 is required for an applied policy"
        fi
    elif [[ -n "$RESTORE_DRILL_DB_HOST" || -n "$RESTORE_DRILL_DB_PORT" ]]; then
        die "--remove accepts only --project"
    fi
}

network_name() {
    local network_suffix="$1"
    printf '%s_%s\n' "$COMPOSE_PROJECT" "$network_suffix"
}

executor_capability_path() {
    printf '%s/%s%s\n' \
        "$EXECUTOR_CAPABILITY_DIRECTORY" "$COMPOSE_PROJECT" "$EXECUTOR_CAPABILITY_SUFFIX"
}

executor_preparation_path() {
    printf '%s/%s%s\n' \
        "$EXECUTOR_CAPABILITY_DIRECTORY" "$COMPOSE_PROJECT" "$EXECUTOR_PREPARATION_SUFFIX"
}

ensure_executor_capability_directory() {
    [[ ! -L "$EXECUTOR_CAPABILITY_DIRECTORY" ]] \
        || die "executor capability directory must not be a symlink"
    install -d -o root -g root -m 0711 "$EXECUTOR_CAPABILITY_DIRECTORY"
    chown root:root "$EXECUTOR_CAPABILITY_DIRECTORY"
    chmod 0711 "$EXECUTOR_CAPABILITY_DIRECTORY"
}

remove_executor_capability() {
    local path
    path="$(executor_capability_path)"
    rm -f -- "$path"
}

remove_executor_preparation() {
    local path
    path="$(executor_preparation_path)"
    rm -f -- "$path"
}

require_root_private_regular_file() {
    local path="$1" permissions
    [[ -f "$path" && ! -L "$path" && -O "$path" ]] \
        || die "executor capability file is not a root-owned regular file"
    permissions="$(stat -c '%a' -- "$path" 2>/dev/null)" \
        || die "could not inspect executor capability file"
    (( (8#$permissions & 8#077) == 0 )) \
        || die "executor capability file must not be group- or world-readable"
}

read_host_boot_id() {
    local boot_id
    boot_id="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)" || true
    [[ "$boot_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] \
        || die "could not read a valid host boot identifier"
    printf '%s\n' "$boot_id"
}

generate_capability_nonce() {
    local nonce
    nonce="$(od -An -N32 -tx1 /dev/urandom | tr -d '[:space:]')" \
        || die "could not generate executor capability nonce"
    [[ "$nonce" =~ ^[a-f0-9]{64}$ ]] || die "could not generate executor capability nonce"
    printf '%s\n' "$nonce"
}

write_executor_preparation() {
    local path temporary_path nonce boot_id
    ensure_executor_capability_directory
    path="$(executor_preparation_path)"
    nonce="$(generate_capability_nonce)"
    boot_id="$(read_host_boot_id)"
    temporary_path="$(mktemp "${EXECUTOR_CAPABILITY_DIRECTORY}/.${COMPOSE_PROJECT}.XXXXXX")" \
        || die "could not create executor preparation"
    printf '%s\nproject=%s\nnonce=%s\nboot_id=%s\n' \
        "$EXECUTOR_PREPARATION_VERSION" "$COMPOSE_PROJECT" "$nonce" "$boot_id" \
        > "$temporary_path"
    chown root:root "$temporary_path"
    chmod 0400 "$temporary_path"
    mv -f -- "$temporary_path" "$path"
    printf '%s\n' "$nonce"
}

read_prepared_executor_nonce() {
    local path boot_id nonce
    local -a lines
    path="$(executor_preparation_path)"
    require_root_private_regular_file "$path"
    mapfile -t lines < "$path"
    ((${#lines[@]} == 4)) || die "executor preparation is malformed"
    [[ "${lines[0]}" == "$EXECUTOR_PREPARATION_VERSION" ]] \
        || die "executor preparation has an unexpected version"
    [[ "${lines[1]}" == "project=${COMPOSE_PROJECT}" ]] \
        || die "executor preparation does not match the project"
    [[ "${lines[2]}" == nonce=* ]] || die "executor preparation is malformed"
    nonce="${lines[2]#nonce=}"
    [[ "$nonce" =~ ^[a-f0-9]{64}$ ]] || die "executor preparation nonce is invalid"
    boot_id="$(read_host_boot_id)"
    [[ "${lines[3]}" == "boot_id=${boot_id}" ]] \
        || die "executor preparation belongs to another host boot"
    printf '%s\n' "$nonce"
}

write_executor_capability() {
    local path temporary_path boot_id
    ensure_executor_capability_directory
    path="$(executor_capability_path)"
    boot_id="$(read_host_boot_id)"
    temporary_path="$(mktemp "${EXECUTOR_CAPABILITY_DIRECTORY}/.${COMPOSE_PROJECT}.XXXXXX")" \
        || die "could not create executor capability"
    # Persist only values the socketless executor can independently observe.
    # The wrapper still discovers Docker container/network identities while it
    # installs policy, but those host-only IDs must not masquerade as executor
    # attestation inputs.
    printf '%s\nproject=%s\nport=%s\nboot_id=%s\nnonce=%s\nexecutor_container_id=%s\nexecutor_ip=%s\nexecutor_gateway=%s\nrelay_ip=%s\n' \
        "$EXECUTOR_CAPABILITY_VERSION" "$COMPOSE_PROJECT" "$RESTORE_DRILL_DB_PORT" "$boot_id" \
        "$PREPARED_CAPABILITY_NONCE" "$PREPARED_EXECUTOR_CONTAINER_ID" \
        "$PREPARED_EXECUTOR_IP" "$PREPARED_EXECUTOR_GATEWAY" "$PREPARED_RELAY_IP" > "$temporary_path"
    chown root:root "$temporary_path"
    chmod 0400 "$temporary_path"
    mv -f -- "$temporary_path" "$path"
    remove_executor_preparation
}

prepare_executor_capability() {
    ensure_executor_capability_directory
    remove_executor_capability
    remove_executor_preparation
    write_executor_preparation
}

chain_suffix() {
    local network="$1"
    printf '%s\n' "$network" | cksum | awk '{print $1}'
}

resolve_bridge() {
    local network_suffix="$1" network bridge net_id
    network="$(network_name "$network_suffix")"

    if [[ "$DRY_RUN" == true ]]; then
        case "$network_suffix" in
            restore_drill_db)
                printf '%s\n' "$DRY_RUN_BRIDGE"
                ;;
            restore_drill_executor)
                printf '%s\n' "$DRY_RUN_EXECUTOR_BRIDGE"
                ;;
            *)
                die "unsupported restore-drill network: $network_suffix"
                ;;
        esac
        return
    fi

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

resolve_network_gateway() {
    local gateway
    gateway="$(docker network inspect "$1" \
        --format '{{with index .IPAM.Config 0}}{{.Gateway}}{{end}}' 2>/dev/null)" || true
    is_relay_peer_ipv4 "$gateway" \
        || die "could not resolve the internal relay network gateway for '$1'"
    printf '%s\n' "$gateway"
}

resolve_created_service_container() {
    local service="$1"
    local -a container_ids
    mapfile -t container_ids < <(
        docker ps --all --quiet --no-trunc \
            --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
            --filter "label=com.docker.compose.service=${service}"
    )
    if ((${#container_ids[@]} != 1)) || ! [[ "${container_ids[0]:-}" =~ ^[a-f0-9]{64}$ ]]; then
        die "expected exactly one created ${service} container for project '${COMPOSE_PROJECT}'"
    fi
    printf '%s\n' "${container_ids[0]}"
}

resolve_container_network_ipv4() {
    local container_id="$1" network="$2" service="$3" template endpoint_ip
    template="{{with index .NetworkSettings.Networks \"${network}\"}}{{.IPAddress}}{{end}}"
    endpoint_ip="$(docker inspect --format "$template" "$container_id" 2>/dev/null)" || true
    is_relay_peer_ipv4 "$endpoint_ip" \
        || die "could not resolve a non-local IPv4 ${service} endpoint on '${network}'"
    printf '%s\n' "$endpoint_ip"
}

read_container_environment_value() {
    local container_id="$1" variable_name="$2" entry value=""
    local -a entries
    mapfile -t entries < <(
        docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container_id" 2>/dev/null
    )
    for entry in "${entries[@]}"; do
        if [[ "$entry" == "${variable_name}="* ]]; then
            [[ -z "$value" ]] || die "created executor has duplicate ${variable_name} values"
            value="${entry#"${variable_name}"=}"
        fi
    done
    [[ -n "$value" ]] || die "created executor is missing ${variable_name}"
    printf '%s\n' "$value"
}

attest_prepared_executor_topology() {
    local executor_network="$1"
    local executor_nonce executor_project relay_container_id relay_gateway
    PREPARED_CAPABILITY_NONCE="$(read_prepared_executor_nonce)"
    PREPARED_EXECUTOR_CONTAINER_ID="$(resolve_created_service_container restore-drill-executor)"
    relay_container_id="$(resolve_created_service_container restore-drill-postgres-proxy)"
    executor_nonce="$(read_container_environment_value \
        "$PREPARED_EXECUTOR_CONTAINER_ID" RESTORE_DRILL_EXECUTOR_FIREWALL_CAPABILITY_NONCE)"
    [[ "$executor_nonce" == "$PREPARED_CAPABILITY_NONCE" ]] \
        || die "created executor does not carry the prepared capability nonce"
    executor_project="$(read_container_environment_value \
        "$PREPARED_EXECUTOR_CONTAINER_ID" RESTORE_DRILL_EXECUTOR_FIREWALL_PROJECT)"
    [[ "$executor_project" == "$COMPOSE_PROJECT" ]] \
        || die "created executor does not carry the prepared project identity"
    PREPARED_EXECUTOR_IP="$(resolve_container_network_ipv4 \
        "$PREPARED_EXECUTOR_CONTAINER_ID" "$executor_network" executor)"
    PREPARED_RELAY_IP="$(resolve_container_network_ipv4 \
        "$relay_container_id" "$executor_network" relay)"
    relay_gateway="$(resolve_network_gateway "$executor_network")"
    [[ "$PREPARED_RELAY_IP" != "$relay_gateway" ]] \
        || die "created restore-drill relay resolves to the internal network gateway"
    PREPARED_EXECUTOR_GATEWAY="$relay_gateway"
}

resolve_executor_relay_ip() {
    local network relay_id relay_ip gateway
    if [[ "$DRY_RUN" == true ]]; then
        printf '%s\n' "$DRY_RUN_RELAY_IP"
        return
    fi

    network="$(network_name restore_drill_executor)"
    relay_id="$(resolve_created_service_container restore-drill-postgres-proxy)"
    relay_ip="$(resolve_container_network_ipv4 "$relay_id" "$network" relay)"
    gateway="$(resolve_network_gateway "$network")"
    [[ "$relay_ip" != "$gateway" ]] \
        || die "created restore-drill relay resolves to the internal network gateway"

    printf '%s\n' "$relay_ip"
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
    local relay_network executor_network relay_bridge executor_bridge relay_ip
    local relay_suffix executor_suffix forward_chain input_chain
    local executor_forward_chain executor_input_chain
    relay_network="$(network_name restore_drill_db)"
    executor_network="$(network_name restore_drill_executor)"
    if [[ "$REQUIRE_EXECUTOR_CAPABILITY" == true && "$DRY_RUN" == false ]]; then
        ensure_executor_capability_directory
        remove_executor_capability
        attest_prepared_executor_topology "$executor_network"
    fi
    relay_bridge="$(resolve_bridge restore_drill_db)"
    executor_bridge="$(resolve_bridge restore_drill_executor)"
    relay_ip="$(resolve_executor_relay_ip)"
    relay_suffix="$(chain_suffix "$relay_network")"
    executor_suffix="$(chain_suffix "$executor_network")"
    forward_chain="BTRL_RDF_${relay_suffix}"
    input_chain="BTRL_RDI_${relay_suffix}"
    executor_forward_chain="BTRL_RDFE_${executor_suffix}"
    executor_input_chain="BTRL_RDIE_${executor_suffix}"

    ensure_chain "$forward_chain"
    run_iptables -A "$forward_chain" \
        -p tcp -d "$RESTORE_DRILL_DB_HOST" --dport "$RESTORE_DRILL_DB_PORT" \
        -j ACCEPT -m comment --comment "$ALLOW_COMMENT"
    run_iptables -A "$forward_chain" -j DROP -m comment --comment "$JUMP_COMMENT"
    # DOCKER-USER is Docker's earliest FORWARD hook for bridge packets.
    ensure_jump DOCKER-USER "$relay_bridge" "$forward_chain"

    # Packets addressed to the host or bridge gateway never traverse FORWARD.
    ensure_chain "$input_chain"
    run_iptables -A "$input_chain" \
        -p tcp -d "$RESTORE_DRILL_DB_HOST" --dport "$RESTORE_DRILL_DB_PORT" \
        -j ACCEPT -m comment --comment "$ALLOW_COMMENT"
    run_iptables -A "$input_chain" -j DROP -m comment --comment "$JUMP_COMMENT"
    ensure_jump INPUT "$relay_bridge" "$input_chain"

    # The executor's internal bridge has a Docker gateway and can otherwise
    # reach suitably configured host services. Permit only its created relay
    # peer on the configured PostgreSQL port; reply packets remain allowed by
    # conntrack, and every other forwarded packet is terminally denied.
    ensure_chain "$executor_forward_chain"
    run_iptables -A "$executor_forward_chain" \
        -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    run_iptables -A "$executor_forward_chain" \
        -p tcp -d "$relay_ip" --dport "$RESTORE_DRILL_DB_PORT" \
        -j ACCEPT -m comment --comment "$RELAY_ALLOW_COMMENT"
    run_iptables -A "$executor_forward_chain" -j DROP -m comment --comment "$JUMP_COMMENT"
    ensure_jump DOCKER-USER "$executor_bridge" "$executor_forward_chain"

    # A host or bridge-gateway destination does not traverse FORWARD.
    ensure_chain "$executor_input_chain"
    run_iptables -A "$executor_input_chain" -j DROP -m comment --comment "$JUMP_COMMENT"
    ensure_jump INPUT "$executor_bridge" "$executor_input_chain"

    if [[ "$REQUIRE_EXECUTOR_CAPABILITY" == true && "$DRY_RUN" == false ]]; then
        [[ "$relay_ip" == "$PREPARED_RELAY_IP" ]] \
            || die "created relay identity changed while applying firewall policy"
        write_executor_capability
    fi

    if [[ "$DRY_RUN" == false ]]; then
        printf 'Restore-drill relay egress bridge: %s\n' "$relay_bridge"
        printf 'Restore-drill executor relay-only bridge: %s (relay %s:%s)\n' \
            "$executor_bridge" "$relay_ip" "$RESTORE_DRILL_DB_PORT"
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
    local relay_network executor_network relay_bridge executor_bridge relay_suffix executor_suffix
    # Remove authorization before topology lookup: `docker compose down` may
    # already have destroyed the bridges, but its stale marker must never
    # authorize a later manual recreate.
    remove_executor_capability
    remove_executor_preparation
    relay_network="$(network_name restore_drill_db)"
    executor_network="$(network_name restore_drill_executor)"
    relay_bridge="$(resolve_bridge restore_drill_db)"
    executor_bridge="$(resolve_bridge restore_drill_executor)"
    relay_suffix="$(chain_suffix "$relay_network")"
    executor_suffix="$(chain_suffix "$executor_network")"
    remove_chain DOCKER-USER "$relay_bridge" "BTRL_RDF_${relay_suffix}"
    remove_chain INPUT "$relay_bridge" "BTRL_RDI_${relay_suffix}"
    remove_chain DOCKER-USER "$executor_bridge" "BTRL_RDFE_${executor_suffix}"
    remove_chain INPUT "$executor_bridge" "BTRL_RDIE_${executor_suffix}"
    printf 'Removed restore-drill firewall policies for bridges: %s, %s\n' \
        "$relay_bridge" "$executor_bridge"
}

parse_args "$@"
if [[ "$PREPARE_EXECUTOR_CAPABILITY" == true ]]; then
    prepare_executor_capability
elif [[ "$REMOVE" == true ]]; then
    remove_rules
else
    apply_rules
fi
