# Connector Base Contract Obligations

Every connector must implement ALL of these. Read `openspec/specs/connector-base-spec/spec.md` for the canonical source. This is a quick-reference checklist of what's easy to miss.

## Obligations Checklist

### 1. ingest.v1 Envelope (every event)

Every submitted event must include:

```
source.channel        = "<channel>"           # registered in RFC 0003
source.provider       = "<provider>"          # registered in RFC 0003
source.endpoint_identity = "<provider>:<scope>:<id>"  # e.g., "steam:user:76561..."
event.type            = "<event_type>"        # service-specific
event.external_event_id = "<globally_unique>" # for dedup
event.observed_at     = "<RFC3339>"           # poll timestamp
sender.identity       = "<provider>:<id>"     # who generated this event
payload.raw           = <full API response>   # Tier 1 = full, Tier 2 = null
payload.normalized_text = "<human summary>"   # one-line summary
control.idempotency_key = "<dedup key>"       # typically = external_event_id
control.policy_tier   = "default"             # or "interactive" for user-initiated
control.ingestion_tier = "full"               # or "metadata" for Tier 2
```

### 2. Filtered Event Batch Flush

After each poll cycle, flush buffered events to `connectors.filtered_events`:

- **Filtered events**: `status='filtered'`, `filter_reason=<rule description>`
- **Error events**: `status='error'`, `error_detail=<exception message>`
- Required columns: `connector_type`, `endpoint_identity`, `source_channel`, `sender_identity`, `subject_or_preview`, `full_payload`
- Flush failure = warning log, does NOT block cursor advancement

### 3. Replay Queue Drain Loop

After each poll cycle (after flush):

```python
# Query pending replays
rows = SELECT * FROM connectors.filtered_events
    WHERE status = 'replay_pending'
    AND connector_type = '<service>'
    AND endpoint_identity = '<endpoint>'
    FOR UPDATE SKIP LOCKED
    LIMIT 10

for row in rows:
    envelope = deserialize(row.full_payload)
    result = submit_to_switchboard(envelope)
    update status = 'replay_complete' or 'replay_failed'
```

### 4. Source Filter Gate

Before submitting any event to Switchboard:

- Initialize `IngestionPolicyEvaluator` with scope `'connector:<service>:<endpoint>'` at startup
- Call `ensure_loaded()` to load rules from DB
- Evaluate each event; if a `block` rule matches, skip submission and record in filtered_events
- No rules = all events pass (opt-in model)
- Define which filter key types your connector supports (e.g., `app_id`, `event_type`, `sender_identity`)

### 5. Heartbeat Protocol

Send `connector.heartbeat.v1` at a configurable interval (default 60s):

```
identity.connector_type = "<service>"
identity.endpoint_identity = "<comma-separated active endpoints>"
health.status = "healthy" | "degraded" | "error"
health.active_accounts = <count>
counters.events_submitted = <total>
counters.events_filtered = <total>
counters.errors = <total>
checkpoint.last_poll_at = <most recent timestamp>
```

Heartbeat failure = warning, continue operating.

### 6. Prometheus Metrics

Export these counter/histogram families (substitute `<service>`):

- `connector_<service>_polls_total{data_type, endpoint_identity, status}`
- `connector_<service>_events_submitted_total{event_type, endpoint_identity}`
- `connector_<service>_events_filtered_total{filter_reason, endpoint_identity}`
- `connector_<service>_api_errors_total{endpoint_identity, http_status}`
- `connector_<service>_api_latency_seconds{data_type, endpoint_identity}` (histogram)
- `connector_<service>_rate_limit_backoffs_total{endpoint_identity}`

### 7. Health Status Endpoint

Aggregate health across all accounts:

- `status`: worst-case (`healthy` > `degraded` > `error`)
- Per-account: endpoint_identity, status, last_poll_at, error
- Redact sensitive identifiers (email → `te***@gmail.com`, SteamID → `7656***0000`)

### 8. Multi-Account Discovery

- Query account registry at startup for active accounts
- Spawn independent loops per account
- Re-scan periodically (configurable interval, default 300s)
- Gracefully shutdown loops for revoked/suspended accounts
- Start in idle/degraded mode if no accounts found

### 9. Cursor Persistence

- Per-account, per-data-type cursors
- Store in `connectors.<service>_cursors` table
- Load on restart for crash-safe resume
- Advance cursor AFTER successful submission (at-least-once delivery)

### 10. Rate Limiting

- Per-account backoff (one account's rate limit does not affect others)
- Exponential backoff on 429/403 (source API) and 5xx (transient)
- Track consecutive failure count; transition to `error` health after threshold

## What Connectors Do NOT Do

- Classify messages (Switchboard does this)
- Route to specific butlers (Switchboard does this)
- Mint canonical `request_id` (Switchboard does this)
- Bypass the Switchboard ingestion path
- Store credentials directly (use entity_info via companion entity)
