# State — KV State Store Testing

## Overview

Every butler has a core KV state store backed by a `state` table with JSONB
values. This is the primary mechanism for butlers to persist data across runtime
sessions — a runtime instance writes state, terminates, and the next runtime instance
reads it back. State E2E tests validate persistence, isolation, JSONB fidelity,
and concurrent access behavior.

## State Store Architecture

### Schema

```sql
CREATE TABLE state (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### MCP Tools

| Tool | Operation | Arguments | Returns |
|------|-----------|-----------|---------|
| `state_get` | Read a value | `key` | JSONB value or `null` |
| `state_set` | Write a value | `key`, `value` | Confirmation |
| `state_list` | List all keys | `prefix?` | Array of `{key, value, updated_at}` |
| `state_delete` | Delete a key | `key` | Confirmation |

### Usage Pattern

```python
# Runtime session 1: Write state
await client.call_tool("state_set", {"key": "user_prefs", "value": {"theme": "dark"}})

# Runtime session 2 (later): Read state back
result = await client.call_tool("state_get", {"key": "user_prefs"})
# → {"theme": "dark"}
```

## Cross-Session Persistence

The primary purpose of the state store is cross-session memory. E2E tests must
validate that state written by one runtime session is readable by the next.

### E2E Cross-Session Tests

| Test | What It Validates |
|------|-------------------|
| Write-then-read | Set a key in session 1, get it in session 2 → same value |
| Write-then-list | Set a key, list → key appears in listing |
| Write-then-delete-then-read | Set, delete, get → returns null |
| Overwrite | Set key to value A, set to value B, get → returns B |
| Timestamp updates | Set, wait, set again → `updated_at` advances |

### Cross-Session Test

```python
async def test_state_persists_across_sessions(butler_ecosystem):
    """State written in one session should be readable in the next."""
    health = butler_ecosystem["health"]

    async with MCPClient(f"http://localhost:{health.port}/sse") as client:
        # Session 1: Write state
        await client.call_tool("state_set", {
            "key": "e2e-test-key",
            "value": {"weight_goal": 75, "unit": "kg"},
        })

    # Simulate a new session by creating a new client
    async with MCPClient(f"http://localhost:{health.port}/sse") as client:
        # Session 2: Read state back
        result = await client.call_tool("state_get", {"key": "e2e-test-key"})
        value = json.loads(result)
        assert value["weight_goal"] == 75
        assert value["unit"] == "kg"
```

## JSONB Value Fidelity

The state store uses PostgreSQL JSONB. This means values are stored as binary
JSON, not text. E2E tests must validate that JSONB round-tripping preserves
data types and structure.

### Type Preservation Tests

| Input Type | Input Value | Expected After Round-Trip |
|-----------|-------------|--------------------------|
| String | `"hello"` | `"hello"` |
| Integer | `42` | `42` (not `42.0`) |
| Float | `3.14` | `3.14` |
| Boolean | `true` | `true` (not `1`) |
| Null | `null` | `null` (not missing) |
| Empty object | `{}` | `{}` |
| Empty array | `[]` | `[]` |
| Nested object | `{"a": {"b": [1, 2]}}` | `{"a": {"b": [1, 2]}}` |
| Large string | 10KB text | Preserved exactly |
| Unicode | `"你好世界"` | `"你好世界"` |
| Special JSON chars | `"line1\nline2\ttab"` | Preserved (escape sequences) |

### JSONB Fidelity Test

```python
async def test_jsonb_type_fidelity(butler_ecosystem):
    """JSONB round-trip should preserve all JSON types exactly."""
    health = butler_ecosystem["health"]

    test_values = {
        "string": "hello",
        "integer": 42,
        "float": 3.14,
        "boolean": True,
        "null_value": None,
        "empty_object": {},
        "empty_array": [],
        "nested": {"a": {"b": [1, 2, 3]}},
        "unicode": "你好世界",
    }

    async with MCPClient(f"http://localhost:{health.port}/sse") as client:
        for type_name, value in test_values.items():
            key = f"e2e-fidelity-{type_name}"
            await client.call_tool("state_set", {"key": key, "value": value})
            result = await client.call_tool("state_get", {"key": key})
            retrieved = json.loads(result)
            assert retrieved == value, (
                f"JSONB fidelity failed for {type_name}: "
                f"expected {value!r}, got {retrieved!r}"
            )
```

## State Isolation Between Butlers

Each butler's state store is in its own database. State keys are not globally
unique — different butlers can use the same key names without conflict.

### E2E Isolation Tests

| Test | What It Validates |
|------|-------------------|
| Same key, different butlers | Health and relationship both set `key="prefs"` → independent values |
| Butler A can't read B's state | Health's state_get cannot access relationship's keys |
| Delete is scoped | Deleting `key="prefs"` on health does not affect relationship's `"prefs"` |

### Isolation Test

```python
async def test_state_isolation_between_butlers(butler_ecosystem):
    """Same key on different butlers should be independent."""
    health = butler_ecosystem["health"]
    relationship = butler_ecosystem["relationship"]

    async with MCPClient(f"http://localhost:{health.port}/sse") as h_client:
        await h_client.call_tool("state_set", {
            "key": "shared-key-name",
            "value": {"source": "health"},
        })

    async with MCPClient(f"http://localhost:{relationship.port}/sse") as r_client:
        await r_client.call_tool("state_set", {
            "key": "shared-key-name",
            "value": {"source": "relationship"},
        })

    # Each butler's value should be independent
    async with MCPClient(f"http://localhost:{health.port}/sse") as h_client:
        result = json.loads(await h_client.call_tool("state_get", {"key": "shared-key-name"}))
        assert result["source"] == "health"

    async with MCPClient(f"http://localhost:{relationship.port}/sse") as r_client:
        result = json.loads(await r_client.call_tool("state_get", {"key": "shared-key-name"}))
        assert result["source"] == "relationship"
```

## Prefix Listing

The `state_list` tool supports an optional `prefix` parameter for namespace-
style key organization:

```python
# Keys: "health:prefs", "health:goals", "health:history", "general:prefs"
result = await client.call_tool("state_list", {"prefix": "health:"})
# → only keys starting with "health:" returned
```

### E2E Prefix Tests

| Test | What It Validates |
|------|-------------------|
| Prefix filter | Set keys with different prefixes, list with prefix → only matching keys |
| Empty prefix | List with no prefix → all keys returned |
| No match | List with nonexistent prefix → empty result |
| Prefix is literal | Prefix `"health"` does not match `"healthcare:..."` (no wildcard) |

## Concurrent State Access

Runtime sessions are serialized per butler (serial dispatch lock), but state tools
can be called concurrently from the MCP client during a session (multiple tool
calls in flight). The state store must handle this correctly.

### Concurrency Scenarios

| Scenario | Expected Behavior |
|----------|-------------------|
| Concurrent reads of same key | Both return the same value (no read anomaly) |
| Concurrent writes to same key | Last writer wins (PostgreSQL row-level lock) |
| Read during write | Read returns either old or new value (snapshot isolation) |
| Delete during read | Read may return value or null (snapshot isolation) |

### E2E Concurrency Test

```python
async def test_concurrent_state_writes(butler_ecosystem):
    """Concurrent writes to the same key should not corrupt data."""
    health = butler_ecosystem["health"]

    async def write_value(value: int):
        async with MCPClient(f"http://localhost:{health.port}/sse") as client:
            await client.call_tool("state_set", {
                "key": "concurrent-test",
                "value": {"counter": value},
            })

    # Fire concurrent writes
    await asyncio.gather(*[write_value(i) for i in range(10)])

    # Read final value — should be one of the written values (last writer wins)
    async with MCPClient(f"http://localhost:{health.port}/sse") as client:
        result = json.loads(
            await client.call_tool("state_get", {"key": "concurrent-test"})
        )
        assert result["counter"] in range(10)
```

## State Store as Session Memory

A common pattern is for runtime sessions to use the state store as working memory:

1. Runtime instance reads `state_get("conversation-context")` at session start
2. Runtime instance does work, calls domain tools
3. Runtime instance writes `state_set("conversation-context", updated_context)` at session end
4. Next runtime session reads the updated context

### E2E Session Memory Pattern Test

```python
async def test_state_as_session_memory(butler_ecosystem):
    """State store should serve as persistent memory between runtime sessions."""
    health = butler_ecosystem["health"]

    # Session 1: Runtime instance writes context
    result1 = await health.spawner.trigger(
        prompt="Remember that my weight goal is 75kg. Store this in state.",
        trigger_source="test",
    )
    assert result1.success

    # Session 2: Runtime instance reads context
    result2 = await health.spawner.trigger(
        prompt="What is my weight goal? Check the state store.",
        trigger_source="test",
    )
    assert result2.success
    # Note: LLM-dependent — assert loosely
    assert "75" in (result2.output or "")
```
