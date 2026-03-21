# Metrics Module
> **Purpose:** Opt-in Prometheus integration that lets any butler define, emit, and query named metrics via MCP tools.
> **Audience:** Contributors.
> **Prerequisites:** [Module System](module-system.md).

## Overview

The Metrics module (`src/butlers/modules/metrics/`) gives each butler a self-service metrics surface. Butlers define counters, gauges, and histograms through MCP tools at runtime. The write side emits observations through the OpenTelemetry SDK (OTLP pipeline), while the read side executes PromQL queries against a Prometheus HTTP API. Metric definitions are persisted to the butler's existing state store (KV JSONB) under `metrics_catalogue:<name>` keys, so they survive daemon restarts without requiring Alembic migrations.

## Architecture

The module has three internal layers:

1. **`__init__.py` -- `MetricsModule`**: Registers five MCP tools and manages the in-process instrument cache.
2. **`prometheus.py`**: Async helpers wrapping Prometheus `/api/v1/query` and `/api/v1/query_range` via `httpx` (30s timeout, 10s connect, Tailscale-gated).
3. **`storage.py`**: State-store helpers persisting definitions under `metrics_catalogue:*` keys using the core `state_set`/`state_list` API.

## MCP Tools

The module registers five tools on the butler's FastMCP server:

| Tool | Description |
|---|---|
| `metrics_define` | Create a named metric (counter, gauge, or histogram). Persists the definition and builds an OTEL instrument. Idempotent on re-definition. |
| `metrics_emit` | Record a single observation to a previously defined metric. Validates value constraints (non-negative for counters/histograms) and label key sets. |
| `metrics_list` | Return all metric definitions registered with this butler. |
| `metrics_query` | Execute an instant PromQL query against the configured Prometheus endpoint. |
| `metrics_query_range` | Execute a range PromQL query with start/end/step parameters. |

## Naming Convention

Metric names are fully qualified using the pattern `butler_<schema>_<name>`. The butler's DB schema name (with hyphens replaced by underscores) serves as the namespace. For example, a `finance` butler defining `api_calls` produces the OTEL instrument name `butler_finance_api_calls`. Bare metric names must match `^[a-z][a-z0-9_]*$`.

## Metric Types

| Type | OTEL Instrument | Value Constraint | Description |
|---|---|---|---|
| `counter` | Counter | >= 0 | Monotonically increasing count |
| `gauge` | UpDownCounter | Any float | Value that can go up and down |
| `histogram` | Histogram | >= 0 | Distribution of observations |

## Lifecycle

On **startup**, the module derives the butler name from the DB schema, stores the asyncpg pool reference, and restores the instrument cache by loading all persisted definitions and rebuilding OTEL instruments. Invalid or unrecognized definitions are logged and skipped.

On **shutdown**, all state references and caches are cleared.

## Guardrails

- **Hard cap**: 1,000 defined metrics per butler. The `metrics_define` tool checks the count before persisting a new definition.
- **Label cardinality advisory**: `metrics_define` documentation warns against high-cardinality label keys (user IDs, UUIDs, request IDs). Only low-cardinality enum-like values are appropriate.
- **Value validation**: Counters and histograms reject negative values. Gauges (implemented as UpDownCounters) accept any float.
- **Label validation**: `metrics_emit` requires the exact label key set declared at definition time -- no missing keys, no extra keys.

## Configuration

The module requires a single config field in `butler.toml`:

```toml
[modules.metrics]
prometheus_query_url = "http://lgtm:9090"
```

This URL is used only for read-side PromQL queries. Write-side emission flows through the existing OTEL/OTLP pipeline configured at the daemon level. The Prometheus endpoint is assumed to be Tailscale-gated with no auth headers required.

## Error Handling

All MCP tools return structured dicts rather than raising exceptions. Success returns `{"ok": true, ...}`. Failures return `{"error": "<descriptive message>"}` so the LLM receives actionable diagnostics without unhandled exceptions crossing the MCP boundary. No database migrations are needed (`migration_revisions()` returns `None`).

## Related Pages

- [Connector Metrics](../connectors/metrics.md) -- Prometheus counters for connector runtimes
- [Module System](module-system.md) -- How modules register tools and lifecycle hooks
