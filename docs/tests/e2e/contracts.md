# Contracts — Data Contract Validation Between Pipeline Stages

## Overview

The Butlers message pipeline is a chain of stages connected by typed data
contracts. Each stage produces output conforming to a schema that the next stage
consumes. Contract validation E2E tests ensure that these schemas are
compatible across stages, that versioning is enforced, and that malformed data
is rejected at the boundary rather than causing silent corruption downstream.

This document specifies every inter-stage contract, its validation rules, its
evolution strategy, and the E2E tests that enforce it.

## Contract Map

```
                    IngestEnvelopeV1
                         │
                         ▼
┌─────────────────────────────────────┐
│          ingest_v1()                │
│   Validates: schema_version,       │
│   source channel/provider pairing, │
│   RFC3339 timestamps, dedupe_key   │
└────────────────┬────────────────────┘
                 │  IngestAcceptedResponse
                 ▼
┌─────────────────────────────────────┐
│       classify_message()            │
│   Produces: ClassificationEntry[]   │
│   Validates: butler exists in       │
│   registry, prompt non-empty,       │
│   segment has metadata              │
└────────────────┬────────────────────┘
                 │  ClassificationEntry[]
                 ▼
┌─────────────────────────────────────┐
│     dispatch_decomposed()           │
│   Consumes: ClassificationEntry[]   │
│   Produces: FanoutPlan              │
│   Validates: route_contract_version │
│   fanout mode, join/abort policies  │
└────────────────┬────────────────────┘
                 │  FanoutSubrequestPlan[]
                 ▼
┌─────────────────────────────────────┐
│           route()                   │
│   Consumes: target butler + args    │
│   Validates: registry lookup,       │
│   eligibility state, endpoint URL   │
│   Produces: MCP tool call result    │
└────────────────┬────────────────────┘
                 │  trigger() args
                 ▼
┌─────────────────────────────────────┐
│     Spawner.trigger()               │
│   Consumes: prompt + trigger_source │
│   Produces: SpawnerResult           │
│   Validates: lock acquired,         │
│   session created, adapter called   │
└─────────────────────────────────────┘
```

## Contract 1: IngestEnvelopeV1

### Schema

Defined in `roster/switchboard/tools/routing/contracts.py`:

```python
class IngestEnvelopeV1(BaseModel):
    schema_version: Literal["ingest.v1"]
    source: SourceDescriptor
    payload: PayloadDescriptor
    idempotency_key: str | None = None
    thread_target: ThreadTarget | None = None
    notification_preferences: NotificationPreferences | None = None
    routing_hints: RoutingHints | None = None
    metadata: dict[str, Any] | None = None
```

### Validation Rules

| Field | Rule | Rejection |
|-------|------|-----------|
| `schema_version` | Must be exactly `"ingest.v1"` | `PydanticCustomError("unsupported_schema_version")` |
| `source.channel` | Must be a valid `SourceChannel` literal | Pydantic validation error |
| `source.provider` | Must be valid for the given channel | `PydanticCustomError` via `_ALLOWED_PROVIDERS_BY_CHANNEL` |
| `payload.sent_at` | Must be RFC3339 with timezone offset | `PydanticCustomError("rfc3339_string_required")` |
| `payload.content_type` | Must be non-empty string | `StringConstraints(min_length=1)` |
| `idempotency_key` | If present, drives deduplication | Unique index on `dedupe_key` in `message_inbox` |

### E2E Contract Tests

| Test | Input | Expected Outcome |
|------|-------|------------------|
| Valid envelope | Well-formed `IngestEnvelopeV1` | `IngestAcceptedResponse` with `status="accepted"` |
| Wrong schema version | `schema_version="ingest.v2"` | Pydantic `ValidationError` raised before DB touch |
| Invalid channel/provider pair | `channel="telegram", provider="gmail"` | Pydantic validation error |
| Naive datetime | `sent_at="2026-02-16T10:00:00"` (no TZ) | `PydanticCustomError("rfc3339_string_required")` |
| Duplicate idempotency key | Same `idempotency_key` sent twice | Second response: `duplicate=True`, same `request_id` |
| Missing required fields | Omit `source` entirely | Pydantic `ValidationError` |
| Extra fields | Add `unknown_field: "value"` | Rejected (`extra="forbid"` on model) |

### Idempotency Contract

The `idempotency_key` field drives deduplication via a unique index on
`dedupe_key` in the `message_inbox` table. The `dedupe_key` is computed as:

```python
dedupe_key = hashlib.sha256(
    f"{source.endpoint_identity}:{source.sender_identity}:{idempotency_key}".encode()
).hexdigest()
```

**Contract guarantee:** Two envelopes with the same `(endpoint_identity,
sender_identity, idempotency_key)` tuple produce the same `request_id`. The
second insert is a no-op (`INSERT ... ON CONFLICT DO NOTHING`), and the response
indicates `duplicate=True`.

**E2E test:**

```python
async def test_idempotency_contract(butler_ecosystem):
    envelope = build_envelope("Log weight 80kg", idempotency_key="test-key-1")

    r1 = await ingest_v1(switchboard_pool, envelope)
    assert r1.status == "accepted"
    assert r1.duplicate is False

    r2 = await ingest_v1(switchboard_pool, envelope)
    assert r2.status == "accepted"
    assert r2.duplicate is True
    assert r2.request_id == r1.request_id
```

## Contract 2: Classification Response

### Schema

The classification LLM returns a JSON array of routing entries. Each entry
must conform to:

```python
_CLASSIFICATION_ENTRY_KEYS = {"butler", "prompt", "segment"}
_SEGMENT_KEYS = {"sentence_spans", "offsets", "rationale"}
```

### Validation Rules

| Field | Rule | Fallback |
|-------|------|----------|
| Top-level | Must be a JSON array | Fallback to `[{butler: "general", prompt: original_text}]` |
| Each entry | Must contain `butler`, `prompt`, `segment` keys | Entry skipped, warning logged |
| `butler` | Must exist in `butler_registry` | Entry skipped or routed to `general` |
| `prompt` | Must be non-empty string | Entry skipped |
| `segment` | Must contain at least one of `sentence_spans`, `offsets`, `rationale` | Warning logged, entry still accepted |

### LLM Output Variance

Classification output is LLM-generated and inherently variable. The contract
validation is intentionally lenient:

- Extra keys in entries are ignored (forward-compatible)
- Missing `segment` sub-keys emit warnings but don't reject the entry
- Parse failure at any level falls back to `general` rather than failing

### E2E Contract Tests

| Test | Input | Expected Outcome |
|------|-------|------------------|
| Well-formed single-domain | "Log weight 80kg" | Array with one entry, `butler="health"` |
| Well-formed multi-domain | Compound message | Array with multiple entries, each self-contained |
| LLM returns non-JSON | Mock spawner returns prose | Fallback to `general` |
| LLM returns empty array | Mock spawner returns `[]` | Fallback to `general` |
| LLM returns unknown butler | Entry with `butler="nonexistent"` | Entry skipped, remaining entries processed |
| Extra keys in entry | Entry has `confidence: 0.9` (not in schema) | Ignored, entry accepted |

## Contract 3: FanoutPlan

### Schema

Defined in `roster/switchboard/tools/routing/dispatch.py`:

```python
@dataclass(frozen=True)
class FanoutPlan:
    mode: FanoutMode          # "parallel" | "ordered" | "conditional"
    join_policy: JoinPolicy   # "wait_for_all" | "first_success"
    abort_policy: AbortPolicy # "continue" | "on_required_failure" | "on_any_failure"
    subrequests: tuple[FanoutSubrequestPlan, ...]

@dataclass(frozen=True)
class FanoutSubrequestPlan:
    subrequest_id: str
    segment_id: str
    butler: str
    prompt: str
    depends_on: tuple[str, ...]
    run_if: DependencyRunIf   # "success" | "completed" | "always"
    required: bool
    arbitration_group: str | None = None
    arbitration_priority: int = 0
```

### Validation Rules

| Field | Rule |
|-------|------|
| `mode` | Must be one of `parallel`, `ordered`, `conditional` |
| `join_policy` | Must be one of `wait_for_all`, `first_success` |
| `abort_policy` | Must be one of `continue`, `on_required_failure`, `on_any_failure` |
| `subrequests` | Non-empty tuple |
| `depends_on` | Each ID must reference another subrequest in the plan |
| `run_if` | Must be one of `success`, `completed`, `always` |

### Default Policy Matrix

| Fanout Mode | Default Join | Default Abort |
|-------------|-------------|---------------|
| `parallel` | `wait_for_all` | `continue` |
| `ordered` | `wait_for_all` | `continue` |
| `conditional` | `wait_for_all` | `continue` |

### E2E Contract Tests

| Test | Input | Expected Outcome |
|------|-------|------------------|
| Single subrequest | One classification entry | FanoutPlan with `mode="parallel"`, one subrequest |
| Multiple subrequests | Multi-domain classification | FanoutPlan with multiple subrequests |
| Invalid fanout mode | Inject `mode="invalid"` | `ValueError("Invalid fanout mode")` |
| Dependency cycle | Subrequest A depends on B depends on A | Cycle detection raises error |
| Missing dependency target | `depends_on=["nonexistent"]` | Validation error |

## Contract 4: Route Contract Version

### Schema

The switchboard registry tracks a `route_contract_version` for each butler:

```sql
butler_registry (
    name TEXT PRIMARY KEY,
    endpoint_url TEXT NOT NULL,
    route_contract_version TEXT DEFAULT 'v1',
    eligibility_state TEXT DEFAULT 'active',
    ...
)
```

### Validation Rules

| Check | Rule | Behavior |
|-------|------|----------|
| Version match | Router's expected version must match registry entry | Mismatch logs warning, routing proceeds (forward-compatible) |
| Eligibility | `eligibility_state` must be `active` | `quarantined` butlers are skipped |
| Staleness | `last_seen_at` within acceptable window | Stale entries may be quarantined |

### E2E Contract Tests

| Test | Setup | Expected Outcome |
|------|-------|------------------|
| Version match | Butler registered with `v1`, router expects `v1` | Route succeeds |
| Version mismatch | Manually update registry to `v2` | Route proceeds with warning log |
| Quarantined butler | Set `eligibility_state='quarantined'` | Route skipped, logged as `target_quarantined` |
| Stale registry | Set `last_seen_at` to far past | Butler treated as potentially unavailable |

## Contract 5: SpawnerResult

### Schema

Defined in `src/butlers/core/spawner.py`:

```python
@dataclass
class SpawnerResult:
    output: str | None = None
    success: bool = False
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0
    model: str | None = None
    session_id: uuid.UUID | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
```

### Contract Guarantees

| Property | Guarantee |
|----------|-----------|
| `session_id` | Always set (assigned before adapter invocation) |
| `duration_ms` | Always >= 0, measures wall-clock time of adapter call |
| `success` | `True` iff adapter returned without exception and output is non-empty |
| `error` | Set iff `success is False` |
| `tool_calls` | List of `{name, arguments, result}` dicts from CC session |
| `model` | The model ID used (e.g., `claude-haiku-4-5-20251001`) |
| `input_tokens` / `output_tokens` | Set when adapter reports usage, `None` otherwise |

### Session Persistence Contract

Every spawner invocation produces exactly two database writes:

1. **Before invocation:** `session_create()` inserts a row with `status="running"`
2. **After invocation:** `session_complete()` updates the row with final status,
   duration, token counts, tool calls, and output

**Invariant:** If the process crashes between (1) and (2), the session row
remains with `status="running"` and no `completed_at`. This is detectable and
recoverable.

### E2E Contract Tests

| Test | Input | Expected Outcome |
|------|-------|------------------|
| Successful invocation | Valid prompt | `success=True`, `session_id` set, `duration_ms > 0` |
| Failed invocation | Invalid API key | `success=False`, `error` set, session logged with error |
| Token counting | Any prompt | `input_tokens > 0`, `output_tokens > 0` |
| Session persistence | Any prompt | Row in `sessions` with matching `session_id` and `status` |

## Contract Evolution

### Versioning Strategy

- `IngestEnvelopeV1` is versioned via `schema_version` field
- Route contracts are versioned via `route_contract_version` in registry
- Classification response schema is unversioned (validated structurally)
- `SpawnerResult` is unversioned (internal dataclass, not serialized)

### Backward Compatibility Rules

1. **New optional fields** can be added to any contract without version bump
2. **New required fields** require a version bump and migration period
3. **Removing fields** requires a version bump
4. **Changing field semantics** requires a version bump

### Migration Testing

When a contract version changes, E2E tests should validate:

1. Old-version payloads are still accepted (or rejected with clear error)
2. New-version payloads are accepted
3. Mixed-version scenarios (old sender, new receiver) degrade gracefully
