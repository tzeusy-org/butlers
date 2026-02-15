# End-to-End Test Suite

> **Status**: Target-state specification (not yet implemented)
> **Bead**: `butlers-921` (epic)
> **Last updated**: 2026-02-16

This directory documents the Butlers end-to-end staging harness — a modular,
extensible test environment that boots the full butler ecosystem in a disposable
staging environment and validates complete message lifecycles against real
infrastructure.

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

## Quick Start

```bash
# Prerequisites
docker info                 # Docker daemon running
echo $ANTHROPIC_API_KEY     # API key set
which claude                # claude CLI on PATH
uv sync --dev               # Dependencies installed

# Run
make test-e2e               # Full suite
uv run pytest tests/e2e/test_ecosystem_health.py -v   # Smoke only (no LLM)
uv run pytest tests/e2e/ -v -k "health"               # One butler domain
```

## File Layout

```
tests/e2e/
├── __init__.py                    # Package marker
├── conftest.py                    # Session fixtures: ecosystem, logging, cost tracker
├── scenarios.py                   # E2EScenario dataclass + declarative scenario registry
├── test_ecosystem_health.py       # Smoke tests (no LLM calls)
├── test_scenario_runner.py        # Parametrized runner from scenarios.py
├── test_health_flow.py            # Complex health butler flows
├── test_switchboard_flow.py       # Classification, decomposition, dedup, dispatch
├── test_relationship_flow.py      # Contact creation via spawner
└── test_cross_butler.py           # Heartbeat tick, full e2e message flow

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
