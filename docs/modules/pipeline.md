# Pipeline Module
> **Purpose:** Message classification and routing pipeline that connects input modules and connectors to the Switchboard's LLM-driven routing.
> **Audience:** Contributors.
> **Prerequisites:** [Module System](module-system.md).

## Overview

The Pipeline module (`src/butlers/modules/pipeline.py`) is the central message-processing engine for the Switchboard butler. It receives inbound messages from connectors (Telegram, Gmail, voice) and the ingest API, classifies them using an LLM-driven triage session, and routes message segments to the appropriate target butler(s) by calling `route_to_butler`. The module wraps the `MessagePipeline` class as a pluggable `Module` conforming to the butler module ABC, and registers a `pipeline_process` MCP tool.

## Pipeline Stages

Each message passes through the following stages, instrumented with OpenTelemetry spans:

1. **Normalize**: Record message receipt via telemetry counters.
2. **Ingress Dedupe**: When enabled, compute a dedupe key from the message's source channel, endpoint identity, and event ID (or content hash). Uses PostgreSQL advisory locks to serialize concurrent inserts and reject duplicates. Deduped messages return immediately with a `"deduped"` result.
3. **Policy Bypass**: If the ingest tool pre-resolved a triage decision via ingestion rules (e.g., `route_to`, `skip`, `metadata_only`), the pipeline honours it and skips LLM classification entirely.
4. **Conversation History**: Load recent messages for context. Strategy varies by channel (see below).
5. **Identity Resolution**: Optionally resolve the sender to a known contact and inject an identity preamble into the routing prompt.
6. **LLM Classification**: Build a routing prompt listing available butlers and their capabilities, then spawn an ephemeral LLM session at `TRIVIAL` complexity. The LLM calls `route_to_butler` tool(s) to dispatch message segments.
7. **Fallback**: If the LLM produces no tool calls, the pipeline infers a target from the LLM's text output or falls back to the `general` butler.
8. **Lifecycle Update**: Write decomposition output, dispatch outcomes, and response summary back to the `message_inbox` table.

## Deduplication Strategies

The dedupe key varies by channel: Telegram uses update IDs, email uses message IDs, API/MCP uses caller idempotency keys, and other channels fall back to a SHA-256 content hash within a 5-minute time bucket. All keys are scoped to the source endpoint identity. Advisory locks (`pg_advisory_xact_lock`) serialize concurrent inserts for the same dedupe key.

## Conversation History Loading

The pipeline uses channel-aware strategies to provide context:

- **Realtime** (Telegram, WhatsApp, Slack, Discord): Union of a 15-minute time window and last 30 messages, deduplicated and sorted chronologically.
- **Email**: Full thread loaded oldest-first, truncated from the oldest end to fit a 50,000-token budget.
- **None** (API, MCP): No history loaded.

History is formatted with direction labels and fenced in code blocks with a security header marking content as untrusted user data.

## Configuration

In `butler.toml`:

```toml
[modules.pipeline]
enable_ingress_dedupe = true
```

The `PipelineModule` is typically enabled only on the Switchboard butler. The `MessagePipeline` instance is attached at daemon startup via `set_pipeline()`, which wires the Switchboard's DB pool, spawner dispatch function, and optional identity resolution and owner notification callbacks.

## Concurrency and Telemetry

The pipeline uses per-task `ContextVar` isolation for routing context, preventing cross-contamination when `max_concurrent_sessions > 1`. All stages are instrumented with OpenTelemetry spans. Key metrics emitted via the Switchboard telemetry singleton include `message_received`, `message_deduplicated`, `ingress_accept_latency_ms`, `routing_decision_latency_ms`, `end_to_end_latency_ms`, `lifecycle_transition` (accepted/processing/parsed/errored/skipped), and `fallback_to_general`.

## Related Pages

- [Connector Interface](../connectors/interface.md) -- How connectors submit messages to the pipeline
- [Metrics Module](metrics.md) -- Butler-level Prometheus integration
- [Telegram Module](telegram.md) -- Telegram ingestion feeds the pipeline
- [Email Module](email.md) -- Email ingestion feeds the pipeline
