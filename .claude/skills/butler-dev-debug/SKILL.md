---
name: butler-dev-debug
description: >
  Investigate butler session errors and debug runtime issues using session IDs,
  database queries, and structured log analysis. Use when debugging a specific
  session failure, tracing a request through the system, or investigating butler
  runtime errors.
---

# Butler Dev Debug

Guide for investigating butler errors, failed sessions, and runtime issues in the Butlers development environment.

## When to Use

- Given a session UUID to investigate
- Debugging a failed routing, delivery, or tool call
- Tracing a request through switchboard → target butler
- Investigating runtime errors from logs

---

## Investigating a Session by ID

### Step 1: Find the session in logs

Search structured logs for the session UUID:

```bash
grep "<session-id>" logs/latest/butlers/up.log
```

`up.log` contains all butler daemon output and logs session creation/completion:
```
Session created: <id> (trigger=tick, model=gpt-5.1)
Session completed: <id> (70094 ms, success=True, in=133240, out=5267)
```

Also check per-butler logs in `logs/butlers/`:
```bash
grep "<session-id>" logs/butlers/switchboard.log
grep "<session-id>" logs/butlers/<butler-name>.log
```

### Step 2: Query session from PostgreSQL

Sessions are stored in each butler's schema in the `sessions` table:

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

If the dashboard is running (port 40200):

```bash
# Single session detail (includes process_log if available)
curl -s http://localhost:40200/api/butlers/<butler-name>/sessions/<session-id> | python3 -m json.tool

# Recent sessions for a butler
curl -s "http://localhost:40200/api/butlers/<butler-name>/sessions?limit=10" | python3 -m json.tool
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

## Dev Logs Structure (`logs/latest`)

`logs/latest` is a symlink to the most recent log run directory (e.g., `logs/20260312_110644`).

```
logs/latest/
├── butlers/
│   └── up.log              # Main daemon output — ALL butlers combined
│                             # Contains: session lifecycle, tool calls, errors,
│                             # module init, scheduler events
│                             # Format: structlog (key=value pairs)
│                             # Key fields: butler=, trace_id=, span_id=
│
├── uvicorn/
│   └── dashboard.log        # Dashboard API server (FastAPI/Uvicorn)
│                             # Contains: HTTP request logs, API errors
│                             # Format: Uvicorn access log
│
├── connectors/
│   ├── telegram_bot.log     # Telegram bot connector
│   ├── telegram_user_client.log  # Telegram userbot connector
│   └── gmail.log            # Gmail connector (IMAP polling + OAuth)
│                             # Contains: message ingestion, webhook delivery,
│                             # connector heartbeats, auth refresh
│                             # Format: structlog JSON
│
└── frontend/
    └── vite.log             # Vite dev server for dashboard frontend
```

### Per-butler logs (persistent, not per-run)

```
logs/butlers/
├── switchboard.log          # Switchboard daemon (structlog JSON)
├── switchboard_cc_stderr.log  # Claude Code stderr from switchboard sessions
├── general.log              # General butler daemon
├── general_cc_stderr.log    # Claude Code stderr from general sessions
├── health.log               # Health butler
├── education.log            # Education butler
├── finance.log              # Finance butler
├── travel.log               # Travel butler
├── messenger.log            # Messenger butler
├── relationship.log         # Relationship butler
└── home.log                 # Home butler
```

**Per-butler log format** (structlog JSON, one object per line):
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

### Searching logs effectively

```bash
# Find all sessions for a butler today
grep '"Session created"' logs/butlers/switchboard.log | grep '2026-03-12'

# Find errors
grep '"level": "error"' logs/butlers/switchboard.log | tail -20

# Find by trace_id (correlate across butlers)
grep '<trace-id>' logs/butlers/*.log

# Find route.execute calls
grep 'route.execute' logs/butlers/switchboard.log

# CC stderr for debugging LLM runtime issues
tail -50 logs/butlers/switchboard_cc_stderr.log
```

---

## Quick Debug Checklist

1. **Get session ID** — from user report, dashboard, or logs
2. **Identify butler** — which butler schema to query
3. **Query session** — get prompt, result, tool_calls, error from PostgreSQL
4. **Check tool calls** — look for `status: error` in tool_calls JSONB
5. **Check logs** — grep session ID in `up.log` and per-butler logs
6. **Check CC stderr** — if the LLM process crashed
7. **Check trace** — use trace_id to follow request across butlers
8. **Check dashboard API** — if running, use `/api/butlers/<name>/sessions/<id>`
