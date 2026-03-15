## 1. Database Schema & Migration

- [ ] 1.1 Create Alembic migration: `connectors` schema with `CREATE SCHEMA IF NOT EXISTS connectors`
- [ ] 1.2 Create `connectors.filtered_events` table with all columns from spec (partitioned by RANGE on received_at)
- [ ] 1.3 Create partition management function for monthly auto-creation (same pattern as `message_inbox`)
- [ ] 1.4 Create index on `(connector_type, endpoint_identity, status, received_at DESC)` for connector drain queries
- [ ] 1.5 Create index on `(received_at DESC)` for unified timeline queries
- [ ] 1.6 Grant connector DB role USAGE on `connectors` schema and SELECT on `shared` schema

## 2. Filtered Event Buffer (Core Library)

- [ ] 2.1 Implement `FilteredEventBuffer` class in `src/butlers/connectors/filtered_event_buffer.py` with `record()` and `flush()` methods
- [ ] 2.2 Define `full_payload` serialization helper that builds the envelope-shaped dict from connector-specific message data
- [ ] 2.3 Define `filter_reason` formatting helpers (`label_exclude:`, `global_rule:skip:`, `validation_error`, etc.)
- [ ] 2.4 Write unit tests for buffer accumulation, flush SQL, and reason formatting

## 3. Gmail Connector Integration

- [ ] 3.1 Instantiate `FilteredEventBuffer` in `GmailConnectorRuntime.__init__`
- [ ] 3.2 Record filtered events at label-exclude path (line ~1737) with reason `label_exclude:<label>`
- [ ] 3.3 Record filtered events at connector-scope rule path (line ~1750) with reason from policy decision
- [ ] 3.4 Record filtered events at global-scope rule skip path (line ~1760) with reason from policy decision
- [ ] 3.5 Record error events in `_ingest_single_message` except-Exception handler (line ~1793) with status=error
- [ ] 3.6 Add `await self._filtered_buffer.flush(pool)` after poll cycle completes (after cursor save)
- [ ] 3.7 Add replay drain loop after flush: query `replay_pending` rows, submit via `_submit_to_ingest_api`, update status
- [ ] 3.8 Write integration test: message filtered → row in `filtered_events` → replay → row in `ingestion_events`

## 4. Other Connector Integration

- [ ] 4.1 Add `FilteredEventBuffer` to Telegram bot connector with flush + drain
- [ ] 4.2 Add `FilteredEventBuffer` to Telegram user-client connector with flush + drain
- [ ] 4.3 Add `FilteredEventBuffer` to Discord connector with flush + drain
- [ ] 4.4 Write unit tests for at least one non-Gmail connector's filtered event path

## 5. Backend API

- [ ] 5.1 Modify `ingestion_events_list()` in `src/butlers/core/ingestion_events.py` to UNION `shared.ingestion_events` with `connectors.filtered_events`, adding `status` and `filter_reason` fields
- [ ] 5.2 Add `status` filter parameter to `GET /api/ingestion/events` endpoint
- [ ] 5.3 Create `POST /api/ingestion/events/{id}/replay` endpoint in `src/butlers/api/routers/ingestion_events.py` that updates status to `replay_pending`
- [ ] 5.4 Update response model with `status` and `filter_reason` fields
- [ ] 5.5 Write API tests for unified list, status filter, and replay endpoint (200, 404, 409 cases)

## 6. Frontend: Timeline Columns

- [ ] 6.1 Add `status` field to `IngestionEvent` TypeScript type and API client response
- [ ] 6.2 Create `StatusBadge` component with color-coded rendering (green/gray/red/blue per status)
- [ ] 6.3 Add Status column to `TimelineTab.tsx` table after Sender column
- [ ] 6.4 Add filter_reason tooltip on hover for filtered/error status badges
- [ ] 6.5 Add Action column as last column with Replay/Retry button
- [ ] 6.6 Implement `replayIngestionEvent()` API client method calling `POST /api/ingestion/events/{id}/replay`
- [ ] 6.7 Wire Replay button click → API call → optimistic status update → error toast on failure
- [ ] 6.8 Add Status filter dropdown to filter bar with options: All, Ingested, Filtered, Error, Replay Pending, Replay Complete, Replay Failed
- [ ] 6.9 Disable row expansion (flamegraph) for filtered event rows (no sessions to show)
- [ ] 6.10 Write component tests for StatusBadge rendering and Replay button states
