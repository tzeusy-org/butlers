## Why

On 2026-04-15 a real lifestyle butler session (`46f18840-4f74-4e0a-a3bf-cafa2b579f3a`) burned **2.7M input tokens over 7m16s** by looping 41 times on a single tool call (`memory_entity_resolve` with `null` arguments that silently returned `[]`). The session was not terminated by any in-process safety net — only the wall-clock timeout (which also appears to have fired late: the observed duration was 436 s against a nominal 300 s budget on the opencode adapter).

The current specs have no in-session loop detection or token-budget concept. The only safety nets are:

- Per-adapter process timeouts (`core-spawner/spec.md:76-80` explicitly says Claude Code `max_turns` SHALL NOT be enforced; `runtime-opencode/spec.md:20-22` sets only a 300 s timeout).
- Nothing in `core-sessions/spec.md` enumerates degenerate-behavior termination reasons.
- No cross-cutting rule requiring MCP tools to raise on invalid input. `memory_entity_resolve` returning an empty list on `name=null` was treated as a valid success and fed the loop.

The `module-memory` entity-resolution surface in archived specs still describes the "empty list on no match" contract without distinguishing *no match* from *invalid input*. The live code is being fixed by a parallel worker; this change makes the spec reflect the new contract so drift does not recur.

Separate issue: `_merge_tool_call_records` collapses differently-prefixed tool names into one record, which obscured the loop in the dashboard. That is being fixed in a parallel worker and is **not** in scope for this change.

## What Changes

- **Runtime-agnostic degenerate-loop detection in the spawner.** The spawner SHALL track repeated-identical tool calls, cumulative tool-call count, and cumulative input tokens within a single session, and SHALL terminate the runtime process when any configured budget is exceeded.
- **Typed termination error taxonomy on sessions.** Sessions terminated by guardrails SHALL record `success=False` and one of the new `error` values: `degenerate_tool_loop`, `tool_call_budget_exceeded`, `token_budget_exceeded`.
- **Per-butler configuration.** Thresholds SHALL be configurable in `butler.toml` under `[butler.runtime_seed]` (merging into the existing seed-and-manage pattern) with conservative defaults. Defaults are normative only as *defaults* — the real contract is that thresholds are configurable and enforced.
- **Cross-cutting tool contract.** MCP tools SHALL raise on invalid input rather than return empty success payloads, so that degenerate callers fail loudly instead of being rewarded with an actionable-looking empty list.
- **Memory entity resolution contract update.** `memory_entity_resolve` SHALL raise on `null`/empty `name` rather than returning `[]`. The "no candidates" path SHALL remain reserved for well-formed inputs that simply do not match any entity.
- **OpenCode timeout enforcement reconciled.** The 300 s timeout in `runtime-opencode/spec.md` is either incorrect (the observed session ran 436 s) or the enforcement is broken. The spec SHALL be updated to state the adapter MUST terminate the OS process when the timeout fires and SHALL include a verification step against the observed incident.
- **OpenCode SQLite migration bootstrap recovery.** The OpenCode adapter SHALL retry once when the first process exits non-zero after emitting only the known one-time SQLite migration completion banner, while preserving the normal error path for partial banners, stdout-bearing exits, retry failures, and actionable stderr.
- **Dashboard surfacing.** The new termination reasons SHALL surface on the sessions UI so operators can triage without reading logs.

## Impact

- Specs touched: `core-spawner`, `core-sessions`, `core-modules`, `module-memory`, `runtime-opencode`.
- Code (implementation work, not part of this proposal): `src/butlers/core/spawner.py` (detector + budget enforcement), `src/butlers/config.py` (new `[butler.runtime_seed]` fields + `RuntimeSeedConfig` plumbing), `src/butlers/core/runtimes/opencode.py` (timeout enforcement and migration-bootstrap retry), every module's MCP tool surface (input validation audit — filed as a separate task, not performed in this change).
- Sessions table: the `error` column must support the new taxonomy values; verify whether schema changes are needed.
- Dashboard: frontend session detail / list components need to render the new `error` strings with human-readable labels.
- Telemetry: a Prometheus counter tagged by termination reason so that degenerate-session rates are observable.

## Capabilities

### Modified Capabilities

- `core-spawner` — adds degenerate-loop detection and token/tool-call budget enforcement per session.
- `core-sessions` — adds the typed termination error taxonomy including the three new reasons.
- `core-modules` — adds the cross-cutting "raise on invalid input, never return empty success" rule.
- `module-memory` — constrains `memory_entity_resolve` to raise on null/empty input rather than silently returning `[]`.
- `runtime-opencode` — tightens the timeout requirement to mandate hard process termination, reconciles the documented 300 s value against observed behavior, and adds the narrow one-time SQLite migration bootstrap retry contract.
