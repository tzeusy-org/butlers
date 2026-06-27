## MODIFIED Requirements

### Requirement: Calendar Workspace
`src/butlers/api/routers/calendar_workspace.py` provides a normalized calendar read surface, metadata endpoint, sync trigger, and mutation endpoints for both user-view and butler-view events. It SHALL additionally expose a read-only accounts surface (`GET /api/calendar/accounts`) and a per-calendar source enable/disable mutation (`POST /api/calendar/sources`); the sync trigger SHALL accept a `full` recovery flag and the metadata endpoint SHALL carry a per-source `error_kind` so the workspace can render the correct Recover/Reconnect CTA. No new table is introduced — source enable/disable reuses the existing `calendar_sources` projection rows, and the accounts surface reuses `public.google_accounts` plus the Google Calendar connector health.

#### Scenario: Workspace read
- **WHEN** `GET /api/calendar/workspace?view=user&start=...&end=...` is called
- **THEN** calendar entries are fan-out queried across butler DBs (joining `calendar_event_instances`, `calendar_events`, `calendar_sources`, and `calendar_sync_cursors`)
- **AND** entries are normalized into `UnifiedCalendarEntry` objects with computed `source_type`, `status`, and `sync_state`
- **AND** optional `timezone` parameter converts all timestamps to the requested display timezone

#### Scenario: Workspace mutations
- **WHEN** user-event or butler-event mutation endpoints are called
- **THEN** the request is proxied to the owning butler via MCP tool calls (`calendar_create_event`, `calendar_update_event`, etc.)
- **AND** projection freshness metadata is fetched after mutation and included in the response

#### Scenario: Meta carries per-source error_kind
- **WHEN** `GET /api/calendar/workspace/meta` is called
- **THEN** each `connected_sources` entry includes an `error_kind` field classifying a failed/stale source as one of `none`, `token_expired`, `auth`, `not_found`, or `transient`
- **AND** a client that ignores `error_kind` observes the pre-change meta shape otherwise unchanged

#### Scenario: Sync trigger forwards full recovery flag
- **WHEN** `POST /api/calendar/workspace/sync` is called with `full=true` (optionally scoped to a `source_key`/`source_id`)
- **THEN** the request is forwarded to `calendar_force_sync(full=true)` for the targeted source(s), running a full re-sync that ignores the stored cursor
- **AND** the response reports per-target whether a full recovery ran
- **AND** `full=false` (or omitting `full`) preserves the existing incremental sync behavior

#### Scenario: List connected calendar accounts with health
- **WHEN** `GET /api/calendar/accounts` is called
- **THEN** the connected `public.google_accounts` rows are returned, each joined with the Google Calendar connector's per-account health (status, `error_kind`, last ingest)
- **AND** when connector health is unavailable, accounts are still returned with a degraded/unknown health indicator rather than the endpoint failing
- **AND** the endpoint is read-only — it does not connect or disconnect accounts (account lifecycle stays in the Google accounts surface)

#### Scenario: Enable or disable a calendar source
- **WHEN** `POST /api/calendar/sources` is called to enable or disable a single calendar as a sync source
- **THEN** the enabled/disabled state is toggled on the existing `calendar_sources` row (no new table)
- **AND** a disabled source is skipped by the sync loop on subsequent syncs
- **AND** a disabled source is surfaced as off (not failed) in the workspace meta so its staleness is not read as an error
