#!/usr/bin/env bash
set -euo pipefail

DEFAULT_POSTGRES_PORT="${POSTGRES_PORT:-54320}"
DEFAULT_FRONTEND_PORT="${FRONTEND_PORT:-40173}"
DEFAULT_DASHBOARD_PORT="${DASHBOARD_PORT:-40200}"
WAIT_SECONDS="${CLEAR_PROCESSES_WAIT_SECONDS:-5}"

if ! [[ "$WAIT_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "Error: CLEAR_PROCESSES_WAIT_SECONDS must be an integer" >&2
  exit 1
fi

if ! command -v lsof >/dev/null 2>&1 && ! command -v ss >/dev/null 2>&1; then
  echo "Error: neither lsof nor ss is available on PATH" >&2
  exit 1
fi

_collect_ports() {
  if [ -n "${EXPECTED_PORTS:-}" ]; then
    printf '%s\n' "$EXPECTED_PORTS" | tr ', ' '\n\n' | awk 'NF {print $0}' | awk '!seen[$0]++'
    return 0
  fi

  printf '%s\n' "$DEFAULT_POSTGRES_PORT" "$DEFAULT_FRONTEND_PORT" "$DEFAULT_DASHBOARD_PORT" | awk '!seen[$0]++'
}

_validate_port() {
  local port="$1"
  [[ "$port" =~ ^[0-9]+$ ]] && [ "$port" -ge 1 ] && [ "$port" -le 65535 ]
}

_listeners_for_port() {
  local port="$1"

  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | awk '!seen[$0]++' || true
    return 0
  fi

  if command -v ss >/dev/null 2>&1; then
    ss -ltnp "sport = :$port" 2>/dev/null | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | awk '!seen[$0]++' || true
    return 0
  fi

  return 0
}

declare -a ports=()
while IFS= read -r port; do
  if ! _validate_port "$port"; then
    echo "Error: invalid port '$port'" >&2
    exit 1
  fi
  ports+=("$port")
done < <(_collect_ports)

if [ "${#ports[@]}" -eq 0 ]; then
  echo "No expected ports configured. Nothing to clear."
  exit 0
fi

declare -A pid_to_ports=()
declare -a pids_to_kill=()

for port in "${ports[@]}"; do
  mapfile -t listeners < <(_listeners_for_port "$port")
  if [ "${#listeners[@]}" -eq 0 ]; then
    echo "Port $port: free"
    continue
  fi

  for pid in "${listeners[@]}"; do
    [ -n "$pid" ] || continue
    if [[ -n "${pid_to_ports[$pid]:-}" ]]; then
      pid_to_ports[$pid]="${pid_to_ports[$pid]},$port"
    else
      pid_to_ports[$pid]="$port"
      pids_to_kill+=("$pid")
    fi
  done
done

if [ "${#pids_to_kill[@]}" -eq 0 ]; then
  echo "No listeners found on expected ports."
  exit 0
fi

echo "Found listeners on expected ports:"
for pid in "${pids_to_kill[@]}"; do
  cmd="$(ps -o args= -p "$pid" 2>/dev/null || echo "<unknown>")"
  echo "  pid=$pid ports=${pid_to_ports[$pid]} cmd=$cmd"
done

echo "Sending SIGTERM..."
kill "${pids_to_kill[@]}" 2>/dev/null || true

if [ "$WAIT_SECONDS" -gt 0 ]; then
  for _ in $(seq 1 "$WAIT_SECONDS"); do
    sleep 1
    declare -a still_alive=()
    for pid in "${pids_to_kill[@]}"; do
      if kill -0 "$pid" 2>/dev/null; then
        still_alive+=("$pid")
      fi
    done
    if [ "${#still_alive[@]}" -eq 0 ]; then
      echo "All processes exited after SIGTERM."
      exit 0
    fi
  done
fi

declare -a still_alive=()
for pid in "${pids_to_kill[@]}"; do
  if kill -0 "$pid" 2>/dev/null; then
    still_alive+=("$pid")
  fi
done

if [ "${#still_alive[@]}" -eq 0 ]; then
  echo "All processes exited after SIGTERM."
  exit 0
fi

echo "Sending SIGKILL to remaining PIDs: ${still_alive[*]}"
kill -9 "${still_alive[@]}" 2>/dev/null || true

sleep 1

declare -a stubborn=()
for pid in "${still_alive[@]}"; do
  if kill -0 "$pid" 2>/dev/null; then
    stubborn+=("$pid")
  fi
done

if [ "${#stubborn[@]}" -gt 0 ]; then
  echo "Warning: some PIDs are still alive: ${stubborn[*]}" >&2
  exit 1
fi

echo "Done."
