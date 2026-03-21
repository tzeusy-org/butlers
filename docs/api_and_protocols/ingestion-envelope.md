# Ingestion Envelope Protocol

> **Purpose:** Define the `ingest.v1` envelope specification used by connectors to submit events to the Switchboard.
> **Audience:** Developers building connectors, operators debugging ingestion issues.
> **Prerequisites:** [Connector Interface](../connectors/interface.md), [Inter-Butler Communication](inter-butler-communication.md).

## Overview

Connectors are transport adapters that normalize events from external systems (Telegram, Gmail, webhooks) into a canonical `ingest.v1` envelope and submit it to the Switchboard's ingestion API via MCP tool call. The Switchboard owns canonical ingestion, request-context assignment, deduplication, and routing. Connectors never classify messages or route directly to specialist butlers.

## Envelope Schema

```json
{
  "schema_version": "ingest.v1",
  "source": {
    "channel": "telegram|slack|email|api|mcp",
    "provider": "telegram|slack|gmail|imap|internal",
    "endpoint_identity": "bot-or-mailbox-or-client-id"
  },
  "event": {
    "external_event_id": "provider-event-id",
    "external_thread_id": "thread-or-conversation-id-or-null",
    "observed_at": "RFC3339 timestamp"
  },
  "sender": {
    "identity": "provider-sender-identity"
  },
  "payload": {
    "raw": {},
    "normalized_text": "text used for routing"
  },
  "control": {
    "idempotency_key": "optional caller key",
    "trace_context": {},
    "policy_tier": "default|interactive|high_priority"
  }
}
```

## Field Reference

### `source` Block

| Field | Required | Description |
|-------|----------|-------------|
| `channel` | Yes | Canonical channel: `telegram`, `slack`, `email`, `api`, `mcp` |
| `provider` | Yes | Transport provider: `telegram`, `slack`, `gmail`, `imap`, `internal` |
| `endpoint_identity` | Yes | Identity of the receiving endpoint (auto-resolved at startup) |

Canonical channel-provider pairings are enforced:
- `channel=telegram` requires `provider=telegram`
- `channel=email` with Gmail requires `provider=gmail`; with IMAP requires `provider=imap`
- `channel=api` or `channel=mcp` requires `provider=internal`

Endpoint identity is auto-resolved from the source API at connector startup: Telegram `getMe()` yields `telegram:bot:@username`; Gmail yields `gmail:user:email`.

### `event` Block

| Field | Required | Description |
|-------|----------|-------------|
| `external_event_id` | When available | Provider's native event identifier (Telegram `update_id`, email `Message-ID`) |
| `external_thread_id` | No | Thread/conversation ID for grouping related messages |
| `observed_at` | Yes | RFC 3339 timestamp when the connector observed the event |

### `sender` Block

| Field | Required | Description |
|-------|----------|-------------|
| `identity` | Yes | Provider-native sender identifier (Telegram user ID, email address) |

The Switchboard resolves this against `shared.contact_info` to build a structured identity preamble for the routed message.

### `payload` Block

| Field | Required | Description |
|-------|----------|-------------|
| `raw` | Yes | Original source payload as-is (preserved for audit and reprocessing) |
| `normalized_text` | Yes | Extracted text content used for routing classification |

### `control` Block

| Field | Required | Description |
|-------|----------|-------------|
| `idempotency_key` | When no `external_event_id` | Caller-provided key for deduplication |
| `trace_context` | No | W3C Trace Context headers for distributed tracing |
| `policy_tier` | No | Priority hint: `default`, `interactive`, or `high_priority` |

## Transport

Connectors submit envelopes via MCP tool call (`ingest`) to the Switchboard's MCP server over SSE-based MCP transport (using `fastmcp.Client`). The endpoint URL is configured via `SWITCHBOARD_MCP_URL` (e.g., `http://localhost:41100/sse`).

## Request-Context Assignment

The connector provides source/event/sender facts only. The Switchboard assigns canonical request context at ingest acceptance:

- **Required**: `request_id` (UUIDv7), `received_at`, `source_channel`, `source_endpoint_identity`, `source_sender_identity`
- **Optional**: `source_thread_identity`, `trace_context`

The response includes the canonical `request_id` for lineage tracking.

## Idempotency and Deduplication

Deduplication is the Switchboard's responsibility at the ingest boundary. Connectors must:

- Always send stable source identity fields (`channel`, `endpoint_identity`, `external_event_id`).
- Provide `control.idempotency_key` when the source has no stable event ID.
- Treat duplicate acceptance as success, not error.
- Reuse the same dedupe identity on retries.

Canonical dedupe key guidance:
- **Telegram**: `update_id` + receiving bot identity
- **Email**: RFC `Message-ID` + receiving mailbox identity
- **API/MCP**: caller idempotency key or deterministic hash

## Heartbeat Protocol

Connectors send periodic `connector.heartbeat.v1` envelopes every 2 minutes via the `connector.heartbeat` MCP tool, carrying self-reported health state (`healthy`, `degraded`, `error`), monotonic counters, and checkpoint state. The Switchboard derives liveness from recency: `online` (< 2 min), `stale` (2-4 min), `offline` (> 4 min). See `docs/connectors/heartbeat.md` for the full specification.

## Related Pages

- [Connector Interface](../connectors/interface.md) -- full connector contract
- [Inter-Butler Communication](inter-butler-communication.md) -- MCP communication model
- [Dashboard API](dashboard-api.md) -- connector statistics visibility
