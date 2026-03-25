## Context

The butler framework has a Calendar module (`src/butlers/modules/calendar.py`) that provides Google Calendar CRUD via MCP tools, but no connector to watch for external calendar changes and ingest them into the Switchboard. Butlers only learn about calendar changes when an LLM session explicitly calls calendar tools. The Gmail connector (`src/butlers/connectors/gmail.py`) already demonstrates the multi-account Google connector pattern: discover accounts from `shared.google_accounts`, resolve OAuth credentials from `butler_secrets` + `entity_info`, spawn per-account poll loops, checkpoint via `cursor_store`, submit `ingest.v1` envelopes to Switchboard.

The Google Calendar API supports two change detection mechanisms:
1. **Incremental sync** via `events.list` with `syncToken` — returns only changed events since the last sync. Polling-based, simple, reliable.
2. **Push notifications** via `events.watch` — webhook-based, near real-time, requires public endpoint. Same pattern as Gmail Pub/Sub mode.

The Calendar module already requires the `calendar` scope in `google_accounts.granted_scopes`, so no additional OAuth consent is needed.

## Goals / Non-Goals

**Goals:**
- Ingest calendar event changes (created, updated, deleted) into the Switchboard in near real-time
- Synthesize "event starting soon" notifications at configurable lead times
- Reuse the Gmail connector's multi-account architecture (shared.google_accounts, credential resolution, per-account loops)
- Follow the connector-base-spec contract (ingest.v1, checkpoint-after-acceptance, heartbeat, metrics, filtered events, replay queue)

**Non-Goals:**
- Calendar CRUD mutations (already handled by the Calendar module)
- Replacing the Calendar module's sync/projection system (the connector feeds the Switchboard; the module manages its own projection)
- Push notification mode in v1 (polling via syncToken is sufficient; push can be added later following the Gmail Pub/Sub pattern)
- Backfill mode (calendar history is finite and already accessible via the module's full sync; not a priority like email backfill)
- Free/busy or scheduling intelligence (downstream butler concern, not connector scope)

## Decisions

### Decision 1: Polling via syncToken (not push notifications) for v1

Google Calendar's incremental sync via `events.list(syncToken=...)` returns only events that changed since the last sync. This is the simplest reliable approach.

**Why not push (events.watch)?** Push requires a publicly routable webhook endpoint, watch subscription renewal every 7 days, and handling of missed notifications. The Gmail connector already handles this complexity for Pub/Sub mode, and the same pattern can be added to the calendar connector later. For v1, polling at 60-second intervals provides adequate latency for calendar use cases (events are rarely time-critical at the second level).

**Alternative considered:** Push-first with polling safety net (like Gmail). Rejected for v1 because calendar change frequency is orders of magnitude lower than email, making the added complexity unjustified.

### Decision 2: Synthetic "event starting soon" notifications

The connector SHALL maintain a forward-looking window of upcoming events and emit synthetic `event_starting_soon` events at a configurable lead time (default 15 minutes). This is a connector-side synthesis, not a Google API feature.

**Implementation:** After each sync cycle, the connector scans the known upcoming events and checks if any fall within the lead-time window. Events that newly enter the window trigger an `event_starting_soon` ingest envelope. A seen-set (keyed by `event_id + lead_time`) prevents duplicate notifications for the same event.

**Why at the connector?** The Calendar module already knows about events, but connectors are the sole ingestion pathway. A butler cannot act on an upcoming event unless the Switchboard dispatches it. The connector is the right place to synthesize time-triggered notifications.

### Decision 3: Reuse Gmail's multi-account architecture

The connector follows the exact same pattern as the Gmail connector:
- Query `shared.google_accounts` for active accounts with `calendar` in `granted_scopes`
- Resolve credentials per-account (`client_id`/`client_secret` from `butler_secrets`, `refresh_token` from companion entity in `entity_info`)
- Spawn independent asyncio poll loops per account
- Dynamic account discovery via periodic re-scan (default 300s)
- Per-account error isolation

### Decision 4: syncToken as cursor

The Google Calendar API's `syncToken` is the natural checkpoint. It is opaque, stable, and server-issued. The connector persists it via `cursor_store` keyed by `google_calendar:user:<email>`. On first run (no cursor), a full sync is performed to establish the baseline token. Subsequent polls use incremental sync.

**Expired syncToken handling:** Google may invalidate a syncToken (returns 410 Gone). The connector falls back to a full sync, re-establishes the token, and continues. This matches the Calendar module's existing `_handle_expired_sync_token` logic.

### Decision 5: Event type classification in normalized_text

The `ingest.v1` envelope's `normalized_text` field carries a structured summary including the event type (`created`, `updated`, `deleted`, `starting_soon`), event title, time, and attendees. This gives the Switchboard router enough signal for domain classification without needing calendar-specific routing logic.

### Decision 6: Source channel/provider naming

- `source.channel = "google_calendar"` (distinct from generic `"calendar"` to allow future iCal/Outlook connectors)
- `source.provider = "google_calendar"`
- `endpoint_identity = "google_calendar:user:<email>"`

This follows the pattern where channel and provider match for single-provider channels (like `telegram`/`telegram`).

## Risks / Trade-offs

- **[Risk] Polling latency (~60s)** — Calendar changes are not ingested in real-time. Mitigation: 60s is acceptable for calendar use cases; push mode can be added in a future iteration.
- **[Risk] syncToken invalidation** — Google may invalidate tokens, forcing a full sync. Mitigation: Graceful fallback to full sync with dedup protection at the Switchboard.
- **[Risk] "Starting soon" notification accuracy** — If the connector is down during the lead-time window, the notification is missed. Mitigation: On restart, the connector checks upcoming events and emits any overdue notifications for events that haven't started yet.
- **[Risk] Overlap with Calendar module sync** — Both the connector and the Calendar module poll Google Calendar. Mitigation: They serve different purposes (connector feeds Switchboard for routing; module maintains projection for CRUD tools). The additional API load is minimal given calendar's low change frequency.
- **[Trade-off] No discretion layer** — Unlike messaging connectors, the calendar connector does not use the shared discretion layer. Calendar events are always relevant (the user explicitly created or accepted them). This keeps the connector simple.

## Open Questions

None. The design is straightforward, following established patterns.
