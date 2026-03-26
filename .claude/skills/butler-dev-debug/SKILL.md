---
name: butler-dev-debug
description: >
  Investigate butler session errors and debug runtime issues using session IDs,
  database queries, and structured log analysis. Use when debugging a specific
  session failure, tracing a request through the system, or investigating butler
  runtime errors.
---

# Butler Dev Debug

Guide for investigating butler errors, failed sessions, and runtime issues in the Butlers Docker Compose development environment.

## When to Use

- Given a session UUID to investigate
- Debugging a failed routing, delivery, or tool call
- Tracing a request through switchboard → target butler
- Investigating runtime errors from logs

---

## Environment Overview

The dev environment runs via `./scripts/dev-compose.sh` using Docker Compose. Key services:

| Service | Container | Port (host) | Description |
|---------|-----------|-------------|-------------|
| `butlers-up` | `rig-butlers-up-1` | 41100 | All butler daemons (switchboard, general, etc.) |
| `dashboard-api` | `rig-dashboard-api-1` | 41200 | FastAPI dashboard backend |
| `frontend-dev` | `rig-frontend-dev-1` | 41173 | Vite dev server (profile: dev) |
| `postgres` | `rig-postgres-1` | 54320 | PostgreSQL (pgvector/pg17) |
| `connector-telegram-bot` | `rig-connector-telegram-bot-1` | — | Telegram bot connector |
| `connector-telegram-user` | `rig-connector-telegram-user-1` | — | Telegram userbot connector |
| `connector-gmail` | `rig-connector-gmail-1` | — | Gmail connector |
| `connector-google-calendar` | `rig-connector-google-calendar-1` | — | Google Calendar connector |
| `connector-spotify` | `rig-connector-spotify-1` | — | Spotify connector |
| `connector-whatsapp-user` | `rig-connector-whatsapp-user-1` | — | WhatsApp connector |
| `connector-owntracks` | `rig-connector-owntracks-1` | — | OwnTracks connector |
| `connector-live-listener` | `rig-connector-live-listener-1` | — | Voice connector (profile: audio) |

---

## Investigating a Session by ID

### Step 1: Find the session in Docker logs

All butler daemon output goes to the `butlers-up` container. Search for a session UUID:

```bash
docker compose logs butlers-up 2>&1 | grep "<session-id>"
```

For connector-related issues, check the specific connector:

```bash
docker compose logs connector-gmail 2>&1 | grep "<session-id>"
docker compose logs connector-telegram-bot 2>&1 | grep "<session-id>"
```

To follow logs live while reproducing an issue:

```bash
docker compose logs -f butlers-up
docker compose logs -f --since 5m butlers-up    # last 5 minutes, then follow
```

### Step 2: Query session from PostgreSQL

Sessions are stored in each butler's schema in the `sessions` table. Postgres is exposed on host port 54320:

```bash
psql -h localhost -p 54320 -U butlers -d butlers -c "
SET search_path TO <butler-schema>;
SELECT id, trigger_source, model, success, error,
       left(result, 500) as result_preview,
       duration_ms, input_tokens, output_tokens,
       started_at, completed_at
FROM sessions WHERE id = '<session-id>';
"
```

### Step 3: Get the full prompt and result

```bash
# Full prompt (can be very large — pipe to less)
psql -h localhost -p 54320 -U butlers -d butlers -t -A -c "
SET search_path TO <butler-schema>;
SELECT prompt FROM sessions WHERE id = '<session-id>';
"

# Full result text
psql -h localhost -p 54320 -U butlers -d butlers -t -A -c "
SET search_path TO <butler-schema>;
SELECT result FROM sessions WHERE id = '<session-id>';
"
```

### Step 4: Inspect tool calls

Tool calls are stored as JSONB. Extract them as formatted JSON:

```bash
psql -h localhost -p 54320 -U butlers -d butlers -t -A -c "
SET search_path TO <butler-schema>;
SELECT tool_calls FROM sessions WHERE id = '<session-id>';
" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
for t in data:
    if t.get('name'):
        print(json.dumps({
            'name': t['name'],
            'input_keys': list(t.get('input', {}).keys()),
            'result': t.get('result', {}),
            'outcome': t.get('outcome', 'unknown'),
        }, indent=2))
"
```

### Step 5: Check process logs (if available)

Process logs capture stderr, exit code, and PID from the runtime adapter:

```bash
psql -h localhost -p 54320 -U butlers -d butlers -c "
SET search_path TO <butler-schema>;
SELECT session_id, pid, exit_code, runtime_type, left(stderr, 1000) as stderr_preview
FROM session_process_logs WHERE session_id = '<session-id>';
"
```

### Step 6: Query via Dashboard API

The dashboard API is on host port 41200:

```bash
# Single session detail (includes process_log if available)
curl -s http://localhost:41200/api/butlers/<butler-name>/sessions/<session-id> | python3 -m json.tool

# Recent sessions for a butler
curl -s "http://localhost:41200/api/butlers/<butler-name>/sessions?limit=10" | python3 -m json.tool
```

---

## Sessions Table Schema

Key columns in the `sessions` table:

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Session ID (PK, auto-generated) |
| `prompt` | TEXT | Full prompt sent to the LLM |
| `trigger_source` | TEXT | What triggered the session (tick, api, etc.) |
| `model` | TEXT | LLM model used |
| `success` | BOOLEAN | Whether session completed without error |
| `error` | TEXT | Error message if failed |
| `result` | TEXT | LLM response text |
| `tool_calls` | JSONB | Array of tool call records |
| `duration_ms` | INTEGER | Wall-clock duration |
| `input_tokens` | INTEGER | Prompt tokens consumed |
| `output_tokens` | INTEGER | Response tokens generated |
| `trace_id` | TEXT | OpenTelemetry trace ID |
| `request_id` | TEXT | Idempotency/correlation ID |
| `complexity` | TEXT | Resolved complexity tier |
| `resolution_source` | TEXT | How complexity was resolved |
| `started_at` | TIMESTAMPTZ | Session start time |
| `completed_at` | TIMESTAMPTZ | Session end time |

---

## Common Error Patterns

### `Missing input.context.notify_request in messenger route.execute request`

**Cause:** Something called `route_to_butler(butler="messenger", ...)` instead of
using the `notify()` tool. The `route_to_butler` tool passes `context` as a plain
string, but messenger's `route.execute` handler requires a structured `notify_request`
envelope in `input.context`.

**Fix:** Use the `notify` MCP tool for outbound email/telegram delivery. Never route
to `butler="messenger"` via `route_to_butler`. The message-triage skill documents
this in its execution contract.

### Session shows `success=True` but tool call has `status: error`

Sessions can succeed (the LLM completed) while individual tool calls fail. Check
the `tool_calls` JSONB for `result.status == "error"` entries.

### `Unsupported channel` or `No butler with module found`

The `deliver()` function checks `butler_registry` for butlers with the required
module. Verify the target butler is registered and has the correct module in its
`butler.toml`.

---

## Docker Compose Log Structure

### Accessing logs

All service output goes to Docker's logging driver and is accessible via `docker compose logs`:

```bash
# All services
docker compose --profile dev logs --since 10m

# Specific service
docker compose logs butlers-up --since 5m
docker compose logs dashboard-api --since 5m

# Follow live
docker compose logs -f butlers-up
docker compose logs -f connector-gmail connector-telegram-bot   # multiple services

# Filter for errors across all services
docker compose --profile dev logs --since 10m 2>&1 | grep -iE 'error|traceback|failed|exception'
```

### File-based logs (bind-mounted `./logs/`)

Services also write to file-based logs via `scripts/dev_entrypoint.sh`, which tees output to `./logs/<run_dir>/<service>/output.log`. The `logs/latest` symlink points to the current run.

```
logs/latest/                          # symlink → timestamped run dir
├── butlers/up/output.log             # butlers-up daemon (all butlers combined)
├── connectors/
│   ├── telegram_bot/output.log
│   ├── telegram_user/output.log
│   ├── gmail/output.log
│   ├── google_calendar/output.log
│   ├── spotify/output.log
│   ├── whatsapp_user/output.log
│   └── owntracks/output.log
```

### Searching file-based logs

```bash
# Find all sessions for a butler today
grep '"Session created"' logs/latest/butlers/up/output.log | grep "$(date +%Y-%m-%d)"

# Find errors
grep '"level": "error"' logs/latest/butlers/up/output.log | tail -20

# Find by trace_id (correlate across services)
grep '<trace-id>' logs/latest/butlers/up/output.log logs/latest/connectors/*/output.log

# Find route.execute calls
grep 'route.execute' logs/latest/butlers/up/output.log
```

### Log format

Butler daemon logs use structlog (one JSON object per line):
```json
{
  "event": "Session created: <uuid> (trigger=tick, model=gpt-5.1)",
  "level": "info",
  "logger": "butlers.core.sessions",
  "timestamp": "2026-03-12T03:34:22.332551Z",
  "butler": "switchboard",
  "trace_id": "741e2384c336525f02ad250d052c5275",
  "span_id": "83080cab60a3e188"
}
```

---

## Docker Compose Service Management

### Checking service health

```bash
# Service status (running, healthy, exited)
docker compose --profile dev ps

# Health check for butlers-up
curl -sf http://localhost:41100/health | python3 -m json.tool

# Health check for dashboard-api
curl -sf http://localhost:41200/health | python3 -m json.tool
```

### Restarting services

```bash
# Restart a single service (preserves other services)
docker compose --profile dev restart butlers-up
docker compose --profile dev restart frontend-dev
docker compose --profile dev restart connector-gmail

# Rebuild and restart (after code changes, without hotreload profile)
docker compose --profile dev up --build -d butlers-up

# Full stack restart
docker compose --profile dev down && ./scripts/dev-compose.sh
```

### Exec into a running container

```bash
# Shell into butlers-up for live debugging
docker compose exec butlers-up bash

# Run a one-off psql query from inside the network
docker compose exec postgres psql -U butlers -d butlers

# Check connector environment
docker compose exec connector-gmail env | sort
```

---

## Quick Debug Checklist

1. **Get session ID** — from user report, dashboard, or logs
2. **Identify butler** — which butler schema to query
3. **Check docker logs** — `docker compose logs butlers-up --since 10m 2>&1 | grep "<session-id>"`
4. **Query session** — get prompt, result, tool_calls, error from PostgreSQL (host port 54320)
5. **Check tool calls** — look for `status: error` in tool_calls JSONB
6. **Check file logs** — `grep "<session-id>" logs/latest/butlers/up/output.log`
7. **Check trace** — use trace_id to follow request across services
8. **Check dashboard API** — `curl http://localhost:41200/api/butlers/<name>/sessions/<id>`
9. **Check service health** — `docker compose --profile dev ps` for crashed/restarting containers
