# Historical Usage Audit (Phase 7 Detail)

Query the butler's `sessions` table to see which tools the runtime LLM has
actually called. Every MCP tool invocation is captured in the JSONB
`tool_calls` column via `_ToolCallLoggingMCP` (`daemon.py`) and persisted by
`sessions.complete()`. This is the primary evidence for removal decisions —
code-level analysis alone cannot tell you whether a tool is actually used.

**Daemon-called tools never appear in session data but are still required:**
`ingest`, `tick`, `route.execute` (daemon dispatches these directly),
`connector.heartbeat`, `backfill.poll`, `backfill.progress` (connector-facing),
`trigger` (called by the scheduler loop). Tools that are exclusively
LLM-facing (memory, calendar, email, state, sessions, schedule, extraction,
etc.) MUST show usage here to justify their existence.

## Database connection

Read `.env.dev` (or `.env.prod` for production) for connection credentials:

```
POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_SSLMODE
```

Connect via `psql` with `PGPASSWORD` env var. Sessions live in `{butler_schema}.sessions`.

## Queries to run

**Tool call frequency (last 30 days):**

```sql
SELECT
    tc->>'name' AS tool_name,
    tc->>'module' AS module,
    COUNT(*) AS call_count
FROM {schema}.sessions,
     jsonb_array_elements(tool_calls) AS tc
WHERE completed_at > now() - interval '30 days'
GROUP BY 1, 2
ORDER BY call_count DESC;
```

**Last-used date per tool (all time):**

```sql
SELECT
    tc->>'name' AS tool_name,
    tc->>'module' AS module,
    COUNT(*) AS total_calls,
    MAX(completed_at) AS last_used
FROM {schema}.sessions,
     jsonb_array_elements(tool_calls) AS tc
GROUP BY 1, 2
ORDER BY last_used ASC;
```

**Session volume (for sample size context):**

```sql
SELECT COUNT(*) AS total_sessions,
       COUNT(*) FILTER (WHERE completed_at > now() - interval '30 days') AS last_30d
FROM {schema}.sessions;
```

Replace `{schema}` with the butler's schema name from `butler.toml` (e.g.
`switchboard`, `finance`).

## Interpreting results

- **Ignore** `command_execution` and `skill` rows — these are runtime
  internals, not MCP tools.
- **Ignore** tool name variants with `mcp__` or `{butler}_` prefixes — these
  are the same tools under different naming conventions. Consolidate counts.
- **Daemon-called tools** — see the list above; they are KEEP regardless of
  zero session calls.
- **Safe to remove** if a tool has:
  - Zero calls over 30+ days AND
  - Is NOT in the daemon-called list above AND
  - Is NOT newly added (check git log for when the tool was introduced —
    `git log --all -1 --format=%ai -- {tool_source_file}`)

## Output format

```
## Historical Usage (last 30 days, N sessions sampled)

| Tool | Module | Calls | Last Used | Verdict |
|---|---|---:|---|---|
| route_to_butler | core | 1292 | 2026-04-07 | KEEP — primary function |
| memory_store_fact | memory | 0 | never | REMOVE — never called, not daemon-internal |
| ingest | core | 0 | n/a | KEEP — daemon-called, not LLM-facing |

### Dead tools (0 calls, safe to remove)
- email_send_message, email_reply_to_thread, ...

### Removal savings
- N tools removable → estimated ~X token savings
```
