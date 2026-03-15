## Why

Every connector ingestion event already receives a UUID7 `request_id` minted by the Switchboard, and sessions already carry that ID — but it is a bare UUID with no backing table. There is no record of what the event actually was, so we cannot retroactively answer: what was the source of this session? How many tokens did this single inbound message consume across all butlers? Which butlers were invoked, in what order, and at what cost? A first-class `shared.ingestion_events` table anchors the `request_id` to real metadata and makes every session traceable back to its originating event, enabling provenance, cost attribution, and topology queries across the entire butler graph.

## What Changes

- `request_id` (UUID7) is now **always non-null** on every session — minted by the Switchboard for connector-sourced sessions, or by the daemon itself at spawn time for internally-triggered sessions (tick, schedule, trigger)
- New `shared.ingestion_events` table with UUID7 primary key, written by the Switchboard at accept-time (one row per canonical ingestion event, deduplicated)
- Sessions gain a nullable FK: `sessions.request_id` remains the correlation ID (always set), and a new `sessions.ingestion_event_id` column is a nullable FK into `shared.ingestion_events.id` — populated only for connector-sourced sessions
- Switchboard ingest path writes the ingestion event row before returning `IngestAcceptedResponse` (inside the existing advisory-lock transaction), then passes the same UUID7 as `request_id` to the spawned session
- `/butlers/traces` is **removed**; its functionality is superseded by a new **Timeline** tab under `/butlers/ingestion`, unified on `request_id` instead of `trace_id` — one inbound event → all sessions, butlers, tokens, cost, and OTel span links in a single view
- `g then r` keyboard shortcut (Traces) is removed; `g then e` (Ingestion) now navigates to the page that subsumes it

## Capabilities

### New Capabilities

- `ingestion-event-registry`: The `shared.ingestion_events` table schema, the Switchboard write path that populates it, FK wiring to sessions, and the query API (get by ID, list by source/thread/sender, aggregate token/cost rollups per ingestion event)

### Modified Capabilities

- `core-sessions`: `request_id` changes from bare nullable UUID to a **required** UUID7 (never null); a new nullable `ingestion_event_id` FK column is added pointing into `shared.ingestion_events.id`; the spawner is responsible for minting a UUID7 when no ingestion event is present
- `connector-base-spec`: Switchboard's ingest accept path now guarantees writing to `shared.ingestion_events` before returning `request_id` to the connector — the returned UUID7 is subsequently passed through as both `request_id` and `ingestion_event_id` on the spawned session
- `dashboard-shell`: **BREAKING** — `/traces` route and Telemetry nav entry removed; `g then r` shortcut removed; Timeline tab added to `/ingestion` with route `/ingestion?tab=timeline`; `/traces` and `/traces/:traceId` redirect to `/ingestion?tab=timeline`
- `dashboard-api`: **BREAKING** — `/api/traces` and `/api/traces/{traceId}` endpoints removed; replaced by request-lineage endpoints under the ingestion API namespace (sessions by `request_id`, rollup of token/cost/butler topology per event)

## Impact

- **Database**: New migration adding `shared.ingestion_events` table and FK constraint on all `sessions` tables (per-butler schemas); existing rows with orphaned `request_id` values will need a backfill or NULL tolerance during migration
- **Switchboard module**: Ingest handler writes to `shared.ingestion_events` inside the existing dedup transaction — no new round-trip, same advisory lock window
- **Session creation**: `session_create()` now requires `request_id` (never NULL — callers must mint a UUID7 if none was provided by the Switchboard); `ingestion_event_id` is a separate optional parameter, set only for connector-sourced sessions
- **Dashboard API**: `/api/traces` and `/api/traces/{traceId}` removed; new read-only endpoints added under the ingestion namespace; no write surface from the API layer
- **Dashboard shell**: `/traces` and `/traces/:traceId` routes removed from router and Telemetry nav group; redirects added so any bookmarked `/traces` URLs land on `/ingestion?tab=timeline`; `g then r` keybinding removed
- **No connector changes**: Connectors already submit envelopes and receive `request_id` back — their interface is unchanged
