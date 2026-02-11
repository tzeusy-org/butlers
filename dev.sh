#!/usr/bin/env bash
# Bootstrap a full Butlers dev environment in a tmux window.
# Creates a "butlers-dev" window with 3 panes:
#   Left:         Backend API  — postgres + butlers up
#   Top-right:    Dashboard    — butlers dashboard
#   Bottom-right: Frontend     — Vite dev server
#
# Usage: ./dev.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
WINDOW="butlers-dev"

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

# Kill existing window if present (idempotent re-runs)
tmux kill-window -t "${SESSION}:${WINDOW}" 2>/dev/null || true

# Create window and capture pane IDs — avoids index assumptions
PANE_BACKEND=$(tmux new-window -t "$SESSION" -n "$WINDOW" -c "$PROJECT_DIR" -P -F '#{pane_id}')
PANE_DASHBOARD=$(tmux split-window -t "$PANE_BACKEND" -h -c "$PROJECT_DIR" -P -F '#{pane_id}')
PANE_FRONTEND=$(tmux split-window -t "$PANE_DASHBOARD" -v -c "${PROJECT_DIR}/frontend" -P -F '#{pane_id}')

# Backend: sync deps, start postgres, run butlers
tmux send-keys -t "$PANE_BACKEND" \
  "export $(grep -v '^#' /secrets/.dev.env | xargs -d '\n') && export $(grep -v '^#' .env | xargs -d '\n') && uv sync --dev && docker compose stop postgres && docker compose up -d postgres && POSTGRES_PORT=54320 uv run butlers up" Enter

# Dashboard API
tmux send-keys -t "$PANE_DASHBOARD" \
  "POSTGRES_PORT=54320 uv run butlers dashboard --host 0.0.0.0 --port 8200" Enter

# Frontend: install + vite dev server
tmux send-keys -t "$PANE_FRONTEND" \
  "npm install && npm run dev -- --host 0.0.0.0" Enter

# Even out the layout and focus backend
tmux select-layout -t "${SESSION}:${WINDOW}" main-vertical
tmux select-pane -t "$PANE_BACKEND"

# Attach if we started detached
if [ -z "${TMUX:-}" ]; then
  exec tmux attach-session -t "$SESSION"
fi
