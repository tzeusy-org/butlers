#!/usr/bin/env bash
# Bootstrap a full Butlers dev environment in tmux.
# Creates three windows:
#   backend     — postgres + butlers up
#   connectors  — telegram bot connector (top-left) + gmail connector (top-right) + telegram user-client connector (bottom)
#   dashboard   — dashboard API (top) + Vite frontend (bottom)
#
# Usage: ./dev.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

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

# Kill existing windows if present (idempotent re-runs)
for WIN in backend connectors dashboard; do
  tmux kill-window -t "${SESSION}:${WIN}" 2>/dev/null || true
done

# ── backend window ──────────────────────────────────────────────────
PANE_BACKEND=$(tmux new-window -t "$SESSION:" -n backend -c "$PROJECT_DIR" -P -F '#{pane_id}')
tmux send-keys -t "$PANE_BACKEND" \
  "${ENV_LOADER} && uv sync --dev && docker compose stop postgres && docker compose up -d postgres && POSTGRES_PORT=54320 uv run butlers up" Enter

# ── connectors window ──────────────────────────────────────────────
PANE_TELEGRAM_BOT=$(tmux new-window -t "$SESSION:" -n connectors -c "$PROJECT_DIR" -P -F '#{pane_id}')
PANE_TELEGRAM_USER=$(tmux split-window -t "$PANE_TELEGRAM_BOT" -v -c "$PROJECT_DIR" -P -F '#{pane_id}')
PANE_GMAIL=$(tmux split-window -t "$PANE_TELEGRAM_BOT" -h -c "$PROJECT_DIR" -P -F '#{pane_id}')

tmux send-keys -t "$PANE_TELEGRAM_BOT" \
  "${ENV_LOADER} && if [ -f \"$TELEGRAM_BOT_CONNECTOR_ENV_FILE\" ]; then set -a && . \"$TELEGRAM_BOT_CONNECTOR_ENV_FILE\" && set +a; fi && mkdir -p .tmp/connectors && sleep 10 && CONNECTOR_PROVIDER=telegram CONNECTOR_CHANNEL=telegram CONNECTOR_ENDPOINT_IDENTITY=\${TELEGRAM_BOT_CONNECTOR_ENDPOINT_IDENTITY:-\${CONNECTOR_ENDPOINT_IDENTITY:-telegram:bot:dev}} CONNECTOR_CURSOR_PATH=\${TELEGRAM_BOT_CONNECTOR_CURSOR_PATH:-\${CONNECTOR_CURSOR_PATH:-.tmp/connectors/telegram_bot_checkpoint.json}} uv run python -m butlers.connectors.telegram_bot" Enter

tmux send-keys -t "$PANE_TELEGRAM_USER" \
  "${ENV_LOADER} && if [ -f \"$TELEGRAM_USER_CONNECTOR_ENV_FILE\" ]; then set -a && . \"$TELEGRAM_USER_CONNECTOR_ENV_FILE\" && set +a; fi && mkdir -p .tmp/connectors && sleep 10 && CONNECTOR_PROVIDER=telegram CONNECTOR_CHANNEL=telegram CONNECTOR_ENDPOINT_IDENTITY=\${TELEGRAM_USER_CONNECTOR_ENDPOINT_IDENTITY:-telegram:user:dev} CONNECTOR_CURSOR_PATH=\${TELEGRAM_USER_CONNECTOR_CURSOR_PATH:-.tmp/connectors/telegram_user_client_checkpoint.json} uv run python -m butlers.connectors.telegram_user_client" Enter

tmux send-keys -t "$PANE_GMAIL" \
  "${ENV_LOADER} && if [ -f \"$GMAIL_CONNECTOR_ENV_FILE\" ]; then set -a && . \"$GMAIL_CONNECTOR_ENV_FILE\" && set +a; fi && mkdir -p .tmp/connectors && sleep 10 && CONNECTOR_PROVIDER=gmail CONNECTOR_CHANNEL=email CONNECTOR_ENDPOINT_IDENTITY=\${GMAIL_CONNECTOR_ENDPOINT_IDENTITY:-gmail:user:dev} CONNECTOR_CURSOR_PATH=\${GMAIL_CONNECTOR_CURSOR_PATH:-.tmp/connectors/gmail_checkpoint.json} uv run python -m butlers.connectors.gmail" Enter

# ── dashboard window ───────────────────────────────────────────────
PANE_DASHBOARD=$(tmux new-window -t "$SESSION:" -n dashboard -c "$PROJECT_DIR" -P -F '#{pane_id}')
PANE_FRONTEND=$(tmux split-window -t "$PANE_DASHBOARD" -v -c "${PROJECT_DIR}/frontend" -P -F '#{pane_id}')

tmux send-keys -t "$PANE_DASHBOARD" \
  "POSTGRES_PORT=54320 uv run butlers dashboard --host 0.0.0.0 --port 8200" Enter
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
