# Test Classification Decision Matrix

Use this to decide keep/delete/rewrite for every test during condensation.

## Quick Decision Tree

For each `def test_*` function, walk this tree:

```
1. Does it assert mock.called_with / call_count / assert_(not_)called / assert_(not_)awaited?
   → NOT auto-delete. Apply the plumbing-vs-contract test below (§1).
     plumbing (call assertion duplicates an already-asserted result) → DELETE the assertion
     contract (the call / no-call / count IS the only proof of an invariant) → KEEP

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

## §1. Mock-call assertions — plumbing vs. contract

**This is the single most error-prone classification node.** A 22-auditor pass
found the old blanket "DELETE all mock-call assertions" rule produced many FALSE
POSITIVES. `assert_called*`, `assert_not_called`, `assert_not_awaited`, and
`call_count` frequently encode REAL behavior contracts. Distinguish:

**call-assertion-as-PLUMBING → DELETE the assertion (keep the test):**
The test already asserts the behavioral RESULT (returned value, DB row, envelope
shape), and the call assertion just re-checks that an internal collaborator was
invoked with exact positional args. It restates the implementation.
```python
# The result assertion already proves the fetch happened. The call assertion is plumbing.
msgs = await connector.fetch_messages()
assert len(msgs) == 2                                   # KEEP — behavioral result
mock_gmail.users().messages().list.assert_called_once_with(
    userId="me", maxResults=100, q="is:unread")         # DELETE — plumbing
```

**call-assertion-as-CONTRACT → KEEP (the call/no-call/count is the ONLY proof):**
No other assertion can express the invariant. Common cases in this codebase:
- **Idempotency / no-duplicate-INSERT** — `owner_bootstrap`, `route_inbox`:
  `mock_insert.assert_called_once()` / `assert_not_called()` on the second run is
  the only proof the op didn't double-write.
- **Delivery / retry cadence** — `port_retry`, `interaction_sync`, liveness
  heartbeat: `call_count == 3` proves the retry schedule; no return value shows it.
- **Forced-channel resolver bypass** — a forced channel must SKIP identity
  resolution: `resolver.assert_not_called()` is the bypass proof.
- **Safety: email-not-sent-on-revoked** — `send.assert_not_awaited()` proves no
  message left the system after a revoked token (a sent side-effect has no
  inspectable return).
- **Canonical-fact-store BOUNDARY (heart-and-soul layering)** —
  `store_fact.assert_not_called()` proves identity/relational facts were
  intercepted BEFORE delegation to the generic memory store. The no-call IS the
  architectural boundary.

**Litmus test:** *If I delete this call assertion, does any surviving assertion
still fail when the invariant is violated?* If YES → it was plumbing, delete it.
If NO (the call assertion is the only thing standing between green and a real
regression) → it's a contract, KEEP it. When unsure, KEEP.

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

### DELETE — Mock Wiring (plumbing only)

```python
# BEFORE (delete this — the call assertion is the WHOLE test, and it only
# restates the implementation's call args; no behavioral result is checked):
async def test_gmail_api_called_with_correct_params(mock_gmail):
    connector = GmailConnector(mock_gmail)
    await connector.fetch_messages()
    mock_gmail.users().messages().list.assert_called_once_with(
        userId="me", maxResults=100, q="is:unread"
    )
```

### KEEP — Call assertion as contract (do NOT delete)

```python
# Idempotency: bootstrapping the owner twice must NOT insert a second row.
# The assert_called_once IS the only proof; deleting it hides a real regression.
async def test_owner_bootstrap_idempotent(mock_pool):
    await bootstrap_owner(mock_pool)
    await bootstrap_owner(mock_pool)               # second call
    assert mock_pool.execute.call_count == 1       # contract — KEEP

# Boundary: identity facts are intercepted before the generic memory store.
async def test_identity_fact_bypasses_memory_store(resolver, store_fact_mock):
    await resolver.handle({"predicate": "telegram_id", ...})
    store_fact_mock.assert_not_called()            # heart-and-soul layering — KEEP
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
