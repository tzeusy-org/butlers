## Why

On 2026-04-15 a real lifestyle butler session (`46f18840-4f74-4e0a-a3bf-cafa2b579f3a`) burned **2.7M input tokens over 7m16s** by looping 41 times on a single tool call (`memory_entity_resolve` with `null` arguments that silently returned `[]`). The session was not terminated by any in-process safety net — only the wall-clock timeout (which also appears to have fired late: the observed duration was 436 s against a nominal 300 s budget on the opencode adapter).

The current specs have no in-session loop detection or token-budget concept. The only safety nets are:

- Per-adapter process timeouts (`core-spawner/spec.md:76-80` explicitly says Claude Code `max_turns` SHALL NOT be enforced; `runtime-opencode/spec.md:20-22` sets only a 300 s timeout).
- Nothing in `core-sessions/spec.md` enumerates degenerate-behavior termination reasons.
- No cross-cutting rule requiring MCP tools to raise on invalid input. `memory_entity_resolve` returning an empty list on `name=null` was treated as a valid success and fed the loop.

The `module-memory` entity-resolution surface in archived specs still describes the "empty list on no match" contract without distinguishing *no match* from *invalid input*. The live code is being fixed by a parallel worker; this change makes the spec reflect the new contract so drift does not recur.

Separate issue: `_merge_tool_call_records` collapses differently-prefixed tool names into one record, which obscured the loop in the dashboard. That is being fixed in a parallel worker and is **not** in scope for this change.

> **Reconciliation note (bu-6fguq).** This change was unarchived because the merged code diverged from the originally-proposed design. The delta specs in this change have been reconciled **spec → as-built**: they now describe the shipped behavior rather than the original aspiration. The bullets below have been updated to match what actually landed. Items the code did not implement (in-flight cancellation, `[butler.runtime_seed]` thresholds + accessor HOT fields + runtime_config columns, healing-session exemption, SIGTERM→grace→SIGKILL escalation, dashboard surfacing, telemetry counter) are recorded as not-built; if any are still wanted they should be filed as fresh changes, not assumed shipped.

## What Changes

- **Runtime-agnostic degenerate-session guardrails in the spawner (post-session).** The spawner evaluates the completed session's merged tool-call list and reported token usage *after* `runtime.invoke()` returns, and raises `RuntimeError` when any guardrail trips (consecutive-identical-call count, cumulative tool-call count, cumulative input tokens). This is a post-session check, NOT in-flight cancellation — the subprocess has already exited when the checks run.
- **Guardrail error markers carried in the session `error` string.** Guardrail terminations record `success=False` with an `error` string beginning with one of the marker tokens `degenerate_tool_loop`, `tool_call_budget_exceeded`, `token_budget_exceeded`. The failover classifier matches these markers as substrings to suppress same-tier failover. (There is no separate `session_complete(error=<bare-enum>)` path; the marker is embedded in the `RuntimeError` text.)
- **Static thresholds, not seed-config.** Thresholds are spawner parameters / module constants (`_DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD = 6`, `_DEFAULT_MAX_TOOL_CALLS = 0` which disables the count budget, token budget defaulting to `None`/disabled). They are NOT `[butler.runtime_seed]` fields, NOT `RuntimeConfigAccessor` HOT fields, and there is no `runtime_config` column and no healing-session exemption.
- **Cross-cutting tool contract.** MCP tools raise on invalid input rather than return empty-success payloads, so degenerate callers fail loudly instead of being rewarded with an actionable-looking empty list.
- **Memory entity resolution contract.** `memory_entity_resolve` raises `ValueError` on null/empty/whitespace lookup (and when both `name` and `identifier` are supplied); the empty-list return is reserved for a well-formed lookup that matches nothing. (Note: the shipped argument is `identifier`, with `name` as a legacy alias.)
- **OpenCode timeout.** On timeout the adapter issues a single `proc.kill()` (SIGKILL), awaits exit, and raises `TimeoutError`. The graduated SIGTERM→grace→SIGKILL escalation and the SIGTERM-trap verification test from the original proposal were NOT implemented.
- **OpenCode SQLite migration bootstrap recovery.** The OpenCode adapter retries once when the first process exits non-zero after emitting only the known one-time SQLite migration completion banner, preserving the normal error path for partial banners, stdout-bearing exits, retry failures, and actionable stderr. (This IS implemented.)
- **Not built:** dashboard surfacing of the markers, the `butler_session_terminations_total` telemetry counter, and the module-wide tool-input audit.

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
