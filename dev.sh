#!/usr/bin/env bash
# Bootstrap a full Butlers dev environment in tmux.
# Creates three windows:
#   backend     — butlers up (starts after OAuth gate)
#   connectors  — telegram bot connector (top-left) + gmail connector (top-right) + telegram user-client connector (bottom)
#   dashboard   — dashboard API (top) + Vite frontend (bottom)
#
# Startup DAG:
#   Layer 0  — postgres starts + tailscale serve check (outer shell)
#   Layer 1a — dashboard API starts (postgres healthy)
#   Layer 1b — telegram connectors + frontend start (no OAuth dependency)
#   Layer 2  — OAuth gate: wait for dashboard responsive, then poll DB for valid
#              Google refresh token (Gmail connector + Calendar module dependency)
#   Layer 3  — butlers up + Gmail connector start (OAuth gate passed)
#
# Usage: ./dev.sh [--skip-oauth-check] [--skip-tailscale-check]
#
# OAuth Bootstrap:
#   Before launching the Gmail connector and Calendar module, this script checks
#   whether Google OAuth credentials are present in the DB or environment. When
#   credentials are missing, the script polls the shared butler_secrets store
#   (every 5s) until OAuth completes via the dashboard — no manual restart needed.
#   All other services (backend, Telegram, dashboard) start normally while waiting.
#
#   The gate polls for a non-null GOOGLE_REFRESH_TOKEN in butler_secrets.
#   Once found, Layer 3 proceeds automatically.
#
#   To suppress the check and start anyway:  ./dev.sh --skip-oauth-check
#
# OAuth Gate Timeout:
#   By default the gate waits indefinitely (infinite timeout). Set the
#   OAUTH_GATE_TIMEOUT environment variable to limit the wait:
#     OAUTH_GATE_TIMEOUT=120 ./dev.sh   # give up after 120s
#     OAUTH_GATE_TIMEOUT=0  ./dev.sh   # wait forever (default)
#
# Tailscale Serve:
#   Google OAuth requires HTTPS for non-localhost redirect URIs. This script
#   verifies that tailscale serve is running and forwarding to the dashboard
#   port (40200). If not running, it attempts to start it automatically.
#
#   Prerequisites:
#     - tailscale CLI installed and available on PATH
#     - tailscale authenticated (run: tailscale up)
#
#   To suppress the tailscale check:  ./dev.sh --skip-tailscale-check

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

SKIP_OAUTH_CHECK=false
SKIP_TAILSCALE_CHECK=false
for arg in "$@"; do
  case "$arg" in
    --skip-oauth-check) SKIP_OAUTH_CHECK=true ;;
    --skip-tailscale-check) SKIP_TAILSCALE_CHECK=true ;;
  esac
done

if ! command -v tmux &>/dev/null; then
  echo "Error: tmux is not installed" >&2
  exit 1
fi

# Determine session — use current if inside tmux, otherwise create one
if [ -n "${TMUX:-}" ]; then
  SESSION="$(tmux display-message -p '#S')"
else
  SESSION="butlers"
  tmux new-session -d -s "$SESSION" -c "$PROJECT_DIR" 2>/dev/null || true
fi

# Shared env loader (secrets + local .env)
ENV_LOADER="export \$(grep -v '^#' /secrets/.dev.env | xargs -d '\n') && export \$(grep -v '^#' .env | xargs -d '\n')"
TELEGRAM_BOT_CONNECTOR_ENV_FILE="${PROJECT_DIR}/secrets/connectors/telegram_bot"
TELEGRAM_USER_CONNECTOR_ENV_FILE="${PROJECT_DIR}/secrets/connectors/telegram_user_client"
GMAIL_CONNECTOR_ENV_FILE="${PROJECT_DIR}/secrets/connectors/gmail"
LOGS_ROOT="${PROJECT_DIR}/logs"
LOGS_RUN_ID="$(date +%Y%m%d_%H%M%S)"
LOGS_RUN_DIR="${LOGS_ROOT}/${LOGS_RUN_ID}"
LOGS_LATEST_LINK="${LOGS_ROOT}/latest"

# Per-invocation logs directory and latest symlink.
mkdir -p \
  "${LOGS_RUN_DIR}/butlers" \
  "${LOGS_RUN_DIR}/connectors" \
  "${LOGS_RUN_DIR}/uvicorn" \
  "${LOGS_RUN_DIR}/frontend"
rm -rf "${LOGS_LATEST_LINK}"
ln -s "${LOGS_RUN_DIR}" "${LOGS_LATEST_LINK}"
echo "Logs for this run: ${LOGS_RUN_DIR}"

# Mirror tmux pane output to a sanitized log file without changing pane behavior.
_pipe_pane_to_log() {
  local pane_id="$1"
  local log_file="$2"
  tmux pipe-pane -o -t "$pane_id" \
    "perl -pe 'BEGIN{\$|=1}; s/\\e\\[[0-9;?]*[ -\\/]*[@-~]//g; s/\\e\\][^\\a]*(?:\\a|\\e\\\\)//g; s/\\r//g; s/[\\x00-\\x08\\x0B-\\x1F\\x7F]//g' >> '$log_file'"
}

# ── Source shared env files (same as ENV_LOADER, before preflight check) ──
# Pre-flight check must see the same credentials as the connector panes.
# Source /secrets/.dev.env and .env now so that credentials stored there
# are visible when _has_google_creds() runs below.
if [ -f "/secrets/.dev.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . /secrets/.dev.env 2>/dev/null || true
  set +a
fi
if [ -f "${PROJECT_DIR}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${PROJECT_DIR}/.env" 2>/dev/null || true
  set +a
fi

# ── Layer 0: Tailscale serve pre-flight check ──────────────────────────────────────
# Google OAuth requires HTTPS for non-localhost redirect URIs. Verify that
# tailscale serve is running and routing:
#   - dashboard UI path -> Vite frontend port (configurable; default 40173)
#   - API path          -> dashboard API port (40200)
# If not running, attempt to start it. If tailscale is unavailable or
# unauthenticated, print actionable instructions and exit.

_normalize_path_prefix() {
  local p="${1:-/}"
  if [ -z "$p" ]; then
    p="/"
  fi
  if [ "${p#/}" = "$p" ]; then
    p="/${p}"
  fi
  if [ "$p" != "/" ]; then
    p="${p%/}"
  fi
  echo "$p"
}

DASHBOARD_PORT=40200
FRONTEND_PORT="${FRONTEND_PORT:-40173}"
TAILSCALE_HTTPS_PORT="${TAILSCALE_HTTPS_PORT:-443}"
# Backward-compat: TAILSCALE_PATH_PREFIX still maps to dashboard path if set.
TAILSCALE_DASHBOARD_PATH_PREFIX_RAW="${TAILSCALE_DASHBOARD_PATH_PREFIX:-${TAILSCALE_PATH_PREFIX:-/butlers}}"
TAILSCALE_API_PATH_PREFIX_RAW="${TAILSCALE_API_PATH_PREFIX:-/butlers-api}"
TAILSCALE_DASHBOARD_PATH_PREFIX="$(_normalize_path_prefix "$TAILSCALE_DASHBOARD_PATH_PREFIX_RAW")"
TAILSCALE_API_PATH_PREFIX="$(_normalize_path_prefix "$TAILSCALE_API_PATH_PREFIX_RAW")"
if [ "${TAILSCALE_DASHBOARD_PATH_PREFIX}" = "/" ]; then
  FRONTEND_BASE_PATH="/"
  FRONTEND_PROXY_TARGET="http://localhost:${FRONTEND_PORT}"
else
  FRONTEND_BASE_PATH="${TAILSCALE_DASHBOARD_PATH_PREFIX}/"
  # Preserve dashboard prefix end-to-end through tailscale path proxying.
  FRONTEND_PROXY_TARGET="http://localhost:${FRONTEND_PORT}${TAILSCALE_DASHBOARD_PATH_PREFIX}"
fi
if [ "${TAILSCALE_API_PATH_PREFIX}" = "/" ]; then
  FRONTEND_API_BASE_PATH="/api"
else
  FRONTEND_API_BASE_PATH="${TAILSCALE_API_PATH_PREFIX}/api"
fi
LOCAL_DASHBOARD_URL="http://localhost:${FRONTEND_PORT}${FRONTEND_BASE_PATH}"
LOCAL_API_BASE_URL="http://localhost:${DASHBOARD_PORT}/api"
OAUTH_BROWSER_URL="${LOCAL_DASHBOARD_URL}"
OAUTH_API_BASE_URL="${LOCAL_API_BASE_URL}"
OAUTH_CALLBACK_URL="${LOCAL_API_BASE_URL}/oauth/google/callback"
POSTGRES_PORT=54320
POSTGRES_HOST=127.0.0.1
POSTGRES_USER=butlers
POSTGRES_DB_DEFAULT=butlers

if [ "${TAILSCALE_DASHBOARD_PATH_PREFIX}" = "${TAILSCALE_API_PATH_PREFIX}" ]; then
  echo "Error: TAILSCALE_DASHBOARD_PATH_PREFIX and TAILSCALE_API_PATH_PREFIX must be different paths" >&2
  echo "  current values: ${TAILSCALE_DASHBOARD_PATH_PREFIX} and ${TAILSCALE_API_PATH_PREFIX}" >&2
  exit 1
fi

# Configurable OAuth gate timeout. 0 = infinite (default).
OAUTH_GATE_TIMEOUT="${OAUTH_GATE_TIMEOUT:-0}"
# Polling interval for the OAuth gate (seconds).
OAUTH_POLL_INTERVAL=5

_tailscale_serve_check() {
  local dashboard_target api_target
  dashboard_target="${FRONTEND_PROXY_TARGET}"
  api_target="http://localhost:${DASHBOARD_PORT}"

  _set_oauth_urls_for_tailnet_host_port_paths() {
    local ts_host="$1"
    local https_port="$2"
    local dashboard_path_prefix="$3"
    local api_path_prefix="$4"
    local dashboard_path_base=""
    local api_path_base=""
    if [ -z "$ts_host" ]; then
      return 0
    fi

    if [ "$dashboard_path_prefix" != "/" ]; then
      dashboard_path_base="$dashboard_path_prefix"
    fi
    if [ "$api_path_prefix" != "/" ]; then
      api_path_base="$api_path_prefix"
    fi

    if [ "${https_port}" = "443" ]; then
      OAUTH_BROWSER_URL="https://${ts_host}${dashboard_path_base}/"
      OAUTH_API_BASE_URL="https://${ts_host}${api_path_base}/api"
      OAUTH_CALLBACK_URL="https://${ts_host}${api_path_base}/api/oauth/google/callback"
    else
      OAUTH_BROWSER_URL="https://${ts_host}:${https_port}${dashboard_path_base}/"
      OAUTH_API_BASE_URL="https://${ts_host}:${https_port}${api_path_base}/api"
      OAUTH_CALLBACK_URL="https://${ts_host}:${https_port}${api_path_base}/api/oauth/google/callback"
    fi
  }

  _get_serve_target_ports_for_path() {
    local target="$1"
    local path_prefix="$2"
    local status_json="$3"
    SERVE_STATUS_JSON="$status_json" python3 - "$target" "$path_prefix" <<'PY'
import json
import os
import sys

target = sys.argv[1]
path_prefix = sys.argv[2]
data = json.loads(os.environ.get("SERVE_STATUS_JSON", "{}"))
ports = set()

for hostport, cfg in (data.get("Web") or {}).items():
    handlers = (cfg or {}).get("Handlers") or {}
    for handler_path, handler in handlers.items():
        if (
            handler_path == path_prefix
            and isinstance(handler, dict)
            and handler.get("Proxy") == target
        ):
            try:
                _, port_str = hostport.rsplit(":", 1)
                port = int(port_str)
            except Exception:
                port = 443
            ports.add(str(port))
            break

if not ports:
    raise SystemExit(1)

print(" ".join(sorted(ports, key=int)))
PY
  }

  _port_present_in_list() {
    local ports="$1"
    local wanted="$2"
    local p
    for p in $ports; do
      if [ "$p" = "$wanted" ]; then
        return 0
      fi
    done
    return 1
  }

  _run_serve_mapping() {
    local path_prefix="$1"
    local target="$2"
    local cmd_output=""
    local rc=0

    if [ "$path_prefix" = "/" ]; then
      cmd_output=$(tailscale serve --yes --bg --https="${TAILSCALE_HTTPS_PORT}" "$target" 2>&1) || rc=$?
    else
      cmd_output=$(tailscale serve --yes --bg --https="${TAILSCALE_HTTPS_PORT}" --set-path "$path_prefix" "$target" 2>&1) || rc=$?
    fi

    if [ "$rc" -ne 0 ] && echo "$cmd_output" | grep -Eqi "(invalid argument format|unknown flag|usage)"; then
      rc=0
      if [ "$path_prefix" = "/" ]; then
        cmd_output=$(tailscale serve "https:${TAILSCALE_HTTPS_PORT}" "$target" 2>&1) || rc=$?
      else
        cmd_output=$(tailscale serve "https:${TAILSCALE_HTTPS_PORT}" "$path_prefix" "$target" 2>&1) || rc=$?
      fi
    fi

    echo "$cmd_output"
    return "$rc"
  }

  # Check tailscale CLI is available
  if ! command -v tailscale &>/dev/null; then
    echo "" >&2
    echo "======================================================================" >&2
    echo "  ERROR: tailscale CLI not found" >&2
    echo "======================================================================" >&2
    echo "" >&2
    echo "  Google OAuth requires HTTPS for non-localhost redirect URIs." >&2
    echo "  Butlers uses tailscale serve to provide a stable HTTPS endpoint." >&2
    echo "" >&2
    echo "  To fix:" >&2
    echo "    1. Install Tailscale: https://tailscale.com/download" >&2
    echo "    2. Authenticate:      tailscale up" >&2
    echo "    3. Re-run:            ./dev.sh" >&2
    echo "" >&2
    echo "  To skip this check (OAuth callback will not work over HTTPS):" >&2
    echo "    ./dev.sh --skip-tailscale-check" >&2
    echo "======================================================================" >&2
    echo "" >&2
    return 1
  fi

  # Check tailscale is authenticated (status returns non-zero or shows Stopped/NeedsLogin)
  local ts_status
  ts_status=$(tailscale status --json 2>/dev/null) || true
  local ts_state
  ts_state=$(echo "$ts_status" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('BackendState','Unknown'))" 2>/dev/null || echo "Unknown")

  if [ "$ts_state" = "NeedsLogin" ] || [ "$ts_state" = "NoState" ] || [ "$ts_state" = "Stopped" ]; then
    echo "" >&2
    echo "======================================================================" >&2
    echo "  ERROR: tailscale is not authenticated (state: ${ts_state})" >&2
    echo "======================================================================" >&2
    echo "" >&2
    echo "  Authenticate tailscale before starting Butlers:" >&2
    echo "    tailscale up" >&2
    echo "" >&2
    echo "  Then re-run: ./dev.sh" >&2
    echo "" >&2
    echo "  To skip this check (OAuth callback will not work over HTTPS):" >&2
    echo "    ./dev.sh --skip-tailscale-check" >&2
    echo "======================================================================" >&2
    echo "" >&2
    return 1
  fi

  # Check if tailscale serve is already running with both required mappings.
  local serve_status_json
  local dashboard_ports api_ports
  local dashboard_ready=false
  local api_ready=false
  serve_status_json="{}"
  serve_status_json=$(tailscale serve status --json 2>/dev/null) || serve_status_json="{}"
  dashboard_ports=$(_get_serve_target_ports_for_path "$dashboard_target" "$TAILSCALE_DASHBOARD_PATH_PREFIX" "$serve_status_json" 2>/dev/null || true)
  api_ports=$(_get_serve_target_ports_for_path "$api_target" "$TAILSCALE_API_PATH_PREFIX" "$serve_status_json" 2>/dev/null || true)
  if _port_present_in_list "$dashboard_ports" "$TAILSCALE_HTTPS_PORT"; then
    dashboard_ready=true
  fi
  if _port_present_in_list "$api_ports" "$TAILSCALE_HTTPS_PORT"; then
    api_ready=true
  fi

  if [ "$dashboard_ready" = "true" ] && [ "$api_ready" = "true" ]; then
    local ts_hostname
    ts_hostname=$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Self',{}).get('DNSName','').rstrip('.'))" 2>/dev/null || echo "")
    _set_oauth_urls_for_tailnet_host_port_paths "$ts_hostname" "$TAILSCALE_HTTPS_PORT" "$TAILSCALE_DASHBOARD_PATH_PREFIX" "$TAILSCALE_API_PATH_PREFIX"
    echo "Tailscale serve: already running (dashboard ${TAILSCALE_DASHBOARD_PATH_PREFIX} -> :${FRONTEND_PORT}, api ${TAILSCALE_API_PATH_PREFIX} -> :${DASHBOARD_PORT})"
    echo "Tailscale dashboard URL: ${OAUTH_BROWSER_URL}"
    echo "Tailscale API base URL: ${OAUTH_API_BASE_URL}"
    return 0
  fi

  if [ "$dashboard_ready" = "false" ] && [ -n "$dashboard_ports" ]; then
    echo "Tailscale serve: existing dashboard mapping found on HTTPS ports [${dashboard_ports}], adding port ${TAILSCALE_HTTPS_PORT}..."
  fi
  if [ "$api_ready" = "false" ] && [ -n "$api_ports" ]; then
    echo "Tailscale serve: existing API mapping found on HTTPS ports [${api_ports}], adding port ${TAILSCALE_HTTPS_PORT}..."
  fi

  # Not fully configured yet — attempt to (re)apply missing mappings.
  local start_output=""
  local start_rc=0
  echo "Tailscale serve: ensuring dashboard (${TAILSCALE_DASHBOARD_PATH_PREFIX}) and API (${TAILSCALE_API_PATH_PREFIX}) mappings on HTTPS port ${TAILSCALE_HTTPS_PORT}..."

  if [ "$dashboard_ready" = "false" ]; then
    local dashboard_cmd_output=""
    if ! dashboard_cmd_output=$(_run_serve_mapping "$TAILSCALE_DASHBOARD_PATH_PREFIX" "$dashboard_target"); then
      start_rc=1
    fi
    if [ -n "$dashboard_cmd_output" ]; then
      start_output="${start_output}
[dashboard ${TAILSCALE_DASHBOARD_PATH_PREFIX} -> ${dashboard_target}]
${dashboard_cmd_output}"
    fi
  fi

  if [ "$api_ready" = "false" ]; then
    local api_cmd_output=""
    if ! api_cmd_output=$(_run_serve_mapping "$TAILSCALE_API_PATH_PREFIX" "$api_target"); then
      start_rc=1
    fi
    if [ -n "$api_cmd_output" ]; then
      start_output="${start_output}
[api ${TAILSCALE_API_PATH_PREFIX} -> ${api_target}]
${api_cmd_output}"
    fi
  fi

  # Verify both mappings after attempting to apply.
  if [ "$start_rc" -eq 0 ]; then
    serve_status_json=$(tailscale serve status --json 2>/dev/null) || serve_status_json="{}"
    dashboard_ports=$(_get_serve_target_ports_for_path "$dashboard_target" "$TAILSCALE_DASHBOARD_PATH_PREFIX" "$serve_status_json" 2>/dev/null || true)
    api_ports=$(_get_serve_target_ports_for_path "$api_target" "$TAILSCALE_API_PATH_PREFIX" "$serve_status_json" 2>/dev/null || true)
    dashboard_ready=false
    api_ready=false
    if _port_present_in_list "$dashboard_ports" "$TAILSCALE_HTTPS_PORT"; then
      dashboard_ready=true
    fi
    if _port_present_in_list "$api_ports" "$TAILSCALE_HTTPS_PORT"; then
      api_ready=true
    fi
    if [ "$dashboard_ready" = "true" ] && [ "$api_ready" = "true" ]; then
      local ts_hostname
      ts_hostname=$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Self',{}).get('DNSName','<your-name>.ts.net').rstrip('.'))" 2>/dev/null || echo "<your-name>.ts.net")
      _set_oauth_urls_for_tailnet_host_port_paths "$ts_hostname" "$TAILSCALE_HTTPS_PORT" "$TAILSCALE_DASHBOARD_PATH_PREFIX" "$TAILSCALE_API_PATH_PREFIX"
      echo "Tailscale serve: ready"
      echo "  Dashboard URL: ${OAUTH_BROWSER_URL} -> ${dashboard_target}"
      echo "  API base URL: ${OAUTH_API_BASE_URL} -> ${api_target}"
      echo ""
      echo "  HTTPS callback URL: ${OAUTH_CALLBACK_URL}"
      echo "  Add this URI to your Google Cloud Console OAuth credentials."
      echo ""
      return 0
    fi
  fi

  # Failed to start tailscale serve
  echo "" >&2
  echo "======================================================================" >&2
  echo "  ERROR: Failed to start tailscale serve" >&2
  echo "======================================================================" >&2
  echo "" >&2
  echo "  Could not configure required mappings:" >&2
  echo "    dashboard ${TAILSCALE_DASHBOARD_PATH_PREFIX} -> ${dashboard_target}" >&2
  echo "    api       ${TAILSCALE_API_PATH_PREFIX} -> ${api_target}" >&2
  if [ -n "$start_output" ]; then
    echo "" >&2
    echo "  tailscale output:" >&2
    echo "  $start_output" >&2
  fi
  if echo "$start_output" | grep -qi "Access denied: serve config denied"; then
    echo "" >&2
    echo "  This node requires elevated permissions to manage serve config." >&2
    echo "  One-time setup to allow your user to manage serve without sudo:" >&2
    echo "    sudo tailscale set --operator=$USER" >&2
  fi
  echo "" >&2
  echo "  To start manually:" >&2
  if [ "$TAILSCALE_DASHBOARD_PATH_PREFIX" = "/" ]; then
    echo "    tailscale serve --yes --bg --https=${TAILSCALE_HTTPS_PORT} ${dashboard_target}" >&2
  else
    echo "    tailscale serve --yes --bg --https=${TAILSCALE_HTTPS_PORT} --set-path ${TAILSCALE_DASHBOARD_PATH_PREFIX} ${dashboard_target}" >&2
  fi
  if [ "$TAILSCALE_API_PATH_PREFIX" = "/" ]; then
    echo "    tailscale serve --yes --bg --https=${TAILSCALE_HTTPS_PORT} ${api_target}" >&2
  else
    echo "    tailscale serve --yes --bg --https=${TAILSCALE_HTTPS_PORT} --set-path ${TAILSCALE_API_PATH_PREFIX} ${api_target}" >&2
  fi
  echo "    tailscale serve status" >&2
  echo "" >&2
  echo "  To skip this check (OAuth callback will not work over HTTPS):" >&2
  echo "    ./dev.sh --skip-tailscale-check" >&2
  echo "======================================================================" >&2
  echo "" >&2
  return 1
}

if [ "$SKIP_TAILSCALE_CHECK" = "false" ]; then
  _tailscale_serve_check || exit 1
fi

# ── Layer 0: Postgres startup + health check ──────────────────────────────
# Start postgres and wait until pg_isready confirms it is accepting connections.
# All subsequent layers depend on a healthy postgres instance.

_wait_for_postgres() {
  local max_wait="${1:-30}"  # seconds
  local interval=1
  local elapsed=0

  echo "Layer 0: Starting postgres..."
  docker compose stop postgres 2>/dev/null || true
  docker compose up -d postgres

  echo "Layer 0: Waiting for postgres to be healthy (pg_isready)..."
  while [ "$elapsed" -lt "$max_wait" ]; do
    if pg_isready -h 127.0.0.1 -p "${POSTGRES_PORT}" -q 2>/dev/null; then
      echo "Layer 0: postgres is healthy (${elapsed}s)"
      return 0
    fi
    sleep "$interval"
    elapsed=$((elapsed + interval))
  done

  echo "" >&2
  echo "======================================================================" >&2
  echo "  ERROR: postgres did not become healthy within ${max_wait}s" >&2
  echo "======================================================================" >&2
  echo "" >&2
  echo "  Check docker compose logs:" >&2
  echo "    docker compose logs postgres" >&2
  echo "" >&2
  return 1
}

_wait_for_postgres 30 || exit 1

# ── OAuth credential pre-flight check ─────────────────────────────────────
# Check whether Google credentials are available in the shared DB secrets store.
# This runs in the *outer* shell before tmux windows are created so that
# developers see the warning immediately rather than only inside a pane.

_is_valid_sql_identifier() {
  local ident="$1"
  [[ "$ident" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]
}

_shared_credentials_db_name() {
  if [ -n "${BUTLER_SHARED_DB_NAME:-}" ]; then
    printf '%s' "${BUTLER_SHARED_DB_NAME}"
    return 0
  fi
  printf '%s' "butlers"
}

_shared_credentials_schema() {
  local schema="${BUTLER_SHARED_DB_SCHEMA:-shared}"
  if ! _is_valid_sql_identifier "$schema"; then
    echo "Warning: invalid BUTLER_SHARED_DB_SCHEMA=${schema}; using 'shared'" >&2
    schema="shared"
  fi
  printf '%s' "$schema"
}

_shared_refresh_token_count() {
  local db_name="$1"
  local schema="$2"
  PGPASSWORD="${POSTGRES_PASSWORD:-butlers}" \
  PGOPTIONS="-c search_path=${schema},public" \
  psql \
    -h "${POSTGRES_HOST:-localhost}" \
    -p "${POSTGRES_PORT:-54320}" \
    -U "${POSTGRES_USER:-butlers}" \
    -d "$db_name" \
    -tAc \
    "SELECT COUNT(*) FROM butler_secrets WHERE secret_key='GOOGLE_REFRESH_TOKEN' AND secret_value IS NOT NULL AND length(secret_value) > 0;" \
    2>/dev/null || echo "0"
}

_has_google_creds() {
  # Check DB for stored credentials using psql (requires DB to be reachable).
  # Primary path: one-db shared schema (butlers.shared).
  if command -v psql >/dev/null 2>&1; then
    local shared_db shared_schema shared_count
    shared_db="$(_shared_credentials_db_name)"
    shared_schema="$(_shared_credentials_schema)"
    shared_count="$(_shared_refresh_token_count "$shared_db" "$shared_schema")"
    if [ "${shared_count:-0}" -gt 0 ] 2>/dev/null; then
      return 0
    fi
  fi

  return 1
}

# ── DB-based Google credential check ──────────────────────────────────────
# Poll for a non-null Google refresh token.
# Checks the one-db shared schema store. Returns 0 on success.

_poll_db_for_refresh_token() {
  local psql_bin
  psql_bin=$(command -v psql 2>/dev/null || echo "")

  if [ -z "$psql_bin" ]; then
    # psql not available — fall back to HTTP status endpoint on the dashboard
    _poll_oauth_via_http && return 0
    return 1
  fi

  local shared_db shared_schema shared_count
  shared_db="$(_shared_credentials_db_name)"
  shared_schema="$(_shared_credentials_schema)"
  shared_count="$(_shared_refresh_token_count "$shared_db" "$shared_schema")"
  if [ "${shared_count:-0}" -gt 0 ] 2>/dev/null; then
    return 0
  fi

  return 1
}

# HTTP fallback: use dashboard credential status endpoint if psql is unavailable
_poll_oauth_via_http() {
  local status_url="http://localhost:${DASHBOARD_PORT}/api/oauth/google/credentials"
  local refresh_present
  refresh_present=$(
    curl -sf "$status_url" 2>/dev/null | python3 -c \
      "import sys,json; d=json.load(sys.stdin); print('1' if d.get('refresh_token_present') else '')" \
      2>/dev/null || echo ""
  )
  [ "$refresh_present" = "1" ]
}

# Pick the DB name the Gmail connector should use for DB-first OAuth credential lookup.
# Preference order:
# 1) Explicit connector override env vars
# 2) Shared credential DB (default one-db canonical: butlers)
_select_google_credentials_db() {
  if [ -n "${GMAIL_CONNECTOR_BUTLER_DB_NAME:-}" ]; then
    printf '%s' "${GMAIL_CONNECTOR_BUTLER_DB_NAME}"
    return 0
  fi
  if [ -n "${CONNECTOR_BUTLER_DB_NAME:-}" ]; then
    printf '%s' "${CONNECTOR_BUTLER_DB_NAME}"
    return 0
  fi

  printf '%s' "$(_shared_credentials_db_name)"
}

# ── Layer 2: OAuth gate ────────────────────────────────────────────────────
# Block the outer shell until Google OAuth credentials are available.
# Credentials are resolved from DB-backed secrets only.
# When credentials are missing, displays actionable instructions covering both
# the Gmail connector and Calendar module — they share the same OAuth bootstrap.
#
# The gate polls at most every OAUTH_POLL_INTERVAL seconds. If OAUTH_GATE_TIMEOUT
# is set to a positive integer, the gate gives up after that many seconds and
# continues startup (non-blocking fallback, with a warning). Set to 0 for infinite
# wait (the default).

_oauth_gate() {
  local poll_interval="${OAUTH_POLL_INTERVAL:-5}"
  local timeout="${OAUTH_GATE_TIMEOUT:-0}"   # 0 = infinite
  local elapsed=0

  # Fast path: skip entirely if --skip-oauth-check was given
  if [ "$SKIP_OAUTH_CHECK" = "true" ]; then
    return 0
  fi

  # Fast path: credentials already available in shared DB secrets
  if _has_google_creds; then
    echo "Layer 2: Google OAuth credentials detected in DB."
    return 0
  fi

  # Slow path: credentials missing — poll the DB
  echo ""
  echo "======================================================================"
  echo "  Layer 2: Waiting for Google OAuth credentials"
  echo "======================================================================"
  echo ""
  echo "  Google OAuth credentials are required by:"
  echo "    - Gmail connector      (outbound email delivery)"
  echo "    - Calendar module      (calendar read/write for all butlers)"
  echo ""
  echo "  To complete OAuth bootstrap:"
  echo "    1. Open ${OAUTH_BROWSER_URL} in your browser"
  echo "    2. Click 'Connect Google' and complete the OAuth flow"
  echo "    3. Butlers will proceed automatically once credentials are stored"
  echo ""
  if [ "$timeout" -gt 0 ] 2>/dev/null; then
    echo "  Polling DB every ${poll_interval}s (timeout: ${timeout}s)..."
  else
    echo "  Polling DB every ${poll_interval}s (no timeout — Ctrl+C to abort)..."
  fi
  echo "  To skip this gate: ./dev.sh --skip-oauth-check"
  echo "======================================================================"
  echo ""

  while true; do
    if _poll_db_for_refresh_token; then
      echo "Layer 2: Google OAuth credentials detected in DB — proceeding to Layer 3."
      echo ""
      return 0
    fi

    # Check timeout (0 means infinite)
    if [ "$timeout" -gt 0 ] 2>/dev/null && [ "$elapsed" -ge "$timeout" ]; then
      echo "" >&2
      echo "======================================================================" >&2
      echo "  WARNING: OAuth gate timed out after ${timeout}s" >&2
      echo "======================================================================" >&2
      echo "" >&2
      echo "  Continuing startup without Google credentials." >&2
      echo "  Gmail connector and Calendar module will be unavailable." >&2
      echo "" >&2
      return 1
    fi

    sleep "$poll_interval"
    elapsed=$((elapsed + poll_interval))
    echo "  Layer 2: Still waiting for Google OAuth... (${elapsed}s elapsed)"
  done
}

GOOGLE_CREDS_AVAILABLE=false
if _has_google_creds; then
  GOOGLE_CREDS_AVAILABLE=true
fi

if [ "$GOOGLE_CREDS_AVAILABLE" = "false" ] && [ "$SKIP_OAUTH_CHECK" = "false" ]; then
  echo ""
  echo "======================================================================"
  echo "  WARNING: Google OAuth credentials not found yet"
  echo "======================================================================"
  echo ""
  echo "  The Gmail connector and Calendar module require Google OAuth"
  echo "  credentials to start. The OAuth gate (Layer 2) will wait for"
  echo "  credentials to be stored in the DB via the dashboard."
  echo ""
  echo "  Option A — Dashboard OAuth flow (recommended):"
  echo "    1. Start Butlers and visit ${OAUTH_BROWSER_URL}"
  echo "    2. Click 'Connect Google' and complete the OAuth flow"
  echo "    3. Credentials are stored in the DB — Layer 3 starts automatically"
  echo ""
  echo "  Affected components: Gmail connector, Calendar module"
  echo "  All other services (backend, Telegram, dashboard) start normally."
  echo ""
  echo "  To suppress this check: ./dev.sh --skip-oauth-check"
  echo "======================================================================"
  echo ""
fi

# ── Gmail connector startup script ────────────────────────────────────────
# When credentials are missing, show guidance and wait rather than crash.
# When credentials are present, start normally.

_build_gmail_pane_cmd() {
  local gmail_cmd_base
  local gmail_creds_db
  local gmail_creds_schema
  gmail_cmd_base="${ENV_LOADER} && if [ -f \"$GMAIL_CONNECTOR_ENV_FILE\" ]; then set -a && . \"$GMAIL_CONNECTOR_ENV_FILE\" && set +a; fi && mkdir -p .tmp/connectors"
  gmail_creds_db="$(_select_google_credentials_db)"
  gmail_creds_schema="$(_shared_credentials_schema)"

  if [ "$SKIP_OAUTH_CHECK" = "true" ] || _has_google_creds; then
    # Credentials available — start connector with DB-first lookup enabled.
    printf '%s' "${gmail_cmd_base} && POSTGRES_HOST=${POSTGRES_HOST} POSTGRES_PORT=${POSTGRES_PORT} POSTGRES_USER=${POSTGRES_USER} CONNECTOR_BUTLER_DB_NAME=${gmail_creds_db} CONNECTOR_BUTLER_DB_SCHEMA=${gmail_creds_schema} BUTLER_SHARED_DB_NAME=${gmail_creds_db} BUTLER_SHARED_DB_SCHEMA=${gmail_creds_schema} CONNECTOR_PROVIDER=gmail CONNECTOR_CHANNEL=email CONNECTOR_ENDPOINT_IDENTITY=\${GMAIL_CONNECTOR_ENDPOINT_IDENTITY:-gmail:user:dev} CONNECTOR_CURSOR_PATH=\${GMAIL_CONNECTOR_CURSOR_PATH:-.tmp/connectors/gmail_checkpoint.json} uv run python -m butlers.connectors.gmail"
    return 0
  fi

  # Credentials missing — show instructions and keep pane alive
  printf '%s' "echo '' && echo '======================================================================' && echo '  Gmail connector is waiting for Google OAuth credentials.' && echo '  Also required by: Calendar module.' && echo '======================================================================' && echo '' && echo '  To complete bootstrap:' && echo '    1. Open ${OAUTH_BROWSER_URL} in your browser' && echo '    2. Click Connect Google and complete the OAuth flow' && echo '    3. Once authorized, restart this pane (tmux: prefix+R or exit+up+Enter)' && echo '' && echo '  Credentials are read from the shared DB (butler_secrets).' && echo '' && echo '  (This pane will remain open — restart it after completing OAuth)' && bash"
}

# Kill existing windows if present (idempotent re-runs)
for WIN in backend connectors dashboard; do
  tmux kill-window -t "${SESSION}:${WIN}" 2>/dev/null || true
done

# ── Layer 1a: dashboard window ─────────────────────────────────────────────
# Dashboard API starts immediately after postgres is healthy.
# OAuth gate (Layer 2) will wait for this to be responsive.
echo "Layer 1a: Starting dashboard API and frontend..."
PANE_DASHBOARD=$(tmux new-window -t "$SESSION:" -n dashboard -c "$PROJECT_DIR" -P -F '#{pane_id}')
PANE_FRONTEND=$(tmux split-window -t "$PANE_DASHBOARD" -v -c "${PROJECT_DIR}/frontend" -P -F '#{pane_id}')
_pipe_pane_to_log "$PANE_DASHBOARD" "${LOGS_RUN_DIR}/uvicorn/dashboard.log"
_pipe_pane_to_log "$PANE_FRONTEND" "${LOGS_RUN_DIR}/frontend/vite.log"

tmux send-keys -t "$PANE_DASHBOARD" \
  "GOOGLE_OAUTH_REDIRECT_URI=${OAUTH_CALLBACK_URL} POSTGRES_PORT=${POSTGRES_PORT} BUTLERS_DISABLE_FILE_LOGGING=1 uv run butlers dashboard --host 0.0.0.0 --port ${DASHBOARD_PORT}" Enter
# Brief wait for shell init in the split pane
sleep 0.3
tmux send-keys -t "$PANE_FRONTEND" \
  "npm install && VITE_API_URL=${FRONTEND_API_BASE_PATH} npm run dev -- --host 0.0.0.0 --port ${FRONTEND_PORT} --strictPort --base ${FRONTEND_BASE_PATH}" Enter

# ── Layer 1b: connectors window (telegram + frontend) ─────────────────────
# Telegram connectors and frontend start without waiting for OAuth.
# Gmail pane is created here but will block until Layer 3 is reached.
echo "Layer 1b: Starting Telegram connectors..."
PANE_TELEGRAM_BOT=$(tmux new-window -t "$SESSION:" -n connectors -c "$PROJECT_DIR" -P -F '#{pane_id}')
PANE_TELEGRAM_USER=$(tmux split-window -t "$PANE_TELEGRAM_BOT" -v -c "$PROJECT_DIR" -P -F '#{pane_id}')
PANE_GMAIL=$(tmux split-window -t "$PANE_TELEGRAM_BOT" -h -c "$PROJECT_DIR" -P -F '#{pane_id}')
_pipe_pane_to_log "$PANE_TELEGRAM_BOT" "${LOGS_RUN_DIR}/connectors/telegram_bot.log"
_pipe_pane_to_log "$PANE_TELEGRAM_USER" "${LOGS_RUN_DIR}/connectors/telegram_user_client.log"
_pipe_pane_to_log "$PANE_GMAIL" "${LOGS_RUN_DIR}/connectors/gmail.log"
# Give newly split panes a moment to finish shell init before send-keys.
# Without this, tmux can occasionally drop early keystrokes on fast reruns.
sleep 0.3

tmux send-keys -t "$PANE_TELEGRAM_BOT" \
  "${ENV_LOADER} && if [ -f \"$TELEGRAM_BOT_CONNECTOR_ENV_FILE\" ]; then set -a && . \"$TELEGRAM_BOT_CONNECTOR_ENV_FILE\" && set +a; fi && mkdir -p .tmp/connectors && CONNECTOR_PROVIDER=telegram CONNECTOR_CHANNEL=telegram CONNECTOR_ENDPOINT_IDENTITY=\${TELEGRAM_BOT_CONNECTOR_ENDPOINT_IDENTITY:-\${CONNECTOR_ENDPOINT_IDENTITY:-telegram:bot:dev}} CONNECTOR_CURSOR_PATH=\${TELEGRAM_BOT_CONNECTOR_CURSOR_PATH:-\${CONNECTOR_CURSOR_PATH:-.tmp/connectors/telegram_bot_checkpoint.json}} uv run python -m butlers.connectors.telegram_bot" Enter

tmux send-keys -t "$PANE_TELEGRAM_USER" \
  "${ENV_LOADER} && if [ -f \"$TELEGRAM_USER_CONNECTOR_ENV_FILE\" ]; then set -a && . \"$TELEGRAM_USER_CONNECTOR_ENV_FILE\" && set +a; fi && mkdir -p .tmp/connectors && CONNECTOR_PROVIDER=telegram CONNECTOR_CHANNEL=telegram CONNECTOR_ENDPOINT_IDENTITY=\${TELEGRAM_USER_CONNECTOR_ENDPOINT_IDENTITY:-telegram:user:dev} CONNECTOR_CURSOR_PATH=\${TELEGRAM_USER_CONNECTOR_CURSOR_PATH:-.tmp/connectors/telegram_user_client_checkpoint.json} uv run python -m butlers.connectors.telegram_user_client" Enter

# Gmail pane shows a waiting message until Layer 3 starts it
# (populated later after OAuth gate passes)

# ── Layer 2: OAuth gate ────────────────────────────────────────────────────
# Phase A: wait for the dashboard to be responsive.
# Phase B: poll the DB for a valid Google refresh token.
# Both phases run in the outer shell. Layer 3 only starts after this completes.

_wait_for_dashboard() {
  local max_wait="${1:-60}"  # seconds
  local interval=2
  local elapsed=0

  echo "Layer 2: Waiting for dashboard API to be responsive (http://localhost:${DASHBOARD_PORT}/health)..."
  while [ "$elapsed" -lt "$max_wait" ]; do
    if curl -sf "http://localhost:${DASHBOARD_PORT}/health" >/dev/null 2>&1; then
      echo "Layer 2: Dashboard API is responsive (${elapsed}s)"
      return 0
    fi
    sleep "$interval"
    elapsed=$((elapsed + interval))
  done

  echo "" >&2
  echo "======================================================================" >&2
  echo "  WARNING: Dashboard API did not respond within ${max_wait}s" >&2
  echo "======================================================================" >&2
  echo "" >&2
  echo "  Continuing OAuth gate check — check the dashboard pane for errors." >&2
  echo "" >&2
  return 1
}

# Phase A: wait for dashboard (non-fatal timeout — continue regardless)
_wait_for_dashboard 60 || true

# Phase B: run the OAuth gate (blocking poll)
# Returns 0 if credentials found (or skipped), 1 if timed out.
_oauth_gate || true

# ── Layer 3: backend window + Gmail ───────────────────────────────────────
# butlers up and Gmail connector start only after the OAuth gate has passed.
echo "Layer 3: Starting butlers up and Gmail connector..."

PANE_BACKEND=$(tmux new-window -t "$SESSION:" -n backend -c "$PROJECT_DIR" -P -F '#{pane_id}')
_pipe_pane_to_log "$PANE_BACKEND" "${LOGS_RUN_DIR}/butlers/up.log"
tmux send-keys -t "$PANE_BACKEND" \
  "${ENV_LOADER} && uv sync --dev && POSTGRES_PORT=${POSTGRES_PORT} BUTLERS_SWITCHBOARD_URL=http://localhost:${DASHBOARD_PORT} uv run butlers up" Enter

# Start Gmail pane (credentials-aware)
GMAIL_PANE_CMD="$(_build_gmail_pane_cmd)"
tmux send-keys -t "$PANE_GMAIL" \
  "${GMAIL_PANE_CMD}" Enter

# Focus the backend window
tmux select-window -t "${SESSION}:backend"

# Attach if we started detached
if [ -z "${TMUX:-}" ]; then
  exec tmux attach-session -t "$SESSION"
fi
