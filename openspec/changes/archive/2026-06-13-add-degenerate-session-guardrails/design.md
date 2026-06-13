> **Reconciliation note (bu-6fguq, archived 2026-06-13).** This design document
> records the *original aspiration*. The code that actually shipped diverged from
> several decisions below. The authoritative, reconciled behavior is in this
> change's delta specs (synced into `openspec/specs/`) and summarized in
> `proposal.md` / `tasks.md`. In particular, decisions 1, 3, 4 (in-flight
> cancellation), 6 (SIGTERM→grace→SIGKILL), and the telemetry/dashboard items were
> NOT implemented as described: guardrails are **post-session** checks raising
> `RuntimeError` with marker strings; thresholds are **static module constants**
> (not seed-config/accessor HOT fields); there is **no healing exemption**; and the
> OpenCode timeout does a **single `proc.kill()`**. Read the delta specs, not this
> document, for what the system does.

## Context

A lifestyle butler session (`46f18840-4f74-4e0a-a3bf-cafa2b579f3a`, 2026-04-15) consumed 2.7M input tokens over 7m16s. The root cause was a tight loop: the agent called `memory_entity_resolve` with `name=null`, the tool returned `[]` as a successful-looking result, and the agent — with no signal that anything was wrong — retried the same call 41 times. Each retry replayed the full conversation as input context, which is why the input-token bill exploded while tool-call output was tiny.

There was no in-process safety net. The only contention was the runtime adapter's wall-clock timeout (nominally 300 s for opencode; this session ran 436 s, so enforcement is also suspect).

The fix decomposes cleanly into four concerns:

1. **Detect degenerate behavior inside the spawner** (butler-agnostic, runtime-agnostic) so every butler and every adapter benefits without per-adapter changes.
2. **Give each session a typed termination reason** so the dashboard, telemetry, and runbooks can discriminate degenerate loops from genuine timeouts and crashes.
3. **Prevent the "silent empty success" antipattern** in MCP tools — the loop only existed because `null` was treated as a valid query.
4. **Ensure timeouts actually kill the process.** A 436 s run under a 300 s budget means either the spec is wrong or enforcement is weak.

## Goals / Non-Goals

**Goals:**

- Detection lives once, in the spawner — not duplicated across four adapters.
- Thresholds are config-driven per butler; defaults exist but are not sacred.
- Typed `error` strings on the session record, consistent across adapters.
- Dashboard can filter / count sessions by termination reason without parsing free-form strings.
- Tools raise on invalid input so degenerate callers see an exception instead of a rewardingly-shaped empty result.

**Non-Goals:**

- This change does NOT fix `_merge_tool_call_records` — that is handled in a parallel worker.
- This change does NOT perform the module-wide tool-input audit — it only mandates the contract and files the audit as a follow-up task.
- This change does NOT introduce cost accounting or per-butler $ budgets — token counts are a proxy that is cheap to track; dollar cost is a dashboard concern downstream.
- No mid-session "soft nudge" back to the agent. When a budget is exceeded, the session ends. A future change could add a warning-before-kill phase; not this one.

## Decisions

### 1. Detection lives in the spawner, not the adapters

**Decision:** The loop detector, tool-call count budget, and token budget are enforced in `src/butlers/core/spawner.py` around the `runtime.invoke(...)` call. Adapters expose an async iterator / callback stream of tool-call events and usage deltas; the spawner consumes that stream and aborts the runtime when a threshold trips.

**Why not per-adapter:**

- We have four adapters (`claude`, `codex`, `gemini`, `opencode`). Duplicating detection logic in each guarantees drift.
- Butler authors write butlers, not adapters. The safety net should be invisible to them.
- The spawner already owns the session lifecycle (`session_create` → `session_complete`), so the natural home for "session terminated by guardrail" is right there.

**Trade-off:** Requires a minor adapter contract change — each adapter must stream tool-call events and usage counts incrementally rather than returning them only at the end. Adapters already do this for `stream-json` / `--format json` parsing; we need to surface the increment to the spawner rather than aggregating internally.

### 2. Three independent budgets, OR-combined

**Decision:** Three separately-configurable budgets that each trip a distinct error:

| Budget | Default | Error value | Rationale |
|---|---|---|---|
| `degenerate_loop_max_repeats` | 5 | `degenerate_tool_loop` | Consecutive identical tool calls (same name + same normalized args). 5 is generous — real agents rarely retry more than 2-3 times. |
| `max_tool_calls_per_session` | 80 | `tool_call_budget_exceeded` | Cumulative tool-call count across the session. 80 allows long legitimate sessions while killing a 41-call loop on a single tool. |
| `max_input_tokens_per_session` | 500_000 | `token_budget_exceeded` | Cumulative `input_tokens` (including cache-read). 500k is 5× a typical long session and <20% of the observed 2.7M incident. |

All three thresholds SHALL be configurable per butler via `butler.toml` under `[butler.runtime_seed]`. The defaults above are normative only as defaults; the binding contract is that the values are read from `RuntimeConfigAccessor` and enforced.

**Why OR-combined and not a single composite budget:** Each signal catches a distinct failure mode. Degenerate-loop count catches tight loops early before they burn tokens. Tool-call cap catches slow-burn loops that vary their args enough to evade the dedupe. Token cap catches anything else that spirals. A single composite gives no diagnostic signal — we want to know which one tripped.

**"Identical tool call" definition:** `(tool_name, canonical_json(args))` where `canonical_json` sorts keys and normalizes `null` / `None`. Two adjacent identical tuples count as one repeat; five consecutive identical tuples without any other tool call in between trips the loop detector. This SHALL be implemented in the spawner, not in each adapter's output parser.

### 3. Thresholds live in `runtime_seed` and flow through the accessor

**Decision:** Reuse the seed-and-manage pattern from the `runtime-config-seed-and-manage` change. New fields are added to `RuntimeSeedConfig` and to the `{schema}.runtime_config` table. They are **hot** fields (read per-spawn via `RuntimeConfigAccessor.get()`) so operators can tune them without a daemon restart.

**Why hot:** Tuning defensive thresholds is exactly the kind of operational iteration the seed-and-manage pattern was built for. We expect to adjust these as we learn what real sessions look like.

**Alternative rejected:** Hard-coded constants in `spawner.py`. Rejected because the lifestyle incident revealed that conservative defaults matter, and we need the ability to raise/lower them per butler without a deploy cycle (e.g. the memory butler legitimately makes many tool calls; the health butler should make few).

### 4. Termination semantics: abort the process, record a typed error, no retry

**Decision:** When a budget trips:

1. The spawner cancels the in-flight `runtime.invoke()` task (which kills the subprocess via the adapter's existing cancellation path).
2. `session_complete` is called with `success=False` and `error=<taxonomy-value>`.
3. The taxonomy values are string literals recorded in the sessions table's existing `error` column: `degenerate_tool_loop`, `tool_call_budget_exceeded`, `token_budget_exceeded`.
4. The self-healing dispatcher fires as usual (these are real failures worth investigating).
5. **No automatic retry.** If an agent is looping, retrying is the worst possible response.

**Schema impact:** The `error` column already stores free-form strings. The taxonomy is enforced by convention in the spawner — we do not introduce a CHECK constraint because healing/crash errors are still free-form. A task verifies this does not need a migration.

### 5. Tools raise on invalid input (cross-cutting rule)

**Decision:** A new core-modules requirement that MCP tools SHALL raise on invalid input (missing required args, wrong types, malformed values) rather than returning an empty success payload.

**Why:** The lifestyle incident was only possible because `memory_entity_resolve(name=null)` returned `[]` — identical in shape to "queried with a valid name, found nothing." The agent could not distinguish them. Raising gives the agent a typed error it can reason about, and also lets the loop detector's repeat counter stop re-firing on identical failing calls (the detector still trips, but the session ends on the first real signal rather than after the fifth reward-shaped empty list).

**Scope:** This is a normative spec rule. The follow-up audit across every module's tool surface is filed as a task (not performed in this change).

### 6. Opencode timeout: spec says 300 s, reality says 436 s

**Decision:** The `runtime-opencode` spec is modified to:

1. Confirm the intended timeout value. We keep 300 s as the default but make clear it is the **default** and can be overridden via `session_timeout_s` in `runtime_seed` (which the spawner already plumbs through per the existing `core-spawner` spec).
2. Strengthen the scenario so the adapter MUST hard-terminate the subprocess (SIGKILL fallback after a SIGTERM grace period) when the timeout fires.
3. Require verification against session `46f18840-4f74-4e0a-a3bf-cafa2b579f3a`: either the timeout was not reaching the subprocess or the subprocess ignored SIGTERM. The task list includes this verification.

**Why not just increase the timeout:** The problem is not that 300 s is too short. The problem is that 300 s was not enforced. Raising the ceiling without fixing enforcement leaves the real bug in place.

### 7. Telemetry: counter per termination reason

**Decision:** A Prometheus counter `butler_session_terminations_total{butler, reason}` where `reason` is one of the new taxonomy values plus existing ones (`timeout`, `crash`, `success`, ...). This is consumed by a dashboard panel so operators see degenerate-session rates at a glance.

**Not in this change:** Alerting thresholds. The counter is enough to ship; alert rules live in the Grafana/alerting repo.

## Risks / Trade-offs

- **[Risk] Legitimate long sessions get killed.** The memory butler's consolidation job can make many tool calls. **Mitigation:** Thresholds are per-butler configurable. Tune up for the memory butler; keep conservative for others.
- **[Risk] Loop detector false positives on intentional idempotent polling.** **Mitigation:** The normalized-args dedupe is intentional — true idempotent polling with identical args is the thing we want to catch. If a butler has a legitimate "poll until ready" pattern, the tool should vary its args (e.g. include a cursor) or the butler should use scheduling instead of in-session polling.
- **[Risk] Tool-input validation audit is large.** Every module touches this. **Mitigation:** Audit is a separate task, not a blocker on this change. Spec rule is declared; compliance is verified incrementally.
- **[Trade-off] Three independent budgets vs one composite.** Picked independent for diagnostic clarity. Downstream: the dashboard must render three different reasons rather than one.
- **[Trade-off] Adapter contract change.** Adapters must stream tool-call events to the spawner. Current adapters already parse them incrementally, so the cost is plumbing, not rewriting.

## Migration Plan

1. Spec change lands (this proposal).
2. Implement budget fields in `RuntimeSeedConfig`, migration for new `runtime_config` columns.
3. Implement loop detector + budgets in spawner, covered by unit tests against a simulated adapter stream.
4. Fix `memory_entity_resolve` to raise on null/empty (also covered by the parallel worker addressing the same code path).
5. Audit module tool surface for empty-success-on-invalid antipatterns (separate task).
6. Verify opencode timeout enforcement against the 436 s session.
7. Wire dashboard to render new `error` values; add telemetry counter.
8. Runbook update: what each new termination reason means, how to triage.

## Resolved Questions

- **Should we also cap output tokens?** No. Output tokens are bounded by the model's max-output and the session timeout. The runaway mode is input-token growth from conversation replay, which is what we cap.
- **Do we need a soft-warning phase before killing?** No for v1. Adding a "budget nearly exceeded, please wrap up" system-prompt injection mid-session is a future enhancement; the immediate need is stopping the bleed.
- **Do we retry killed sessions?** No. See decision 4.

## Open Questions

- Whether the `error` column on `sessions` has a max length that would truncate the longest taxonomy string (`tool_call_budget_exceeded` = 26 chars). Verification task added; likely fine.
- Whether the opencode subprocess ignores SIGTERM and requires SIGKILL. This is what the verification task is for.
