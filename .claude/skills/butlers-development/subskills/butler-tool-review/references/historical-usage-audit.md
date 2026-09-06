# Historical Usage Audit (Phase 7 Detail)

Query the butler's `sessions` table to see which tools the runtime LLM has
actually called. Every MCP tool invocation is captured in the JSONB
`tool_calls` column via `_ToolCallLoggingMCP` (`daemon.py`) and persisted by
`sessions.complete()`. This is one LLM-use signal and candidate-evidence
source. It is never primary removal authority: code-level analysis and session
history answer different consumer questions, and neither grants permission to
remove a tool.

Session history is evidence of LLM-facing usage only. It does not enumerate
infrastructure or server-to-server consumers: for example, `ingest`,
`route.execute`, `cancel_session`, and `chronicler_day_close_refresh` may have
zero session calls while remaining required. These examples are non-exhaustive.
Before classifying any zero-session tool as eligible for removal review, search
repository call sites and roster configuration, API, connector, and scheduler
use.

## Database connection

Read `.env.dev` (or `.env.prod` for production) for connection credentials:

```
POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_SSLMODE
```

Connect via `psql` with `PGPASSWORD` env var. Sessions live in `{butler_schema}.sessions`.

## Queries to run

**Tool call frequency (last 30 days):**

```sql
WITH normalized_calls AS (
    SELECT
        s.completed_at,
        CASE
            WHEN jsonb_typeof(tc->'name') = 'string' THEN tc->>'name'
            WHEN jsonb_typeof(tc->'tool_name') = 'string' THEN tc->>'tool_name'
            WHEN jsonb_typeof(tc->'tool') = 'string' THEN tc->>'tool'
            WHEN jsonb_typeof(tc->'tool') = 'object'
                 AND jsonb_typeof(tc->'tool'->'name') = 'string'
                THEN tc->'tool'->>'name'
            WHEN jsonb_typeof(tc->'call') = 'object'
                 AND jsonb_typeof(tc->'call'->'name') = 'string'
                THEN tc->'call'->>'name'
            WHEN jsonb_typeof(tc->'tool_call') = 'object'
                 AND jsonb_typeof(tc->'tool_call'->'name') = 'string'
                THEN tc->'tool_call'->>'name'
            WHEN jsonb_typeof(tc->'toolCall') = 'object'
                 AND jsonb_typeof(tc->'toolCall'->'name') = 'string'
                THEN tc->'toolCall'->>'name'
            WHEN jsonb_typeof(tc->'function') = 'object'
                 AND jsonb_typeof(tc->'function'->'name') = 'string'
                THEN tc->'function'->>'name'
            ELSE '<unresolved>'
        END AS tool_name
    FROM {schema}.sessions AS s
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(s.tool_calls) = 'array' THEN s.tool_calls
            ELSE '[]'::jsonb
        END
    ) AS calls(tc)
)
SELECT
    tool_name,
    COUNT(*) AS call_count
FROM normalized_calls
WHERE completed_at > now() - interval '30 days'
GROUP BY tool_name
ORDER BY call_count DESC;
```

**Tool call frequency (all time):**

```sql
WITH normalized_calls AS (
    SELECT
        s.completed_at,
        CASE
            WHEN jsonb_typeof(tc->'name') = 'string' THEN tc->>'name'
            WHEN jsonb_typeof(tc->'tool_name') = 'string' THEN tc->>'tool_name'
            WHEN jsonb_typeof(tc->'tool') = 'string' THEN tc->>'tool'
            WHEN jsonb_typeof(tc->'tool') = 'object'
                 AND jsonb_typeof(tc->'tool'->'name') = 'string'
                THEN tc->'tool'->>'name'
            WHEN jsonb_typeof(tc->'call') = 'object'
                 AND jsonb_typeof(tc->'call'->'name') = 'string'
                THEN tc->'call'->>'name'
            WHEN jsonb_typeof(tc->'tool_call') = 'object'
                 AND jsonb_typeof(tc->'tool_call'->'name') = 'string'
                THEN tc->'tool_call'->>'name'
            WHEN jsonb_typeof(tc->'toolCall') = 'object'
                 AND jsonb_typeof(tc->'toolCall'->'name') = 'string'
                THEN tc->'toolCall'->>'name'
            WHEN jsonb_typeof(tc->'function') = 'object'
                 AND jsonb_typeof(tc->'function'->'name') = 'string'
                THEN tc->'function'->>'name'
            ELSE '<unresolved>'
        END AS tool_name
    FROM {schema}.sessions AS s
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(s.tool_calls) = 'array' THEN s.tool_calls
            ELSE '[]'::jsonb
        END
    ) AS calls(tc)
)
SELECT
    tool_name,
    COUNT(*) AS total_calls
FROM normalized_calls
GROUP BY tool_name
ORDER BY total_calls DESC;
```

The normalization CTE is intentionally identical in both queries. Its
type-dispatched `CASE` prevents an object-valued `tool` field from being
stringified before nested-name extraction. `<unresolved>` counts preserve
evidence that an unsupported call shape exists without exposing arguments or
payloads; investigate those counts before making any zero-use claim. Query
outputs remain content-blind: normalized tool names and aggregate counts only.

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
- Strip a prefix and consolidate the result only when the stripped name exists
  in the mechanically derived registered inventory or in a verified alias set.
  Consolidate any other alias only through that verified alias set. Retain
  unmatched names for investigation.
- **Zero session calls are not a removal verdict.** Check repository call sites,
  roster configuration, API, connector, and scheduler use before classifying
  a tool as removable.
- **Candidate eligibility review.** A tool is eligible for removal review only
  when all of the following evidence is recorded:
  - Zero session calls, treated only as a candidate signal AND
  - No repository, roster-config, API, connector, or scheduler consumer AND
  - No RFC, OpenSpec, manifesto, or role-contract requirement AND
  - No rare recovery or scheduled cadence consumer AND
  - Introduction evidence does not show it is too new for the sampled window.
    Locate the registering line with `git blame`, or search history for the
    registration/name:

    ```bash
    git blame -L <registration-line>,<registration-line> -- {tool_source_file}
    git log --max-count=20 --format='%h %ad %s' --date=short -S'<registration-or-tool-name>' -- {tool_source_file}
    git log --max-count=20 --format='%h %ad %s' --date=short -G'<registration-regex>' -- {tool_source_file}
    ```

    These results are evidence, not proof: renames and moved code can break
    history continuity.

Eligibility is still not authority. Do not remove a tool unless the owning
issue, specification decision, or owner instruction explicitly authorizes that
removal.

## Output format

```
## Historical Usage (last 30 days, N sessions sampled)

| Tool | Calls | Verdict |
|---|---:|---|
| route_to_butler | 1292 | RETAIN — primary function |
| memory_store_fact | 0 | CANDIDATE — complete eligibility and authority review |
| ingest | 0 | RETAIN — verified infrastructure consumer |

### Eligibility candidates (zero calls, pending evidence review)
- email_send_message, email_reply_to_thread, ...

### Potential savings
- If N eligible tools receive explicit removal authority: estimated ~X token savings
```
