#!/usr/bin/env bash
# Launch Butlers via Docker Compose (dev by default, --prod for production DB).
#
# Usage:
#   ./scripts/compose.sh                           # dev database, hotreload on (default)
#   ./scripts/compose.sh --with-restore-drill      # dev stack plus protected restore-drill executor
#   ./scripts/compose.sh --prod                    # production database, baked image
#   ./scripts/compose.sh --no-hotreload            # dev mode without source volume-mount
#   ./scripts/compose.sh --hotreload               # explicit (already on for dev; no-op)
#   ./scripts/compose.sh --skip-oauth-check        # skip OAuth gate
#   ./scripts/compose.sh --skip-tailscale-check    # skip tailscale serve setup
#   ./scripts/compose.sh --audio                   # include live-listener (needs /dev/snd)
#   ./scripts/compose.sh --observability           # enable observability stack (Prometheus, Grafana, Tempo)
#   ./scripts/compose.sh --hardened                # opt into hardened posture (disables Grafana anon viewer)
#
# DEPLOYMENT POSTURE:
#   Default posture is "dev" (anonymous Grafana viewer enabled when --observability is set).
#   To opt into hardened posture, pass --hardened or set BUTLERS_POSTURE=hardened in the environment.
#   See docs/operations/deployment-posture.md for the full posture reference.
#
# Dev defaults to hotreload because the baked image only re-bakes when this
# script rebuilds it -- editing src/ on the host has no effect on a baked
# dashboard-api or butlers-up container until rebuild + restart. Hotreload
# variants volume-mount src/ and pick up edits immediately. Pass
# --no-hotreload to reproduce the prod-style baked-image path in dev.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

# The protected restore-drill services live in a separate Compose fragment.
# Ordinary dev deliberately omits it; an explicit dev opt-in or prod selects it
# and the launcher prepares the required firewall capability before either
# protected service starts.
PROFILES=(dev)
COMPOSE_ENV=()
SKIP_TAILSCALE=false
OBSERVABILITY=false
BUTLERS_MODE=dev
RESTORE_DRILL_ENABLED=false
# A data-plane probe must run from an independent tailnet client.  An on-host
# request to this machine's own tailnet name can be intercepted by another
# listener (for example Docker/Traefik) before it reaches Tailscale Serve.
# Keep the executor opt-in so a launcher never presents an on-host result as
# off-host evidence.  The context value is a policy gate, not evidence: the
# executed probe must derive and attest its own Tailscale identity.  The command
# is split into argv without eval when used.
TAILSCALE_SERVE_PROBE_CONTEXT="${TAILSCALE_SERVE_PROBE_CONTEXT:-}"
TAILSCALE_SERVE_PROBE_COMMAND="${TAILSCALE_SERVE_PROBE_COMMAND:-}"
TAILSCALE_SERVE_PROBE_TIMEOUT_SECONDS="${TAILSCALE_SERVE_PROBE_TIMEOUT_SECONDS:-10}"
TAILSCALE_SERVE_PROBE_RETRIES="${TAILSCALE_SERVE_PROBE_RETRIES:-2}"
TAILSCALE_SERVE_PROBE_RETRY_DELAY_SECONDS="${TAILSCALE_SERVE_PROBE_RETRY_DELAY_SECONDS:-1}"
TAILSCALE_SERVE_HEALTH_URL=""
TAILSCALE_SERVE_PROBE_ARGV=()
# Hotreload defaults to on for dev, off for prod; resolved after arg parsing.
# Tri-state: empty = use mode default; true/false = user opted in/out.
HOTRELOAD_OPT=""

# Deployment posture: "dev" (default when unset) or "hardened" (explicit opt-in).
# Governs security-gated toggles such as Grafana anonymous viewer access.
# Inherits from the environment; can also be set via --hardened flag.
BUTLERS_POSTURE="${BUTLERS_POSTURE:-dev}"

for arg in "$@"; do
  case "$arg" in
    --prod)                 BUTLERS_MODE=prod ;;
    --with-restore-drill)   RESTORE_DRILL_ENABLED=true ;;
    --hardened)             BUTLERS_POSTURE=hardened ;;
    --hotreload)            HOTRELOAD_OPT=true ;;
    --no-hotreload)         HOTRELOAD_OPT=false ;;
    --audio)                PROFILES+=(audio) ;;
    --observability)        OBSERVABILITY=true ;;
    --skip-oauth-check)     COMPOSE_ENV+=("SKIP_OAUTH_CHECK=true") ;;
    --skip-tailscale-check) SKIP_TAILSCALE=true ;;
    *)                      echo "Unknown flag: $arg" >&2; exit 1 ;;
  esac
done

# Production always includes the protected executor. Dev must opt in with the
# explicit flag rather than enabling a privileged service merely because a
# secret happens to be configured in its environment file.
if [ "$BUTLERS_MODE" = "prod" ]; then
  RESTORE_DRILL_ENABLED=true
fi
readonly RESTORE_DRILL_ENABLED

# Preserve the protected mode when a firewall preparation/apply failure tells an
# operator how to retry. A bare dev invocation would deliberately omit it.
if [ "$BUTLERS_MODE" = "prod" ]; then
  RESTORE_DRILL_RETRY_COMMAND="./scripts/compose.sh --prod"
else
  RESTORE_DRILL_RETRY_COMMAND="./scripts/compose.sh --with-restore-drill"
fi
readonly RESTORE_DRILL_RETRY_COMMAND

# Resolve hotreload default: dev mode opts in unless --no-hotreload is set;
# prod mode opts out unless --hotreload is set (rarely useful but allowed).
if [ -z "$HOTRELOAD_OPT" ]; then
  if [ "$BUTLERS_MODE" = "dev" ]; then
    HOTRELOAD_OPT=true
  else
    HOTRELOAD_OPT=false
  fi
fi
if [ "$HOTRELOAD_OPT" = "true" ]; then
  PROFILES+=(hotreload)
fi

# ── Load environment-specific database config ──────────────────────────
ENV_FILE="${PROJECT_DIR}/.env.${BUTLERS_MODE}"
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: Missing ${ENV_FILE}" >&2
  echo "  Create it with POSTGRES_HOST, POSTGRES_PASSWORD, etc." >&2
  exit 1
fi

# Shell ``source`` normalizes unquoted trailing whitespace in assignments.
# Endpoint literals flow to the root-owned firewall unchanged, so reject raw
# whitespace before sourcing rather than letting the shell silently rewrite it.
_restore_drill_reject_raw_endpoint_whitespace() {
  local line name raw_value
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?(POSTGRES_HOST|POSTGRES_PORT|RESTORE_DRILL_EXECUTOR_DB_HOST|RESTORE_DRILL_EXECUTOR_DB_PORT|RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST)= ]]; then
      name="${BASH_REMATCH[2]}"
      case "$name" in
        POSTGRES_HOST|POSTGRES_PORT) ;;
        *) [ "$RESTORE_DRILL_ENABLED" = "true" ] || continue ;;
      esac
      raw_value="${line#*=}"
      if [[ "$raw_value" =~ [[:space:]] ]]; then
        echo "ERROR: ${name} in ${ENV_FILE} must not contain whitespace; endpoint literals are not trimmed." >&2
        exit 1
      fi
    fi
  done < "$ENV_FILE"
}

_restore_drill_reject_raw_endpoint_whitespace
set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

# ``.env`` can override or unset process-supplied probe settings. Normalize
# every external input after sourcing and before any Serve or Compose lifecycle
# work, rather than letting a later policy check or argv splitter fail after
# the stack has started. The health URL is internal launcher output, never an
# environment input.
TAILSCALE_SERVE_PROBE_CONTEXT="${TAILSCALE_SERVE_PROBE_CONTEXT:-}"
TAILSCALE_SERVE_PROBE_COMMAND="${TAILSCALE_SERVE_PROBE_COMMAND:-}"
TAILSCALE_SERVE_PROBE_TIMEOUT_SECONDS="${TAILSCALE_SERVE_PROBE_TIMEOUT_SECONDS:-10}"
TAILSCALE_SERVE_PROBE_RETRIES="${TAILSCALE_SERVE_PROBE_RETRIES:-2}"
TAILSCALE_SERVE_PROBE_RETRY_DELAY_SECONDS="${TAILSCALE_SERVE_PROBE_RETRY_DELAY_SECONDS:-1}"
TAILSCALE_SERVE_HEALTH_URL=""
TAILSCALE_SERVE_PROBE_ARGV=()
if [[ -n "$TAILSCALE_SERVE_PROBE_COMMAND" && -z "${TAILSCALE_SERVE_PROBE_COMMAND//[[:space:]]/}" ]]; then
  echo "ERROR: TAILSCALE_SERVE_PROBE_COMMAND is whitespace-only; configure a nonempty approved off-host executor or unset it; no Serve or Compose lifecycle mutation was attempted." >&2
  exit 1
fi

# Restore-drill executor password-file preflight: when the protected fragment
# is selected, Compose interpolates this secret even for lifecycle commands, so
# fail before Docker can stop the existing stack. Metadata checks never read or
# disclose the secret.
if [ "$RESTORE_DRILL_ENABLED" = "true" ]; then
  if [[ -z "${RESTORE_DRILL_EXECUTOR_PASSWORD_FILE:-}" ]]; then
    echo "ERROR: RESTORE_DRILL_EXECUTOR_PASSWORD_FILE must name the private restore-drill executor password file." >&2
    exit 1
  fi
  if [[ ! -f "$RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" || ! -r "$RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" || ! -s "$RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" ]]; then
    echo "ERROR: RESTORE_DRILL_EXECUTOR_PASSWORD_FILE must name a readable, non-empty regular file." >&2
    exit 1
  fi
fi

echo "Database: ${BUTLERS_MODE} (${POSTGRES_HOST}:${POSTGRES_PORT:-5432})"
if [ "$RESTORE_DRILL_ENABLED" = "true" ]; then
  echo "Restore drill: enabled"
else
  echo "Restore drill: disabled (dev default; pass --with-restore-drill to enable)"
fi

# The restore-drill executor has an internal-only network and reaches the
# database through an uncredentialed, default-deny egress relay. Resolve the
# relay's firewall endpoint on the host before Compose creates either service.
# Keep a DNS connection host intact: verify-full needs it to check the
# PostgreSQL certificate identity, while Docker resolves that name only to the
# internal relay alias and the relay alone dials the resolved IPv4.
_restore_drill_is_ipv4() {
  local ip="$1" octet
  local -a octets
  [[ "$ip" =~ ^[0-9]+(\.[0-9]+){3}$ ]] || return 1
  IFS='.' read -r -a octets <<< "$ip"
  [ "${#octets[@]}" -eq 4 ] || return 1
  for octet in "${octets[@]}"; do
    # Keep the literal canonical before it reaches the root-owned firewall:
    # iptables treats a leading-zero octet as octal and changes the target.
    [[ "$octet" =~ ^(0|[1-9][0-9]{0,2})$ ]] || return 1
    ((10#$octet <= 255)) || return 1
  done
}

_restore_drill_is_remote_ipv4() {
  local ip="$1" first_octet second_octet third_octet
  _restore_drill_is_ipv4 "$ip" || return 1
  IFS='.' read -r first_octet second_octet third_octet _ <<< "$ip"
  first_octet=$((10#$first_octet))
  second_octet=$((10#$second_octet))
  third_octet=$((10#$third_octet))

  # This must remain in parity with the root firewall and proxy policy. Reject
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

_restore_drill_is_dns_name() {
  local host="$1" label
  local -a labels
  [[ -n "$host" && ${#host} -le 253 && "$host" != *"." ]] || return 1
  IFS='.' read -r -a labels <<< "$host"
  for label in "${labels[@]}"; do
    [[ ${#label} -le 63 ]] || return 1
    [[ "$label" =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$ ]] || return 1
  done
}

_restore_drill_is_legacy_numeric_ipv4() {
  # libc accepts inet_aton-compatible decimal, octal, and hexadecimal forms
  # (including abbreviated dotted forms) as numeric addresses before DNS.
  # Treat every noncanonical form as invalid rather than letting it become a
  # misleading TLS hostname or a different resolved firewall endpoint.
  [[ "$1" =~ ^(0[xX][0-9A-Fa-f]+|[0-9]+)(\.(0[xX][0-9A-Fa-f]+|[0-9]+)){0,3}$ ]]
}

_restore_drill_is_loopback_dns_identity() {
  case "${1,,}" in
    localhost|localhost.localdomain) return 0 ;;
    *) return 1 ;;
  esac
}

if [ "$RESTORE_DRILL_ENABLED" = "true" ]; then
  _restore_drill_source_host="${RESTORE_DRILL_EXECUTOR_DB_HOST:-${POSTGRES_HOST:?Set POSTGRES_HOST in ${ENV_FILE}}}"
  if _restore_drill_is_legacy_numeric_ipv4 "$_restore_drill_source_host"; then
    echo "ERROR: RESTORE_DRILL_EXECUTOR_DB_HOST must be a DNS hostname for the internal relay; numeric IPv4 literals are not supported." >&2
    exit 1
  fi
  if ! _restore_drill_is_dns_name "$_restore_drill_source_host"; then
    echo "ERROR: RESTORE_DRILL_EXECUTOR_DB_HOST must be a DNS hostname for the internal relay." >&2
    exit 1
  fi
  if _restore_drill_is_loopback_dns_identity "$_restore_drill_source_host"; then
    echo "ERROR: RESTORE_DRILL_EXECUTOR_DB_HOST must not be localhost." >&2
    exit 1
  fi
  RESTORE_DRILL_EXECUTOR_DB_HOST="$_restore_drill_source_host"
  if [ -n "${RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST:-}" ]; then
    if ! _restore_drill_is_remote_ipv4 "$RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST"; then
      echo "ERROR: RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST must be a remote IPv4 PostgreSQL endpoint." >&2
      exit 1
    fi
  else
    RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST="$(getent ahostsv4 "$_restore_drill_source_host" | awk 'NR == 1 {print $1}')"
    if ! _restore_drill_is_remote_ipv4 "$RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST"; then
      echo "ERROR: Could not resolve a remote IPv4 PostgreSQL endpoint for restore-drill executor: $_restore_drill_source_host" >&2
      exit 1
    fi
  fi
  RESTORE_DRILL_EXECUTOR_DB_PORT="${RESTORE_DRILL_EXECUTOR_DB_PORT:-${POSTGRES_PORT:-5432}}"
  # Keep this check ASCII-only even in locales where Bash's [0-9] range also
  # matches fullwidth digits. Never send an unchecked value into an arithmetic
  # context: malformed 10# expressions must reject the launch.
  if [[ ! "$RESTORE_DRILL_EXECUTOR_DB_PORT" =~ ^[0123456789]+$ ]] \
    || [[ ! "$RESTORE_DRILL_EXECUTOR_DB_PORT" =~ ^[1-9][0123456789]{0,4}$ ]]; then
    echo "ERROR: RESTORE_DRILL_EXECUTOR_DB_PORT must use canonical decimal 1..65535." >&2
    exit 2
  fi
  if ! ((restore_drill_executor_db_port_value = 10#$RESTORE_DRILL_EXECUTOR_DB_PORT)); then
    echo "ERROR: RESTORE_DRILL_EXECUTOR_DB_PORT must use canonical decimal 1..65535." >&2
    exit 2
  fi
  if ((restore_drill_executor_db_port_value < 1 || restore_drill_executor_db_port_value > 65535)); then
    echo "ERROR: RESTORE_DRILL_EXECUTOR_DB_PORT must use canonical decimal 1..65535." >&2
    exit 2
  fi
  export RESTORE_DRILL_EXECUTOR_DB_HOST RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST RESTORE_DRILL_EXECUTOR_DB_PORT
  echo "Restore-drill endpoint: ${RESTORE_DRILL_EXECUTOR_DB_HOST}:${RESTORE_DRILL_EXECUTOR_DB_PORT} (TLS identity; relay firewall IPv4 ${RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST})"
fi

# ── Mode-dependent configuration ────────────────────────────────────────
# Prod and dev use different URL prefixes, host ports, and project names
# so both can run simultaneously on the same machine.
if [ "$BUTLERS_MODE" = "prod" ]; then
  URL_PREFIX="butlers"
  API_PREFIX="butlers-api"
  OWNTRACKS_PREFIX="owntracks"
  export COMPOSE_PROJECT_NAME="butlers"
  export SWITCHBOARD_HOST_PORT=41100
  export DASHBOARD_HOST_PORT=41200
  export FRONTEND_HOST_PORT=41173
  export OWNTRACKS_HOST_PORT=40086
else
  URL_PREFIX="butlers-dev"
  API_PREFIX="butlers-dev-api"
  OWNTRACKS_PREFIX="owntracks-dev"
  export COMPOSE_PROJECT_NAME="butlers-dev"
  export SWITCHBOARD_HOST_PORT=42100
  export DASHBOARD_HOST_PORT=42200
  export FRONTEND_HOST_PORT=42173
  export OWNTRACKS_HOST_PORT=42086
fi
export FRONTEND_BASE_PATH="/${URL_PREFIX}/"
export VITE_API_URL="/${API_PREFIX}/api"
export OWNTRACKS_WEBHOOK_BASE_PATH="/${OWNTRACKS_PREFIX}"

# ── Tailscale serve configuration ─────────────────────────────────────
# Configure tailscale serve to expose all externally-accessible services
# with TLS termination. Required for Google OAuth (HTTPS redirect URIs)
# and for mobile app connectivity (OwnTracks).
if [ "$SKIP_TAILSCALE" = "false" ]; then
  if ! command -v tailscale &>/dev/null; then
    echo "ERROR: tailscale CLI not found. Install from https://tailscale.com/download" >&2
    echo "  Or skip: $0 --skip-tailscale-check" >&2
    exit 1
  fi
  ts_state=$(tailscale status --json 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('BackendState','Unknown'))" \
    2>/dev/null || echo "Unknown")
  if [ "$ts_state" = "NeedsLogin" ] || [ "$ts_state" = "Stopped" ]; then
    echo "ERROR: tailscale not authenticated (state: ${ts_state}). Run: tailscale up" >&2
    exit 1
  fi

  TAILSCALE_HTTPS_PORT="${TAILSCALE_HTTPS_PORT:-443}"

  # Tailscale serve path mappings: "path_prefix|local_target"
  # Each entry creates an HTTPS -> HTTP proxy via tailscale serve.
  SERVE_MAPPINGS=(
    "/${URL_PREFIX}|http://localhost:${FRONTEND_HOST_PORT}/${URL_PREFIX}"       # Dashboard UI
    "/${API_PREFIX}|http://localhost:${DASHBOARD_HOST_PORT}"                    # Dashboard API
    "/${OWNTRACKS_PREFIX}|http://localhost:${OWNTRACKS_HOST_PORT}/owntracks"    # OwnTracks webhook
  )

  # ── Helper: apply a single tailscale serve mapping ──────────────────
  _ts_run_serve() {
    local path_prefix="$1" target="$2"
    local out="" rc=0
    if [ "$path_prefix" = "/" ]; then
      out=$(tailscale serve --yes --bg --https="${TAILSCALE_HTTPS_PORT}" "$target" 2>&1) || rc=$?
    else
      out=$(tailscale serve --yes --bg --https="${TAILSCALE_HTTPS_PORT}" --set-path "$path_prefix" "$target" 2>&1) || rc=$?
    fi
    # Fallback for older tailscale CLI syntax
    if [ "$rc" -ne 0 ] && echo "$out" | grep -Eqi "(invalid argument format|unknown flag|usage)"; then
      rc=0
      if [ "$path_prefix" = "/" ]; then
        out=$(tailscale serve "https:${TAILSCALE_HTTPS_PORT}" "$target" 2>&1) || rc=$?
      else
        out=$(tailscale serve "https:${TAILSCALE_HTTPS_PORT}" "$path_prefix" "$target" 2>&1) || rc=$?
      fi
    fi
    [ -n "$out" ] && echo "    $out"
    return "$rc"
  }

  # ── Helper: check if a mapping already exists ──────────────────────
  _ts_check_mapping() {
    local target="$1" path_prefix="$2" status_json="$3"
    SERVE_STATUS_JSON="$status_json" python3 - "$target" "$path_prefix" "$TAILSCALE_HTTPS_PORT" <<'PY'
import json, os, sys
target, path_prefix, wanted_port = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.loads(os.environ.get("SERVE_STATUS_JSON", "{}"))
for hostport, cfg in (data.get("Web") or {}).items():
    for hp, handler in ((cfg or {}).get("Handlers") or {}).items():
        if hp == path_prefix and isinstance(handler, dict) and handler.get("Proxy") == target:
            try:
                port = hostport.rsplit(":", 1)[1]
            except Exception:
                port = "443"
            if port == wanted_port:
                raise SystemExit(0)
raise SystemExit(1)
PY
  }

  _ts_validate_serve_status_json() {
    SERVE_STATUS_JSON="$1" python3 - <<'PY'
import json, os

data = json.loads(os.environ["SERVE_STATUS_JSON"])
if not isinstance(data, dict):
    raise SystemExit(1)
web = data.get("Web")
if web is not None and not isinstance(web, dict):
    raise SystemExit(1)
for host_config in (web or {}).values():
    if not isinstance(host_config, dict):
        raise SystemExit(1)
    handlers = host_config.get("Handlers")
    if handlers is not None and not isinstance(handlers, dict):
        raise SystemExit(1)
PY
  }

  _ts_read_serve_status() {
    local status_json=""
    if ! status_json=$(tailscale serve status --json 2>/dev/null); then
      return 40
    fi
    if ! _ts_validate_serve_status_json "$status_json" 2>/dev/null; then
      return 41
    fi
    printf '%s' "$status_json"
  }

  _ts_require_readable_serve_status() {
    local status_rc="$1"
    local stop_detail="$2"
    case "$status_rc" in
      40)
        echo "ERROR: Tailscale Serve control-plane status-unreadable: 'tailscale serve status --json' failed; ${stop_detail}." >&2
        ;;
      41)
        echo "ERROR: Tailscale Serve control-plane status-malformed: 'tailscale serve status --json' did not return a valid status object; ${stop_detail}." >&2
        ;;
      *)
        return 1
        ;;
    esac
    return 0
  }

  _ts_read_usable_hostname() {
    tailscale status --json 2>/dev/null | python3 -c '
import json
import re
import sys

try:
    data = json.load(sys.stdin)
except (json.JSONDecodeError, TypeError):
    raise SystemExit(1)

if not isinstance(data, dict) or not isinstance(data.get("Self"), dict):
    raise SystemExit(1)
hostname = data["Self"].get("DNSName")
if not isinstance(hostname, str):
    raise SystemExit(1)
if hostname.endswith("."):
    hostname = hostname[:-1]
hostname = hostname.lower()
labels = hostname.split(".")
label_pattern = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
if (
    not hostname
    or len(hostname) > 253
    or len(labels) < 3
    or labels[-2:] != ["ts", "net"]
    or any(not label_pattern.fullmatch(label) for label in labels)
):
    raise SystemExit(1)

print(hostname)
'
  }

  # ── Validate data-plane probe context before any lifecycle mutation ────
  # The command is normally an SSH wrapper (or another operator-supplied
  # executor) that runs scripts/tailscale_serve_probe.py from a different
  # tailnet client.  Refuse an explicitly on-host context: a local self-
  # request is not evidence of the public Serve route.
  if [ -n "$TAILSCALE_SERVE_PROBE_COMMAND" ] \
    && [ "$TAILSCALE_SERVE_PROBE_CONTEXT" != "off-host" ]; then
    echo "ERROR: Tailscale Serve data-plane probe requires TAILSCALE_SERVE_PROBE_CONTEXT=off-host; refusing an on-host or unspecified probe context." >&2
    echo "  Set TAILSCALE_SERVE_PROBE_COMMAND to an approved off-host, read-only probe executor." >&2
    exit 1
  fi

  TAILSCALE_SERVE_PROBE_OUTER_TIMEOUT_SECONDS=""
  if [ -n "$TAILSCALE_SERVE_PROBE_COMMAND" ]; then
    # Split only once, before lifecycle work, so a missing local executable
    # cannot surface as a post-start probe failure.  This intentionally does
    # not validate remote argv: an SSH wrapper may be locally resolvable while
    # its remote command can only fail after the service is running.
    IFS=$' \t\n' read -r -a TAILSCALE_SERVE_PROBE_ARGV <<< "$TAILSCALE_SERVE_PROBE_COMMAND"
    probe_executor_path=""
    if [ "${#TAILSCALE_SERVE_PROBE_ARGV[@]}" -eq 0 ] \
      || ! probe_executor_path=$(type -P "${TAILSCALE_SERVE_PROBE_ARGV[0]}" 2>/dev/null) \
      || [ -z "$probe_executor_path" ] \
      || [ ! -x "$probe_executor_path" ]; then
      echo "ERROR: Tailscale Serve data-plane executor-unavailable: the configured off-host executor is not locally resolvable; no Serve or Compose lifecycle mutation was attempted." >&2
      exit 1
    fi
    TAILSCALE_SERVE_PROBE_ARGV[0]="$probe_executor_path"
    if ! command -v timeout &>/dev/null; then
      echo "ERROR: Tailscale Serve probe requires the 'timeout' command to bound the executor; no mapping or lifecycle mutation was attempted." >&2
      exit 1
    fi
    probe_settings_rc=0
    normalized_probe_settings=$(python3 - \
      "$TAILSCALE_SERVE_PROBE_TIMEOUT_SECONDS" \
      "$TAILSCALE_SERVE_PROBE_RETRIES" \
      "$TAILSCALE_SERVE_PROBE_RETRY_DELAY_SECONDS" <<'PY'
import math, sys

try:
    timeout = float(sys.argv[1])
    retries = int(sys.argv[2])
    retry_delay = float(sys.argv[3])
except (TypeError, ValueError):
    raise SystemExit(1)
if (
    not math.isfinite(timeout)
    or not math.isfinite(retry_delay)
    or not 0 < timeout <= 30
    or not 0 <= retries <= 3
    or not 0 <= retry_delay <= 5
    or str(retries) != sys.argv[2]
):
    raise SystemExit(1)
# Include the probe's five-second local identity check plus a bounded executor
# establishment allowance (for example SSH DNS/authentication setup).
outer_timeout = timeout * (retries + 1) + retry_delay * retries + 10
print(format(timeout, ".15g"), retries, format(retry_delay, ".15g"), format(outer_timeout, ".15g"))
PY
    ) || probe_settings_rc=$?
    if [ "$probe_settings_rc" -ne 0 ]; then
      echo "ERROR: invalid Tailscale Serve probe settings: timeout must be finite in (0,30], retries an integer in [0,3], and retry delay finite in [0,5]; no mapping or lifecycle mutation was attempted." >&2
      exit 1
    fi
    read -r TAILSCALE_SERVE_PROBE_TIMEOUT_SECONDS \
      TAILSCALE_SERVE_PROBE_RETRIES \
      TAILSCALE_SERVE_PROBE_RETRY_DELAY_SECONDS \
      TAILSCALE_SERVE_PROBE_OUTER_TIMEOUT_SECONDS <<< "$normalized_probe_settings"

    # An explicit executor promises data-plane evidence.  Derive and validate
    # its target before any Serve or Compose lifecycle mutation so a missing or
    # malformed Self.DNSName cannot silently turn that promise into a no-op.
    TS_HOSTNAME=""
    if ! TS_HOSTNAME=$(_ts_read_usable_hostname); then
      echo "ERROR: Tailscale Serve data-plane target-unavailable: an explicit off-host probe is configured but 'tailscale status --json' did not provide a usable Self.DNSName; no Serve or Compose lifecycle mutation was attempted." >&2
      echo "  Restore a valid Tailscale DNS name or unset TAILSCALE_SERVE_PROBE_COMMAND to retain control-plane-only mapping validation." >&2
      exit 1
    fi
  fi

  # ── Apply mappings ─────────────────────────────────────────────────
  echo "Tailscale serve: configuring HTTPS mappings (port ${TAILSCALE_HTTPS_PORT})..."
  serve_status_rc=0
  serve_status=$(_ts_read_serve_status) || serve_status_rc=$?
  if [ "$serve_status_rc" -ne 0 ]; then
    _ts_require_readable_serve_status \
      "$serve_status_rc" \
      "no mapping or lifecycle mutation was attempted" || true
    exit 1
  fi
  ts_serve_ok=true
  for mapping in "${SERVE_MAPPINGS[@]}"; do
    IFS='|' read -r path_prefix target <<< "$mapping"
    if _ts_check_mapping "$target" "$path_prefix" "$serve_status" 2>/dev/null; then
      echo "  ${path_prefix} -> ${target} (ok)"
    else
      echo "  ${path_prefix} -> ${target} (configuring...)"
      if ! _ts_run_serve "$path_prefix" "$target"; then
        echo "  ERROR: failed to configure ${path_prefix}" >&2
        ts_serve_ok=false
      fi
    fi
  done

  if [ "$ts_serve_ok" = "false" ]; then
    echo "" >&2
    echo "ERROR: Some tailscale serve mappings failed." >&2
    echo "  If 'Access denied', run: sudo tailscale set --operator=$USER" >&2
    echo "  To skip: $0 --skip-tailscale-check" >&2
    exit 1
  fi

  # A successful `tailscale serve` invocation is not proof that the requested
  # handler was retained.  Re-read the control-plane state and fail with a
  # route-specific class before the data-plane probe or Compose lifecycle.
  serve_status_rc=0
  serve_status=$(_ts_read_serve_status) || serve_status_rc=$?
  if [ "$serve_status_rc" -ne 0 ]; then
    _ts_require_readable_serve_status \
      "$serve_status_rc" \
      "no further Serve mutation or lifecycle startup was attempted" || true
    exit 1
  fi
  ts_mapping_missing=false
  for mapping in "${SERVE_MAPPINGS[@]}"; do
    IFS='|' read -r path_prefix target <<< "$mapping"
    if ! _ts_check_mapping "$target" "$path_prefix" "$serve_status" 2>/dev/null; then
      echo "  ERROR: Tailscale Serve mapping-missing after configuration: ${path_prefix} -> ${target} (HTTPS port ${TAILSCALE_HTTPS_PORT})." >&2
      echo "    Recheck the read-only 'tailscale serve status --json' result and the exact path/target; no further Serve mutation was attempted." >&2
      ts_mapping_missing=true
    fi
  done
  if [ "$ts_mapping_missing" = "true" ]; then
    echo "ERROR: Tailscale Serve control-plane validation failed (mapping-missing)." >&2
    exit 1
  fi

  # ── Export computed URLs for docker-compose interpolation ───────────
  if [ -z "${TS_HOSTNAME:-}" ]; then
    TS_HOSTNAME=$(_ts_read_usable_hostname 2>/dev/null || true)
  fi

  if [ -n "$TS_HOSTNAME" ]; then
    if [ "$TAILSCALE_HTTPS_PORT" = "443" ]; then
      TS_BASE="https://${TS_HOSTNAME}"
    else
      TS_BASE="https://${TS_HOSTNAME}:${TAILSCALE_HTTPS_PORT}"
    fi
    export GOOGLE_OAUTH_REDIRECT_URI="${TS_BASE}/${API_PREFIX}/api/oauth/google/callback"
    export SPOTIFY_OAUTH_REDIRECT_URI="${TS_BASE}/${API_PREFIX}/api/connectors/spotify/oauth/callback"
    export OWNTRACKS_CONNECTOR_HOST="${TS_HOSTNAME}"
    export OWNTRACKS_CONNECTOR_PORT="${TAILSCALE_HTTPS_PORT}"
    TAILSCALE_SERVE_HEALTH_URL="${TS_BASE}/${API_PREFIX}/api/health"

    echo ""
    echo "Tailscale serve: mappings ready (${TS_HOSTNAME})"
    echo "  Dashboard:      ${TS_BASE}/${URL_PREFIX}/"
    echo "  API:            ${TS_BASE}/${API_PREFIX}/api"
    echo "  OwnTracks:      ${TS_BASE}/${OWNTRACKS_PREFIX}/webhook"
    echo "  OAuth (Google):  ${GOOGLE_OAUTH_REDIRECT_URI}"
    echo "  OAuth (Spotify): ${SPOTIFY_OAUTH_REDIRECT_URI}"
  else
    echo "Tailscale serve: mappings applied (could not resolve hostname)"
  fi

  _ts_run_data_plane_probe() {
    local health_url="$1"
    if [ -z "$TAILSCALE_SERVE_PROBE_COMMAND" ]; then
      echo "Tailscale serve: data-plane probe deferred for ${health_url} (set TAILSCALE_SERVE_PROBE_COMMAND to an approved off-host executor; control-plane mappings only)"
      return 0
    fi

    if [ "${#TAILSCALE_SERVE_PROBE_ARGV[@]}" -eq 0 ]; then
      echo "ERROR: Tailscale Serve data-plane executor-unavailable: no locally resolved executor is available." >&2
      return 2
    fi

    local probe_rc=0
    local probe_attestation_rc=0
    local -a probe_status=()
    # The executor is operator-configured but untrusted for diagnostics. Keep
    # its stdout streaming through the exact attestation matcher and discard
    # stderr, so command arguments or remote error text cannot escape through
    # launcher output.  Stable exit classes below remain actionable.
    if timeout --kill-after=1 \
      "${TAILSCALE_SERVE_PROBE_OUTER_TIMEOUT_SECONDS}s" \
      "${TAILSCALE_SERVE_PROBE_ARGV[@]}" \
      --url "$health_url" \
      --timeout "$TAILSCALE_SERVE_PROBE_TIMEOUT_SECONDS" \
      --retries "$TAILSCALE_SERVE_PROBE_RETRIES" \
      --retry-delay "$TAILSCALE_SERVE_PROBE_RETRY_DELAY_SECONDS" 2>/dev/null \
      | grep -Fx 'TAILSCALE_SERVE_PROBE_IDENTITY=verified-distinct' >/dev/null; then
      probe_status=("${PIPESTATUS[@]}")
    else
      probe_status=("${PIPESTATUS[@]}")
    fi
    probe_rc="${probe_status[0]}"
    probe_attestation_rc="${probe_status[1]}"
    if [ "$probe_rc" -eq 0 ]; then
      if [ "$probe_attestation_rc" -ne 0 ]; then
        echo "ERROR: Tailscale Serve probe identity-unverified: the executor returned success without attesting an actual Tailscale identity distinct from the target." >&2
        return 27
      fi
      echo "Tailscale Serve data-plane: ready (off-host executor identity attested)."
      return 0
    fi

    # scripts/tailscale_serve_probe.py uses stable exit classes.  Keep a
    # shell-side summary too so custom approved executors remain actionable.
    case "$probe_rc" in
      124|137)
        echo "ERROR: Tailscale Serve data-plane executor-timeout for ${health_url}; the approved executor exceeded its validated outer deadline. Check executor DNS, SSH, and authentication reachability; no further Serve mutation was attempted." >&2
        return 28
        ;;
      20)
        echo "ERROR: Tailscale Serve mapping-ok-but-cert-invalid for ${health_url}; strict TLS rejected the public certificate (hostname, trust chain, or expiry). Verify from an off-host tailnet client; no further Serve mutation was attempted." >&2
        ;;
      21)
        echo "ERROR: Tailscale Serve mapping-ok-but-route-404 for ${health_url}; the HTTPS listener returned 404. Recheck the exact path mapping and proxy target; no further Serve mutation was attempted." >&2
        ;;
      22)
        echo "ERROR: Tailscale Serve mapping-ok-but-timeout for ${health_url}; the off-host probe exhausted its bounded retries. Check tailnet reachability and API startup; no further Serve mutation was attempted." >&2
        ;;
      *)
        echo "ERROR: Tailscale Serve data-plane probe failed for ${health_url} (exit ${probe_rc}); inspect the off-host probe result. No further Serve mutation was attempted." >&2
        ;;
    esac
    return "$probe_rc"
  }
  echo ""
fi

# ── Observability stack configuration ────────────────────────────────
if [ "$OBSERVABILITY" = "true" ]; then
  PROFILES+=(observability)
  export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318

  # Gate Grafana anonymous viewer on deployment posture.
  # dev (default when unset): anon viewer on — convenient for local iteration.
  # hardened: anon viewer off — login required (admin/admin or overridden creds).
  if [ "$BUTLERS_POSTURE" = "hardened" ]; then
    export GF_AUTH_ANONYMOUS_ENABLED=false
    echo "Observability stack enabled: Grafana at http://localhost:3000 (posture=hardened, login required)"
  else
    export GF_AUTH_ANONYMOUS_ENABLED=true
    echo "Observability stack enabled: Grafana at http://localhost:3000 (posture=dev, anonymous viewer on)"
  fi
  echo ""
fi

# ── Build compose command ─────────────────────────────────────────────
# The protected fragment is intentionally absent from bare Compose. This
# launcher includes it only for explicit protected execution, where it
# stops/creates the executor, installs its firewall, then starts the merged
# service set below.
CMD=(docker compose -f docker-compose.yml)
if [ "$RESTORE_DRILL_ENABLED" = "true" ]; then
  CMD+=(-f docker-compose.restore-drill.yml)
fi
for p in "${PROFILES[@]}"; do
  CMD+=(--profile "$p")
done

# Export env overrides
for e in "${COMPOSE_ENV[@]}"; do
  export "${e?}"
done

# ── Handle hotreload: scale down base services that hotreload replaces ─
SCALE_ARGS=()
for p in "${PROFILES[@]}"; do
  if [ "$p" = "hotreload" ]; then
    SCALE_ARGS+=(--scale butlers-up=0 --scale dashboard-api=0)
    echo "Hotreload: scaling down butlers-up and dashboard-api (replaced by *-hotreload variants)"
  fi
done

# ── Ensure base image is current ───────────────────────────────────────
# The base image (butlers-base) contains system deps, Node.js, LLM CLIs,
# and uv.  Fingerprint the Dockerfile plus every local source it COPYs: Docker
# cache invalidation alone cannot make an existing tagged image stale when a
# generator or PID1 helper changes.  This list intentionally excludes dotenv
# and ambient deployment configuration.
# shellcheck source=base-image-input-fingerprint.sh
source "${SCRIPT_DIR}/base-image-input-fingerprint.sh"
BASE_IMAGE_BUILD_INPUTS=(
  Dockerfile.base
  scripts/runtime_cli_sandbox_init.c
  scripts/generate_runtime_cli_sandbox_manifest.py
)
if command -v sha256sum &>/dev/null; then
  _base_dockerfile_hasher() { sha256sum; }
else
  _base_dockerfile_hasher() { shasum -a 256; }
fi
BASE_DOCKERFILE_SHA=$(_base_dockerfile_hasher < Dockerfile.base | awk '{print $1}')
BASE_INPUT_SHA=$(butlers_base_image_input_fingerprint "${BASE_IMAGE_BUILD_INPUTS[@]}")

# Keep the legacy Dockerfile-only label for existing image consumers.  The
# input label is the authoritative freshness receipt for this broader closure;
# images without it predate the sandbox inputs and rebuild fail closed.
BASE_IMAGE_DOCKERFILE_SHA=$(
  docker image inspect butlers-base:latest \
    --format '{{ index .Config.Labels "butlers.base.dockerfile_sha" }}' \
    2>/dev/null || true
)
BASE_IMAGE_INPUT_SHA=$(
  docker image inspect butlers-base:latest \
    --format '{{ index .Config.Labels "butlers.base.input_sha" }}' \
    2>/dev/null || true
)

if [ -z "$BASE_IMAGE_DOCKERFILE_SHA" ] || [ -z "$BASE_IMAGE_INPUT_SHA" ]; then
  echo "Building butlers-base image (~5-10 min)..."
  docker build \
    --label "butlers.base.dockerfile_sha=${BASE_DOCKERFILE_SHA}" \
    --label "butlers.base.input_sha=${BASE_INPUT_SHA}" \
    -f Dockerfile.base \
    -t butlers-base . || {
    echo "ERROR: Failed to build butlers-base image" >&2
    exit 1
  }
  echo ""
elif [ "$BASE_IMAGE_DOCKERFILE_SHA" != "$BASE_DOCKERFILE_SHA" ] \
  || [ "$BASE_IMAGE_INPUT_SHA" != "$BASE_INPUT_SHA" ]; then
  echo "Rebuilding butlers-base image because its copied runtime inputs changed..."
  docker build \
    --label "butlers.base.dockerfile_sha=${BASE_DOCKERFILE_SHA}" \
    --label "butlers.base.input_sha=${BASE_INPUT_SHA}" \
    -f Dockerfile.base \
    -t butlers-base . || {
    echo "ERROR: Failed to rebuild butlers-base image" >&2
    exit 1
  }
  echo ""
fi

echo "Starting Butlers stack (${BUTLERS_MODE})..."
echo "  Profiles: ${PROFILES[*]:-default}"
echo "  Compose:  ${CMD[*]} up"
echo ""

# ── Resolve tailnet hosts for egress firewall allowlist ───────────────
# Butlers needs these tailnet services. Resolve IPs dynamically so the
# firewall stays correct even if tailscale reassigns addresses.
if [ -z "${ALLOWED_TAILNET_HOSTS:-}" ] && command -v tailscale &>/dev/null; then
  # Tailnet services Butlers needs to reach. Uses DNS names (the stable
  # identifiers in tailscale) to resolve current IPs.
  TAILNET_SERVICES=(
    otel               # OpenTelemetry collector (tracing)
    butlers-db-dev     # PostgreSQL for .env.dev (default target; despite "dev", the LIVE host with real data)
    butlers-db         # PostgreSQL for .env.prod (despite "prod", NOT the live host)
    ollama             # Local LLM inference
    tzehouse-synology  # Garage S3 storage
    homeassistant      # Home Assistant (home + health butler modules)
  )
  resolved=()
  ts_domain=$(tailscale status --json 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('MagicDNSSuffix',''))" \
    2>/dev/null) || true
  for host in "${TAILNET_SERVICES[@]}"; do
    ip=$(tailscale status --json 2>/dev/null \
      | python3 -c "
import sys, json
target_dns = '${host}.${ts_domain}.'
peers = json.load(sys.stdin).get('Peer', {})
for p in peers.values():
    if p.get('DNSName','') == target_dns:
        addrs = p.get('TailscaleIPs', [])
        if addrs:
            print(addrs[0])
            break
" 2>/dev/null) || true
    if [ -n "$ip" ]; then
      resolved+=("$ip")
    else
      echo "  WARN: tailnet host '$host' not found (skipped)"
    fi
  done
  if [ ${#resolved[@]} -gt 0 ]; then
    export ALLOWED_TAILNET_HOSTS="${resolved[*]}"
    echo "Tailnet allowlist: ${ALLOWED_TAILNET_HOSTS}"
  fi
fi

# ── Cap build cache to prevent unbounded growth ──────────────────────
# Docker build cache grows ~500MB+ per rebuild across all services.
# Without a cap, it consumed 717GB. Keep it under 20GB.
docker builder prune --keep-storage=20g -f 2>/dev/null || true

# ── Build shared app image (used by all services) ────────────────────
# All services reference butlers-app:${BUTLERS_APP_TAG:-latest} — includes
# Go whatsapp-bridge binary and whatsapp extra (just qrcode). One image
# for everything.
#
# Override BUTLERS_APP_TAG to pin to a specific build (e.g. a git SHA):
#   BUTLERS_APP_TAG=$(git rev-parse --short HEAD) ./scripts/compose.sh
# See docs/operations/image-bump-procedure.md for the full bump process.
BUTLERS_APP_TAG="${BUTLERS_APP_TAG:-latest}"
export BUTLERS_APP_TAG

# GIT_SHA: baked into the image (Dockerfile ARG/ENV) so the running process
# can record its own provenance in public.deployments (bu-9r3hd.2 deployments
# ledger; see src/butlers/core/deployments.py). Falls back to "unknown" when
# not run from a git checkout.
GIT_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
export GIT_SHA

echo "Building butlers-app image (tag: ${BUTLERS_APP_TAG}, sha: ${GIT_SHA})..."
DOCKER_BUILDKIT=1 docker build --build-arg GIT_SHA="${GIT_SHA}" \
  -t "butlers-app:${BUTLERS_APP_TAG}" . || {
  echo "ERROR: Failed to build butlers-app image" >&2
  exit 1
}

# Build profile-specific images (live-listener if audio profile active)
if [[ " ${PROFILES[*]} " == *" audio "* ]]; then
  echo "Building butlers-app-audio image (tag: ${BUTLERS_APP_TAG})..."
  DOCKER_BUILDKIT=1 docker build --build-arg EXTRAS=live-listener --build-arg GIT_SHA="${GIT_SHA}" \
    -t "butlers-app-audio:${BUTLERS_APP_TAG}" . || {
    echo "ERROR: Failed to build butlers-app-audio image" >&2
    exit 1
  }
fi

# ── Ensure the beads export file exists before compose mounts it ──────
# bu-hmdqz.6: docker-compose.yml bind-mounts ./.beads/issues.export.jsonl:ro
# into dashboard-api(-hotreload)/butlers-up(-hotreload). If that host path
# doesn't exist yet (fresh clone, or a worktree that's never run `bd
# export`), Docker creates a *directory* there to satisfy the mount --
# permanently breaking `bd export -o` afterwards with IsADirectoryError.
# This dev flow doesn't go through `butlers deploy`
# (src/butlers/core/deploy.py::materialize_beads_export is the prod-deploy
# equivalent of this same guard), so it needs its own. Prefer a real export
# when `bd` is available and reachable; fall back to an empty placeholder
# file otherwise -- either way, a regular file exists before `compose up`.
mkdir -p .beads
if [ ! -f .beads/issues.export.jsonl ]; then
  bd export -o .beads/issues.export.jsonl 2>/dev/null || touch .beads/issues.export.jsonl
fi

# ── Swap: stop old containers, start new ones ─────────────────────────
# --remove-orphans clears containers from renamed/removed services.
if ! "${CMD[@]}" down --remove-orphans; then
  echo "ERROR: The prior Compose stack could not be stopped." >&2
  exit 1
fi

if [ "$RESTORE_DRILL_ENABLED" = "true" ]; then
  # A versioned root-owned preparation both rejects an old installed wrapper and
  # emits an unguessable generation-bound nonce. Compose injects it only into the
  # not-yet-started executor; the wrapper independently confirms that exact
  # created container carries it before it writes the post-fence capability.
  if ! RESTORE_DRILL_EXECUTOR_FIREWALL_CAPABILITY_NONCE="$(sudo -n /usr/local/libexec/butlers-restore-drill-firewall \
    --prepare-executor-capability-v1 \
    --project "${COMPOSE_PROJECT_NAME}")" \
    || ! [[ "$RESTORE_DRILL_EXECUTOR_FIREWALL_CAPABILITY_NONCE" =~ ^[a-f0-9]{64}$ ]]; then
    echo "ERROR: restore-drill executor remains stopped because its required prepared firewall capability could not be created." >&2
    echo "  Install the current reviewed /usr/local/libexec/butlers-restore-drill-firewall wrapper and matching sudoers policy, then rerun ${RESTORE_DRILL_RETRY_COMMAND} (and repeat any other selected flags)." >&2
    exit 1
  fi
  export RESTORE_DRILL_EXECUTOR_FIREWALL_CAPABILITY_NONCE

  # Create the relay and executor without starting either. The relay's external
  # PostgreSQL bridge must be fenced before the credentialed process can reach
  # it through its separate internal-only network.
  "${CMD[@]}" create restore-drill-postgres-proxy restore-drill-executor
  if ! sudo -n /usr/local/libexec/butlers-restore-drill-firewall \
    --project "${COMPOSE_PROJECT_NAME}" \
    --db-host "${RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST}" \
    --db-port "${RESTORE_DRILL_EXECUTOR_DB_PORT}" \
    --require-executor-capability-v1; then
    echo "ERROR: restore-drill executor remains stopped because its required root-owned firewall wrapper could not be applied." >&2
    echo "  Install the current reviewed /usr/local/libexec/butlers-restore-drill-firewall wrapper and matching sudoers policy, then rerun ${RESTORE_DRILL_RETRY_COMMAND} (and repeat any other selected flags)." >&2
    exit 1
  fi
fi

"${CMD[@]}" up -d "${SCALE_ARGS[@]}"
if [ "$RESTORE_DRILL_ENABLED" = "true" ]; then
  unset RESTORE_DRILL_EXECUTOR_FIREWALL_CAPABILITY_NONCE
fi

# ── Apply egress firewall (blocks private subnet access from containers) ─
if sudo -n true 2>/dev/null; then
  sudo ALLOWED_TAILNET_HOSTS="${ALLOWED_TAILNET_HOSTS:-}" \
    "${SCRIPT_DIR}/egress-firewall.sh" && echo ""
else
  echo "NOTE: Run 'sudo ALLOWED_TAILNET_HOSTS=\"${ALLOWED_TAILNET_HOSTS:-}\" ./scripts/egress-firewall.sh'"
  echo "  to block container access to LAN/Tailscale (sudo requires a password)."
  echo ""
fi

# Mapping validation runs before lifecycle startup; the HTTPS health probe runs
# after the API containers are started so it checks the actual data plane.  The
# probe derives its actual Tailscale identity and rejects this target host;
# the caller-provided context label alone is never accepted as evidence.
# Keep the probe after the firewall step so a failed readiness check cannot
# bypass that guard.
if [ -n "${TAILSCALE_SERVE_HEALTH_URL:-}" ]; then
  if ! _ts_run_data_plane_probe "$TAILSCALE_SERVE_HEALTH_URL"; then
    echo "ERROR: Tailscale Serve readiness is not proven; Compose is running but the public HTTPS data plane is degraded." >&2
    exit 1
  fi
fi
