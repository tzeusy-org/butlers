# Ingestion Envelope Protocol

> **Purpose:** Explain the `ingest.v1` envelope rules connectors follow when submitting events to
> the Switchboard.
> **Audience:** Developers building connectors, operators debugging ingestion issues.
> **Prerequisites:** [Connector Interface](../connectors/overview.md), [Inter-Butler Communication](inter-butler-communication.md).

## Overview

Connectors are transport adapters that normalize events from external systems (Telegram, Gmail, webhooks) into a canonical `ingest.v1` envelope and submit it to the Switchboard's ingestion API via MCP tool call. The Switchboard owns canonical ingestion, request-context assignment, deduplication, and routing. Connectors never classify messages or route directly to specialist butlers.

## Envelope Contract

The wire shape is defined by Pydantic models in `roster/switchboard/tools/routing/contracts.py`:
`IngestEnvelopeV1` and its blocks `IngestSourceV1`, `IngestEventV1`, `IngestSenderV1`,
`IngestPayloadV1`, and `IngestControlV1`. The closed vocabularies — `SourceChannel`,
`SourceProvider`, `PolicyTier`, `IngestionTier` — and the allowed channel-to-provider pairs
(`_ALLOWED_PROVIDERS_BY_CHANNEL`) live in the same file. The normative contract is
[RFC 0003](../../about/legends-and-lore/rfcs/0003-switchboard-routing-and-ingestion.md). Every
model is `extra="forbid"`, so an unknown field is a validation error, not a silent drop.

Rules the models encode and connectors must respect:

- **Source identity is a validated pair.** `source.channel` names what kind of conversation this
  is (for example `telegram_bot` vs `telegram_user_client`); `source.provider` names the
  transport. An unlisted pair is rejected at ingest. Adding a source means extending the
  vocabularies and the pair map in `contracts.py` and RFC 0003 together.
- **`source.endpoint_identity` is the receiving endpoint** (the bot, mailbox, or client
  account), resolved by the connector at startup and stable across restarts.
- **`event.external_event_id` is required** and must be the provider's native, stable event id.
  Placeholder values (`unknown`, `none`, …) are treated as missing and fall back to content-hash
  dedupe. `event.observed_at` must be an RFC 3339 string with a timezone.
- **Conversation identity is split from reply targeting.** `event.external_conversation_id` is
  the stable, channel-namespaced conversation key (for example `telegram:<chat_id>`, with a
  `:topic:<topic_id>` suffix when Telegram marks the message `is_topic_message`,
  `whatsapp:<chat_jid>`, or the Gmail `threadId`). Continuity consumers (conversation anchors,
  provider-session resume, realtime history) key on it. `event.reply_target_ref` is the
  provider-native per-message target used only for replies and reactions (for example
  `<chat_id>:<message_id>`). Telegram bot ingress without both fields is rejected. A producer that
  has not adopted the split still sends `event.external_thread_id`, which then serves as both its
  conversation key and its reply target; a split field always wins over it
  (`butlers.conversation_identity.event_conversation_identity`).
- **`sender.identity` is provider-native** (user id, email address). The Switchboard resolves it
  to an entity (see [Identity Model](../concepts/identity-model.md)); connectors never resolve
  identity themselves.
- **Ingestion tier constrains the payload.** `full` (default) requires a non-null `payload.raw`;
  `metadata` requires `payload.raw = null` and bypasses LLM classification.
  `payload.normalized_text` may be empty only when `payload.attachments` carries the content.
- **Policy tier is a dispatch-priority hint**, not a routing decision: `high_priority`,
  `interactive`, and `default` map to the Switchboard buffer's priority lanes. `passive` marks
  observed, not-addressed traffic from user-client connectors (a message that addresses the
  butlers is promoted to `interactive`); it is persisted on the ingestion event, and the buffer,
  which has no passive lane, queues it as `default`.
- **`control.pinned_target`** routes deterministically to a named, routable butler without LLM
  classification; an unknown or non-routable target is rejected, never silently misrouted.

## Transport

Connectors submit envelopes via the Switchboard's `ingest` MCP tool over SSE (`fastmcp.Client`),
at the URL in `SWITCHBOARD_MCP_URL` (for example `http://localhost:41100/sse`).

## Request-Context Assignment

The connector provides source, event, and sender facts only. The Switchboard assigns the
canonical request context (`RouteRequestContextV1`) at ingest acceptance — including the
`request_id` (UUIDv7) and `received_at` — and returns the `request_id` for lineage tracking.
Lineage fields are immutable once assigned. The request context carries
`external_conversation_id` and `reply_target_ref` from the event; `source_thread_identity` is
retained as the notify-facing name for the reply target and never carries the conversation key.

## Idempotency and Deduplication

Deduplication is the Switchboard's responsibility at the ingest boundary (`_compute_dedupe_key`
in `roster/switchboard/tools/ingestion/ingest.py`). The key prefers `control.idempotency_key`,
then `external_event_id` plus source identity, then a content hash; a secondary content-hash
check catches the same message arriving through two connectors. Connectors must:

- Send stable source identity fields on every submission, including retries.
- Provide `control.idempotency_key` when the source has no stable event id.
- Treat duplicate acceptance as success, not error.

## Heartbeat Protocol

Connectors send periodic `connector.heartbeat.v1` envelopes (default every 2 minutes,
`CONNECTOR_HEARTBEAT_INTERVAL_S`) via the `connector.heartbeat` MCP tool, carrying self-reported health state (`healthy`, `degraded`, `error`), monotonic counters, and checkpoint state. The Switchboard derives liveness from recency: `online` (< 2 min), `stale` (2-4 min), `offline` (> 4 min). See [Heartbeat](../connectors/heartbeat.md).

## Verification

To confirm the ingestion envelope protocol is implemented as specified:

```bash
# 1. Submit a minimal valid ingest.v1 envelope and check acceptance
# (Requires SWITCHBOARD_MCP_URL to be set; adjust as needed)
python3 - <<'EOF'
import asyncio, json
from fastmcp import Client

async def test_ingest():
    async with Client("http://localhost:41100/sse") as client:
        result = await client.call_tool("ingest", {
            "schema_version": "ingest.v1",
            "source": {"channel": "api", "provider": "internal", "endpoint_identity": "test:verify"},
            "event": {"external_event_id": "verify-001", "observed_at": "2026-07-02T00:00:00Z"},
            "sender": {"identity": "test-sender"},
            "payload": {"raw": {}, "normalized_text": "verification test"},
            "control": {}
        })
        print(json.dumps(result, indent=2))

asyncio.run(test_ingest())
EOF
# Expected: response includes a canonical request_id (UUIDv7) and accepted status

# 2. Duplicate submission returns same request_id (deduplication)
# Submit the same envelope twice; both should return the same canonical reference
# Expected: request_id matches on the second call; no double-processing

# 3. ingestion_events table has the new row with correct source fields
psql -h localhost -U butlers -d butlers -c \
  "SELECT source_channel, source_provider, source_endpoint_identity, received_at
   FROM public.ingestion_events ORDER BY received_at DESC LIMIT 3;"
# Expected: most recent row reflects the envelope source fields submitted above

# 4. Heartbeat endpoint accepts connector.heartbeat.v1 envelopes
# Connectors should appear in connector_registry after their first heartbeat
psql -h localhost -U butlers -d butlers -c \
  "SELECT connector_type, endpoint_identity, state, last_heartbeat_at
   FROM switchboard.connector_registry ORDER BY last_heartbeat_at DESC LIMIT 5;"
# Expected: active connectors listed with recent heartbeat timestamps
```

## Related Pages

- [Connector Interface](../connectors/overview.md) -- full connector contract
- [Inter-Butler Communication](inter-butler-communication.md) -- MCP communication model
- [Dashboard API](dashboard-api.md) -- connector statistics visibility
