#!/usr/bin/env bash
# Print `--set` args for site-specific hosts taken from the environment (see scripts/site-env.sh).
# Appended after the -f files in deploy/helm/butlers/makefile, so they override values.local.yaml.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../site-env.sh"
[ -z "${BUTLERS_IMAGE_REGISTRY:-}" ] || printf '%s\n' \
    "--set image.repository=${BUTLERS_IMAGE_REGISTRY}/butlers-app" \
    "--set frontendImage.repository=${BUTLERS_IMAGE_REGISTRY}/butlers-frontend" \
    "--set beadsExport.imageRepository=${BUTLERS_IMAGE_REGISTRY}/butlers-beads"
[ -z "${BUTLERS_PUBLIC_HOST:-}" ] || printf '%s\n' "--set publicUrl.host=${BUTLERS_PUBLIC_HOST}"
[ -z "${BUTLERS_OTLP_ENDPOINT:-}" ] || printf '%s\n' "--set commonEnv.OTEL_EXPORTER_OTLP_ENDPOINT=${BUTLERS_OTLP_ENDPOINT}"
[ -z "${LIVE_LISTENER_TRANSCRIPTION_URL:-}" ] || printf '%s\n' "--set commonEnv.LIVE_LISTENER_TRANSCRIPTION_URL=${LIVE_LISTENER_TRANSCRIPTION_URL}"
# The tracker bridge needs the host and, for the tracker-egress NetworkPolicy, its address. An
# unresolvable host prints no CIDR, so an enabled bridge fails the render instead of shipping
# without the egress boundary.
if [ -n "${BEADS_DOLT_SERVER_HOST:-}" ]; then
    printf '%s\n' "--set beadsExport.doltHost=${BEADS_DOLT_SERVER_HOST}"
    _dolt_ip="$(getent ahostsv4 "${BEADS_DOLT_SERVER_HOST}" 2>/dev/null | awk 'NR == 1 {print $1}')"
    [ -z "${_dolt_ip}" ] || printf '%s\n' "--set beadsExport.doltEgressCidr=${_dolt_ip}/32"
fi
