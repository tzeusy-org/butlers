#!/usr/bin/env bash
# Bootstrap a full Butlers dev environment in tmux.
# Creates three windows:
#   backend     — postgres + butlers up
#   connectors  — telegram bot connector (top-left) + gmail connector (top-right) + telegram user-client connector (bottom)
#   dashboard   — dashboard API (top) + Vite frontend (bottom)
#
# Usage: ./dev.sh [--skip-oauth-check]
#
# OAuth Bootstrap:
#   Before launching the Gmail connector, this script checks whether Google
#   OAuth credentials are present in the secrets file or environment. If
#   credentials are missing, a prominent warning is printed and the Gmail
#   connector pane shows clear instructions for completing the bootstrap via
#   the dashboard (http://localhost:8200 -> "Connect Google").
#
#   To suppress the check and start anyway:  ./dev.sh --skip-oauth-check

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

SKIP_OAUTH_CHECK=false
for arg in "$@"; do
  case "$arg" in
    --skip-oauth-check) SKIP_OAUTH_CHECK=true ;;
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

_GMAIL_CMD_BASE="${ENV_LOADER} && if [ -f \"$GMAIL_CONNECTOR_ENV_FILE\" ]; then set -a && . \"$GMAIL_CONNECTOR_ENV_FILE\" && set +a; fi && mkdir -p .tmp/connectors && sleep 10"

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

# ── backend window ──────────────────────────────────────────────────
PANE_BACKEND=$(tmux new-window -t "$SESSION:" -n backend -c "$PROJECT_DIR" -P -F '#{pane_id}')
tmux pipe-pane -o -t "$PANE_BACKEND" "cat >> '${LOGS_RUN_DIR}/butlers/up.log'"
tmux send-keys -t "$PANE_BACKEND" \
  "${ENV_LOADER} && uv sync --dev && docker compose stop postgres && docker compose up -d postgres && POSTGRES_PORT=54320 BUTLERS_DISABLE_FILE_LOGGING=1 uv run butlers up" Enter

# ── connectors window ──────────────────────────────────────────────
PANE_TELEGRAM_BOT=$(tmux new-window -t "$SESSION:" -n connectors -c "$PROJECT_DIR" -P -F '#{pane_id}')
PANE_TELEGRAM_USER=$(tmux split-window -t "$PANE_TELEGRAM_BOT" -v -c "$PROJECT_DIR" -P -F '#{pane_id}')
PANE_GMAIL=$(tmux split-window -t "$PANE_TELEGRAM_BOT" -h -c "$PROJECT_DIR" -P -F '#{pane_id}')
tmux pipe-pane -o -t "$PANE_TELEGRAM_BOT" "cat >> '${LOGS_RUN_DIR}/connectors/telegram_bot.log'"
tmux pipe-pane -o -t "$PANE_TELEGRAM_USER" "cat >> '${LOGS_RUN_DIR}/connectors/telegram_user_client.log'"
tmux pipe-pane -o -t "$PANE_GMAIL" "cat >> '${LOGS_RUN_DIR}/connectors/gmail.log'"

tmux send-keys -t "$PANE_TELEGRAM_BOT" \
  "${ENV_LOADER} && if [ -f \"$TELEGRAM_BOT_CONNECTOR_ENV_FILE\" ]; then set -a && . \"$TELEGRAM_BOT_CONNECTOR_ENV_FILE\" && set +a; fi && mkdir -p .tmp/connectors && sleep 10 && CONNECTOR_PROVIDER=telegram CONNECTOR_CHANNEL=telegram CONNECTOR_ENDPOINT_IDENTITY=\${TELEGRAM_BOT_CONNECTOR_ENDPOINT_IDENTITY:-\${CONNECTOR_ENDPOINT_IDENTITY:-telegram:bot:dev}} CONNECTOR_CURSOR_PATH=\${TELEGRAM_BOT_CONNECTOR_CURSOR_PATH:-\${CONNECTOR_CURSOR_PATH:-.tmp/connectors/telegram_bot_checkpoint.json}} uv run python -m butlers.connectors.telegram_bot" Enter

tmux send-keys -t "$PANE_TELEGRAM_USER" \
  "${ENV_LOADER} && if [ -f \"$TELEGRAM_USER_CONNECTOR_ENV_FILE\" ]; then set -a && . \"$TELEGRAM_USER_CONNECTOR_ENV_FILE\" && set +a; fi && mkdir -p .tmp/connectors && sleep 10 && CONNECTOR_PROVIDER=telegram CONNECTOR_CHANNEL=telegram CONNECTOR_ENDPOINT_IDENTITY=\${TELEGRAM_USER_CONNECTOR_ENDPOINT_IDENTITY:-telegram:user:dev} CONNECTOR_CURSOR_PATH=\${TELEGRAM_USER_CONNECTOR_CURSOR_PATH:-.tmp/connectors/telegram_user_client_checkpoint.json} uv run python -m butlers.connectors.telegram_user_client" Enter

tmux send-keys -t "$PANE_GMAIL" \
  "${GMAIL_PANE_CMD}" Enter

# ── dashboard window ───────────────────────────────────────────────
PANE_DASHBOARD=$(tmux new-window -t "$SESSION:" -n dashboard -c "$PROJECT_DIR" -P -F '#{pane_id}')
PANE_FRONTEND=$(tmux split-window -t "$PANE_DASHBOARD" -v -c "${PROJECT_DIR}/frontend" -P -F '#{pane_id}')
tmux pipe-pane -o -t "$PANE_DASHBOARD" "cat >> '${LOGS_RUN_DIR}/uvicorn/dashboard.log'"
tmux pipe-pane -o -t "$PANE_FRONTEND" "cat >> '${LOGS_RUN_DIR}/frontend/vite.log'"

tmux send-keys -t "$PANE_DASHBOARD" \
  "POSTGRES_PORT=54320 BUTLERS_DISABLE_FILE_LOGGING=1 uv run butlers dashboard --host 0.0.0.0 --port 8200" Enter
# Brief wait for shell init in the split pane
sleep 0.3
tmux send-keys -t "$PANE_FRONTEND" \
  "npm install && npm run dev -- --host 0.0.0.0" Enter

# Focus the backend window
tmux select-window -t "${SESSION}:backend"

# Attach if we started detached
if [ -z "${TMUX:-}" ]; then
  exec tmux attach-session -t "$SESSION"
fi
