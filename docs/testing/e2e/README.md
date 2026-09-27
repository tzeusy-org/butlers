# End-to-End Test Suite

`tests/e2e/` boots every roster butler against a disposable PostgreSQL testcontainer, drives real
LLM sessions, and asserts routing, tool calls, and database side effects. Required behavior of the
staging bootstrap lives in
[`openspec/specs/e2e-ecosystem-staging`](../../../openspec/specs/e2e-ecosystem-staging/spec.md);
this page says how to run the suite and what each file proves.

The suite never runs in CI. `make test-qg`, the CI unit job and the nightly job all pass
`--ignore=tests/e2e`, and session-scoped autouse fixtures skip everything unless
`ANTHROPIC_API_KEY` is set and `claude` is on `PATH`. Run it with `-n 0`: each xdist worker would
boot its own ecosystem.

## Bootstrap

The session-scoped `butler_ecosystem` fixture (`tests/e2e/conftest.py`) runs five phases in order
and tears everything down in a `finally`, including on `KeyboardInterrupt`:

1. **Provision:** start the testcontainer, run every migration chain, create the current
   `message_inbox` partition.
2. **Configure:** add `E2E_PORT_OFFSET` (11000) to each butler's roster port (switchboard 41100
   becomes 52100) and point non-switchboard butlers' `switchboard_url` at the offset switchboard.
3. **Authenticate:** probe each available CLI auth provider for a valid token, not just a token
   file, and block on an interactive re-auth when one is missing or expired.
4. **Boot:** start each `ButlerDaemon` in-process. Daemons serve MCP over streamable HTTP at `/mcp`
   and legacy SSE at `/sse`; the harness health-checks `/sse` and wires `switchboard_url` to it.
5. **Validate:** smoke-check each running daemon.

Modules whose external credentials are absent (Telegram, Gmail, Calendar) are expected to degrade,
not fail boot. External-service integrations are out of scope here; connector tests cover them.

## Running

```bash
make test-e2e-validate                                          # -m "e2e and not benchmark"
make test-e2e-benchmark BENCHMARK_MODELS=claude-sonnet-4-5,gpt-4o
uv run pytest tests/e2e/ -v -s -n 0 --scenarios=smoke           # scenarios tagged "smoke"
uv run pytest tests/e2e/test_state.py -v -s -n 0                # one file
```

**Validate mode** (default) uses whatever model each butler resolves today and fails on the first
routing or tool-call mismatch; a scenario that times out is recorded, not failed.

**Benchmark mode** (`--benchmark`, models from `--benchmark-models` or `E2E_BENCHMARK_MODELS`) pins
each model in turn with catalog overrides at `priority=999` tagged `source='e2e-benchmark'`, so a
crashed run can be cleaned up with one `DELETE`. Only one corpus path is collected per mode
(`pytest_collection_modifyitems`). Benchmark mode accumulates results instead of failing and writes
per-model routing, tool-call and cost scorecards to `.tmp/e2e-scorecards/<timestamp>/`
(`tests/e2e/reporting.py`), pricing tokens from the repo-root `pricing.toml` (unknown models cost
zero).

## Writing tests

- **Declare, don't code.** A single-envelope input with an expected butler, expected tool names
  and SQL post-conditions is a `Scenario` in [`scenarios.py`](../../../tests/e2e/scenarios.py),
  built with the factories in `envelopes.py`. `test_scenario_runner.py` picks it up; tags drive
  `--scenarios`. Write a `test_*_flow.py` only for multi-step or cross-butler orchestration.
- **Assert loosely on LLM output.** Tool calls are a subset match; DB checks look for a matching
  row (`ILIKE`, ranges), not exact values. Infrastructure assertions stay exact.
- **Only what needs the ecosystem.** Schema, contract and scheduler-function checks belong in
  `tests/contracts/` or `tests/core/`; several e2e tests were moved there for that reason.

## Coverage

| File | Proves |
| --- | --- |
| [`test_ecosystem_health.py`](../../../tests/e2e/test_ecosystem_health.py) | No LLM calls: every butler is reachable and answers `status()` (also concurrently), all are in the switchboard registry, and core, health and relationship tables exist. |
| [`test_scenario_runner.py`](../../../tests/e2e/test_scenario_runner.py) | Each `Scenario` injected via `ingest_v1()` triages to the expected butler (`routing_accuracy`), calls the expected tools (`tool_accuracy`) and leaves the expected rows; duplicate injection is flagged. Hosts the benchmark loop (`benchmark`). |
| [`test_switchboard_flow.py`](../../../tests/e2e/test_switchboard_flow.py) | Re-ingesting the same envelope returns `duplicate=True`. |
| [`test_contracts.py`](../../../tests/e2e/test_contracts.py) | The same idempotency key yields the same `request_id` with the duplicate flag. |
| [`test_cross_butler.py`](../../../tests/e2e/test_cross_butler.py) | Switchboard routes health and relationship queries to the right butler; unknown or quarantined targets error; `routing_log` records successes and failures; `notify.v1` to an unknown messenger is logged and an `origin_butler` mismatch is rejected. |
| [`test_health_flow.py`](../../../tests/e2e/test_health_flow.py) | Measurement tools round-trip directly, and a spawner-driven medication prompt writes the expected rows. |
| [`test_relationship_flow.py`](../../../tests/e2e/test_relationship_flow.py) | Spawner-driven prompts create contacts and notes, and each run is tracked as a session. |
| [`test_state.py`](../../../tests/e2e/test_state.py) | The KV store persists across MCP sessions, round-trips every JSON type, isolates butlers, filters by prefix, and overwrites and deletes. |
| [`test_scheduling.py`](../../../tests/e2e/test_scheduling.py) | `butler.toml` schedules sync to `scheduled_tasks` at startup, schedule CRUD works over MCP, and timer plus external triggers serialize without deadlock. |
| [`test_approvals.py`](../../../tests/e2e/test_approvals.py) | A gated tool creates a pending approval instead of running, runs once approved, and expires on timeout; non-gated tools bypass the gate; the audit trail records its metadata. |
| [`test_security.py`](../../../tests/e2e/test_security.py) | Runtimes cannot read undeclared env vars, see only their own butler's tools, and cannot reach another butler's tables; the API key never reaches logs; inter-butler traffic goes only through the switchboard. |
| [`test_observability.py`](../../../tests/e2e/test_observability.py) | A routed request carries one `trace_id` into the target butler's session; session rows are complete, with accurate `duration_ms` and captured tool calls; a clean run logs no unexpected errors; cost totals match per-session tokens. |
| [`test_resilience.py`](../../../tests/e2e/test_resilience.py) | A butler with a failed module still serves core tools. |
| [`test_performance.py`](../../../tests/e2e/test_performance.py) | Concurrent triggers serialize without deadlock, an erroring trigger releases the dispatch lock, and a saturated DB pool queues rather than rejects. |
