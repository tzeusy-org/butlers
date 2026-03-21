# Spawner

> **Purpose:** Document the LLM CLI spawner that invokes ephemeral runtime instances for butler sessions.
> **Audience:** Developers working on session dispatch, runtime adapters, or concurrency tuning.
> **Prerequisites:** [Trigger Flow](../concepts/trigger-flow.md), [MCP Model](../concepts/mcp-model.md).

## Overview

The Spawner (`src/butlers/core/spawner.py`) is the core component that invokes ephemeral AI runtime instances for a butler. Each butler has exactly one Spawner instance. When triggered, the Spawner acquires concurrency slots, resolves the model from the catalog, generates a locked-down MCP config, invokes the runtime via an adapter, captures tool calls, logs the session, and returns the result.

## Class Structure

The `Spawner` class is initialized with:

- **`config`** (`ButlerConfig`) --- the butler's parsed configuration
- **`config_dir`** (`Path`) --- path to the butler's config directory (containing `CLAUDE.md`)
- **`pool`** (`asyncpg.Pool`) --- database connection pool for session logging
- **`module_credentials_env`** --- mapping of module names to required env var names
- **`runtime`** (`RuntimeAdapter`) --- optional injected adapter (defaults to `ClaudeCodeAdapter`)
- **`credential_store`** (`CredentialStore`) --- optional DB-first credential resolver

## Trigger Method

The primary entry point is `trigger()`:

```python
async def trigger(
    self,
    prompt: str,
    trigger_source: str,
    context: str | None = None,
    max_turns: int = 20,
    parent_context: Context | None = None,
    request_id: str | None = None,
    complexity: Complexity = Complexity.MEDIUM,
    cwd: str | None = None,
    bypass_butler_semaphore: bool = False,
) -> SpawnerResult:
```

The method returns a `SpawnerResult` dataclass containing `output`, `success`, `tool_calls`, `error`, `duration_ms`, `model`, `session_id`, `input_tokens`, and `output_tokens`.

## Execution Pipeline

### 1. Concurrency Acquisition

Two semaphores must be acquired in order:

1. **Per-butler semaphore** --- `asyncio.Semaphore(max_concurrent_sessions)` from `butler.toml`. Default is 1 (serial dispatch). The Switchboard uses 3. Can be bypassed with `bypass_butler_semaphore=True` for internal dispatch.
2. **Global semaphore** --- Process-wide `asyncio.Semaphore` defaulting to 3, configurable via `BUTLERS_MAX_GLOBAL_SESSIONS`. Limits total concurrent sessions across all butlers in the process.

Metrics track queue depth at both levels (`butlers.spawner.queued_triggers` and `butlers.spawner.global_queue_depth`).

### 2. Model Resolution

The spawner resolves the model dynamically via the catalog:

1. Query `shared.model_catalog` with optional `shared.butler_model_overrides` for the butler's name and the requested complexity tier.
2. If the catalog returns a result: use that model's `runtime_type`, `model_id`, and `extra_args`. Check token quota before proceeding.
3. If the catalog returns nothing: fall back to the TOML-configured `runtime.model`.

The resolution source (`"catalog"` or `"toml_fallback"`) is recorded on the session.

### 3. Session Creation

A session row is inserted into the `sessions` table with the prompt, trigger source, model, request ID, complexity tier, and resolution source. The returned session UUID is used for all subsequent correlation.

### 4. MCP Config Generation

The spawner generates a config declaring a single MCP server --- this butler's FastMCP instance. The URL includes a `runtime_session_id` query parameter for tool call correlation. Only declared credentials are included in the environment; undeclared env vars do not leak.

### 5. System Prompt Composition

The system prompt is composed from three layers (in stable order for token-cache efficiency):

1. **Base system prompt** --- read from the butler's `CLAUDE.md`
2. **Owner routing instructions** --- fetched from the `routing_instructions` table, sorted by priority
3. **Memory context** --- retrieved from the memory module based on the prompt content

### 6. Runtime Invocation

The appropriate `RuntimeAdapter` is selected based on the resolved `runtime_type`. The adapter spawns the LLM CLI as a subprocess with the MCP config, system prompt, user prompt, environment, and model parameters. Trace context is propagated via the `TRACEPARENT` environment variable.

### 7. Tool Call Capture and Merge

During the session, tool calls executed on the MCP server are captured in a thread-safe buffer keyed by `runtime_session_id`. After the runtime returns, the spawner merges parser-extracted tool calls (from the adapter's output parsing) with server-side executed tool calls. The merge uses signature matching (tool name + input payload) to reconcile records while preserving retry attempts.

### 8. Session Completion

The session row is updated with the output, merged tool calls, duration, token usage, cost, success status, and error (if any). Token usage is also recorded to the `shared.token_usage_ledger` for quota tracking and reported to OpenTelemetry metrics.

### 9. Memory Episode Storage

If the memory module is enabled and the session produced output, the spawner stores the session as an episode for future retrieval.

## Self-Healing Integration

The spawner can be wired to a self-healing module via `wire_healing_module()`. When a session fails with a hard crash, the spawner's exception handler fires `dispatch_healing()` as a background task, which analyzes the failure and may attempt automatic recovery.

## Adapter Pool

The spawner maintains a cache of `RuntimeAdapter` instances keyed by `runtime_type`. The TOML-configured adapter is seeded at construction. When the model catalog resolves a different runtime type, a new adapter is lazily instantiated via the adapter registry (`get_adapter()`). Provider-specific configuration (e.g., Ollama base URL from `shared.provider_config`) is forwarded to adapters that accept it.

## Related Pages

- [Trigger Flow](../concepts/trigger-flow.md) --- the two trigger sources that invoke the spawner
- [Session Lifecycle](session-lifecycle.md) --- session creation and completion details
- [Model Routing](model-routing.md) --- how models are resolved from the catalog
- [Tool Call Capture](tool-call-capture.md) --- how tool execution is tracked
- [Observability](../architecture/observability.md) --- trace context propagation through the spawner
