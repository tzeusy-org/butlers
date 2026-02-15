# Flows — End-to-End Message Flows and Validation

## Overview

This document specifies every end-to-end message flow that the harness validates.
Each flow is defined by its mock input, the full simulated butler pipeline it
traverses, the expected side effects it produces, and the assertions that
validate those side effects.

## The Canonical Message Flow

Every user-facing message follows the same canonical pipeline. Individual test
scenarios exercise different segments of this pipeline, but the complete path
is:

```
1. Test builds IngestEnvelopeV1 (mock Telegram/Email/API message)
         │
         ▼
2. ingest_v1(switchboard_pool, envelope)
   → Validates envelope against IngestEnvelopeV1 contract
   → Computes dedupe_key from source identity + idempotency key
   → Persists to message_inbox (INSERT ... ON CONFLICT for idempotency)
   → Returns IngestAcceptedResponse { request_id, status, duplicate }
         │
         ▼
3. classify_message(switchboard_pool, text, spawner.trigger)
   → Reads butler_registry table for available butlers + capabilities
   → Composes classification prompt with registry context
   → Spawns Claude Code (Haiku) on switchboard butler
   → LLM returns JSON: [{butler, prompt, segment}, ...]
   → Validates response structure (_CLASSIFICATION_ENTRY_KEYS)
   → Validates segment metadata (_SEGMENT_KEYS)
   → Fallback: on parse failure, routes to "general" with original text
         │
         ▼
4. dispatch_decomposed(switchboard_pool, classification, route_fn)
   → Builds FanoutPlan from classification entries
   → For each FanoutSubrequestPlan:
     → Resolves routing target from butler_registry
     → Validates route contract version
     → Checks eligibility state (active, not quarantined)
     → route(pool, target_butler, "trigger", {prompt: ...})
       → Gets or creates cached MCPClient for target endpoint
       → Health-checks existing client connection
       → Injects TRACEPARENT for distributed tracing
       → Calls target butler's "trigger" MCP tool via SSE
   → Respects fanout mode (parallel | ordered | conditional)
   → Respects join policy (wait_for_all | first_success)
   → Respects abort policy (continue | on_required_failure | on_any_failure)
   → Logs execution to fanout_execution_log table
         │
         ▼
5. Target butler's trigger() tool
   → Spawner acquires serial dispatch lock (one CC session at a time)
   → Generates locked-down MCP config pointing exclusively at this butler
   → Reads CLAUDE.md system prompt from roster/{butler}/CLAUDE.md
   → Optionally appends memory context (if memory module active)
   → Builds restricted env dict (only declared credentials, ANTHROPIC_API_KEY)
   → Invokes ClaudeCodeAdapter with Haiku model
   → CC instance calls domain tools (measurement_log, contact_add, etc.)
   → Domain tools execute SQL against the butler's own database
   → Session recorded: session_create() before invocation, session_complete() after
         │
         ▼
6. Response aggregation (if multi-butler dispatch)
   → aggregate_responses() combines results from multiple butlers
   → Conflict arbitration by priority / butler name
   → Optional CC synthesis for multi-butler responses
   → Fallback: concatenation if synthesis unavailable
         │
         ▼
7. Test validates:
   → DB rows exist in expected butler databases
   → Session logged in sessions table with correct metadata
   → routing_log in switchboard DB shows success status
   → fanout_execution_log records subrequest outcomes
   → No unhandled exceptions in application log
```

## Mock Input Construction

### IngestEnvelopeV1

All E2E tests that exercise the full pipeline start by constructing an
`IngestEnvelopeV1` — the canonical ingestion contract. This is a Pydantic model
with strict validation:

```python
envelope = IngestEnvelopeV1(
    schema_version="ingest.v1",
    source=SourceDescriptor(
        channel="telegram",           # telegram | slack | email | api | mcp
        provider="telegram",          # must be valid for channel
        endpoint_identity="bot_123",  # bot or account identifier
        sender_identity="user_456",   # who sent the message
    ),
    payload=PayloadDescriptor(
        content_type="text/plain",
        body="Log my weight: 80kg",
        sent_at="2026-02-16T10:00:00Z",  # RFC3339 with timezone
    ),
    # Optional fields:
    idempotency_key="unique-key-123",    # for deduplication
    thread_target=None,                   # for reply threading
    notification_preferences=None,        # response delivery config
    routing_hints=None,                   # pre-classification hints
    metadata=None,                        # opaque passthrough
)
```

**Validation guarantees enforced by the contract:**
- `schema_version` must be exactly `"ingest.v1"`
- `channel` must be a valid `SourceChannel` literal
- `provider` must be valid for the given `channel` (e.g., `telegram` channel
  only accepts `telegram` provider)
- `sent_at` must be RFC3339 with timezone offset (not naive datetime)
- `idempotency_key`, if provided, drives deduplication via unique index

### Simplified Test Inputs

For tests that skip the ingestion layer and test classification or routing
directly, the input is a plain text prompt string:

```python
# Classification-only test
result = await classify_message(pool, "Log my weight: 80kg", spawner.trigger)
```

```python
# Direct butler trigger (skip switchboard entirely)
result = await spawner.trigger(prompt="Log weight 80kg", trigger_source="test")
```

This layered approach lets tests target specific pipeline segments without
paying the cost of unnecessary upstream processing.

## Declarative Scenario Registry

### E2EScenario Dataclass

Simple input-output test cases are defined as `E2EScenario` instances. Each
scenario is a complete specification of a single-input flow:

```python
@dataclass
class E2EScenario:
    id: str                              # Unique scenario identifier
    description: str                     # Human-readable description
    input_prompt: str                    # The message text to classify/route
    expected_butler: str                 # Which butler should handle it
    expected_tool_calls: list[str]       # Tools the butler should call
    db_assertions: list[DbAssertion]    # Expected database side effects
    tags: tuple[str, ...]               # For filtering (pytest -k)
    timeout_seconds: int = 120          # Per-scenario timeout
    skip_reason: str | None = None      # Conditional skip
```

### DbAssertion Dataclass

Database side effects are specified as `DbAssertion` instances. Each assertion
checks that at least one row exists in a specific table with specific column
values:

```python
@dataclass
class DbAssertion:
    butler: str                          # Which butler's database
    table: str                           # Table name
    where: dict[str, Any]               # WHERE clause (column: value)
    column_checks: dict[str, Any]       # Additional column value assertions
```

**Assertion semantics:** `DbAssertion` checks for _existence_, not _uniqueness_.
If multiple rows match the `where` clause, the assertion passes as long as at
least one of them satisfies all `column_checks`. This accounts for LLM
non-determinism in how data is structured.

### Current Scenario Registry

#### Switchboard Classification Scenarios

These scenarios validate that the switchboard's LLM-mediated classification
routes messages to the correct butler. They do not validate tool execution or
database side effects — only routing correctness.

| Scenario ID | Input Prompt | Expected Butler | Tags |
|------------|-------------|-----------------|------|
| `switchboard-classify-health` | "Log my weight: 80kg" | health | switchboard, classify, smoke |
| `switchboard-classify-general` | "What's the weather today?" | general | switchboard, classify, smoke |
| `switchboard-multi-domain` | "I saw Dr. Smith and need to send her a thank-you card" | health, relationship | switchboard, classify, decomposition |

**Assertion strategy:** The test calls `classify_message()` and asserts that the
`expected_butler` appears in the returned routing entries. For multi-domain
messages, it asserts that _all_ expected butlers appear.

**What is NOT asserted:** The exact prompt text, segment offsets, or rationale.
These are LLM-generated and vary between runs. Only the structural routing
decision (which butler) is validated.

#### Health Butler Scenarios

These scenarios validate the full pipeline from classification through tool
execution to database persistence.

| Scenario ID | Input Prompt | Expected Tool Calls | Database Assertion |
|------------|-------------|--------------------|--------------------|
| `health-weight-log` | "Log my weight: 80kg" | `measurement_log` | `measurements` table: type contains "weight" |
| `health-medication-add` | "Started taking metformin 500mg twice daily" | `medication_add` | `medications` table: name contains "metformin" |

**Assertion strategy:**
1. Classify → assert routes to `health`
2. Dispatch → assert `route()` succeeds (no MCP transport error)
3. Session → assert `sessions` table has a row with `trigger_source="external"`
4. Side effect → assert domain table has at least one matching row

**Why loose matching on text fields:** The LLM determines what values to pass
to domain tools. "Log my weight: 80kg" might result in a measurement with
`type="weight"` or `type="body_weight"` or `type="Weight"`. The assertion uses
case-insensitive containment (`ILIKE '%weight%'`) rather than exact equality.

#### Relationship Butler Scenarios

| Scenario ID | Input Prompt | Expected Tool Calls | Database Assertion |
|------------|-------------|--------------------|--------------------|
| `relationship-add-contact` | "Add Sarah Johnson as a new contact" | `contact_add` | `contacts` table: name contains "Sarah" |

**Same assertion strategy as health scenarios.**

## Complex Flow Tests

For scenarios that go beyond single-input-single-output, dedicated test modules
orchestrate multi-step interactions.

### test_ecosystem_health.py — Smoke Tests

**LLM calls: 0** (no Haiku invocations)

These tests validate that the ecosystem booted correctly before any expensive
LLM-dependent tests run.

| Test | What It Validates |
|------|-------------------|
| Port liveness | Every butler's SSE endpoint responds to HTTP |
| Core tables | `state`, `scheduled_tasks`, `sessions` tables exist in every DB |
| Butler-specific tables | Domain tables exist (measurements, contacts, etc.) |
| Butler registry | `butler_registry` table in switchboard DB has all butlers |
| Module status | Expected modules are `active` or `failed` with correct phase |

**Failure mode:** If any smoke test fails, the entire E2E session should be
considered invalid. Smoke tests run first (alphabetically by module name or via
explicit ordering).

### test_switchboard_flow.py — Classification and Dispatch

**LLM calls: 3–4**

This module tests the switchboard's core responsibilities: classification,
decomposition, deduplication, and dispatch.

| Test | Flow | Assertions |
|------|------|------------|
| Single-domain classification | text → `classify_message()` → routing entries | Correct butler in result |
| Multi-domain decomposition | compound text → `classify_message()` → multiple entries | Multiple butlers, each with self-contained prompt |
| Deduplication | Same message ingested twice → `ingest_v1()` × 2 | Second call returns `duplicate=True`, same `request_id` |
| Full dispatch | text → classify → dispatch → route to target | `routing_log` has success entry, target butler session logged |

**Multi-domain decomposition detail:**

The classification LLM is expected to split a compound message like _"I saw
Dr. Smith today and got prescribed metformin. Remind me to send her a thank-you
card."_ into two routing entries:

1. `{butler: "health", prompt: "...", segment: {...}}` — medication tracking
2. `{butler: "relationship", prompt: "...", segment: {...}}` — social follow-up

The test validates:
- Both `health` and `relationship` appear in the routing entries
- Each prompt is self-contained (not a fragment referencing the other)
- Each segment has valid metadata (at least one of `sentence_spans`, `offsets`,
  `rationale`)

### test_health_flow.py — Health Butler Domain Flows

**LLM calls: 2–3**

| Test | Flow | Assertions |
|------|------|------------|
| Direct tool call | Call `measurement_log` MCP tool directly (no LLM) | Row in `measurements` table |
| Spawner trigger | `spawner.trigger("Log weight 80kg")` | Session logged, row in `measurements` |
| Full ingest → route | `ingest_v1()` → classify → dispatch → health trigger | Full pipeline validated end-to-end |
| Multi-measurement | "Weight 80kg, blood pressure 120/80" | Multiple rows in `measurements` |

**Direct tool call detail:**

This test bypasses the LLM entirely and calls the butler's MCP tool directly:

```python
async with MCPClient("http://localhost:8103/sse") as client:
    result = await client.call_tool("measurement_log", {
        "type": "weight",
        "value": 80.0,
        "unit": "kg",
    })
```

This validates that the tool's SQL is correct and the migration schema matches
the tool's expectations, independent of LLM behavior.

### test_relationship_flow.py — Relationship Butler Domain Flows

**LLM calls: 1**

| Test | Flow | Assertions |
|------|------|------------|
| Contact creation via spawner | `spawner.trigger("Add Sarah Johnson as a contact")` | Session logged, row in `contacts` |
| Full ingest → route | `ingest_v1()` → classify → dispatch → relationship trigger | Pipeline validated, `routing_log` + `contacts` |

### test_cross_butler.py — Cross-Butler Interactions

**LLM calls: 2–3**

These tests validate interactions that span multiple butlers — the most complex
and integration-sensitive flows.

| Test | Flow | Assertions |
|------|------|------------|
| Heartbeat tick | `_tick(heartbeat_pool)` → calls `trigger()` on all registered butlers | Each butler has a new session with `trigger_source="heartbeat"` |
| Full e2e message flow | Mock Telegram message → ingest → classify → dispatch → butler tools → DB | Complete pipeline from external input to database side effects |

**Heartbeat tick detail:**

The heartbeat butler's `tick()` function reads scheduled tasks, finds due ones,
and calls `trigger()` on target butlers. In the E2E harness, this validates:

1. The heartbeat butler can read the `butler_registry` from the switchboard
2. The heartbeat butler can reach each target butler via MCP
3. Each target butler's spawner fires and records a session
4. The session's `trigger_source` is `"heartbeat"` (not `"external"`)

**Full e2e message flow detail:**

This is the most comprehensive test. It exercises every layer of the canonical
pipeline:

```
Mock Telegram IngestEnvelopeV1
    → ingest_v1() persists to message_inbox
    → classify_message() LLM classifies to correct butler
    → dispatch_decomposed() routes via MCP
    → target butler trigger() spawns CC
    → CC calls domain tools
    → domain tools write to DB
    → test queries both switchboard DB (routing_log) and target DB (domain table)
```

Assertions span two databases:
- **Switchboard DB:** `routing_log` has entry with `status="success"`,
  `target_butler` matches expected, `fanout_execution_log` records the
  subrequest outcome
- **Target butler DB:** Domain-specific table has at least one matching row,
  `sessions` table has entry with correct `trigger_source` and non-zero
  `duration_ms`

## Assertion Strategies

### Structural Assertions (Deterministic)

These assertions validate infrastructure behavior that is fully deterministic
and must always pass exactly:

| Assertion | Example | Tolerance |
|-----------|---------|-----------|
| Table exists | `information_schema.tables WHERE table_name = 'measurements'` | Exact |
| Port responds | HTTP 200 on butler's SSE endpoint | Exact |
| Session logged | Row in `sessions` with matching `trigger_source` | Exact |
| Routing logged | Row in `routing_log` with `status='success'` | Exact |
| Module status | `status.modules.telegram.status == "failed"` | Exact |

### Content Assertions (LLM-Dependent)

These assertions validate outcomes that depend on LLM output and are inherently
non-deterministic:

| Assertion | Example | Tolerance |
|-----------|---------|-----------|
| Correct butler | `"health"` appears in classification routing entries | Set membership |
| Tool was called | `"measurement_log"` appears in session's `tool_calls` | Set membership |
| Row exists | `measurements` table has row with `type ILIKE '%weight%'` | Case-insensitive containment |
| Value range | `measurements.value` is between 75 and 85 | Numeric range |
| Multi-domain split | Both `"health"` and `"relationship"` in routing | All-of set membership |

### Negative Assertions

Some scenarios validate that certain things _do not_ happen:

| Assertion | What It Validates |
|-----------|-------------------|
| No duplicate ingest | Second `ingest_v1()` with same idempotency key returns `duplicate=True` |
| No cross-DB writes | Health butler tools do not write to relationship DB |
| No unhandled exceptions | Application log contains no `ERROR`-level entries for the test duration |
| Module isolation | Failed module does not prevent core tools from working |

## LLM Non-Determinism

LLM responses are inherently non-deterministic. The test suite handles this
with four strategies:

### 1. Loose Assertions

Classification tests assert the expected butler _appears_ in the routing result,
not that the exact prompt or segment metadata matches a template.

### 2. DB-Level Validation

Side-effect tests check that _some_ row exists in the expected table, not that
the exact values match. Case-insensitive containment (`ILIKE`) is used for text
fields. Numeric fields use range checks where exact values cannot be predicted.

### 3. Timeout Tolerance

Each LLM scenario has a configurable `timeout_seconds` (default 120s) to account
for API latency variance. The timeout covers the full round-trip: spawner lock
acquisition, MCP config generation, API call, tool execution, session logging.

### 4. Retry-Friendly Isolation

Individual scenarios can be re-run in isolation without ecosystem restart. The
`butler_ecosystem` fixture is session-scoped, so the butlers, databases, and
MCP servers persist across test re-runs within the same session. A flaking
scenario can be re-run with `-k "scenario-id"` without the 20-second ecosystem
bootstrap cost.

### Debugging Flakes

If a scenario flakes due to LLM variance, the structured log contains:
- The full classification prompt sent to the LLM
- The raw classification response (JSON)
- The parsed routing entries
- The dispatch plan (fanout mode, subrequests)
- The route call results (success/failure per butler)
- The spawner invocation log (prompt, model, duration, tool_calls)

```bash
# Find classification results for a specific test run
grep 'classify_message.*result' .tmp/e2e-logs/e2e-latest.log

# Find tool calls made by the spawner
grep 'tool_span.*measurement_log\|tool_span.*contact_add' .tmp/e2e-logs/e2e-latest.log
```

## Adding New Flows

### Adding a Declarative Scenario

For single-input test cases, append an `E2EScenario` to `scenarios.py`:

```python
E2EScenario(
    id="health-symptom-log",
    description="Log a headache symptom with severity",
    input_prompt="I have a bad headache, severity 7 out of 10",
    expected_butler="health",
    expected_tool_calls=["symptom_log"],
    db_assertions=[
        DbAssertion(
            butler="health",
            table="symptoms",
            where={"name": "headache"},
            column_checks={"severity": 7},
        )
    ],
    tags=("health", "symptom"),
)
```

The parametrized runner picks it up automatically. No new test function needed.

### Adding a Complex Flow Test

For multi-step or multi-butler scenarios, create a new test module:

```python
# tests/e2e/test_messenger_flow.py

import pytest

pytestmark = pytest.mark.e2e


async def test_notification_delivery(butler_ecosystem):
    """Verify that a routed message produces a notification side effect."""
    switchboard = butler_ecosystem["switchboard"]
    messenger = butler_ecosystem["messenger"]

    # Step 1: Ingest a message that should produce a notification
    envelope = build_envelope("Remind me to call Mom tomorrow")
    response = await ingest_v1(switchboard.pool, envelope)
    assert not response.duplicate

    # Step 2: Classify and dispatch
    classification = await classify_message(
        switchboard.pool, envelope.payload.body, switchboard.spawner.trigger
    )
    await dispatch_decomposed(switchboard.pool, classification, route)

    # Step 3: Verify notification was queued in messenger DB
    row = await messenger.pool.fetchrow(
        "SELECT * FROM notification_log WHERE request_id = $1",
        response.request_id,
    )
    assert row is not None
    assert row["channel"] in ("telegram", "email")
    assert row["status"] == "queued"  # not "sent" — no real credentials
```

### Adding a New Butler's Flows

When a new butler is added to the roster, its flows can be added incrementally:

1. **Automatic:** The new butler appears in smoke tests immediately (port
   liveness, core tables, module status).
2. **Declarative:** Add `E2EScenario` instances for simple input-output cases.
3. **Complex:** Create `test_{butler}_flow.py` for multi-step scenarios.

No changes to `conftest.py` or the ecosystem bootstrap are needed.

## Flow Coverage Matrix

| Pipeline Segment | Smoke | Classification | Dispatch | Spawner | Direct Tool | Full E2E |
|-----------------|-------|---------------|----------|---------|-------------|----------|
| Port liveness | X | | | | | |
| Core tables | X | | | | | |
| Butler registry | X | | | | | |
| Module status | X | | | | | |
| IngestEnvelopeV1 | | | | | | X |
| message_inbox persistence | | | | | | X |
| Deduplication | | | X | | | |
| LLM classification | | X | X | | | X |
| Multi-domain decomposition | | X | | | | |
| Fanout planning | | | X | | | |
| MCP routing | | | X | | | X |
| routing_log | | | X | | | X |
| Serial dispatch lock | | | | X | | X |
| MCP config generation | | | | X | | X |
| System prompt loading | | | | X | | X |
| CC invocation (Haiku) | | | | X | | X |
| Domain tool execution | | | | X | X | X |
| Database side effects | | | | X | X | X |
| Session logging | | | | X | | X |
| Response aggregation | | | | | | X |
| Heartbeat tick | | | | | | X |
