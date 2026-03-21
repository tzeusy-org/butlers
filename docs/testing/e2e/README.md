# End-to-End Test Suite

> **Status**: Implemented — phased bootstrap + scenario runner + benchmark harness
> **Last updated**: 2026-03-12

The E2E test suite validates complete message lifecycles against the full butler
ecosystem. It boots all roster butlers in a disposable staging environment (PostgreSQL
testcontainer) and asserts routing accuracy, tool-call accuracy, and database side
effects from envelope injection through session completion.

## Documents

### Core

| Document | Contents |
|----------|----------|
| [Introduction](introduction.md) | Intent, goals, design philosophy, extensibility model |
| [Infrastructure](infrastructure.md) | Staging PostgreSQL, Docker, port allocation, database isolation, testcontainer resilience, module degradation, CI exclusion, cost model, logging |
| [Flows](flows.md) | End-to-end message flows, scenario definitions, per-input validation, assertion strategies, LLM non-determinism |

### Testing Domains

| Document | Contents |
|----------|----------|
| [Resilience](resilience.md) | Failure injection, chaos testing, graceful degradation, serial dispatch lock contention, timeout cascades |
| [Contracts](contracts.md) | Data contract validation between pipeline stages, schema versioning, idempotency, backward compatibility |
| [Observability](observability.md) | Distributed tracing, TRACEPARENT propagation, tool span instrumentation, metrics, session log completeness |
| [Security](security.md) | Credential sandboxing, MCP config lockdown, cross-DB isolation, log redaction, approval gates |
| [Scheduling](scheduling.md) | Heartbeat tick, cron lifecycle, TOML schedule sync, timer/external trigger interleaving, tick idempotency |
| [State](state.md) | KV state store persistence, JSONB fidelity, cross-session memory, state isolation, concurrent access |
| [Approvals](approvals.md) | Gated tool interception, approval decisions, conditional gates, argument sensitivity, audit trail |
| [Performance](performance.md) | Load testing, serial dispatch throughput, connection pool saturation, latency profiling, benchmarking baselines |

## Architecture

### Five-Phase Bootstrap (`butler_ecosystem` fixture)

The `butler_ecosystem` session-scoped fixture provisions the full roster before any
test runs:

| Phase | Name | What happens |
|---|---|---|
| 1 | Provision | Start PostgreSQL testcontainer, run all Alembic migration chains (core + butler-specific + module), create per-butler schemas, ensure `message_inbox` partition |
| 2 | Configure | Apply `E2E_PORT_OFFSET` (+11000) to every butler port, patch `switchboard_url` on non-switchboard butlers |
| 3 | Authenticate | Validate OAuth/CLI token validity for all configured providers; prompt interactively when missing or expired |
| 4 | Boot | Start all `ButlerDaemon` instances, health-check `/sse` endpoints (HTTP 200) |
| 5 | Validate | Smoke-test each running butler — verify DB pool connectivity |

Teardown is guaranteed via `try/finally`: all daemons stopped, pools closed, container removed — even on `KeyboardInterrupt` or `SIGTERM`.

### Execution Modes

#### Validate Mode (default)

Runs scenarios using the current model configuration (whatever `resolve_model()` returns
for each butler). Each test asserts hard failures immediately:

```
Routing mismatch → pytest.fail()     # fails the test, stops at first error
Tool-call mismatch → pytest.fail()   # fails the test, stops at first error
Timeout → pytest.skip()              # graceful skip
```

Start with:

```bash
make test-e2e-validate
# or
uv run pytest tests/e2e/ -v -s -m "e2e and not benchmark"
```

#### Benchmark Mode

Activated with `--benchmark`. Iterates over a model list, pinning each model in turn
via catalog overrides at `priority=999` (tagged `source='e2e-benchmark'` for crash-safe
cleanup). Results are accumulated without hard failures; scorecards are generated at
session end via `pytest_sessionfinish`.

```bash
make test-e2e-benchmark BENCHMARK_MODELS=claude-sonnet-4-5,gpt-4o
# or
uv run pytest tests/e2e/ --benchmark --benchmark-models=claude-sonnet-4-5,gpt-4o -v -s
```

### Scenario System

Scenarios are declarative `Scenario` dataclass instances in `scenarios.py`. Each scenario
specifies:

- `envelope`: An `ingest.v1` payload built via `email_envelope()` or `telegram_envelope()`
- `expected_routing`: Target butler name (or `None` for multi-target edge cases)
- `expected_tool_calls`: Tool names that must appear in the session (subset match)
- `db_assertions`: SQL queries + expected results for post-execution state validation
- `tags`: Channel, butler, and scope labels for filtering

Tag-based filtering is available via `--scenarios=<tag>` (e.g. `--scenarios=smoke`).

### Scorecard Output (Benchmark Mode)

After a benchmark run, scorecards are written to `.tmp/e2e-scorecards/<timestamp>/`:

```
.tmp/e2e-scorecards/<timestamp>/
├── summary.md                         # Cross-model comparison table
├── <model>/
│   ├── routing-scorecard.md           # Overall + per-tag accuracy + confusion matrix
│   ├── tool-call-scorecard.md         # Overall + per-butler accuracy + failure details
│   ├── cost-summary.md                # Token usage + estimated cost per scenario
│   └── raw-results.json               # Machine-readable results (schema_version "1.0")
```

Pricing is loaded from `pricing.toml` (per-model `input_price_per_token` /
`output_price_per_token`). Falls back to zero-cost when a model is not listed.

## Quick Start

```bash
# Prerequisites
docker info                 # Docker daemon running
echo $ANTHROPIC_API_KEY     # API key set
which claude                # claude CLI on PATH
uv sync --dev               # Dependencies installed

# Validate mode (default — hard fail on mismatch)
make test-e2e-validate

# Benchmark mode
make test-e2e-benchmark BENCHMARK_MODELS=claude-sonnet-4-5,gpt-4o

# Smoke scenarios only (lower token cost)
uv run pytest tests/e2e/ -v -s --scenarios=smoke

# Single domain
uv run pytest tests/e2e/ -v -s -k "health"

# By marker
uv run pytest tests/e2e/ -v -s -m "e2e and routing_accuracy"
```

## File Layout

```
tests/e2e/
├── __init__.py                    # Package marker
├── conftest.py                    # Session fixtures (phased bootstrap, benchmark accumulator,
│                                  #   sessionfinish scorecard hook)
├── benchmark.py                   # pin_model/unpin_model, BenchmarkResult accumulator,
│                                  #   run_benchmark loop, resolve_benchmark_models
├── scoring.py                     # compute_routing_scorecard, compute_tool_call_scorecard,
│                                  #   compute_all_scorecards, BenchmarkCostTracker, load_pricing
├── reporting.py                   # generate_scorecards — writes all scorecard .md + .json files
├── scenarios.py                   # Scenario dataclass + ALL_SCENARIOS registry
│                                  #   (email-calendar, telegram-health, interactive, edge-cases)
├── envelopes.py                   # email_envelope() / telegram_envelope() factories
├── baselines.json                 # Accuracy baseline targets per scenario
├── test_scenario_runner.py        # Parametrized routing (routing_accuracy) + tool-call
│                                  #   (tool_accuracy) tests; benchmark mode wired here
├── test_ecosystem_health.py       # Smoke tests — no LLM calls, validates ecosystem boot
├── test_contracts.py              # IngestEnvelopeV1 data contract validation
├── test_security.py               # Credential isolation, MCP lockdown, cross-DB boundaries
├── test_observability.py          # TRACEPARENT propagation, session log completeness
├── test_resilience.py             # Failure injection, timeout cascades
├── test_state.py                  # KV state persistence, JSONB fidelity
├── test_scheduling.py             # Tick lifecycle, cron schedule sync
├── test_approvals.py              # Gated tool interception, approval workflow
├── test_performance.py            # Throughput and latency profiling
├── test_health_flow.py            # Complex health butler message flows
├── test_switchboard_flow.py       # Classification, decomposition, dedup, dispatch
├── test_relationship_flow.py      # Contact creation via spawner
└── test_cross_butler.py           # Cross-butler heartbeat, full e2e message flow

docs/tests/e2e/
├── README.md                      # This index
├── introduction.md                # Intent, goals, extensibility
├── infrastructure.md              # Staging environment, Docker, databases
├── flows.md                       # Message flows and validation
├── resilience.md                  # Failure injection and degradation
├── contracts.md                   # Data contract validation
├── observability.md               # Tracing, metrics, diagnostics
├── security.md                    # Credential isolation, boundaries
├── scheduling.md                  # Timer-driven flows, cron lifecycle
├── state.md                       # KV state store testing
├── approvals.md                   # Gated tool workflow
└── performance.md                 # Load testing, benchmarking
```

## Pytest Markers

| Marker | Description |
|---|---|
| `e2e` | All E2E tests — require API key, claude binary, Docker |
| `benchmark` | Benchmark mode tests |
| `routing_accuracy` | Routing accuracy tests (`test_scenario_routing`) |
| `tool_accuracy` | Tool-call accuracy tests (`test_scenario_tool_calls`) |

## CLI Options (E2E-specific)

| Option | Description |
|---|---|
| `--scenarios=TAG` | Run only scenarios tagged with TAG |
| `--benchmark` | Activate benchmark mode |
| `--benchmark-models=LIST` | Comma-separated model IDs for benchmark |

`E2E_BENCHMARK_MODELS` env var is the fallback when `--benchmark-models` is not provided.
