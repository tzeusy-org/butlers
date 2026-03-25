# RFC 0003: Switchboard Routing and Ingestion

**Status:** Accepted
**Date:** 2026-03-24

## Summary

The Switchboard butler is the single ingress point for all external events entering the Butlers framework. Connectors normalize events into `ingest.v1` envelopes and submit them via MCP. The Switchboard processes these through a multi-stage pipeline: deduplication, pre-classification triage (deterministic rules + thread affinity), and LLM classification fallback. Routed messages are dispatched to target butlers via `route.execute` with full identity preamble and trace context. A durable route inbox provides crash recovery, and email priority queuing prevents urgent messages from being buried behind bulk traffic.

## Motivation

Personal email and messaging inboxes generate bursty, heterogeneous traffic. Without structured ingestion, every message would require an LLM classification call, creating multi-minute queue times during bursts. The pre-classification triage layer eliminates 50-70% of classification calls. Thread affinity preserves routing consistency for email conversations. The route inbox ensures no message is lost across daemon crashes. Priority queuing ensures that messages from known contacts and direct correspondence are processed before newsletters and bulk mail.

## Design

### ingest.v1 Envelope Format

Connectors submit events using this canonical envelope:

```json
{
  "schema_version": "ingest.v1",
  "source": {
    "channel": "telegram|slack|email|api|mcp",
    "provider": "telegram|slack|gmail|imap|internal",
    "endpoint_identity": "<auto-resolved at startup>"
  },
  "event": {
    "external_event_id": "<provider-event-id>",
    "external_thread_id": "<thread-or-conversation-id | null>",
    "observed_at": "<RFC 3339 timestamp>"
  },
  "sender": {
    "identity": "<provider-native sender identity>"
  },
  "payload": {
    "raw": {},
    "normalized_text": "<text used for routing>"
  },
  "control": {
    "idempotency_key": "<optional caller key>",
    "trace_context": {},
    "policy_tier": "default|interactive|high_priority"
  }
}
```

**Field contracts:**

- `source.channel` and `source.provider` MUST use canonical pairings: `telegram/telegram`, `email/gmail`, `email/imap`, `api/internal`, `mcp/internal`.
- `source.endpoint_identity` is auto-resolved from the source API at connector startup (e.g., Telegram `getMe()` yields `telegram:bot:@username`; Gmail yields `gmail:user:email`).
- `event.external_event_id` is REQUIRED when the source provides a stable event identifier (Telegram `update_id`, email `Message-ID`).
- `sender.identity` is the provider-native identifier used for identity resolution (see RFC 0004).
- `payload.raw` preserves the original source payload for audit and reprocessing.
- `control.policy_tier` defaults to `"default"` when absent.
- `control.trace_context` carries W3C Trace Context headers for distributed tracing (see RFC 0005).

### Request Context Assignment

The Switchboard assigns canonical request context at ingest acceptance:

- `request_id` (UUIDv7) -- canonical identifier for the request lifecycle
- `received_at` -- server-side timestamp
- `source_channel`, `source_endpoint_identity`, `source_sender_identity` -- derived from the envelope
- `source_thread_identity` -- from `event.external_thread_id` when present
- `trace_context` -- propagated from `control.trace_context`

The ingest response includes the canonical `request_id` for lineage tracking.

### Deduplication

Deduplication is the Switchboard's responsibility at the ingest boundary. Canonical dedupe keys:

- **Telegram:** `update_id` + receiving bot identity
- **Email:** RFC `Message-ID` + receiving mailbox identity
- **API/MCP:** caller `idempotency_key` or deterministic hash

Connectors MUST provide stable source identity fields and treat duplicate acceptance as success, not error.

### Pre-Classification Triage Pipeline

After deduplication and envelope normalization, the triage pipeline evaluates incoming messages in this order:

**Stage 1: Thread affinity** (email only, if enabled)

Given `source_channel = "email"` and a non-null `event.external_thread_id`:

1. Check global disable flag.
2. Check thread-specific override (`disabled` or `force:<butler>`).
3. Query `routing_log` for recent history within TTL window (default 30 days):

```sql
SELECT target_butler, MAX(created_at) AS last_routed_at
FROM routing_log
WHERE source_channel = 'email'
  AND thread_id = :tid
  AND created_at >= NOW() - (:ttl_days || ' days')::INTERVAL
GROUP BY target_butler
ORDER BY last_routed_at DESC
LIMIT 2;
```

4. Decision: 0 rows = miss (continue), 1 row = hit (route directly), 2+ distinct butlers = conflict (continue to LLM).

Thread affinity never causes a hard failure. Lookup errors increment `miss` with `reason=error` and fall through.

**Stage 2: Triage rules**

Rules are stored in `switchboard.triage_rules`:

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | Primary key |
| `rule_type` | TEXT | `sender_domain`, `sender_address`, `header_condition`, `mime_type` |
| `condition` | JSONB | Type-specific matching condition |
| `action` | TEXT | `skip`, `metadata_only`, `low_priority_queue`, `pass_through`, `route_to:<butler>` |
| `priority` | INTEGER | Evaluation order (lower = higher priority) |
| `enabled` | BOOLEAN | Dispatch gate |
| `created_by` | TEXT | `dashboard`, `api`, `seed` |
| `deleted_at` | TIMESTAMPTZ | Soft-delete marker |

Rules are evaluated in `priority ASC, created_at ASC, id ASC` order. First match wins.

**Condition schemas:**

- `sender_domain` -- `{"domain": "...", "match": "exact|suffix"}`. Suffix match handles subdomains.
- `sender_address` -- `{"address": "..."}`. Lowercase RFC 5322 form.
- `header_condition` -- `{"header": "...", "op": "present|equals|contains", "value?": "..."}`.
- `mime_type` -- `{"type": "..."}`. Supports wildcard subtype (`image/*`).

**Triage output:**

```json
{
  "decision": "route_to|skip|metadata_only|low_priority_queue|pass_through",
  "target_butler": "<butler name or null>",
  "matched_rule_id": "<uuid or null>",
  "matched_rule_type": "<rule_type or thread_affinity or null>",
  "reason": "<human-readable explanation>"
}
```

**Runtime cache:** Active rules are cached in memory, refreshed every 60 seconds and on mutation events. Reload is atomic (full rule set swap). On failure, the cache fails open (`pass_through`).

**Stage 3: LLM classification fallback**

Messages that pass through triage without a routing decision are submitted to an LLM-based classifier. The classifier receives the normalized text, sender identity preamble, and butler registry (names, descriptions, domains) and returns a target butler name.

### route.execute Envelope

The Switchboard dispatches classified messages to target butlers via the `route.execute` MCP tool. The envelope includes:

- Schema version
- Request context (request_id, timestamps, source metadata, trace context)
- Input (the message content with identity preamble prepended)
- Subrequest metadata
- Source metadata (channel, provider, endpoint identity)
- Trace context for cross-butler span correlation (see RFC 0005)

Interactive channels (Telegram, WhatsApp) receive additional delivery guidance instructing the LLM to use `notify()` for reply delivery.

### Route Inbox and Crash Recovery

When a target butler receives `route.execute`, it:

1. Persists the request to `route_inbox` in `accepted` state.
2. Returns `{"status": "accepted"}` immediately.
3. A background task transitions the row to `processing` and calls `spawner.trigger()`.
4. On success, the row transitions to `processed` with the `session_id`.
5. On failure, the row transitions to `errored` with the error message.

State machine:

```
accepted --> processing --> processed (session_id stored)
                       \--> errored   (error message stored)
```

**Crash recovery:** On startup, each butler scans for rows in `accepted` or `processing` state older than a configurable grace period (default 10 seconds) and re-dispatches them.

### Email Priority Queuing

The Gmail connector assigns `control.policy_tier` before submitting to the Switchboard:

| Tier | Condition | Priority |
|------|-----------|----------|
| `high_priority` | Sender matches known contact address (cached, refreshed every 15 min) | 1 (highest) |
| `high_priority` | `In-Reply-To` references a user-sent `Message-ID` | 1 |
| `interactive` | User in `To`/`Cc`, no `List-Unsubscribe`, no bulk `Precedence` | 2 |
| `default` | All other messages | 3 (lowest) |

The Switchboard's `DurableBuffer` dequeues in tier order with FIFO within each tier. A starvation guard (`max_consecutive_same_tier`, default 10) forces a lower-tier dequeue after N consecutive same-tier dequeues when lower-priority queues are non-empty.

### Heartbeat Protocol

Connectors send `connector.heartbeat.v1` envelopes every 2 minutes via the `connector.heartbeat` MCP tool. The Switchboard derives liveness: `online` (< 2 min since last heartbeat), `stale` (2-4 min), `offline` (> 4 min).

## Integration

- **RFC 0001:** `route.execute` triggers arrive at the daemon and enter the session lifecycle.
- **RFC 0002:** `route.execute` is registered as a core tool on every butler.
- **RFC 0004:** Identity resolution produces the sender preamble prepended to routed messages.
- **RFC 0005:** Trace context propagates through the `control.trace_context` and `request_context.trace_context` fields.
- **RFC 0006:** `routing_log`, `triage_rules`, `route_inbox`, and `ingestion_events` tables live in the switchboard schema.
- **RFC 0011:** The insight broker module runs within the Switchboard daemon. Candidate submissions arrive as `propose_insight_candidate` MCP tool calls, and the delivery cycle runs as a Switchboard scheduled task.

## Alternatives Considered

**LLM-only classification.** Rejected due to cost and latency. At 5-15 seconds per classification in serial mode, a burst of 20 emails creates multi-minute wait times. Triage eliminates 50-70% of calls.

**Connector-side routing.** Rejected because connectors should be transport adapters only. Moving routing logic into connectors would create inconsistent classification across channels and eliminate the Switchboard's role as a central audit point.

**Eager processing instead of route inbox.** Rejected because synchronous processing in the `route.execute` handler would block the Switchboard's MCP event loop during session execution, preventing it from accepting further ingestion requests. The async inbox-and-background-task pattern decouples acceptance from processing.
