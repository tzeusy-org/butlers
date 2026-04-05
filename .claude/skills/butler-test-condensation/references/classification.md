# Test Classification Decision Matrix

Use this to decide keep/delete/rewrite for every test during condensation.

## Quick Decision Tree

For each `def test_*` function, walk this tree:

```
1. Does it assert mock.called_with / call_count / call_args?
   YES → DELETE (mock-wiring test, proves nothing)

2. Does it assert an error message STRING (not exception type)?
   YES → DELETE or REWRITE to assert exception type/error code

3. Does it import a private function (from butlers.*._ or _helper)?
   YES → Go to step 4
   NO  → Go to step 5

4. Is the private function's behavior tested through a public API/tool elsewhere?
   YES → DELETE (covered by higher-level test)
   NO  → REWRITE as a public API test covering the same behavior

5. Does it validate an architectural invariant? (schema isolation, MCP-only,
   daemon phases, tool scoping, module boundaries, credential tiers, approval
   gates, shutdown order, session lifecycle, identity resolution, context bus,
   routing pipeline, connector-as-transport, staffer exclusion)
   YES → KEEP as Tier 1. Move to tests/contracts/, add @pytest.mark.contract

6. Does it validate an RFC-defined schema or state machine? (ingest.v1 envelope,
   route inbox states, Module ABC contract, migration outcomes, API response shapes)
   YES → KEEP as Tier 2

7. Does it validate a capability through the MCP tool interface or public API?
   YES → KEEP as Tier 3

8. Does it test a pure helper function (URL builder, string formatter, parser)?
   → Is the helper's logic complex enough to warrant its own test?
     YES (>10 lines, branching logic) → KEEP but consolidate into domain file
     NO  (trivial, <5 lines)          → DELETE (implicit coverage from callers)

9. None of the above → DELETE
```

## Concrete Examples

### KEEP — Tier 1 (Contract)

```python
# tests/contracts/test_schema_isolation.py
@pytest.mark.contract
async def test_butler_cannot_query_other_schema(db_pool):
    """RFC 0006: Per-butler schema isolation.
    
    A butler with search_path=[health, public] must NOT be able to
    read from the finance schema.
    """
    async with db_pool.acquire() as conn:
        await conn.execute("SET search_path TO health, public")
        with pytest.raises(UndefinedTableError):
            await conn.fetch("SELECT * FROM finance.transactions")
```

### KEEP — Tier 2 (Wire Contract)

```python
# tests/core/test_route_inbox.py
async def test_route_inbox_state_machine(db):
    """RFC 0001: Route inbox transitions accepted→processing→processed."""
    inbox = RouteInbox(db)
    entry = await inbox.accept(request_id=uuid7(), payload={...})
    assert entry.status == "accepted"
    
    await inbox.mark_processing(entry.id)
    refreshed = await inbox.get(entry.id)
    assert refreshed.status == "processing"
```

### KEEP — Tier 3 (Capability Behavior)

```python
# tests/modules/memory/test_memory_tools.py
async def test_store_and_recall_fact(memory_module):
    """Validates store+recall roundtrip through MCP tool interface."""
    result = await memory_module.call_tool("memory_store", {
        "subject": "user", "predicate": "favorite_color", "object": "blue"
    })
    assert result["id"] is not None  # structural assertion
    
    recalled = await memory_module.call_tool("memory_recall", {
        "query": "favorite color"
    })
    assert len(recalled["facts"]) >= 1  # structural assertion
```

### DELETE — Mock Wiring

```python
# BEFORE (delete this):
async def test_gmail_api_called_with_correct_params(mock_gmail):
    connector = GmailConnector(mock_gmail)
    await connector.fetch_messages()
    mock_gmail.users().messages().list.assert_called_once_with(
        userId="me", maxResults=100, q="is:unread"
    )
```

### DELETE — Error Message String

```python
# BEFORE (delete this):
async def test_invalid_scope_error_message():
    with pytest.raises(ValueError) as exc:
        await discover_accounts(scopes=[])
    assert "at least one scope" in str(exc.value)  # brittle string match

# AFTER (if keeping): assert exception type only
async def test_invalid_scope_raises_valueerror():
    with pytest.raises(ValueError):
        await discover_accounts(scopes=[])
```

### REWRITE — Internal Helper → Public API

```python
# BEFORE (test_storage_fact.py — imports private function):
from butlers.modules.memory.storage import store_fact
async def test_tags_json_serialized(mock_pool, embedding):
    result = await store_fact(mock_pool, "user", "likes", "cats", embedding, tags=["pet"])
    assert result["tags"] == ["pet"]

# AFTER (test through MCP tool):
async def test_store_fact_with_tags(memory_module):
    result = await memory_module.call_tool("memory_store", {
        "subject": "user", "predicate": "likes", "object": "cats", "tags": ["pet"]
    })
    assert isinstance(result["id"], str)  # structural
    recalled = await memory_module.call_tool("memory_recall", {"query": "likes cats"})
    assert "pet" in recalled["facts"][0].get("tags", [])
```

### KEEP (Consolidate) — Complex Pure Helper

```python
# Keep because _detect_change_type has branching logic (>10 lines, 6 branches).
# But move from 6 separate tests into a parametrized test:
@pytest.mark.parametrize("change,expected", [
    ({"file": {"trashed": True}}, "trashed"),
    ({"file": {"name": "new"}, "prev_name": "old"}, "renamed"),
    ({"file": {"parents": ["a"]}, "prev_parents": ["b"]}, "moved"),
])
def test_detect_change_type(change, expected):
    assert _detect_change_type(change) == expected
```

## "MCP Tool Interface" — What It Means in Practice

The skill says "test through MCP tool interface." In practice this means:

- **For modules**: Call `module.call_tool("tool_name", {args})` using a test fixture
  that initializes the module with a real (test) DB and mocked external services.
  Do NOT mock the DB pool for module tests — use a test database.

- **For connectors**: Test the connector's public methods (`fetch_messages()`,
  `poll_changes()`) with mocked external APIs but real envelope construction.
  Verify the output envelope matches ingest.v1 schema.

- **For API endpoints**: Call the FastAPI test client (`client.get("/api/v1/...")`)
  and validate response with `PydanticModel.model_validate(resp.json())`.

- **For core infrastructure**: Test public methods on Daemon, Scheduler, Spawner,
  StateStore. These can use mocked dependencies but must validate state transitions,
  not mock call sequences.

## Test Marker Categories

When writing or classifying tests, use these markers:

| Marker | Meaning | External deps? | Tier |
|--------|---------|:-:|---:|
| `@pytest.mark.contract` | Architectural invariant | Varies | 1 |
| `@pytest.mark.unit` (or none) | Isolated logic | None | 2-3 |
| `@pytest.mark.integration` | DB/Docker required | Yes | 2-3 |
| `@pytest.mark.e2e` | Full deployment | Yes | 3 |
| `@pytest.mark.nightly` | Real LLM API keys | Yes | 3 |
