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
