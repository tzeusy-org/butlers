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

The dev environment runs as Docker Compose containers with the `butlers-dev-` prefix. Access logs directly via `docker logs <container-name>`.

| Container | Port (host) | Description |
|-----------|-------------|-------------|
| `butlers-dev-butlers-up-1` | 42100→41100 | All butler daemons (switchboard, general, etc.) |
| `butlers-dev-dashboard-api-1` | 42200→41200 | FastAPI dashboard backend |
| `butlers-dev-connector-telegram-bot-1` | — | Telegram bot connector |
| `butlers-dev-connector-telegram-user-1` | — | Telegram userbot connector |
| `butlers-dev-connector-gmail-1` | — | Gmail connector |
| `butlers-dev-connector-google-calendar-1` | — | Google Calendar connector |
| `butlers-dev-connector-google-drive-1` | — | Google Drive connector |
| `butlers-dev-connector-spotify-1` | — | Spotify connector |
| `butlers-dev-connector-whatsapp-user-1` | — | WhatsApp connector |
| `butlers-dev-connector-owntracks-1` | — | OwnTracks connector |
| `butlers-dev-connector-home-assistant-1` | — | Home Assistant connector |
| `butlers-dev-connector-live-listener-1` | — | Voice connector (profile: audio) |

---

## Investigating a Session by ID

### Step 1: Find the session in Docker container logs

All butler daemon output goes to the `butlers-dev-butlers-up-1` container. Search for a session UUID:

```bash
docker logs butlers-dev-butlers-up-1 2>&1 | grep "<session-id>"
```

For connector-related issues, check the specific connector container:

```bash
docker logs butlers-dev-connector-gmail-1 2>&1 | grep "<session-id>"
docker logs butlers-dev-connector-telegram-bot-1 2>&1 | grep "<session-id>"
```

To follow logs live while reproducing an issue:

```bash
docker logs -f butlers-dev-butlers-up-1
docker logs -f --since 5m butlers-dev-butlers-up-1    # last 5 minutes, then follow
```

### Step 2: Query session from PostgreSQL

Sessions are stored in each butler's schema in the `sessions` table. Postgres is exposed on host port 54320 (mapped from container port 5432):

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

## Docker Container Logs

### Accessing logs

All service output is accessible via `docker logs <container-name>`. Use the full container name from `docker ps`.

```bash
# Butler daemon logs
docker logs butlers-dev-butlers-up-1
docker logs butlers-dev-butlers-up-1 --since 5m
docker logs butlers-dev-butlers-up-1 --since 5m --tail 200

# Connector logs (use exact container name)
docker logs butlers-dev-connector-gmail-1
docker logs butlers-dev-connector-whatsapp-user-1
docker logs butlers-dev-connector-telegram-bot-1

# Follow live
docker logs -f butlers-dev-butlers-up-1
docker logs -f butlers-dev-connector-whatsapp-user-1

# Filter for errors in a specific container
docker logs butlers-dev-butlers-up-1 --since 10m 2>&1 | grep -iE 'error|traceback|failed|exception'

# Search across multiple containers
for c in $(docker ps --format '{{.Names}}' | grep butlers-dev); do
  echo "=== $c ===" && docker logs "$c" --since 10m 2>&1 | grep -iE 'error|traceback|failed|exception'
done
```

### Listing running containers

```bash
# All butlers-dev containers with status
docker ps --filter name=butlers-dev --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# Just container names (for scripting)
docker ps --filter name=butlers-dev --format '{{.Names}}'
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

## Docker Container Management

### Checking service health

```bash
# All butlers-dev containers with status
docker ps --filter name=butlers-dev --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# Health check for butlers-up (host port 42100)
curl -sf http://localhost:42100/health | python3 -m json.tool

# Health check for dashboard-api (host port 42200)
curl -sf http://localhost:42200/health | python3 -m json.tool

# Check which containers are restarting (unhealthy)
docker ps --filter name=butlers-dev --filter status=restarting --format '{{.Names}}\t{{.Status}}'
```

### Restarting containers

```bash
# Restart a single container
docker restart butlers-dev-butlers-up-1
docker restart butlers-dev-connector-gmail-1

# Stop and remove a single container (will be recreated by compose)
docker stop butlers-dev-connector-gmail-1
```

### Exec into a running container

```bash
# Shell into butlers-up for live debugging
docker exec -it butlers-dev-butlers-up-1 bash

# Run a one-off psql query from inside the network
docker exec -it butlers-dev-postgres-1 psql -U butlers -d butlers

# Check connector environment
docker exec butlers-dev-connector-gmail-1 env | sort
```

---

## Quick Debug Checklist

1. **Get session ID** — from user report, dashboard, or logs
2. **Identify butler** — which butler schema to query
3. **Check container logs** — `docker logs butlers-dev-butlers-up-1 --since 10m 2>&1 | grep "<session-id>"`
4. **Query session** — get prompt, result, tool_calls, error from PostgreSQL (host port 54320)
5. **Check tool calls** — look for `status: error` in tool_calls JSONB
6. **Check connector logs** — `docker logs butlers-dev-connector-<name>-1 --since 10m`
7. **Check trace** — use trace_id to follow request across containers
8. **Check dashboard API** — `curl http://localhost:42200/api/butlers/<name>/sessions/<id>`
9. **Check container health** — `docker ps --filter name=butlers-dev` for crashed/restarting containers
