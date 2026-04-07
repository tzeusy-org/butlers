---
name: butler-tool-review
description: Deep audit of every butler's MCP tool surface — tool count per module, historical usage analysis from session data, docstring quality for LLM explainability, failure mode documentation with actionable error messages, and tool group configuration. Use when asked to review butler tools, audit tool counts, check docstring quality, review error messages, find unused tools, or optimize the tool surface. Also use when onboarding a new module to ensure its tools meet quality standards.
---

# Butler Tool Review

Comprehensive audit of the MCP tool surface across all butlers. Produces a structured report covering tool inventory, docstring quality, error message quality, and group configuration.

## Execution Strategy

**Use subagents per butler or module to avoid context bloat.** Each butler has 30-150 tools across multiple modules. Loading all tools into one context is wasteful. Instead:

1. Dispatch one Explore subagent per butler (or per large module) to gather raw data
2. Collect results, then synthesize the final report in the main context
3. For docstring/error audits on large modules (>20 tools), use a dedicated subagent per module

## Audit Phases

### Phase 1: Inventory

For each butler in `roster/*/butler.toml`:

1. Read butler.toml — get enabled modules and configured `groups`
2. Count core daemon tools using the butler's type — see [references/tool-budget.md](references/tool-budget.md)
3. Count module tools, respecting `groups` config
4. Produce per-butler inventory table

**Output format:**
```
| Butler | Module | Groups | Tools | Total |
|---|---|---|---:|---:|
| switchboard | core (staffer+switchboard) | — | 30 | |
| | memory | core | 8 | |
| | calendar | core | 8 | |
| | switchboard | routing, extraction | 8 | |
| | ... | | | 59 |
```

### Phase 2: Docstring Quality

For each module with >=10 tools, dispatch a subagent to read the tool definitions and assess each docstring:

- **Purpose**: First line clearly states what the tool does
- **Parameters**: All params documented with types and allowed values
- **Return value**: Return schema described (keys, types, status codes)
- **LLM guidance**: Helps an LLM decide WHEN to use this tool vs alternatives
- **Examples**: Complex params have usage examples

Rate each: `GOOD` / `NEEDS_WORK` / `MISSING`. See [references/quality-patterns.md](references/quality-patterns.md) for before/after fix examples.

**Output format:**
```
| Module | Tool | Rating | Issues |
|---|---|---|---|
| memory | memory_search | GOOD | — |
| memory | memory_store_fact | NEEDS_WORK | Missing return schema |
```

### Phase 3: Error Message Quality

For each module with >=10 tools, dispatch a subagent to find all error return paths and assess:

- **Actionable**: Tells the LLM what to do differently on the next call
- **Specific**: Names the parameter or value that failed
- **Retryable**: Indicates whether the operation can be retried
- **No bare exceptions**: Avoids generic `str(exc)` without context

**Output format:**
```
| Module | Tool | Error Path | Quality | Issue |
|---|---|---|---|---|
| finance | record_transaction | missing amount | GOOD | — |
| memory | memory_store_fact | predicate validation | BAD | Generic str(exc), no hint |
```

### Phase 4: Tool Overlap Detection

Flag tools on the same butler that have overlapping functionality (confuses the model into picking the wrong one). Common patterns:

- Module-specific fact tools vs `memory_store_fact` (e.g., finance SPO tools)
- Multiple "list" tools with similar signatures across modules
- `route` vs `route_to_butler` vs `route.execute` on switchboard

For each overlap found, report which tools conflict and recommend consolidation or clearer disambiguation in docstrings.

### Phase 5: Token Cost Estimation

Estimate per-butler token overhead from tool descriptions. Tool schemas get serialized into the model context at discovery time.

- Rule of thumb: 1 tool ≈ 100-400 tokens depending on docstring length and parameter count
- Sum estimated tokens per butler; flag butlers exceeding ~15k tool tokens
- Identify the most expensive individual tools (verbose docstrings, many params)

This matters more than raw tool count — 40 terse tools may cost less than 30 verbose ones.

### Phase 6: Group Configuration Review

For each butler, verify:

1. All modules with group support have `groups` configured in butler.toml
2. Cross-cutting modules pruned appropriately (memory, calendar, approvals, etc.)
3. Domain modules on their specialist butler keep ALL groups (ownership principle)
4. Report estimated savings if any module is unconfigured

See [references/tool-budget.md](references/tool-budget.md) for group taxonomy.

### Phase 7: Historical Usage Audit

**This phase is critical for removal decisions.** Query the butler's `sessions` table to see which tools the runtime LLM has actually called. Every MCP tool invocation is captured in the JSONB `tool_calls` column via `_ToolCallLoggingMCP` (daemon.py) and persisted by `sessions.complete()`.

**Important distinction:** Some tools (e.g. `ingest`, `tick`, `route.execute`, `connector.heartbeat`, `backfill.poll`, `backfill.progress`) are called by the daemon directly, not through the LLM's MCP session. They will NOT appear in session tool_calls but are still required. Tools that are exclusively LLM-facing (memory, calendar, email, state, sessions, schedule, extraction, etc.) MUST show usage here to justify their existence.

#### Database connection

Read `.env.dev` (or `.env.prod` for production) for connection credentials:

```
POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_SSLMODE
```

Connect via `psql` with `PGPASSWORD` env var. Sessions live in `{butler_schema}.sessions`.

#### Queries to run

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

Replace `{schema}` with the butler's schema name from `butler.toml` (e.g. `switchboard`, `finance`).

#### Interpreting results

- **Ignore** `command_execution` and `skill` rows — these are runtime internals, not MCP tools.
- **Ignore** tool name variants with `mcp__` or `{butler}_` prefixes — these are the same tools under different naming conventions. Consolidate counts.
- **Daemon-called tools** (never appear in session data but still required):
  - `ingest`, `tick`, `route.execute` — daemon dispatches these directly
  - `connector.heartbeat`, `backfill.poll`, `backfill.progress` — connector-facing, called by external connectors
  - `trigger` — called by the scheduler loop
- **Safe to remove** if a tool has:
  - Zero calls over 30+ days AND
  - Is NOT in the daemon-called list above AND
  - Is NOT newly added (check git log for when the tool was introduced — `git log --all -1 --format=%ai -- {tool_source_file}`)

#### Output format

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

### Phase 8: MCP Connection Reliability

Query recent session records for MCP connection failures (Codex CLI intermittently fails to discover tools):

```sql
-- via dashboard API: GET /api/butlers/{name}/sessions?limit=50
-- then check process_log for mcp_connection_failed
```

For each butler, report:
- Total sessions sampled
- Sessions with `mcp_connection_failed: true`
- Retry success rate (`retry_succeeded: true` / `retry_attempted: true`)
- Flag butlers with >10% MCP failure rate

### Phase 9: Report

Synthesize into a single structured report. **Historical usage data (Phase 7) should be the primary driver of removal recommendations** — code-level analysis alone cannot tell you whether a tool is actually used.

```markdown
## Tool Surface Audit Report

### Summary
| Butler | Type | Registered Tools | Actually Used (30d) | Dead Tools | Est. Token Overhead |

### Dead Tool Removal (highest impact)
Tools with 0 calls that are safe to remove. Group by module for clean removal:
| Module | Dead Tools | Action |
| email | email_send_message, ... (4) | Remove module from butler.toml |
| memory | memory_confirm, ... (3) | Prune to used groups only |

### Docstring / Error Issues
(only for tools that are actually used — no point fixing dead tools)

### Recommendations
1. Module removals (entire modules with 0 usage)
2. Group pruning (modules with partial usage)
3. Core tool excludes (universal tools never called by this butler's LLM)
4. Docstring/error fixes (for surviving tools only)

### Per-Butler Details
(full tool listings with usage counts per butler)
```

## Subagent Prompt Templates

**Inventory agent (per butler):**
```
Read roster/{butler}/butler.toml. List all enabled modules with their
configured groups. For each module, count the tools that would be
registered given the groups config. Report as a markdown table.
Core daemon tools: see the UNIVERSAL/DOMAIN/MESSENGER/SWITCHBOARD
constants in src/butlers/daemon.py.
```

**Docstring audit agent (per module):**
```
Read {module_file}. For each @mcp.tool() or @_tool() decorated function,
assess the docstring against these criteria:
1. Clear purpose line (first sentence)
2. All parameters documented with types and valid values
3. Return schema described
4. LLM guidance on when to use this tool vs alternatives
Rate each GOOD/NEEDS_WORK/MISSING. List specific issues per tool.
Report as a markdown table.
```

**Error audit agent (per module):**
```
Read {module_file}. For each tool function, find all error return paths
({"status": "error"}, raise, except blocks). For each error:
1. Is the message actionable? (tells LLM what to fix)
2. Is it specific? (names the bad param/value)
3. Does it indicate retryability?
4. Does it avoid bare str(exc) without context?
Rate each GOOD/BAD. Report as a markdown table with the error path
description and specific issues.
```

**Historical usage audit agent (per butler):**
```
Connect to the butler's database using credentials from .env.dev
(POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD).
Run the following queries against the {schema}.sessions table:

1. Tool call frequency (last 30 days):
   SELECT tc->>'name', tc->>'module', COUNT(*)
   FROM {schema}.sessions, jsonb_array_elements(tool_calls) AS tc
   WHERE completed_at > now() - interval '30 days'
   GROUP BY 1, 2 ORDER BY 3 DESC;

2. Session volume:
   SELECT COUNT(*), COUNT(*) FILTER (WHERE completed_at > now() - interval '30 days')
   FROM {schema}.sessions;

Report the raw results. Ignore 'command_execution' and 'skill' rows
(runtime internals). Consolidate mcp__{butler}__ and {butler}_ prefixed
tool names with their bare equivalents.
```
