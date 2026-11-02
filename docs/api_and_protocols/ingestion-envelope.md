# Ingestion Envelope Protocol

> **Purpose:** Explain the `ingest.v1` envelope rules connectors follow when submitting events to
> the Switchboard.
> **Audience:** Developers building connectors, operators debugging ingestion issues.
> **Prerequisites:** [Connector Interface](../connectors/overview.md), [Inter-Butler Communication](inter-butler-communication.md).

## Overview

Connectors are transport adapters that normalize events from external systems (Telegram, Gmail, webhooks) into a canonical `ingest.v1` envelope and submit it to the Switchboard's ingestion API via MCP tool call. The Switchboard owns canonical ingestion, request-context assignment, deduplication, and routing. Connectors never classify messages or route directly to specialist butlers.

## Envelope Contract

The wire shape is defined by Pydantic models in `roster/switchboard/tools/contracts.py`:
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
check catches the same message arriving through two connectors. Content hashes derive from the
bearer-scrubbed text, so a stored key cannot be brute-forced back to a withheld one-time code;
when bearer material was withheld (by ingest or by a pre-scrubbing connector) the secondary check
is skipped and `content_hash_key` is omitted. This decision includes all content subtrees scrubbed
for storage, including bearer links present only in `payload.raw` beside benign visible text.
The same decision applies before and inside the advisory lock. Connectors must:

- Send stable source identity fields on every submission, including retries.
- Provide `control.idempotency_key` when the source has no stable event id.
- Treat duplicate acceptance as success, not error.

## Dark goal input and reply references

The trusted closed goal harness reads canonical retained `normalized_text`, attachments and
`payload.untrusted_spans` by server request reference. It recomputes the input digest and returns a
frozen sequence whose marked ranges and attribution labels are data. There is no caller-supplied
trusted cache, duplicate raw store, live ingress consumer, model launch or key mount. Owner sender
verification is independent of quoted authors, display names and body claims. Email From/domain
verdicts alone do not authenticate an owner principal. Authenticated dashboard authority requires
the private stored owner admission bound to the exact accepted origin/body and unique live entity.

`event.in_reply_to_ref` identifies the message being answered; `event.reply_target_ref` identifies
where to send the current reply. Neither is a caller-selected goal/effect ID. Exact stored inbound
or outbound provenance must match transport, endpoint, conversation and verified recipient.
Unknown/foreign references create no guessed continuity. Missing inbound trust is exposed; a bot
reply uses the producing goal's actual exposure. A live owner prompt may receive one immutable
source-bound inbox entry without a new budget or invocation; Stop/expiry are rechecked after locks.

Channel kind comes only from the authoritative registry. A successful missing/invalid declaration
means message; lookup failure refuses. Feed input records through its domain-owned deterministic
service and creates no goal without a matching Git declaration. Registry kind drift cannot change
the canonical accepted request identity or allocate a second budget on retry.

Roll out receiver/schema before marker/reference producers because envelope models forbid extras.
Complete buffered and filtered replay propagation before consumption. Keep live activation off;
rollback disables activation/consumption before removing reader or producer support.

## Sender Authentication Verdict

For every accepted `email` envelope the Switchboard stores one verdict on the canonical `public.ingestion_events.sender_authentication` column: `authenticated`, `unverified` or `failed`. It describes the From domain only and is computed once at ingest from the topmost `Authentication-Results` header kept in `payload.raw` (a lower or injected header never counts). An aligned `dkim=pass` or a `dmarc=pass` gives `authenticated`; a `dmarc` fail or permerror, or an aligned `dkim` fail, gives `failed`; a missing header, a metadata-tier row with no raw payload, `temperror`, `none` and unaligned passes give `unverified`. Non-email rows, and rows ingested before the column existed, read `NULL`. A verifier error stores `NULL` and never fails the ingest. Header parsing is quote-, comment- and literal-aware, so sender text echoed by the receiver cannot forge a clause; an unreadable header is `unverified`, unless it still carries a `dmarc` fail or permerror or a fail on an aligned `dkim` signature, which reads `failed` (a stray character in echoed text can hide a forgery from the strict parser but never a real failure). Alignment uses `header.d` only. The verdict is the receiving provider's claim from the topmost header: it is not proof for mail placed without SMTP receipt (IMAP APPEND, import or insert), where the sender can write that header. The verdict never grants owner origin. Readers use this column instead of parsing headers themselves. See `openspec/specs/ingestion-event-registry/spec.md`.

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
