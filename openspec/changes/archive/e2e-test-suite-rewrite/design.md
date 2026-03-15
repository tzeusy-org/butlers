## Context

The current E2E suite (`tests/e2e/`) has a working `butler_ecosystem` fixture that boots all roster butlers against a testcontainer PostgreSQL, a `CostTracker`, and scenario-based tests. But it lacks model benchmarking, has no ingress injection tests, and the staging/OAuth flow is ad-hoc. The rewrite rebuilds the suite around three concerns: ecosystem staging, ingress injection with effect verification, and model benchmarking with scorecards.

Key existing infrastructure we build on:
- `ingest_v1(pool, payload)` — canonical switchboard ingestion boundary
- `capture_tool_call()` / `consume_runtime_session_tool_calls(session_id)` — contextvars-based tool call recording
- `shared.model_catalog` + `shared.butler_model_overrides` — DB-driven model resolution
- `sessions` table with `input_tokens`/`output_tokens` columns
- 9 roster butlers (switchboard, general, relationship, health, messenger, education, finance, travel, home) on ports 41100–41108

## Goals / Non-Goals

**Goals:**
- Stage the full butler ecosystem with OAuth credential prompting before test execution
- Inject `ingest.v1` envelopes at the switchboard boundary and verify downstream effects (DB state, tool calls, outbound delivery)
- Benchmark multiple models by pinning one model for switchboard + all butlers per run
- Produce per-model scorecards for routing accuracy and tool-call accuracy
- Track and report token cost per model per scenario
- Run locally/manually — no CI/CD integration

**Non-Goals:**
- Testing connector normalization (Gmail/Telegram polling, `_process_update()`) — already unit-tested
- Testing ingestion policy rules in isolation — already unit-tested
- Achieving full fanout (model-per-butler × model-for-switchboard) — explicitly avoided
- Real external service round-trips (no actual emails sent/received, no Telegram bot polling)
- Performance/load testing — separate concern, kept in existing `test_performance.py`

## Decisions

### 1. Injection at `ingest_v1()` — not connector level, not raw MCP

**Decision:** All ingress scenarios call `ingest_v1(pool, payload)` directly with constructed `ingest.v1` envelopes.

**Why:** The switchboard boundary is the canonical entry point. Connector normalization is already well-tested. Going lower (raw MCP `call_tool("ingest", ...)`) adds SSE transport noise. Going higher (real Gmail/Telegram) adds external service fragility. `ingest_v1()` is the Goldilocks injection point — it exercises dedup, policy, classification, routing, and spawning.

**Alternative considered:** MCP client → switchboard SSE → `ingest` tool. Rejected because it couples tests to the SSE transport layer and makes error diagnosis harder.

### 2. Model pinning via `shared.model_catalog` + `shared.butler_model_overrides`

**Decision:** Before each benchmark run, insert a catalog entry for the target model and upsert `butler_model_overrides` for all butlers with `priority=999` (highest). After the run, delete the overrides.

**Why:** This uses the existing `resolve_model()` resolution path without modifying TOML files or monkey-patching. The priority system ensures the test model wins over any existing catalog entries. Cleanup is a single `DELETE FROM shared.butler_model_overrides WHERE priority = 999`.

**Alternative considered:** Patching `butler.toml` runtime.model at test time. Rejected because it requires file I/O, risks leaving stale config, and bypasses the catalog resolution path we actually want to test.

### 3. Unified scenario corpus — one definition, multiple evaluation dimensions

**Decision:** Replace the separate `E2EScenario` and routing test definitions with a single `Scenario` dataclass:

```python
@dataclass
class Scenario:
    id: str
    description: str
    envelope: dict                          # ingest.v1 payload
    expected_routing: str | None            # Expected butler name (routing scorecard)
    expected_tool_calls: list[str]          # Expected tool names (tool-call scorecard)
    db_assertions: list[DbAssertion]       # Post-execution DB checks (effect verification)
    tags: list[str]                         # Categorization: ["email", "health", "smoke"]
    timeout_seconds: int = 60
```

**Why:** A single scenario definition serves three evaluation modes: routing accuracy (does `triage_target` match `expected_routing`?), tool-call accuracy (does tool call capture contain `expected_tool_calls`?), and effect verification (do `db_assertions` pass?). No duplication between routing tests and flow tests.

### 4. Envelope factory functions for scenario authoring

**Decision:** Provide helper factories for constructing realistic `ingest.v1` payloads:

```python
def email_envelope(
    sender: str, subject: str, body: str,
    thread_id: str | None = None,
) -> dict: ...

def telegram_envelope(
    chat_id: int, text: str, from_user: str = "test-user",
    message_id: int | None = None,
) -> dict: ...
```

**Why:** Raw `ingest.v1` payloads are verbose (source, event, sender, payload, control sections). Factories make scenario definitions readable while ensuring correct envelope structure. They also centralize idempotency key generation (`tg:<chat_id>:<message_id>`, `email:<message_id>`).

### 5. Scorecard as post-run artifact — not inline assertions

**Decision:** Benchmark runs collect results into a `BenchmarkResult` accumulator during execution. After the full run, a scorecard renderer writes JSON + markdown to `.tmp/e2e-scorecards/<timestamp>/`.

Scorecard structure:
```
{model_name}/
  routing-scorecard.md      # Per-scenario routing accuracy
  tool-call-scorecard.md    # Per-scenario tool-call accuracy
  cost-summary.md           # Token usage and estimated cost
  raw-results.json          # Machine-readable full results
summary.md                  # Cross-model comparison table
```

**Why:** Inline `assert` failures stop the run on the first model's first failure. Scorecard mode runs all scenarios for all models, accumulates pass/fail/score, and produces a comparison artifact. This is the whole point — comparing models, not gating on one.

**Alternative considered:** pytest parametrize over models with `--no-fail` flag. Rejected because pytest's reporting isn't designed for cross-parameter comparison matrices.

### 6. Two execution modes: `validate` and `benchmark`

**Decision:** The suite supports two modes controlled by pytest marker or CLI flag:

- **`validate`** (default): Runs all scenarios with the currently configured model. Uses standard pytest assertions — pass/fail. Good for "does the ecosystem work?" validation after changes.
- **`benchmark`** (`--benchmark` or `-m benchmark`): Iterates over a model list, pins each, runs full corpus, produces scorecards. No hard assertion failures — everything is scored.

**Why:** Day-to-day validation shouldn't require specifying a model list. Benchmarking is a distinct activity with different output expectations (scorecards vs pass/fail).

### 7. Ecosystem staging with explicit OAuth phase

**Decision:** The `butler_ecosystem` fixture is restructured into explicit phases:

1. **Provision** — Start PostgreSQL testcontainer, run migrations
2. **Configure** — Load roster configs, apply port offsets, patch switchboard URLs
3. **Authenticate** — For each butler with OAuth-dependent modules (calendar, email), check for cached credentials. If missing, prompt interactively with clear instructions. Block until credentials are valid.
4. **Boot** — Start all butler daemons, health-check all `/sse` endpoints
5. **Validate** — Smoke-test each butler with a no-op MCP call

**Why:** The current fixture silently fails when OAuth tokens are expired. Explicit phasing surfaces credential problems before any test runs, saving token burn on doomed runs.

### 8. Tool-call verification via `consume_runtime_session_tool_calls()`

**Decision:** After each scenario's spawner session completes, retrieve tool calls via the existing contextvars capture API. Match against `expected_tool_calls` using set containment (expected is a subset of actual).

**Why:** The capture infrastructure already exists and is production-tested. Set containment (not exact match) allows butlers to make internal tool calls (state reads, context fetches) without requiring scenarios to enumerate every internal call.

### 9. Cost tracking per model per scenario

**Decision:** Extend `CostTracker` to key costs by `(model, scenario_id)`. Query `sessions.input_tokens`/`output_tokens` after each scenario. Aggregate into per-model cost summaries in the scorecard.

**Why:** The primary value of model benchmarking is cost-quality tradeoffs. A model that routes perfectly but costs 10x more per prompt is useful to know. Per-scenario granularity shows which scenarios are expensive.

## Risks / Trade-offs

**[Token cost of benchmark runs]** → Each model in the benchmark list runs the full scenario corpus. For N models × M scenarios, cost scales linearly. Mitigation: keep the scenario corpus focused (20-30 scenarios), support `--scenarios=smoke` tag filtering, and print cost projections before starting.

**[Model catalog cleanup]** → If a benchmark run crashes mid-flight, `priority=999` overrides remain in the DB. Mitigation: use a fixture with try/finally cleanup. Also tag override rows with a `source='e2e-benchmark'` metadata column for manual cleanup.

**[Session timing]** → LLM sessions may timeout or hang during benchmark runs. Mitigation: per-scenario timeout (default 60s), skip-and-score on timeout rather than fail the entire run.

**[Scorecard drift]** → Scorecard format may evolve, breaking consumers. Mitigation: version the `raw-results.json` schema. Human-readable markdown is best-effort.

**[OAuth credential staleness]** → Cached OAuth tokens expire between runs. Mitigation: the authenticate phase validates tokens (not just checks for file existence) and prompts for re-auth if expired.
