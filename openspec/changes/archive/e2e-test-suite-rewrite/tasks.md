## 1. Ecosystem Staging

- [ ] 1.1 Rewrite `tests/e2e/conftest.py` with phased `butler_ecosystem` fixture: Provision (testcontainer + migrations), Configure (port offsets + switchboard URL patching), Authenticate (OAuth validation + interactive prompts), Boot (daemon startup + health checks), Validate (smoke MCP calls)
- [ ] 1.2 Implement OAuth credential validation in Authenticate phase — check token validity (not just file existence) for calendar and email modules, prompt with provider-specific instructions when missing or expired
- [ ] 1.3 Add try/finally teardown that stops all daemons, closes pools, and removes the PostgreSQL container on normal exit, failure, or interrupt
- [ ] 1.4 Ensure `message_inbox` partition for current month is created after migrations run

## 2. Envelope Factories & Scenario Corpus

- [ ] 2.1 Create `tests/e2e/envelopes.py` with `email_envelope(sender, subject, body, thread_id=None)` and `telegram_envelope(chat_id, text, from_user="test-user", message_id=None)` factory functions that produce valid `ingest.v1` payloads with deterministic idempotency keys
- [ ] 2.2 Define unified `Scenario` dataclass in `tests/e2e/scenarios.py` with fields: `id`, `description`, `envelope` (dict), `expected_routing` (str | None), `expected_tool_calls` (list[str]), `db_assertions` (list[DbAssertion]), `tags` (list[str]), `timeout_seconds` (int)
- [ ] 2.3 Author initial scenario corpus (20–30 scenarios) covering: email→calendar routing, telegram→health measurements, telegram→interactive replies, multi-butler classification edge cases. Tag each with channel type and butler category
- [ ] 2.4 Add `--scenarios` pytest CLI option for tag-based filtering (e.g., `--scenarios=smoke`)

## 3. Scenario Runner & Ingress Injection

- [ ] 3.1 Rewrite `tests/e2e/test_scenario_runner.py` — inject each scenario's envelope via `ingest_v1(pool, envelope)` directly, await session completion, then verify routing, tool calls, and DB assertions
- [ ] 3.2 Implement routing verification: compare `IngestAcceptedResponse.triage_target` against `scenario.expected_routing`
- [ ] 3.3 Implement tool-call verification: retrieve calls via `consume_runtime_session_tool_calls(session_id)`, assert `expected_tool_calls` is a subset of actual tool names
- [ ] 3.4 Implement DB assertion verification: execute each `DbAssertion.query` against the appropriate butler's pool, compare result against `expected`
- [ ] 3.5 Add per-scenario timeout handling — skip-and-record on timeout rather than failing the entire run

## 4. Model Benchmark Harness

- [ ] 4.1 Create `tests/e2e/benchmark.py` with `pin_model(pool, model_name, butler_names)` and `unpin_model(pool)` functions that insert/delete `shared.model_catalog` + `shared.butler_model_overrides` entries with `priority=999` and `source='e2e-benchmark'`
- [ ] 4.2 Add `--benchmark` pytest flag and `--benchmark-models` CLI option (comma-separated model list, also readable from `E2E_BENCHMARK_MODELS` env var)
- [ ] 4.3 Implement benchmark runner loop: for each model in list, pin model → run full scenario corpus → collect results → unpin model. Ensure try/finally cleanup of overrides on crash
- [ ] 4.4 Create `BenchmarkResult` accumulator dataclass that collects per-scenario results keyed by `(model, scenario_id)` — routing pass/fail, tool-call pass/fail, tokens, duration, actual values

## 5. Scoring & Scorecards

- [ ] 5.1 Implement routing scorecard computation: per-model accuracy percentage, per-tag breakdown, confusion matrix (expected→actual butler for misroutes)
- [ ] 5.2 Implement tool-call scorecard computation: per-model accuracy percentage, per-butler breakdown, missing tool details
- [ ] 5.3 Extend `CostTracker` to key costs by `(model, scenario_id)` and support per-model pricing configuration

## 6. Scorecard Reporting

- [ ] 6.1 Create `tests/e2e/reporting.py` with scorecard renderer that writes to `.tmp/e2e-scorecards/<timestamp>/`
- [ ] 6.2 Implement per-model markdown scorecards: `routing-scorecard.md`, `tool-call-scorecard.md`, `cost-summary.md`
- [ ] 6.3 Implement `raw-results.json` output with `schema_version: "1.0"` and per-scenario detail (scenario_id, routing_expected/actual/pass, tool_calls_expected/actual/pass, input_tokens, output_tokens, duration_ms)
- [ ] 6.4 Implement cross-model `summary.md` with comparison table (model, routing accuracy, tool-call accuracy, total tokens, estimated cost) sorted by routing accuracy descending
- [ ] 6.5 Print scorecard output directory path to terminal after generation

## 7. Validate Mode & Integration

- [ ] 7.1 Wire validate mode (default, no `--benchmark` flag): run scenarios with currently configured model, use standard pytest assertions (hard fail on mismatch)
- [ ] 7.2 Wire benchmark mode: run scenarios per model, accumulate without hard failures, generate scorecards at session end via pytest `sessionfinish` hook
- [ ] 7.3 Add pytest markers: `e2e`, `benchmark`, `routing-accuracy`, `tool-accuracy`
- [ ] 7.4 Update `Makefile` with `test-e2e-validate` and `test-e2e-benchmark` targets

## 8. Cleanup & Migration

- [ ] 8.1 Remove or archive old E2E test files that are fully replaced (old `test_scenario_runner.py`, old `scenarios.py`)
- [ ] 8.2 Adapt non-benchmarking E2E tests (contracts, security, observability, resilience) to use the new phased staging fixture
- [ ] 8.3 Update `docs/tests/e2e/README.md` to reflect the rewritten suite, two execution modes, and scorecard output
