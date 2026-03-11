## Why

Connector-side filtering decisions (skip, metadata_only, label exclusion, validation errors) are invisible — they exist only in connector stdout logs and vanish on restart. When an email like a Stripe receipt fails to ingest due to a validation bug, there is no persistent record and no way to replay it from the UI. Operators must SSH in, grep logs, manually roll back cursors, and restart connectors to recover. The ingestion timeline (`/butlers/ingestion?tab=timeline`) only shows successfully-ingested events, giving an incomplete picture of what the system actually received.

## What Changes

- **New `connectors` Postgres schema** — a dedicated schema owned by connector processes, decoupling connector-side persistence from the `switchboard` schema. Houses filtered event records, replay queue, and (migrated) connector registry/checkpoint state.
- **Filtered events table** (`connectors.filtered_events`) — every message a connector sees but does not submit to Switchboard is persisted here with full payload, filter reason, and status. Monthly partitioned with configurable retention. Written via batch flush at the end of each poll cycle, not per-message.
- **Replay queue table** (`connectors.replay_queue`) — a durable queue where the UI (or operator) marks messages for re-ingestion. Connectors drain this queue on each poll cycle and submit stored payloads to `ingest_v1` as normal. Dedup-safe: filtered and errored messages have no prior `ingestion_events` row.
- **Batch flush write path** — connectors accumulate filtered events in memory during a poll cycle and INSERT them in a single batch after the cycle completes. Best-effort: crash mid-cycle loses unflushed events (acceptable for operational visibility data).
- **Ingestion timeline UI enhancements** — the timeline at `/butlers/ingestion?tab=timeline` gains two new columns and a unified data source:
  - **Status column** — `ingested`, `filtered`, `error`, `replaying`, showing the outcome for every message the system touched.
  - **Action column** — a "Replay" button for filtered/errored events that INSERTs into `connectors.replay_queue`. Disabled for already-ingested events.
  - **Unified data source** — timeline queries `shared.ingestion_events` UNION `connectors.filtered_events` to show all events regardless of outcome, ordered by received_at.

## Capabilities

### New Capabilities
- `connector-filtered-events`: Persistent record of connector-side filtered/errored messages with full payload. Covers the `connectors` schema, `filtered_events` table (monthly partitioned), batch flush write path, and DB role/permissions for connector processes.
- `connector-replay-queue`: Durable replay queue enabling re-ingestion of filtered/errored messages. Covers the `replay_queue` table, connector drain loop, and replay lifecycle (pending → in_progress → completed/failed).

### Modified Capabilities
- `ingestion-event-registry`: Timeline query functions must UNION with `connectors.filtered_events` to produce a unified event stream. Response model gains `status` field.
- `dashboard-visibility`: Ingestion timeline table gains Status and Action columns. Action column renders Replay button for filtered/errored rows and calls a new API endpoint to enqueue replay.
- `connector-base-spec`: Base connector contract gains filtered-event batch flush obligation and replay queue drain loop in the poll cycle.

## Impact

- **Database**: New `connectors` schema, two new tables, monthly partition management, new DB role with USAGE/CREATE on `connectors` and SELECT on `shared`.
- **Connector code**: All connectors (Gmail, Telegram bot, Telegram user-client, Discord) gain batch flush + replay drain. Gmail connector is the reference implementation; others follow the same base-contract pattern.
- **Backend API**: New endpoint `POST /api/ingestion/events/{id}/replay` to enqueue replay. Modified `GET /api/ingestion/events` to return unified stream with status field.
- **Frontend**: `TimelineTab.tsx` updated with Status and Action columns. New API client method for replay. Status badge component (color-coded by outcome).
- **Migrations**: Alembic migration for `connectors` schema + tables. Optional follow-up migration to move `switchboard.connector_registry` → `connectors.connector_registry`.
