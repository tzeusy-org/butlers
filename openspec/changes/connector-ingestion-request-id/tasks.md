## 1. Shared Utility

- [ ] 1.1 Extract `_generate_uuid7_string()` from `pipeline.py` into `butlers/core/utils.py` and import it back in `pipeline.py`

## 2. Database Migration

- [ ] 2.1 Write Alembic migration: create `shared.ingestion_events` table with columns `id` (UUID7 PK), `received_at`, `source_channel`, `source_provider`, `source_endpoint_identity`, `source_sender_identity`, `source_thread_identity`, `external_event_id`, `dedupe_key`, `dedupe_strategy`, `ingestion_tier`, `policy_tier`, `triage_decision`, `triage_target`, and UNIQUE on `dedupe_key`
- [ ] 2.2 Write Alembic migration: add `ingestion_event_id UUID REFERENCES shared.ingestion_events(id)` (nullable) to all butler `sessions` tables
- [ ] 2.3 Write Alembic migration: set `sessions.request_id` NOT NULL (backfill any existing null rows with `gen_random_uuid()` first)

## 3. Switchboard Pipeline Module

- [ ] 3.1 In `pipeline.py` `_handle_ingest_accept`: inside the advisory-lock transaction, after the `INSERT INTO message_inbox`, also insert a row into `shared.ingestion_events` using the same UUID7 (use the existing `switchboard` pool which has access to `shared` schema)
- [ ] 3.2 Ensure duplicate submissions skip the `shared.ingestion_events` insert (same guard as `message_inbox` — return existing `request_id` when `inserted=false`)

## 4. Sessions Module

- [ ] 4.1 Update `session_create()` signature: change `request_id: str | None = None` to `request_id: str` (required); add `ingestion_event_id: str | None = None`; raise `ValueError` if `request_id` is `None`
- [ ] 4.2 Add `ingestion_event_id` to the `INSERT INTO sessions` statement and `RETURNING` / read-back in `session_create()`
- [ ] 4.3 Add `ingestion_event_id` to the persisted fields list in `sessions_get()` and `sessions_list()` return values

## 5. Spawner

- [ ] 5.1 In `spawner.py`: import `generate_uuid7_string` from `butlers.core.utils`
- [ ] 5.2 In the internal session trigger paths (tick handler, scheduler dispatch in `daemon.py`): mint a UUID7 and pass it as `request_id` to `session_create()`; pass `ingestion_event_id=None`
- [ ] 5.3 In the connector-sourced session path: pass the pipeline-provided `request_id` as both `request_id` and `ingestion_event_id` to `session_create()`

## 6. Ingestion Events Query Module

- [ ] 6.1 Create `butlers/core/ingestion_events.py` with `ingestion_event_get(pool, event_id) -> dict | None`
- [ ] 6.2 Add `ingestion_events_list(pool, limit=20, offset=0, source_channel=None) -> list[dict]`
- [ ] 6.3 Add `ingestion_event_sessions(pool, request_id) -> list[dict]` — fan-out across all butler schemas joining on `request_id`; include `butler_name`, session fields, and `trace_id`
- [ ] 6.4 Add `ingestion_event_rollup(pool, request_id) -> dict` — aggregate `total_sessions`, `total_input_tokens`, `total_output_tokens`, `total_cost`, and `by_butler` breakdown using the `cost` JSONB field on sessions

## 7. Dashboard API

- [ ] 7.1 Create `src/butlers/api/routers/ingestion_events.py` with `GET /api/ingestion/events` (paginated list with optional `source_channel` filter)
- [ ] 7.2 Add `GET /api/ingestion/events/{requestId}` endpoint (single event detail)
- [ ] 7.3 Add `GET /api/ingestion/events/{requestId}/sessions` endpoint (cross-butler lineage)
- [ ] 7.4 Add `GET /api/ingestion/events/{requestId}/rollup` endpoint (token/cost/butler topology)
- [ ] 7.5 Register the new router in the API factory; remove (or comment out) the `/api/traces` and `/api/traces/{traceId}` handlers

## 8. Dashboard Frontend — Routing and Navigation

- [ ] 8.1 Remove `/traces` and `/traces/:traceId` route definitions from the router; add `<Navigate replace to="/ingestion?tab=timeline" />` for both paths
- [ ] 8.2 Remove the Traces nav item from the Telemetry section in the sidebar navigation config
- [ ] 8.3 Remove the `g then r` keybinding from `useKeyboardShortcuts`
- [ ] 8.4 Update the shortcut hints dialog to remove the Traces entry

## 9. Dashboard Frontend — Timeline Tab on Ingestion Page

- [ ] 9.1 Add a Timeline tab to the `/ingestion` page tab list (alongside Connectors)
- [ ] 9.2 Create `use-ingestion-events.ts` with `useIngestionEvents(filters)` hook (calls `GET /api/ingestion/events`, 30s stale time, cache key `["ingestion", "events", filters]`)
- [ ] 9.3 Add `useIngestionEventLineage(requestId)` hook that fetches sessions and rollup in parallel (cache keys `["ingestion", "events", requestId, "sessions"]` and `["ingestion", "events", requestId, "rollup"]`, 30s stale time)
- [ ] 9.4 Build the Timeline tab UI: table/list of ingestion events with `request_id`, `received_at`, `source_channel`, `source_sender_identity`, session count, and total cost; clicking an event expands a lineage view showing all downstream sessions with `butler_name`, duration, token counts, and `trace_id` as an external link
- [ ] 9.5 Remove `useTraces` hook and delete the Traces page component

## 10. Tests

- [ ] 10.1 Unit test `ingestion_event_get`, `ingestion_events_list`, `ingestion_event_rollup` with a test DB fixture
- [ ] 10.2 Unit test `session_create()` raises `ValueError` when `request_id=None`
- [ ] 10.3 Unit test spawner mints a UUID7 for internally-triggered sessions (mock `generate_uuid7_string`)
- [ ] 10.4 Integration test the Switchboard pipeline: verify that accepting an ingest envelope inserts a row in both `message_inbox` and `shared.ingestion_events` with the same UUID7
- [ ] 10.5 API test `GET /api/ingestion/events/{requestId}/sessions` returns sessions across butler schemas
