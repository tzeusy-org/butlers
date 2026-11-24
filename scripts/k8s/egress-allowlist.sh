#!/usr/bin/env bash
# Source me: resolves the dev egress allowlist (bu-viat6h.12).
#
#   . scripts/k8s/egress-allowlist.sh
#   butlers_resolve_egress_allowlist || exit 2
#
# Exports ALLOWED_TAILNET_HOSTS (space-separated IPv4 addresses), the input
# scripts/k8s/site-helm-args.sh maps onto egressPolicy.allowedCidrs. An already
# set, non-empty ALLOWED_TAILNET_HOSTS wins untouched. Otherwise each service in
# BUTLERS_EGRESS_SERVICES is resolved as <service>.$TAILNET_NAME with
# `getent ahostsv4`; any service that does not resolve fails closed, because with
# the policy on a missing address is silently blocked at runtime.
#
# Prints service names and an address count only, never an address.

# The single committed service list (docs/operations/kubernetes-deployment.md refers here).
# Same set as the compose egress firewall minus the compose-only Postgres hosts (dev
# Postgres is in-cluster), plus the transcription host the k8s stack uses.
# v-on-shenton is the Home Assistant host the connector actually dials; the
# homeassistant name alone blocked it.
BUTLERS_EGRESS_SERVICES="otel ollama tzehouse-synology homeassistant v-on-shenton wyoming-faster-whisper"

butlers_resolve_egress_allowlist() {
    if [ -n "${ALLOWED_TAILNET_HOSTS:-}" ]; then
        echo "egress allow: ALLOWED_TAILNET_HOSTS already set; not resolving"
        return 0
    fi
    if [ -z "${TAILNET_NAME:-}" ]; then
        echo "egress allow: TAILNET_NAME not set (site env); cannot build the egress allowlist" >&2
        return 2
    fi
    local svc ips all=""
    for svc in $BUTLERS_EGRESS_SERVICES; do
        ips="$({ getent ahostsv4 "$svc.$TAILNET_NAME" || true; } | awk '{print $1}' | sort -u | tr '\n' ' ')"
        if [ -z "${ips// /}" ]; then
            echo "egress allow: $svc did not resolve; refusing (retry, or set ALLOWED_TAILNET_HOSTS)" >&2
            return 2
        fi
        all="$all $ips"
        echo "egress allow: $svc resolved"
    done
    # Shared hosts (one NAS serving two services) would otherwise repeat.
    local deduped
    deduped="$(printf '%s\n' $all | sort -u | tr '\n' ' ')"
    deduped="${deduped% }"
    export ALLOWED_TAILNET_HOSTS="$deduped"
    local -a counted
    read -ra counted <<<"$deduped"
    echo "egress allowlist: ${#counted[@]} address(es)"
}
