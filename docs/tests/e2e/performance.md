# Performance — Load Testing, Concurrency, and Benchmarking

## Overview

The E2E harness boots a real ecosystem of HTTP servers on real ports. This makes
it directly compatible with external load testing tools and internal concurrency
tests. Performance E2E tests validate throughput limits, latency characteristics,
resource consumption, and degradation behavior under load.

## Performance Domains

| Domain | What It Measures | Why It Matters |
|--------|-----------------|----------------|
| **Throughput** | Messages processed per minute | Capacity planning for concurrent users |
| **Latency** | End-to-end pipeline time per message | User experience (response time) |
| **Concurrency** | Serial dispatch lock behavior under load | Queueing behavior when multiple triggers compete |
| **Connection pools** | asyncpg pool saturation and queueing | Database becomes bottleneck before LLM |
| **MCP transport** | SSE connection overhead and caching | Router client lifecycle costs |
| **LLM budget** | Token consumption per scenario | Cost predictability at scale |

## Load Testing

### External Tools

The running ecosystem is accessible on standard HTTP ports, making it compatible
with any HTTP load testing tool:

| Tool | Command | What It Tests |
|------|---------|--------------|
| **k6** | `k6 run --vus 10 --duration 30s script.js` | Sustained load against switchboard |
| **locust** | `locust -f locustfile.py --host http://localhost:40100` | Ramp-up load with user simulation |
| **wrk** | `wrk -t4 -c10 -d30s http://localhost:40100/sse` | Raw SSE connection throughput |
| **hey** | `hey -n 100 -c 5 http://localhost:40103/sse` | Quick latency distribution |

### k6 Load Test Script

```javascript
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
    stages: [
        { duration: '30s', target: 5 },   // ramp up to 5 VUs
        { duration: '1m', target: 5 },     // sustain
        { duration: '30s', target: 0 },    // ramp down
    ],
};

export default function () {
    // Call the switchboard's status tool
    const res = http.post('http://localhost:40100/sse', JSON.stringify({
        method: 'tools/call',
        params: { name: 'status', arguments: {} },
    }), { headers: { 'Content-Type': 'application/json' } });

    check(res, {
        'status is 200': (r) => r.status === 200,
        'response time < 500ms': (r) => r.timings.duration < 500,
    });

    sleep(1);
}
```

### Ecosystem Fixture for Load Testing

The session-scoped `butler_ecosystem` fixture can be extracted into a standalone
script for interactive load testing:

```python
# scripts/staging.py
"""Hold the E2E ecosystem open indefinitely for load testing."""

import asyncio
from tests.e2e.conftest import bootstrap_ecosystem

async def main():
    ecosystem = await bootstrap_ecosystem()
    print("Ecosystem running. Press Ctrl+C to stop.")
    print("Switchboard: http://localhost:40100/sse")
    print("Health:      http://localhost:40103/sse")
    try:
        await asyncio.Event().wait()  # block forever
    finally:
        await ecosystem.shutdown()

asyncio.run(main())
```

## Serial Dispatch Lock Under Load

### The Bottleneck

The spawner's serial dispatch lock is the primary throughput bottleneck. Each
butler can only run one runtime session at a time. Under load, incoming triggers
queue on the lock:

```
t=0    Trigger A → acquires lock → runtime session starts (~30s)
t=1    Trigger B → blocks on lock
t=5    Trigger C → blocks on lock
t=30   Trigger A completes → lock released → Trigger B acquires
t=31   Trigger D → blocks on lock
t=60   Trigger B completes → Trigger C acquires
...
```

### Queueing Metrics

| Metric | Definition | Target |
|--------|-----------|--------|
| Lock wait time | Time between trigger arrival and lock acquisition | < 2x session duration |
| Queue depth | Number of triggers waiting on the lock | Observable, not bounded |
| Starvation | Any trigger waiting > N × session_duration | Should not occur (FIFO) |
| Throughput | Sessions completed per minute | ~2/min per butler (at 30s/session) |

### E2E Lock Contention Tests

| Test | What It Validates |
|------|-------------------|
| Serial execution | 5 concurrent triggers → all complete → sessions are sequential |
| No deadlock | 10 concurrent triggers → all eventually complete |
| FIFO ordering | Triggers arrive in order 1-5 → sessions created in order 1-5 |
| Lock released on error | Trigger 1 errors out → Trigger 2 still acquires lock |
| Lock released on timeout | Trigger 1 times out → Trigger 2 still acquires lock |

### Lock Contention Test

```python
async def test_serial_dispatch_under_load(butler_ecosystem):
    """Multiple concurrent triggers should serialize, not deadlock."""
    health = butler_ecosystem["health"]
    n = 5

    # Fire N triggers concurrently
    tasks = [
        health.spawner.trigger(
            prompt=f"Test trigger {i}",
            trigger_source=f"load-test-{i}",
        )
        for i in range(n)
    ]
    results = await asyncio.gather(*tasks)

    # All should succeed
    assert sum(1 for r in results if r.success) == n

    # Sessions should be sequential (non-overlapping)
    sessions = await health.pool.fetch(
        "SELECT created_at, completed_at FROM sessions "
        "WHERE trigger_source LIKE 'load-test-%' ORDER BY created_at"
    )
    assert len(sessions) == n
    for i in range(1, len(sessions)):
        assert sessions[i - 1]["completed_at"] <= sessions[i]["created_at"], (
            f"Sessions {i-1} and {i} overlap — serial dispatch lock violated"
        )
```

## Connection Pool Saturation

### Pool Configuration

Each butler's asyncpg pool is configured with `min_pool_size` and
`max_pool_size` (default 2–10). Under load, pool exhaustion causes tool calls
to queue on the pool:

```
Runtime session starts → tool_1 → acquires conn → executes SQL → releases conn
                  → tool_2 → acquires conn → executes SQL → releases conn
                  → tool_3 → acquires conn → ...
```

With serial dispatch, only one runtime session runs at a time, so pool saturation
is unlikely during normal operation. It becomes relevant when:

1. A tool holds a connection for a long time (complex query)
2. Multiple tools fire concurrently within one runtime session
3. Background tasks (scheduler, heartbeat) share the pool

### E2E Pool Tests

| Test | What It Validates |
|------|-------------------|
| Pool size respected | Set `max_pool_size=2`, verify no more than 2 concurrent connections |
| Pool exhaustion queueing | Set `max_pool_size=1`, fire concurrent tool calls → calls queue, don't error |
| Pool recovery | Exhaust pool, wait for connections to return, verify next call succeeds |
| Pool stats | After load, pool reports `free`, `used`, `size` correctly |

### Pool Saturation Test

```python
async def test_pool_exhaustion_queues_gracefully(butler_ecosystem):
    """Tool calls should queue on pool, not crash, when pool is saturated."""
    health = butler_ecosystem["health"]

    # Temporarily reduce pool size
    # (In practice, set via butler.toml or fixture override)

    async with MCPClient(f"http://localhost:{health.port}/sse") as client:
        # Fire many tool calls concurrently
        tasks = [
            client.call_tool("state_set", {"key": f"load-{i}", "value": i})
            for i in range(20)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should succeed (queued, not rejected)
        errors = [r for r in results if isinstance(r, Exception)]
        assert len(errors) == 0, f"Pool saturation caused errors: {errors}"
```

## MCP Transport Overhead

### Client Caching

The switchboard caches MCP clients for each target butler's endpoint:

```python
_ROUTER_CLIENTS: dict[str, tuple[MCPClient, Any]] = {}
```

Client creation involves an HTTP connection and SSE handshake. Caching
eliminates this overhead for repeated routes to the same butler.

### E2E Transport Tests

| Test | What It Validates |
|------|-------------------|
| Client cache hit | Route to health twice → second route reuses cached client |
| Client cache miss | Route to health, invalidate cache, route again → new client created |
| Stale client recovery | Shut down health, restart, route → cached client detected as stale, new client created |
| Client creation latency | Measure time to create new client vs. reuse cached → cache is faster |

## Latency Profiling

### Pipeline Stage Latencies

Each stage of the message pipeline has distinct latency characteristics:

| Stage | Typical Latency | Bottleneck |
|-------|----------------|------------|
| `ingest_v1()` | 5–20ms | DB insert |
| `classify_message()` | 2–5s | LLM API call |
| `dispatch_decomposed()` | 10–50ms | Plan construction |
| `route()` (cached client) | 50–200ms | MCP SSE round-trip |
| `route()` (new client) | 500–2000ms | SSE handshake + round-trip |
| `spawner.trigger()` | 10–60s | LLM API call + tool execution |
| Tool execution | 5–50ms | DB query |

### E2E Latency Tests

| Test | What It Validates |
|------|-------------------|
| Classification latency | `classify_message()` completes within 10s |
| Route latency | `route()` to healthy butler completes within 5s |
| Full pipeline latency | Ingest → classify → dispatch → trigger → response within 120s |
| Tool execution latency | Direct tool call completes within 1s |

### Latency Measurement

```python
async def test_pipeline_latency_budget(butler_ecosystem):
    """Full pipeline should complete within the latency budget."""
    import time

    start = time.monotonic()
    envelope = build_envelope("Log weight 80kg")
    await ingest_v1(switchboard_pool, envelope)
    classification = await classify_message(switchboard_pool, envelope.payload.body, ...)
    await dispatch_decomposed(switchboard_pool, classification, route)
    elapsed = time.monotonic() - start

    assert elapsed < 120, f"Full pipeline took {elapsed:.1f}s (budget: 120s)"

    # Verify session duration aligns
    session = await health_pool.fetchrow(
        "SELECT duration_ms FROM sessions ORDER BY created_at DESC LIMIT 1"
    )
    assert session["duration_ms"] > 0
    assert session["duration_ms"] < 90_000  # 90s max for spawner portion
```

## Benchmarking Baselines

### Establishing Baselines

The first E2E performance run on a codebase should establish baseline metrics.
Subsequent runs compare against these baselines to detect regressions.

### Baseline Metrics

| Metric | Baseline Method | Regression Threshold |
|--------|----------------|---------------------|
| Classification latency | p95 over 10 runs | > 2x baseline |
| Route latency (cached) | p95 over 10 runs | > 3x baseline |
| Full pipeline latency | p95 over 5 runs | > 1.5x baseline |
| Token consumption | Mean over 10 runs | > 1.5x baseline |
| Session cost | Mean over 10 runs | > 2x baseline |

### Baseline Storage

Baselines can be stored as state in the switchboard's KV store or as a JSON
file in the test fixtures:

```json
// tests/e2e/baselines.json
{
    "classify_latency_p95_ms": 4200,
    "route_latency_cached_p95_ms": 180,
    "pipeline_latency_p95_ms": 65000,
    "tokens_per_classification_mean": 1800,
    "cost_per_run_mean_usd": 0.046
}
```

### Regression Detection Test

```python
async def test_no_latency_regression(butler_ecosystem, baselines):
    """Pipeline latency should not regress beyond threshold."""
    latencies = []
    for _ in range(3):
        start = time.monotonic()
        await run_full_pipeline(butler_ecosystem, "Log weight 80kg")
        latencies.append(time.monotonic() - start)

    p95 = sorted(latencies)[int(len(latencies) * 0.95)]
    threshold = baselines["pipeline_latency_p95_ms"] / 1000 * 1.5

    assert p95 < threshold, (
        f"Pipeline p95 latency {p95:.1f}s exceeds regression threshold "
        f"{threshold:.1f}s (baseline: {baselines['pipeline_latency_p95_ms']}ms)"
    )
```

## Resource Consumption

### Memory

Each butler daemon, FastMCP server, and asyncpg pool consumes memory. Under
load, memory grows with:

- Cached MCP clients (one per unique endpoint)
- asyncpg connection pool (up to `max_pool_size` connections)
- In-flight runtime session state
- Accumulated session logs and routing logs

### E2E Resource Tests

| Test | What It Validates |
|------|-------------------|
| Memory stable under load | After 20 sequential triggers, RSS does not grow unboundedly |
| Connection pool cleanup | After ecosystem shutdown, all connections are closed |
| MCP client cleanup | After ecosystem shutdown, all cached clients are closed |
| Log file size bounded | After full suite, log file size < 50MB |

## Cost Under Load

### Token Budget at Scale

| Scale | Messages/Hour | LLM Calls/Hour | Est. Cost/Hour |
|-------|--------------|----------------|----------------|
| 1 user | 10 | 20 | $0.05 |
| 10 users | 100 | 200 | $0.50 |
| 100 users (theoretical) | 1000 | 2000 | $5.00 |

Serial dispatch limits throughput to ~2 sessions/minute per butler. With 6
butlers, the theoretical ceiling is ~720 sessions/hour before queueing delays
become unacceptable.

### E2E Cost-at-Scale Test

```python
async def test_cost_scales_linearly(butler_ecosystem, cost_tracker):
    """Cost should scale linearly with message count (no prompt bloat)."""
    n = 5
    for i in range(n):
        await trigger_full_pipeline(butler_ecosystem, f"Log weight {70 + i}kg")

    # Cost per message should be roughly constant
    cost_per_message = cost_tracker.total_cost / n
    assert cost_per_message < 0.02, (
        f"Cost per message ${cost_per_message:.4f} exceeds budget $0.02"
    )
```
