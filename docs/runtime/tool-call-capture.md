# Tool Call Capture

> **Purpose:** Describe how MCP tool executions are intercepted, recorded, and reconciled with parser-extracted tool calls for accurate session logs.
> **Audience:** Developers building modules, debugging tool call records, or extending the session logging pipeline.
> **Prerequisites:** [MCP Model](../concepts/mcp-model.md), [Session Lifecycle](session-lifecycle.md).

## Overview

The tool call capture system (`src/butlers/core/tool_call_capture.py`) tracks MCP tool executions as they happen inside the butler daemon. When an LLM session calls a tool via MCP, the daemon records the call in an in-memory buffer keyed by the runtime session ID. After the session completes, these ground-truth executed call records are merged with the tool calls parsed from the LLM's output, producing an accurate and complete tool call log for the session record.

## The Problem

There are two sources of tool call information:

1. **Parser-extracted calls** --- The runtime adapter parses the LLM CLI's stdout to extract tool call records. These capture what the LLM *requested* but may miss retries, lack execution outcomes, or contain incomplete data.
2. **Daemon-observed calls** --- The MCP server sees every tool invocation that actually executes. These are ground truth but only available inside the daemon process.

Neither source alone gives the full picture. The capture system bridges them by collecting daemon-side execution records and merging them with parser-side records after the session ends.

## Context Variable: Runtime Session ID

The capture system uses a `contextvars.ContextVar` to track which runtime session is currently active in each async task. Before the spawner invokes the runtime adapter, it sets the runtime session ID. Tool handlers running in the MCP server's async context can then read this variable to know which session they belong to.

The setters return a reset token, and a read with no bound session returns `None`.

## Capture Buffer

Tool call records accumulate in a module-level `defaultdict(list)` keyed by session ID string. A `threading.Lock` protects all mutations, since the capture buffer may be accessed from multiple threads.

The spawner pre-allocates the buffer before invocation and consumes (returns and clears) it after
the runtime returns; error paths discard it. A capture with no bound session ID is silently
dropped, so a tool call outside a spawned session never lands in a session record.

## Tool Call Record Format

Each record carries the tool `name` plus optional `module`, `input`, `outcome`, `result`, and
`error` (`capture_tool_call()` is the authoritative shape). All values pass through `_json_safe()` before storage, which recursively converts non-serializable types to JSON-safe representations: Pydantic models via `model_dump(mode="json")`, bytes decoded as UTF-8 with replacement, sets/tuples to lists, and arbitrary objects via `str()` fallback.

## Routing Context

The capture system also manages per-session routing context --- metadata about how a request was routed to this butler. Functions include `set_runtime_session_routing_context`, `get_runtime_session_routing_context`, `get_current_runtime_session_routing_context`, and `clear_runtime_session_routing_context`, all keyed by session ID. This allows tool handlers (e.g., `notify`) to access ingestion metadata for reply targeting without passing it through every function signature.

## Merge Algorithm

After a session completes, the spawner calls `_merge_tool_call_records(parsed_calls, executed_calls)`:

1. For each executed call, compute a signature from `(tool_name, JSON-serialized input)`.
2. Match each executed call to an unmatched parsed call with the same signature.
3. Matched pairs are merged (executed data overwrites parsed, preserving extra fields).
4. Unmatched executed calls are added as-is (calls the parser missed).
5. Unmatched parsed calls are added as-is (calls that may not have executed).

## Integration Points

- **`_ToolCallLoggingMCP` proxy** --- Calls `capture_tool_call()` after every tool handler completes.
- **Spawner** --- Calls `ensure_runtime_session_capture()` before invocation and `consume_runtime_session_tool_calls()` after.
- **Session completion** --- The merged tool call list is written to `sessions.tool_calls` as JSONB.

## Related Pages

- [MCP Model](../concepts/mcp-model.md) --- how tools are registered and the logging proxy works
- [Session Lifecycle](session-lifecycle.md) --- where tool call records end up in the session log
- [LLM CLI Spawner](spawner.md) --- the merge step in the post-invocation pipeline
