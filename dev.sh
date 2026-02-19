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
#   credentials are missing, the script polls the google_oauth_credentials table
#   (every 5s) until OAuth completes via the dashboard — no manual restart needed.
#   All other services (backend, Telegram, dashboard) start normally while waiting.
#
#   The gate polls the google_oauth_credentials table for a non-null refresh_token.
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
#   port (8200). If not running, it attempts to start it automatically.
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
# tailscale serve is running and pointing to the dashboard port (8200).
# If not running, attempt to start it. If tailscale is unavailable or
# unauthenticated, print actionable instructions and exit.

DASHBOARD_PORT=8200
TAILSCALE_HTTPS_PORT="${TAILSCALE_HTTPS_PORT:-443}"
TAILSCALE_PATH_PREFIX="${TAILSCALE_PATH_PREFIX:-/butlers}"
LOCAL_DASHBOARD_URL="http://localhost:${DASHBOARD_PORT}"
OAUTH_BROWSER_URL="${LOCAL_DASHBOARD_URL}"
OAUTH_CALLBACK_URL="${LOCAL_DASHBOARD_URL}/api/oauth/google/callback"
POSTGRES_PORT=54320
POSTGRES_HOST=127.0.0.1
POSTGRES_USER=butlers
POSTGRES_DB_DEFAULT=butler_general

# Normalize serve path prefix:
# - empty -> /
# - always starts with /
# - trim trailing / except root
if [ -z "${TAILSCALE_PATH_PREFIX}" ]; then
  TAILSCALE_PATH_PREFIX="/"
fi
if [ "${TAILSCALE_PATH_PREFIX#/}" = "${TAILSCALE_PATH_PREFIX}" ]; then
  TAILSCALE_PATH_PREFIX="/${TAILSCALE_PATH_PREFIX}"
fi
if [ "${TAILSCALE_PATH_PREFIX}" != "/" ]; then
  TAILSCALE_PATH_PREFIX="${TAILSCALE_PATH_PREFIX%/}"
fi

# Configurable OAuth gate timeout. 0 = infinite (default).
OAUTH_GATE_TIMEOUT="${OAUTH_GATE_TIMEOUT:-0}"
# Polling interval for the OAuth gate (seconds).
OAUTH_POLL_INTERVAL=5

_tailscale_serve_check() {
  local proxy_target
  proxy_target="http://localhost:${DASHBOARD_PORT}"

  _set_oauth_urls_for_tailnet_host_port_path() {
    local ts_host="$1"
    local https_port="$2"
    local path_prefix="$3"
    local path_base=""
    if [ -z "$ts_host" ]; then
      return 0
    fi

    if [ "$path_prefix" != "/" ]; then
      path_base="$path_prefix"
    fi

    if [ "${https_port}" = "443" ]; then
      OAUTH_BROWSER_URL="https://${ts_host}${path_base}/"
      OAUTH_CALLBACK_URL="https://${ts_host}${path_base}/api/oauth/google/callback"
    else
      OAUTH_BROWSER_URL="https://${ts_host}:${https_port}${path_base}/"
      OAUTH_CALLBACK_URL="https://${ts_host}:${https_port}${path_base}/api/oauth/google/callback"
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

  # Check if tailscale serve is already running with the requested HTTPS port.
  local serve_status serve_status_json serve_ports desired_port_present
  serve_status=$(tailscale serve status 2>/dev/null) || serve_status=""
  serve_status_json=$(tailscale serve status --json 2>/dev/null) || serve_status_json="{}"
  serve_ports=$(_get_serve_target_ports_for_path "$proxy_target" "$TAILSCALE_PATH_PREFIX" "$serve_status_json" 2>/dev/null || true)
  desired_port_present=false
  for p in $serve_ports; do
    if [ "$p" = "$TAILSCALE_HTTPS_PORT" ]; then
      desired_port_present=true
      break
    fi
  done

  if [ "$desired_port_present" = "true" ]; then
    local ts_hostname
    ts_hostname=$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Self',{}).get('DNSName','').rstrip('.'))" 2>/dev/null || echo "")
    _set_oauth_urls_for_tailnet_host_port_path "$ts_hostname" "$TAILSCALE_HTTPS_PORT" "$TAILSCALE_PATH_PREFIX"
    echo "Tailscale serve: already running (forwarding to localhost:${DASHBOARD_PORT} on path ${TAILSCALE_PATH_PREFIX})"
    echo "Tailscale URL: ${OAUTH_BROWSER_URL}"
    return 0
  fi

  if [ -n "$serve_ports" ]; then
    echo "Tailscale serve: existing mapping for localhost:${DASHBOARD_PORT} on path ${TAILSCALE_PATH_PREFIX} found on HTTPS ports [${serve_ports}], adding port ${TAILSCALE_HTTPS_PORT}..."
  fi

  # Not yet serving — attempt to start it
  # Newer tailscale versions (>=1.9x) use --https/--bg syntax while
  # older versions accept the legacy positional https:443 form.
  local start_output=""
  local start_rc=0
  echo "Tailscale serve: not active for path ${TAILSCALE_PATH_PREFIX} on HTTPS port ${TAILSCALE_HTTPS_PORT}, starting..."
  if [ "$TAILSCALE_PATH_PREFIX" = "/" ]; then
    start_output=$(tailscale serve --yes --bg --https="${TAILSCALE_HTTPS_PORT}" "http://localhost:${DASHBOARD_PORT}" 2>&1) || start_rc=$?
  else
    start_output=$(tailscale serve --yes --bg --https="${TAILSCALE_HTTPS_PORT}" --set-path "${TAILSCALE_PATH_PREFIX}" "http://localhost:${DASHBOARD_PORT}" 2>&1) || start_rc=$?
  fi
  if [ "$start_rc" -ne 0 ] && echo "$start_output" | grep -Eqi "(invalid argument format|unknown flag|usage)"; then
    if [ "$TAILSCALE_PATH_PREFIX" = "/" ]; then
      start_output=$(tailscale serve "https:${TAILSCALE_HTTPS_PORT}" "http://localhost:${DASHBOARD_PORT}" 2>&1) || start_rc=$?
    else
      start_output=$(tailscale serve "https:${TAILSCALE_HTTPS_PORT}" "${TAILSCALE_PATH_PREFIX}" "http://localhost:${DASHBOARD_PORT}" 2>&1) || start_rc=$?
    fi
  fi

  if [ "$start_rc" -eq 0 ]; then
    # Verify it started correctly
    serve_status_json=$(tailscale serve status --json 2>/dev/null) || serve_status_json="{}"
    serve_ports=$(_get_serve_target_ports_for_path "$proxy_target" "$TAILSCALE_PATH_PREFIX" "$serve_status_json" 2>/dev/null || true)
    desired_port_present=false
    for p in $serve_ports; do
      if [ "$p" = "$TAILSCALE_HTTPS_PORT" ]; then
        desired_port_present=true
        break
      fi
    done
    if [ "$desired_port_present" = "true" ]; then
      local ts_hostname
      ts_hostname=$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Self',{}).get('DNSName','<your-name>.ts.net').rstrip('.'))" 2>/dev/null || echo "<your-name>.ts.net")
      _set_oauth_urls_for_tailnet_host_port_path "$ts_hostname" "$TAILSCALE_HTTPS_PORT" "$TAILSCALE_PATH_PREFIX"
      echo "Tailscale serve: started — ${OAUTH_BROWSER_URL} → http://localhost:${DASHBOARD_PORT} (path ${TAILSCALE_PATH_PREFIX})"
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
  echo "  Could not start tailscale serve for http://localhost:${DASHBOARD_PORT}" >&2
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
  if [ "$TAILSCALE_PATH_PREFIX" = "/" ]; then
    echo "    tailscale serve --yes --bg --https=${TAILSCALE_HTTPS_PORT} http://localhost:${DASHBOARD_PORT}" >&2
  else
    echo "    tailscale serve --yes --bg --https=${TAILSCALE_HTTPS_PORT} --set-path ${TAILSCALE_PATH_PREFIX} http://localhost:${DASHBOARD_PORT}" >&2
  fi
  echo "    # (older tailscale CLI fallback)" >&2
  if [ "$TAILSCALE_PATH_PREFIX" = "/" ]; then
    echo "    tailscale serve https:${TAILSCALE_HTTPS_PORT} http://localhost:${DASHBOARD_PORT}" >&2
  else
    echo "    tailscale serve https:${TAILSCALE_HTTPS_PORT} ${TAILSCALE_PATH_PREFIX} http://localhost:${DASHBOARD_PORT}" >&2
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
# Check whether Google credentials are available via env or secrets file.
# This runs in the *outer* shell before tmux windows are created so that
# developers see the warning immediately rather than only inside a pane.

_has_google_creds() {
  # Check Calendar-style JSON blob (legacy — deprecated)
  if [ -n "${BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON:-}" ]; then
    return 0
  fi

  # Check individual env vars (mirrors GoogleCredentials.from_env order)
  local client_id="" client_secret="" refresh_token=""
  client_id="${GOOGLE_OAUTH_CLIENT_ID:-${GMAIL_CLIENT_ID:-}}"
  client_secret="${GOOGLE_OAUTH_CLIENT_SECRET:-${GMAIL_CLIENT_SECRET:-}}"
  refresh_token="${GOOGLE_REFRESH_TOKEN:-${GMAIL_REFRESH_TOKEN:-}}"

  if [ -n "$client_id" ] && [ -n "$client_secret" ] && [ -n "$refresh_token" ]; then
    return 0
  fi

  # Check connector env file (sourced at connector startup)
  if [ -f "$GMAIL_CONNECTOR_ENV_FILE" ]; then
    if grep -Eq '^(GOOGLE_OAUTH_CLIENT_ID|GMAIL_CLIENT_ID)=.+' "$GMAIL_CONNECTOR_ENV_FILE" 2>/dev/null \
      && grep -Eq '^(GOOGLE_OAUTH_CLIENT_SECRET|GMAIL_CLIENT_SECRET)=.+' "$GMAIL_CONNECTOR_ENV_FILE" 2>/dev/null \
      && grep -Eq '^(GOOGLE_REFRESH_TOKEN|GMAIL_REFRESH_TOKEN)=.+' "$GMAIL_CONNECTOR_ENV_FILE" 2>/dev/null; then
      return 0
    fi
  fi

  # Check DB for stored credentials using psql (requires DB to be reachable).
  # This allows the OAuth dashboard flow to provide credentials without env vars.
  local db_host db_port db_user db_pass db_name
  db_host="${POSTGRES_HOST:-localhost}"
  db_port="${POSTGRES_PORT:-54320}"
  db_user="${POSTGRES_USER:-butlers}"
  db_pass="${POSTGRES_PASSWORD:-butlers}"
  db_name="${CONNECTOR_BUTLER_DB_NAME:-butlers}"
  if command -v psql >/dev/null 2>&1; then
    local db_count
    db_count=$(PGPASSWORD="$db_pass" psql -h "$db_host" -p "$db_port" -U "$db_user" -d "$db_name" -tAc       "SELECT COUNT(*) FROM google_oauth_credentials WHERE credential_key='google' AND (credentials->>'refresh_token') IS NOT NULL AND length(credentials->>'refresh_token') > 0;"       2>/dev/null || echo "0")
    if [ "${db_count:-0}" -gt 0 ] 2>/dev/null; then
      return 0
    fi
  fi

  return 1
}

# ── DB-based Google credential check ──────────────────────────────────────
# Poll the google_oauth_credentials table for a non-null refresh_token.
# Checks all known butler databases in order. Returns 0 on success.

_poll_db_for_refresh_token() {
  # Known butler databases in alphabetical order (matches roster discovery order).
  # The dashboard API stores OAuth credentials to the first registered butler's DB.
  local dbs=(
    "butler_general"
    "butler_health"
    "butler_messenger"
    "butler_relationship"
    "butler_switchboard"
  )

  local psql_bin
  psql_bin=$(command -v psql 2>/dev/null || echo "")

  if [ -z "$psql_bin" ]; then
    # psql not available — fall back to HTTP status endpoint on the dashboard
    _poll_oauth_via_http && return 0
    return 1
  fi

  for db in "${dbs[@]}"; do
    local result
    result=$(
      PGPASSWORD="${POSTGRES_PASSWORD:-butlers}" \
      psql \
        -h "${POSTGRES_HOST}" \
        -p "${POSTGRES_PORT}" \
        -U "${POSTGRES_USER}" \
        -d "$db" \
        -t -c \
        "SELECT credentials->>'refresh_token' FROM google_oauth_credentials
         WHERE credential_key = 'google'
           AND credentials->>'refresh_token' IS NOT NULL
           AND credentials->>'refresh_token' != ''
         LIMIT 1" \
        2>/dev/null || echo ""
    )
    # Trim whitespace; non-empty means a valid refresh token was found
    result="${result#"${result%%[![:space:]]*}"}"
    result="${result%"${result##*[![:space:]]}"}"
    if [ -n "$result" ]; then
      return 0
    fi
  done

  return 1
}

# HTTP fallback: use /api/oauth/status endpoint if psql is unavailable
_poll_oauth_via_http() {
  local status_url="http://localhost:${DASHBOARD_PORT}/api/oauth/status"
  local state
  state=$(curl -sf "$status_url" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('state',''))" 2>/dev/null || echo "")
  # OAuthCredentialState.OK is the value when credentials are present
  [ "$state" = "ok" ]
}

# ── Layer 2: OAuth gate ────────────────────────────────────────────────────
# Block the outer shell until Google OAuth credentials are available.
# Credentials are resolved from env vars first (fast path), then via DB polling.
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

  # Fast path: env vars already present
  if _has_google_creds; then
    echo "Layer 2: Google OAuth credentials found in environment."
    return 0
  fi

  # Slow path: credentials not in env — poll the DB
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
  echo "  Alternatively, set credentials in ${GMAIL_CONNECTOR_ENV_FILE}:"
  echo "    GOOGLE_OAUTH_CLIENT_ID=<your-client-id>"
  echo "    GOOGLE_OAUTH_CLIENT_SECRET=<your-client-secret>"
  echo "    GOOGLE_REFRESH_TOKEN=<your-refresh-token>"
  echo "  Then rerun: ./dev.sh"
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
  echo "  WARNING: Google OAuth credentials not found in environment"
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
  echo "  Option B — Environment variables in secrets file:"
  echo "    Add to ${GMAIL_CONNECTOR_ENV_FILE}:"
  echo "      GOOGLE_OAUTH_CLIENT_ID=<your-client-id>"
  echo "      GOOGLE_OAUTH_CLIENT_SECRET=<your-client-secret>"
  echo "      GOOGLE_REFRESH_TOKEN=<your-refresh-token>"
  echo "    Then rerun ./dev.sh"
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

_GMAIL_CMD_BASE="${ENV_LOADER} && if [ -f \"$GMAIL_CONNECTOR_ENV_FILE\" ]; then set -a && . \"$GMAIL_CONNECTOR_ENV_FILE\" && set +a; fi && mkdir -p .tmp/connectors"

if [ "$GOOGLE_CREDS_AVAILABLE" = "true" ] || [ "$SKIP_OAUTH_CHECK" = "true" ]; then
  # Credentials available — start connector normally (guard in connector will verify)
  GMAIL_PANE_CMD="${_GMAIL_CMD_BASE} && CONNECTOR_PROVIDER=gmail CONNECTOR_CHANNEL=email CONNECTOR_ENDPOINT_IDENTITY=\${GMAIL_CONNECTOR_ENDPOINT_IDENTITY:-gmail:user:dev} CONNECTOR_CURSOR_PATH=\${GMAIL_CONNECTOR_CURSOR_PATH:-.tmp/connectors/gmail_checkpoint.json} uv run python -m butlers.connectors.gmail"
else
  # Credentials missing — show instructions and keep pane alive
  GMAIL_PANE_CMD="echo '' && echo '======================================================================' && echo '  Gmail connector is waiting for Google OAuth credentials.' && echo '  Also required by: Calendar module.' && echo '======================================================================' && echo '' && echo '  To complete bootstrap:' && echo '    1. Open ${OAUTH_BROWSER_URL} in your browser' && echo '    2. Click Connect Google and complete the OAuth flow' && echo '    3. Once authorized, restart this pane (tmux: prefix+R or exit+up+Enter)' && echo '' && echo '  Or set credentials in: $GMAIL_CONNECTOR_ENV_FILE' && echo '    GOOGLE_OAUTH_CLIENT_ID=...' && echo '    GOOGLE_OAUTH_CLIENT_SECRET=...' && echo '    GOOGLE_REFRESH_TOKEN=...' && echo '' && echo '  Then rerun: ./dev.sh' && echo '' && echo '  (This pane will remain open — restart it after completing OAuth)' && bash"
fi

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
tmux pipe-pane -o -t "$PANE_DASHBOARD" "cat >> '${LOGS_RUN_DIR}/uvicorn/dashboard.log'"
tmux pipe-pane -o -t "$PANE_FRONTEND" "cat >> '${LOGS_RUN_DIR}/frontend/vite.log'"

tmux send-keys -t "$PANE_DASHBOARD" \
  "POSTGRES_PORT=${POSTGRES_PORT} BUTLERS_DISABLE_FILE_LOGGING=1 uv run butlers dashboard --host 0.0.0.0 --port ${DASHBOARD_PORT}" Enter
# Brief wait for shell init in the split pane
sleep 0.3
tmux send-keys -t "$PANE_FRONTEND" \
  "npm install && npm run dev -- --host 0.0.0.0" Enter

# ── Layer 1b: connectors window (telegram + frontend) ─────────────────────
# Telegram connectors and frontend start without waiting for OAuth.
# Gmail pane is created here but will block until Layer 3 is reached.
echo "Layer 1b: Starting Telegram connectors..."
PANE_TELEGRAM_BOT=$(tmux new-window -t "$SESSION:" -n connectors -c "$PROJECT_DIR" -P -F '#{pane_id}')
PANE_TELEGRAM_USER=$(tmux split-window -t "$PANE_TELEGRAM_BOT" -v -c "$PROJECT_DIR" -P -F '#{pane_id}')
PANE_GMAIL=$(tmux split-window -t "$PANE_TELEGRAM_BOT" -h -c "$PROJECT_DIR" -P -F '#{pane_id}')
tmux pipe-pane -o -t "$PANE_TELEGRAM_BOT" "cat >> '${LOGS_RUN_DIR}/connectors/telegram_bot.log'"
tmux pipe-pane -o -t "$PANE_TELEGRAM_USER" "cat >> '${LOGS_RUN_DIR}/connectors/telegram_user_client.log'"
tmux pipe-pane -o -t "$PANE_GMAIL" "cat >> '${LOGS_RUN_DIR}/connectors/gmail.log'"

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
tmux pipe-pane -o -t "$PANE_BACKEND" "cat >> '${LOGS_RUN_DIR}/butlers/up.log'"
tmux send-keys -t "$PANE_BACKEND" \
  "${ENV_LOADER} && uv sync --dev && POSTGRES_PORT=${POSTGRES_PORT} BUTLERS_DISABLE_FILE_LOGGING=1 uv run butlers up" Enter

# Start Gmail pane (credentials-aware)
tmux send-keys -t "$PANE_GMAIL" \
  "${GMAIL_PANE_CMD}" Enter

# Focus the backend window
tmux select-window -t "${SESSION}:backend"

# Attach if we started detached
if [ -z "${TMUX:-}" ]; then
  exec tmux attach-session -t "$SESSION"
fi
