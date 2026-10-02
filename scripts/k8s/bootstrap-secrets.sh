#!/usr/bin/env bash
# Create the Secrets the Helm chart expects but cannot source from Bitwarden
# yet (deploy/helm/butlers, values `localSecrets`). Idempotent.
#
# Usage: bws run --project-id "$BWS_PROJECT_ID" -- scripts/k8s/bootstrap-secrets.sh <dev|prod>
#
#   butlers-runtime-probe-control  files named by RUNTIME_PROBE_CONTROL_{SIGNING_KEY,VERIFIERS}_FILE
#                                  (falls back to the committed unprovisioned placeholders)
#   butlers-local-env              DASHBOARD_API_KEY / DASHBOARD_AUTH_DB_USER from .env.<env>
#
# Values are piped straight to kubectl and never printed.
set -euo pipefail

ENV_NAME="${1:?usage: $0 <dev|prod>}"
case "$ENV_NAME" in
  dev)  NAMESPACE=butlers-dev ;;
  prod) NAMESPACE=butlers ;;
  *) echo "unknown environment: $ENV_NAME" >&2; exit 1 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_FILE="${BUTLERS_ENV_FILE:-${PROJECT_DIR}/.env.${ENV_NAME}}"

trap 'rm -f "${signer_tmp:-}" "${tmp:-}"' EXIT

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

signer="${RUNTIME_PROBE_CONTROL_SIGNING_KEY_FILE:-${PROJECT_DIR}/deploy/runtime-probe-control/signing-key-unprovisioned.json}"
verifiers="${RUNTIME_PROBE_CONTROL_VERIFIERS_FILE:-${PROJECT_DIR}/deploy/runtime-probe-control/verifiers-unprovisioned.json}"
# The provisioned signer is root-owned 0400 by design; read it with sudo then.
signer_tmp="$(mktemp)"; chmod 600 "$signer_tmp"
if [ -r "$signer" ]; then cat "$signer" > "$signer_tmp"; else sudo cat "$signer" > "$signer_tmp"; fi
kubectl -n "$NAMESPACE" create secret generic butlers-runtime-probe-control \
  --from-file=runtime_probe_control_signing_key="$signer_tmp" \
  --from-file=runtime_probe_control_verifiers="$verifiers" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
rm -f "$signer_tmp"
echo "secret/butlers-runtime-probe-control applied in ${NAMESPACE}"

# Pass values through a private temp file, not argv (visible in `ps`).
tmp="$(mktemp)"; chmod 600 "$tmp"
if [ -f "$ENV_FILE" ]; then
  for key in DASHBOARD_API_KEY DASHBOARD_AUTH_DB_USER; do
    value="$(set -a; . "$ENV_FILE"; printf '%s' "${!key:-}")"
    [ -n "$value" ] && printf '%s=%s\n' "$key" "$value" >> "$tmp"
  done
fi
kubectl -n "$NAMESPACE" create secret generic butlers-local-env --from-env-file="$tmp" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
echo "secret/butlers-local-env applied in ${NAMESPACE} ($(wc -l < "$tmp") keys)"
rm -f "$tmp"
