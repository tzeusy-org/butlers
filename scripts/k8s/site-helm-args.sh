#!/usr/bin/env bash
# Print `--set` args for site-specific hosts taken from the environment (see scripts/site-env.sh).
# Appended after the -f files in deploy/helm/butlers/makefile, so they override values.local.yaml.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../site-env.sh"
[ -z "${BUTLERS_IMAGE_REGISTRY:-}" ] || printf '%s\n' \
    "--set image.repository=${BUTLERS_IMAGE_REGISTRY}/butlers-app" \
    "--set frontendImage.repository=${BUTLERS_IMAGE_REGISTRY}/butlers-frontend"
[ -z "${BUTLERS_PUBLIC_HOST:-}" ] || printf '%s\n' "--set publicUrl.host=${BUTLERS_PUBLIC_HOST}"
[ -z "${BUTLERS_OTLP_ENDPOINT:-}" ] || printf '%s\n' "--set commonEnv.OTEL_EXPORTER_OTLP_ENDPOINT=${BUTLERS_OTLP_ENDPOINT}"
[ -z "${LIVE_LISTENER_TRANSCRIPTION_URL:-}" ] || printf '%s\n' "--set commonEnv.LIVE_LISTENER_TRANSCRIPTION_URL=${LIVE_LISTENER_TRANSCRIPTION_URL}"
# The retained tracker-egress NetworkPolicy needs every IPv4 address of the tracker host
# it resolves to (pods are IPv4-only). An unresolvable host prints no CIDRs, so the render fails
# instead of shipping without the egress boundary.
if [ -n "${BEADS_DOLT_SERVER_HOST:-}" ]; then
    _dolt_cidrs="$(getent ahostsv4 "${BEADS_DOLT_SERVER_HOST}" 2>/dev/null \
        | awk '{print $1 "/32"}' | sort -u | paste -sd, -)"
    [ -z "${_dolt_cidrs}" ] || printf '%s\n' "--set 'trackerEgress.deniedCidrs={${_dolt_cidrs}}'"
fi
# Hosts the opt-in egress policy (egressPolicy.enabled) still admits; same input as
# scripts/egress-firewall.sh. Never committed: operators supply real addresses.
if [ -n "${ALLOWED_TAILNET_HOSTS:-}" ]; then
    read -ra _allowed_hosts <<<"${ALLOWED_TAILNET_HOSTS}"
    _allowed_cidrs=""
    for _host in "${_allowed_hosts[@]}"; do
        # IPv4 only, like the compose firewall; reject anything else rather than glob or guess.
        case "${_host}" in
            *[!0-9.]* | "") echo "site-helm-args.sh: ALLOWED_TAILNET_HOSTS entries must be IPv4 addresses" >&2; exit 1 ;;
        esac
        _allowed_cidrs="${_allowed_cidrs:+${_allowed_cidrs},}${_host}/32"
    done
    [ -z "${_allowed_cidrs}" ] || printf '%s\n' "--set 'egressPolicy.allowedCidrs={${_allowed_cidrs}}'"
fi
