## Why

The butler framework has a Calendar MODULE (Google Calendar CRUD via MCP tools) but no CONNECTOR that watches for calendar changes and ingests them into the Switchboard. Currently calendar data is only accessed when a butler's LLM session explicitly calls calendar tools — butlers have zero real-time awareness of calendar changes. A connector would enable event-driven awareness: new meetings appearing, events being rescheduled, upcoming event reminders — all flowing through the standard ingestion pipeline. This is trivial to build because it reuses the existing Google OAuth infrastructure already in place for Gmail (shared `google_accounts` table, credential resolution, multi-account discovery).

## What Changes

- **New Google Calendar connector** (`src/butlers/connectors/google_calendar.py`): Long-running standalone process that watches for calendar changes via Google Calendar API `events.list` with `syncToken` (polling mode, default) and optionally `events.watch` (push notifications). Normalizes calendar events into `ingest.v1` envelopes and submits to Switchboard. Multi-account support via `shared.google_accounts` (same pattern as Gmail connector). Source channel: `google_calendar`, provider: `google_calendar`.
- **Event types ingested**: event created, event updated, event deleted, event starting soon (configurable lead time, default 15 minutes).
- **Switchboard routing registration**: Add `google_calendar` to `SourceChannel` and `SourceProvider` enums, validate channel-provider pair in `_ALLOWED_PROVIDERS_BY_CHANNEL`.
- **Database migration**: Cursor persistence via existing `cursor_store` (keyed by `google_calendar:user:<email>`). No new tables required — the connector is stateless beyond the sync token cursor.
- **Docker compose**: Add google-calendar-connector service definition.

## Capabilities

### New Capabilities
- `connector-google-calendar`: Standalone connector process that polls/watches Google Calendar for changes, normalizes events to ingest.v1 envelopes, and submits to Switchboard. Multi-account via `shared.google_accounts`, checkpoint-after-acceptance via syncToken cursor, heartbeat protocol, Prometheus metrics, filtered event persistence, replay queue drain.

### Modified Capabilities
- `connector-base-spec`: Add `google_calendar` to `SourceChannel` enum and `google_calendar` to `SourceProvider` enum; add valid channel-provider pair `google_calendar`/`google_calendar`
- `butler-switchboard`: Register `google_calendar` source channel for routing, add to `HISTORY_STRATEGY` and interactive channel configuration

## Impact

- **Routing contracts** (`roster/switchboard/tools/routing/contracts.py`): Extend `SourceChannel` and `SourceProvider` literals, add to `_ALLOWED_PROVIDERS_BY_CHANNEL`
- **Pipeline config** (`src/butlers/modules/pipeline.py`): Add `"google_calendar": "realtime"` to `HISTORY_STRATEGY`
- **Google OAuth**: No new OAuth scopes needed — the Calendar module already requires `calendar` scope in `google_accounts.granted_scopes`; the connector reuses the same credentials
- **Credential resolution**: Same pattern as Gmail connector — `client_id`/`client_secret` from `butler_secrets`, `refresh_token` from account's companion entity in `entity_info`
- **Docker compose**: New service definition for the connector process
- **No new external dependencies**: Uses `google-auth` and `aiohttp`/`httpx` already in the project
