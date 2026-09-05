---
name: butler-tool-review
description: Deep audit of every butler's MCP tool surface — tool count per module, historical usage analysis from session data, docstring quality for LLM explainability, failure mode documentation with actionable error messages, and tool group configuration. Use when asked to review butler tools, audit tool counts, check docstring quality, review error messages, find unused tools, or optimize the tool surface. Also use when onboarding a new module to ensure its tools meet quality standards.
metadata:
  owner: tze
  authors:
    - tze
    - Claude
  status: active
  last_reviewed: "2026-09-05"
---

# Butler Tool Review

Comprehensive audit of the MCP tool surface across all butlers. Produces a structured report covering tool inventory, docstring quality, error message quality, and group configuration.

## Support files

- [references/tool-budget.md](references/tool-budget.md) — core/module tool counts and group taxonomy. Load in Phase 1 (inventory) and Phase 6 (group config review). Living catalog — carries its own maintenance contract.
- [references/quality-patterns.md](references/quality-patterns.md) — before/after fix examples for docstrings and error messages. Load in Phase 2 and Phase 3 when writing up specific issues or fixes.
- [references/historical-usage-audit.md](references/historical-usage-audit.md) — DB connection details, SQL queries, and result-interpretation rules for Phase 7. Load only when running Phase 7.
- [references/subagent-prompts.md](references/subagent-prompts.md) — copy-ready dispatch prompts for the Phase 1/2/3/7 subagents. Load when dispatching those subagents.

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

**This phase is critical for removal decisions** — code-level analysis alone cannot tell you whether a tool is actually used. Query the butler's `{schema}.sessions` table (JSONB `tool_calls` column) to see which tools the runtime LLM has actually called. Some tools (`ingest`, `tick`, `route.execute`, `connector.heartbeat`, `backfill.poll`, `backfill.progress`, `trigger`) are called by the daemon or scheduler directly and will NOT appear in session data but are still required — everything else that's LLM-facing MUST show usage to justify its existence.

See [references/historical-usage-audit.md](references/historical-usage-audit.md) for the DB connection details, the exact SQL queries to run, result-interpretation rules (what to ignore, what counts as safe-to-remove), and the output format.

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

Synthesize into a single structured report. **Historical usage data (Phase 7) should be the primary driver of removal recommendations** — see Phase 7 for why.

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

Copy-ready dispatch prompts for the Phase 1 (inventory), Phase 2 (docstring), Phase 3 (error), and Phase 7 (historical usage) subagents live in [references/subagent-prompts.md](references/subagent-prompts.md) — load it when dispatching a subagent for those phases.
