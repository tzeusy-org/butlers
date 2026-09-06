# Historical Usage Audit (Phase 7 Detail)

Query the butler's `sessions` table to see which tools the runtime LLM has
actually called. Every MCP tool invocation is captured in the JSONB
`tool_calls` column via `_ToolCallLoggingMCP` (`daemon.py`) and persisted by
`sessions.complete()`. This is the primary evidence for removal decisions —
code-level analysis alone cannot tell you whether a tool is actually used.

Session history is evidence of LLM-facing usage only. It does not enumerate
infrastructure or server-to-server consumers: for example, `ingest`,
`route.execute`, `cancel_session`, and `chronicler_day_close_refresh` may have
zero session calls while remaining required. These examples are non-exhaustive.
Before recommending REMOVE for any zero-session tool, search repository call
sites and roster configuration, API, connector, and scheduler use.

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
    COALESCE(tc->>'name', tc->>'tool', tc->>'tool_name', tc->'call'->>'name', tc->'tool_call'->>'name', tc->'function'->>'name') AS tool_name,
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
- **Zero session calls are not a removal verdict.** Check repository call sites,
  roster configuration, API, connector, and scheduler use before classifying
  a tool as removable.
- **Safe to remove** if a tool has:
  - Zero calls are only a candidate signal AND
  - No repository, roster-config, API, connector, or scheduler consumer AND
  - No RFC, OpenSpec, manifesto, or role-contract requirement AND
  - No rare recovery or scheduled cadence consumer AND
  - Is NOT newly added (check git log for when the tool was introduced —
    `git log --all -1 --format=%ai -- {tool_source_file}`)

## Output format

```
## Historical Usage (last 30 days, N sessions sampled)

| Tool | Module | Calls | Last Used | Verdict |
|---|---|---:|---|---|
| route_to_butler | core | 1292 | 2026-04-07 | KEEP — primary function |
| memory_store_fact | memory | 0 | never | INVESTIGATE — search non-session consumers before REMOVE |
| ingest | core | 0 | n/a | KEEP — verified infrastructure consumer |

### Removal candidates (zero calls, pending consumer search)
- email_send_message, email_reply_to_thread, ...

### Removal savings
- N tools removable → estimated ~X token savings
```
