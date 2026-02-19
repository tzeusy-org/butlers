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
#   Layer 2  — OAuth gate check (dashboard responsive)
#   Layer 3  — butlers up + Gmail connector start (OAuth gate passed)
#
# Usage: ./dev.sh [--skip-oauth-check] [--skip-tailscale-check]
#
# OAuth Bootstrap:
#   Before launching the Gmail connector, this script checks whether Google
#   OAuth credentials are present in the secrets file or environment. If
#   credentials are missing, a prominent warning is printed and the Gmail
#   connector pane shows clear instructions for completing the bootstrap via
#   the dashboard (http://localhost:8200 -> "Connect Google").
#
#   To suppress the check and start anyway:  ./dev.sh --skip-oauth-check
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
POSTGRES_PORT=54320

_tailscale_serve_check() {
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

  # Check if tailscale serve is already running and includes port 8200
  local serve_status
  serve_status=$(tailscale serve status 2>/dev/null) || serve_status=""
  if echo "$serve_status" | grep -q "localhost:${DASHBOARD_PORT}"; then
    echo "Tailscale serve: already running (forwarding to localhost:${DASHBOARD_PORT})"
    return 0
  fi

  # Not yet serving — attempt to start it
  echo "Tailscale serve: not active for port ${DASHBOARD_PORT}, starting..."
  if tailscale serve https:443 "http://localhost:${DASHBOARD_PORT}" 2>/dev/null; then
    # Verify it started correctly
    serve_status=$(tailscale serve status 2>/dev/null) || serve_status=""
    if echo "$serve_status" | grep -q "localhost:${DASHBOARD_PORT}"; then
      local ts_hostname
      ts_hostname=$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Self',{}).get('DNSName','<your-name>.ts.net').rstrip('.'))" 2>/dev/null || echo "<your-name>.ts.net")
      echo "Tailscale serve: started — https://${ts_hostname}/ → http://localhost:${DASHBOARD_PORT}"
      echo ""
      echo "  HTTPS callback URL: https://${ts_hostname}/api/oauth/google/callback"
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
  echo "  Could not start: tailscale serve https:443 http://localhost:${DASHBOARD_PORT}" >&2
  echo "" >&2
  echo "  To start manually:" >&2
  echo "    tailscale serve https:443 http://localhost:${DASHBOARD_PORT}" >&2
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
  # Check Calendar-style JSON blob first (matches startup_guard.py priority order)
  if [ -n "${BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON:-}" ]; then
    return 0
  fi

  # Check individual env vars next (mirrors GoogleCredentials.from_env order)
  local client_id="" client_secret="" refresh_token=""
  client_id="${GOOGLE_OAUTH_CLIENT_ID:-${GMAIL_CLIENT_ID:-}}"
  client_secret="${GOOGLE_OAUTH_CLIENT_SECRET:-${GMAIL_CLIENT_SECRET:-}}"
  refresh_token="${GOOGLE_REFRESH_TOKEN:-${GMAIL_REFRESH_TOKEN:-}}"

  if [ -n "$client_id" ] && [ -n "$client_secret" ] && [ -n "$refresh_token" ]; then
    return 0
  fi

  # Check connector env file (sourced at connector startup)
  if [ -f "$GMAIL_CONNECTOR_ENV_FILE" ]; then
    local file_has_id file_has_secret file_has_token
    file_has_id=$(grep -E '^(GOOGLE_OAUTH_CLIENT_ID|GMAIL_CLIENT_ID)=.+' "$GMAIL_CONNECTOR_ENV_FILE" 2>/dev/null | wc -l || echo 0)
    file_has_secret=$(grep -E '^(GOOGLE_OAUTH_CLIENT_SECRET|GMAIL_CLIENT_SECRET)=.+' "$GMAIL_CONNECTOR_ENV_FILE" 2>/dev/null | wc -l || echo 0)
    file_has_token=$(grep -E '^(GOOGLE_REFRESH_TOKEN|GMAIL_REFRESH_TOKEN)=.+' "$GMAIL_CONNECTOR_ENV_FILE" 2>/dev/null | wc -l || echo 0)
    if [ "$file_has_id" -gt 0 ] && [ "$file_has_secret" -gt 0 ] && [ "$file_has_token" -gt 0 ]; then
      return 0
    fi
  fi

  return 1
}

GOOGLE_CREDS_AVAILABLE=false
if _has_google_creds; then
  GOOGLE_CREDS_AVAILABLE=true
fi

if [ "$GOOGLE_CREDS_AVAILABLE" = "false" ] && [ "$SKIP_OAUTH_CHECK" = "false" ]; then
  echo ""
  echo "======================================================================"
  echo "  WARNING: Google OAuth credentials not found"
  echo "======================================================================"
  echo ""
  echo "  The Gmail connector requires Google OAuth credentials to start."
  echo "  These can be supplied in one of two ways:"
  echo ""
  echo "  Option A — Dashboard OAuth flow (recommended):"
  echo "    1. Start Butlers and visit http://localhost:8200"
  echo "    2. Click 'Connect Google' and complete the OAuth flow"
  echo "    3. The refresh token is stored in the DB automatically"
  echo "    4. Restart the Gmail connector pane (prefix+R in tmux)"
  echo ""
  echo "  Option B — Environment variables in secrets file:"
  echo "    Add to ${GMAIL_CONNECTOR_ENV_FILE}:"
  echo "      GOOGLE_OAUTH_CLIENT_ID=<your-client-id>"
  echo "      GOOGLE_OAUTH_CLIENT_SECRET=<your-client-secret>"
  echo "      GOOGLE_REFRESH_TOKEN=<your-refresh-token>"
  echo "    Then rerun ./dev.sh"
  echo ""
  echo "  The Gmail connector pane will show this guidance and wait."
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
  GMAIL_PANE_CMD="echo '' && echo '======================================================================' && echo '  Gmail connector is waiting for Google OAuth credentials.' && echo '======================================================================' && echo '' && echo '  To complete bootstrap:' && echo '    1. Open http://localhost:8200 in your browser' && echo '    2. Click Connect Google and complete the OAuth flow' && echo '    3. Once authorized, restart this pane (tmux: prefix+R or exit+up+Enter)' && echo '' && echo '  Or set credentials in: $GMAIL_CONNECTOR_ENV_FILE' && echo '    GOOGLE_OAUTH_CLIENT_ID=...' && echo '    GOOGLE_OAUTH_CLIENT_SECRET=...' && echo '    GOOGLE_REFRESH_TOKEN=...' && echo '' && echo '  Then rerun: ./dev.sh' && echo '' && echo '  (This pane will remain open — restart it after completing OAuth)' && bash"
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

# ── Layer 2: OAuth gate — wait for dashboard to be responsive ─────────────
# Block the outer shell until the dashboard API is reachable. Once it is,
# run the OAuth gate check. This ensures butlers up and Gmail only start
# after the dashboard is ready to serve OAuth redirects.

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
  echo "  Continuing startup — butlers up and Gmail will start without" >&2
  echo "  OAuth gate confirmation. Check the dashboard pane for errors." >&2
  echo "" >&2
  return 1
}

# Wait for dashboard; if it times out, continue rather than abort (non-fatal)
_wait_for_dashboard 60 || true

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
