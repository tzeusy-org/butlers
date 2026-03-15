## Context

Connectors are standalone processes that poll external messaging systems and submit normalized events to the Switchboard. Today, their only persistent state is the checkpoint cursor in `switchboard.connector_registry`. When a connector filters a message (label exclusion, ingestion policy rule, validation error), the decision exists only in stdout logs — no DB record, no UI visibility, no replay path.

The ingestion timeline at `/butlers/ingestion?tab=timeline` reads from `shared.ingestion_events`, which only contains successfully-accepted messages. Operators have no way to see what was filtered, why, or to recover from connector bugs (like the `IngestAttachment` validation failure that silently dropped PDF-bearing emails).

Connectors currently write to `switchboard.*` tables even though they are not the Switchboard. This couples connector persistence to the Switchboard's schema ownership.

## Goals / Non-Goals

**Goals:**
- Give connectors their own Postgres schema (`connectors`) with clear ownership boundaries
- Persist every message a connector touches — ingested, filtered, or errored — for operational visibility
- Enable replay of filtered/errored messages from the dashboard without manual cursor manipulation
- Show a unified ingestion timeline with Status and Action columns covering all event outcomes
- Keep the hot ingestion path fast via batch-flush writes for filtered events

**Non-Goals:**
- Migrating `switchboard.connector_registry` to `connectors.connector_registry` in this change (follow-up)
- Building a bulk replay UI (single-message replay only)
- Changing the Switchboard's deduplication logic
- Adding filtered-event persistence for the backfill pipeline (regular polling only)
- Real-time filtered event streaming / WebSocket push

## Decisions

### D1: Dedicated `connectors` schema

Connectors get their own Postgres schema. A new DB role `connector_writer` has USAGE + CREATE on `connectors` and SELECT on `shared` (for credential/contact resolution). All connector processes use this role for filtered-event and replay-queue writes.

**Alternative considered:** Extending `switchboard.*` with new tables. Rejected because connectors are not the Switchboard — mixing ownership creates ambiguous migration responsibility and permission sprawl.

### D2: `connectors.filtered_events` table (monthly partitioned)

```sql
CREATE TABLE connectors.filtered_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    connector_type  TEXT NOT NULL,          -- 'gmail', 'telegram_bot', etc.
    endpoint_identity TEXT NOT NULL,        -- 'gmail:user:dev', 'telegram:bot:mybot'
    external_message_id TEXT NOT NULL,      -- Gmail message ID, Telegram update_id, etc.
    source_channel  TEXT NOT NULL,          -- 'email', 'telegram', 'discord'
    sender_identity TEXT NOT NULL,          -- From header, Telegram user ID
    subject_or_preview TEXT,               -- Subject line or first 200 chars
    filter_reason   TEXT NOT NULL,          -- 'label_exclude:CATEGORY_PROMOTIONS', 'global_rule:skip:sender_domain', 'validation_error:...'
    status          TEXT NOT NULL DEFAULT 'filtered',  -- 'filtered', 'error', 'replay_pending', 'replay_complete', 'replay_failed'
    full_payload    JSONB NOT NULL,         -- Complete normalized payload for replay
    error_detail    TEXT,                   -- Stack trace or validation error for status='error'
    replay_requested_at TIMESTAMPTZ,       -- When replay was requested
    replay_completed_at TIMESTAMPTZ,       -- When replay finished
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE (received_at);
```

Monthly partitions auto-created by a partition management function (same pattern as `message_inbox`). Retention: 90 days default, configurable.

**Key index:** `(connector_type, endpoint_identity, status, received_at DESC)` for the connector drain query and dashboard listing.

**Alternative considered:** Separate `replay_queue` table. Rejected in favor of using `status` column on `filtered_events` itself — replay is just a state transition on the filtered event, not a separate entity. This avoids join complexity and keeps the replay lifecycle in one place.

### D3: Replay as status transition (no separate queue table)

Instead of a separate `replay_queue` table, replay is modeled as a status transition on `filtered_events`:

```
filtered → replay_pending → replay_complete
error    → replay_pending → replay_complete
                          → replay_failed
```

The connector's drain loop queries: `SELECT ... FROM connectors.filtered_events WHERE status = 'replay_pending' AND connector_type = $1 AND endpoint_identity = $2 ORDER BY received_at ASC LIMIT 10 FOR UPDATE SKIP LOCKED`. This handles concurrency if multiple connector instances exist.

On replay, the connector deserializes `full_payload`, builds an `ingest.v1` envelope, and submits to `ingest_v1` normally. Since filtered/errored messages never reached `ingest_v1`, there is no deduplication conflict.

For messages that DID reach `ingest_v1` but failed after acceptance (edge case), the dedup key would match and `ingest_v1` returns `duplicate=true` — harmless, status transitions to `replay_complete`.

### D4: Batch flush for filtered events

Connectors accumulate filtered events in an in-memory list during each poll cycle. After the cycle completes (all messages processed, cursor advanced), the list is flushed in a single `INSERT ... VALUES (...), (...)` statement.

```python
class FilteredEventBuffer:
    def __init__(self): self._buffer: list[dict] = []
    def record(self, event: dict): self._buffer.append(event)
    async def flush(self, pool: asyncpg.Pool):
        if not self._buffer:
            return
        await pool.executemany(INSERT_SQL, [self._to_row(e) for e in self._buffer])
        self._buffer.clear()
```

Crash mid-cycle loses unflushed events. Acceptable: filtered events are operational visibility, not audit trail. The successfully-ingested events in `shared.ingestion_events` remain authoritative.

### D5: Unified timeline query

The ingestion timeline API (`GET /api/ingestion/events`) returns a unified stream by querying both tables:

```sql
SELECT id, received_at, source_channel, sender_identity, ingestion_tier, policy_tier,
       triage_decision, 'ingested' AS status, NULL AS filter_reason
FROM shared.ingestion_events
WHERE received_at BETWEEN $start AND $end

UNION ALL

SELECT id, received_at, source_channel, sender_identity, NULL, NULL,
       NULL, status, filter_reason
FROM connectors.filtered_events
WHERE received_at BETWEEN $start AND $end

ORDER BY received_at DESC
LIMIT $limit OFFSET $offset
```

The response model gains a `status` field (`ingested`, `filtered`, `error`, `replay_pending`, `replay_complete`, `replay_failed`) and a `filter_reason` field (null for ingested events).

### D6: Timeline UI columns

The `TimelineTab.tsx` component gains two columns:

- **Status** — color-coded badge: green=`ingested`, gray=`filtered`, red=`error`, blue=`replay_pending`, green-outline=`replay_complete`. Replaces implicit "everything here is ingested" assumption.
- **Action** — "Replay" button, visible for `filtered` and `error` rows. Calls `POST /api/ingestion/events/{id}/replay`. Disabled/hidden for `ingested` and `replay_*` rows. Shows spinner while `replay_pending`, checkmark on `replay_complete`.

New API endpoint:
```
POST /api/ingestion/events/{id}/replay
```
Updates `connectors.filtered_events` SET `status = 'replay_pending', replay_requested_at = now()` WHERE `id = $1 AND status IN ('filtered', 'error')`. Returns 200 on success, 409 if already replaying/completed.

### D7: Payload shape for `full_payload`

The `full_payload` JSONB column stores enough to reconstruct an `ingest.v1` envelope without re-fetching from the external API. Shape:

```json
{
  "source": { "channel": "email", "provider": "gmail", "endpoint_identity": "gmail:user:dev" },
  "event": { "external_event_id": "<RFC-Message-ID>", "external_thread_id": "...", "observed_at": "..." },
  "sender": { "identity": "Anomaly <invoice@stripe.com>" },
  "payload": { "raw": { ... }, "normalized_text": "Subject: ...\n\n..." },
  "control": { "policy_tier": "default" }
}
```

This is the same shape as the ingest envelope minus `schema_version` (always `ingest.v1`) and minus `attachments` (which caused the original bug and are handled separately via `attachment_refs`). On replay, the connector wraps this in `{"schema_version": "ingest.v1", ...}` and submits.

## Risks / Trade-offs

- **[Storage growth]** → Filtered events accumulate. Mitigation: monthly partitioning + 90-day retention policy that drops old partitions. High-volume connectors (Gmail with many marketing emails) will generate more rows than the ingestion path itself.
- **[Batch flush data loss]** → Connector crash mid-cycle loses unflushed filtered events. Mitigation: acceptable for operational visibility data. Critical path (ingested events) is unaffected.
- **[UNION query performance]** → Querying two tables is slower than one. Mitigation: both tables are partitioned by time; the query uses a time-bounded WHERE clause. Index on `(received_at DESC)` on both tables keeps it fast.
- **[Replay of stale messages]** → Replaying a month-old filtered email may confuse downstream butlers. Mitigation: UI shows the original received_at; butler pipeline already handles out-of-order messages. Consider adding a staleness warning in the UI for events older than 7 days.
- **[Schema permissions]** → Connectors currently use the main `butlers` DB role. Adding a `connector_writer` role requires connection string changes. Mitigation: phase this — initially use the existing role with explicit schema grants, add dedicated role in follow-up.
