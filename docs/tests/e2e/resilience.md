# Resilience — Failure Injection and Graceful Degradation

## Overview

The Butlers ecosystem is a distributed system: multiple butler daemons
communicating over HTTP/SSE, each with its own database, spawning ephemeral
LLM sessions that may fail, time out, or produce unexpected output. Resilience
testing validates that the system degrades gracefully under every failure mode
rather than cascading into hard failures.

This document specifies the failure modes, injection techniques, expected
degradation behaviors, and validation strategies for resilience E2E tests.

## Failure Taxonomy

### Layer 1: Infrastructure Failures

| Failure Mode | Injection | Expected Behavior |
|-------------|-----------|-------------------|
| PostgreSQL unavailable | Stop testcontainer mid-test | `Database.connect()` raises, butler fails to start |
| PostgreSQL connection exhaustion | Set `max_pool_size=1`, fire concurrent requests | Requests queue on pool, timeout after configurable wait |
| PostgreSQL slow queries | `pg_sleep()` advisory locks | Tool calls timeout, session logged as `error` |
| Docker daemon unresponsive | Not injectable in-process (CI-only) | Testcontainer patches retry with backoff |

### Layer 2: Butler Daemon Failures

| Failure Mode | Injection | Expected Behavior |
|-------------|-----------|-------------------|
| Butler process crash | `daemon.shutdown()` or `os.kill()` mid-request | Switchboard gets `ConnectionError`, logs `target_unavailable` |
| Butler port unreachable | Shut down FastMCP server, keep daemon alive | MCPClient health check fails, reconnect attempted |
| Butler startup failure | Invalid `butler.toml` or missing migration | Daemon logs error, does not register in `butler_registry` |
| Slow butler startup | Inject `asyncio.sleep()` in `on_startup` | Ecosystem bootstrap timeout, other butlers unaffected |

### Layer 3: MCP Transport Failures

| Failure Mode | Injection | Expected Behavior |
|-------------|-----------|-------------------|
| SSE connection drop | Close server socket during tool call | Client detects disconnect, retries or returns error |
| Stale cached client | Invalidate `_ROUTER_CLIENTS` entry between calls | Health check detects staleness, creates new client |
| MCP tool timeout | Target tool blocks indefinitely | Caller's `asyncio.wait_for()` fires, returns timeout error |
| Malformed tool response | Mock tool returns invalid JSON | MCP client raises, switchboard logs routing failure |

### Layer 4: LLM / Spawner Failures

| Failure Mode | Injection | Expected Behavior |
|-------------|-----------|-------------------|
| API key invalid | Override `ANTHROPIC_API_KEY` with garbage | Spawner returns `error` result, session logged with error |
| API rate limit | Trigger 429 response (hard to inject) | Adapter retries with backoff (SDK-level) |
| LLM timeout | Set very low timeout on adapter | Spawner returns timeout error, session logged |
| Classification parse failure | LLM returns non-JSON | `classify_message()` falls back to `general` butler |
| Empty classification | LLM returns `[]` | Fallback to `general` with original text |
| Tool call to nonexistent tool | LLM hallucinates a tool name | MCP server returns `ToolNotFoundError`, runtime retries |

### Layer 5: Cross-Butler Failures

| Failure Mode | Injection | Expected Behavior |
|-------------|-----------|-------------------|
| Target butler down during dispatch | Kill target after classification | `dispatch_decomposed()` logs failure, other subrequests continue (abort policy: `continue`) |
| All butlers down | Kill all non-switchboard butlers | All subrequests fail, aggregation returns error summary |
| Circular routing | Butler A routes to B routes to A | Depth limit or cycle detection prevents infinite loop |
| Registry stale | Butler registered but endpoint changed | Route fails, registry marks butler as quarantined |

## Serial Dispatch Lock Contention

The spawner enforces a serial dispatch lock — one runtime session at a time per
butler. This is a critical concurrency boundary:

### What the Lock Protects

- Database connection pool is sized for a single runtime session's tool calls
- Butler's MCP tool state is not designed for concurrent access
- Session logging assumes sequential session IDs

### Contention Scenarios

| Scenario | Expected Behavior |
|----------|-------------------|
| Two concurrent `trigger()` calls | Second caller blocks on `asyncio.Lock`, executes after first completes |
| `trigger()` while tick is running | Tick's trigger blocks until the external trigger's session completes |
| Lock holder crashes (runtime session hangs) | Lock held indefinitely until timeout; subsequent callers blocked |
| Lock holder times out | `asyncio.wait_for()` on the lock, timeout releases and logs error |

### Test Approach

```python
async def test_serial_dispatch_contention(butler_ecosystem):
    """Two concurrent triggers should execute serially, not fail."""
    health = butler_ecosystem["health"]

    # Fire two triggers concurrently
    results = await asyncio.gather(
        health.spawner.trigger("Log weight 80kg", trigger_source="test-1"),
        health.spawner.trigger("Log weight 75kg", trigger_source="test-2"),
    )

    # Both should succeed (serial execution)
    assert all(r.success for r in results)

    # Sessions should be sequential (non-overlapping timestamps)
    sessions = await health.pool.fetch(
        "SELECT created_at, completed_at FROM sessions ORDER BY created_at"
    )
    assert sessions[0]["completed_at"] <= sessions[1]["created_at"]
```

## Switchboard Degradation

The switchboard is the single entry point for all external messages. Its
degradation behavior under failure is the most critical resilience property.

### Classification Failure Fallback

When the classification LLM fails (timeout, parse error, empty response), the
switchboard falls back to routing the entire message to the `general` butler:

```python
# Fallback behavior in classify_message()
except (json.JSONDecodeError, KeyError, TimeoutError):
    return [{"butler": "general", "prompt": original_text, "segment": {"rationale": "Fallback"}}]
```

**Test:** Inject a classification failure (mock spawner returns garbage) and
assert that the message reaches `general` with the original text intact.

### Partial Dispatch Failure

When dispatching a multi-domain message, some target butlers may be unavailable:

| Abort Policy | Behavior on Partial Failure |
|-------------|---------------------------|
| `continue` | Remaining subrequests execute, failed ones logged |
| `on_any_failure` | All remaining subrequests cancelled |
| `on_required_failure` | Cancel only if the failed subrequest was marked `required` |

**Test:** Decompose a multi-domain message, kill one target butler before
dispatch, verify that the other target butler still receives and processes its
segment.

### Registry Quarantine

When a butler fails routing attempts repeatedly, the registry can mark it as
quarantined:

```
eligibility_state: active → quarantined
```

Quarantined butlers are excluded from classification context, so the LLM stops
routing to them. This is a self-healing mechanism — the heartbeat butler's tick
can re-activate quarantined butlers when they respond to health checks.

**Test:** Route to a butler, kill it, route again (triggers quarantine), restart
it, verify that heartbeat tick re-activates it.

## Module Failure Isolation

Module failures must not cascade to the butler's core functionality or to other
modules.

### Startup Failure Isolation

```python
# daemon.py module startup sequence
for module in topological_order(modules):
    try:
        await module.on_startup(config, db)
        _module_statuses[module.name] = {"status": "active"}
    except Exception as exc:
        _module_statuses[module.name] = {"status": "failed", "error": str(exc)}
        # Butler continues with remaining modules
```

**Test:** Configure a module with invalid credentials, verify the butler starts
successfully, and verify that other modules' tools are registered and functional.

### Runtime Tool Failure Isolation

If a module's MCP tool raises an exception during invocation, the exception is
caught by the FastMCP framework and returned as a tool error to the runtime session.
The butler daemon itself is unaffected:

**Test:** Call a module tool that raises, then call a core tool on the same
butler. The core tool should succeed.

### Dependency Chain Failure

Modules declare dependencies via `dependencies` property. If module A depends on
module B, and B fails to start, A is also skipped:

**Test:** Fail module B (invalid credentials), verify that both B and A are
reported as `failed` in the status, and that modules with no dependency on B
are unaffected.

## Timeout Behavior

### Per-Layer Timeouts

| Layer | Timeout | Configurable? | Default |
|-------|---------|---------------|---------|
| MCP tool call (client side) | `asyncio.wait_for()` | Yes (per route call) | 120s |
| Spawner runtime session | Adapter timeout | Yes (butler.toml) | 120s |
| Database query | asyncpg statement timeout | Yes (pool config) | 30s |
| Classification LLM call | Spawner timeout | Yes | 120s |
| Ecosystem bootstrap | Fixture-level timeout | Yes (pytest timeout) | 300s |

### Timeout Cascade

When a timeout fires at one layer, it must propagate cleanly:

1. Spawner timeout → session logged with `error="timeout"`, lock released
2. Route timeout → `routing_log` entry with `status="timeout"`, dispatch
   continues to next subrequest
3. DB timeout → tool returns error to the runtime instance, which may retry or report failure
4. Classification timeout → fallback to `general` butler

**Test:** Set an artificially low timeout on the spawner, trigger a prompt that
requires multiple tool calls, verify that the session is logged with a timeout
error and the serial dispatch lock is released for subsequent requests.

## Chaos Testing Patterns

### Butler Kill-and-Recover

```python
async def test_kill_and_recover(butler_ecosystem):
    """Kill a butler, verify switchboard handles it, restart, verify recovery."""
    health = butler_ecosystem["health"]

    # Verify healthy
    assert await route(switchboard_pool, "health", "status", {})

    # Kill
    await health.daemon.shutdown()

    # Route should fail gracefully
    result = await route(switchboard_pool, "health", "trigger", {"prompt": "test"})
    assert result["error"] == "target_unavailable"

    # Restart
    await health.daemon.start()

    # Route should succeed again
    result = await route(switchboard_pool, "health", "status", {})
    assert result is not None
```

### Connection Pool Exhaustion

```python
async def test_pool_exhaustion(butler_ecosystem):
    """Exhaust DB pool, verify tool calls queue and eventually succeed."""
    health = butler_ecosystem["health"]

    # Hold all connections
    async with health.pool.acquire() as conn1:
        async with health.pool.acquire() as conn2:
            # Next tool call should block on pool, not crash
            result = await asyncio.wait_for(
                health_tool_call("measurement_log", {...}),
                timeout=10.0,
            )
            # May timeout, but should not raise an unhandled exception
```

### Cascading Failure Prevention

The most important resilience property: a failure in one butler must not affect
other butlers or the switchboard itself.

**Test:** Crash the health butler, immediately send a message to the relationship
butler via the switchboard. The relationship butler should process it normally.
The switchboard's routing log should show `success` for relationship and
`target_unavailable` for health.

## Validation Strategies

### Health Check Assertions

```python
# After failure injection
status = await client.call_tool("status", {})
assert status["butler"] == "health"
assert status["daemon"]["uptime_seconds"] > 0
assert status["modules"]["telegram"]["status"] == "failed"  # expected
```

### Routing Log Assertions

```python
# After routing to an unavailable butler
row = await switchboard_pool.fetchrow(
    "SELECT * FROM routing_log WHERE target_butler = $1 ORDER BY created_at DESC",
    "health",
)
assert row["status"] in ("error", "timeout", "target_unavailable")
assert row["error_class"] is not None
```

### Session Log Assertions

```python
# After a spawner timeout
row = await health_pool.fetchrow(
    "SELECT * FROM sessions ORDER BY created_at DESC LIMIT 1"
)
assert row["error"] is not None
assert "timeout" in row["error"].lower()
assert row["duration_ms"] >= timeout_ms  # ran until timeout
```

### Lock Release Assertions

```python
# After a failed session, verify lock is released
result = await health.spawner.trigger("simple prompt", trigger_source="test")
assert result.success  # would hang if lock wasn't released
```
