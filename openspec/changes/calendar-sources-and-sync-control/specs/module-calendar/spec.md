## MODIFIED Requirements

### Requirement: Calendar Event CRUD Tools

The module registers 22 MCP tools total. The core CRUD tools are: `calendar_list_events`, `calendar_get_event`, `calendar_create_event`, `calendar_update_event`, `calendar_delete_event`, the occurrence-targeted `calendar_update_event_instance`, `calendar_delete_event_instance`, and the read/utility tools `calendar_find_free_slots` and `calendar_list_calendars`. The remaining tools are enumerated by the Butler Event Management Tools (4: `calendar_create_butler_event`, `calendar_update_butler_event`, `calendar_delete_butler_event`, `calendar_toggle_butler_event`), Attendee Management Tools (2: `calendar_add_attendees`, `calendar_remove_attendees`), Reminder Tools (3: `reminder_create`, `reminder_list`, `reminder_dismiss`), and Calendar Sync Tools (3: `calendar_sync_status`, `calendar_force_sync`, `calendar_set_primary`) requirements below, plus the `calendar_propose_event` producer (behavior specified by the `calendar-event-proposals` capability). Butler-authored events SHALL default to the dedicated "Butlers" calendar when no explicit `calendar_id` is given; the user's own events SHALL be edited in place on whichever calendar they live on, resolved by event id. A caller MAY pass an explicit `calendar_id` to target a specific calendar; the available `calendar_id` values SHALL be enumerated by the read-only `calendar_list_calendars` source-listing tool (see the Calendar Source Listing Tool requirement).

#### Scenario: List events with time window

- **WHEN** `calendar_list_events` is called with optional `start_at`, `end_at`, and `limit`
- **THEN** events from the provider are returned as serialized dicts
- **AND** provider failures return a fail-open response with empty events list and error metadata

#### Scenario: Get single event

- **WHEN** `calendar_get_event` is called with an event_id
- **THEN** the full event is returned from the provider
- **AND** a 404 response returns `{"status": "not_found", "event": null}`

#### Scenario: Create butler-generated event

- **WHEN** `calendar_create_event` is called with title, start_at, end_at, and optional fields
- **THEN** the event is created on the provider with butler-generated metadata in `extendedProperties.private`
- **AND** conflict detection runs according to the configured policy (suggest alternatives, fail, or allow with approval gate)
- **AND** the event payload is normalized (timezone, all-day inference, notification defaults)

#### Scenario: Create event on an explicitly selected calendar

- **WHEN** `calendar_create_event` is called with an explicit `calendar_id` chosen from the `calendar_list_calendars` result
- **THEN** the event is created on that calendar
- **AND** the `calendar_id` must be one of the discovered provider calendars, else a validation error is raised

#### Scenario: Eager projection write-through on provider mutations

- **WHEN** a provider mutation succeeds (`calendar_create_event`, `calendar_update_event`, or `calendar_delete_event`)
- **THEN** the event is eagerly projected into the projection tables via `_project_provider_mutation` before the sync round-trip
- **AND** a background `_refresh_user_projection` sync still runs for reconciliation and freshness metadata
- **AND** failures in eager projection are logged but do not block the mutation response (fail-open)
- **BECAUSE** Google's incremental sync API has indexing latency (1-5s) after writes, and relying on a sync round-trip to project the mutation creates a race condition where the event may never reach the projection tables

#### Scenario: Update event with partial patch

- **WHEN** `calendar_update_event` is called with an event_id and partial fields
- **THEN** only non-None fields are sent to the provider's PATCH endpoint
- **AND** timezone changes re-emit start/end boundaries with the new timezone

#### Scenario: Delete event

- **WHEN** `calendar_delete_event` is called with an event_id
- **THEN** the event is deleted from the provider calendar

### Requirement: Calendar Sync Tools

The module registers MCP tools for sync observability and manual triggering: `calendar_sync_status`, `calendar_force_sync`, and `calendar_set_primary`. `calendar_force_sync` SHALL support an operator-driven full re-sync (cursor recovery) in addition to the default incremental sync, and `calendar_sync_status` SHALL expose a per-source `error_kind` classification so the dashboard can distinguish a stale source that needs **Recover** (full re-sync) from one that needs **Reconnect** (re-authorization).

#### Scenario: Query sync status

- **WHEN** `calendar_sync_status` is called
- **THEN** it returns the current sync state: last sync time, sync token validity, pending changes count, and last error
- **AND** if sync is not configured, returns `sync_enabled=False` (fail-open)

#### Scenario: Sync status carries per-source error_kind

- **WHEN** `calendar_sync_status` is called and a source has a recorded error
- **THEN** the per-source freshness includes an `error_kind` classifying the failure as one of `none`, `token_expired`, `auth`, `not_found`, or `transient`
- **AND** a healthy source reports `error_kind = "none"`
- **AND** the raw `last_error` string remains available alongside `error_kind`

#### Scenario: Force incremental sync (default)

- **WHEN** `calendar_force_sync` is called without `full` (or with `full=false`)
- **THEN** an immediate sync is triggered outside the normal polling schedule using the stored incremental sync token
- **AND** if a background poller is running, it is signaled; otherwise an inline one-off sync runs
- **AND** provider errors are recorded in `last_sync_error` rather than raised (fail-open)

#### Scenario: Force full re-sync for cursor recovery

- **WHEN** `calendar_force_sync` is called with `full=true`
- **THEN** the sync runs against `sync_token=None` (a full re-sync over the configured `full_sync_window_days` window) instead of the stored incremental token
- **AND** the recovery is logged so operators can see that a full re-sync ran
- **AND** the response indicates that a full recovery was performed

#### Scenario: Token-expiry recovery is logged

- **WHEN** an incremental sync fails because the sync token expired (Google `410 Gone`) and the module falls back to a full re-sync
- **THEN** the token-expiry recovery is logged
- **AND** the source's `error_kind` is classified as `token_expired`

#### Scenario: Set primary calendar

- **WHEN** `calendar_set_primary` is called with a `calendar_id`
- **THEN** the in-memory default `_primary_calendar_id` is updated to the specified calendar
- **AND** the choice is persisted to the credential store so it survives restarts
- **AND** the `calendar_id` must be one of the discovered provider calendars

## ADDED Requirements

### Requirement: Calendar Source Listing Tool

The module SHALL register a read-only `calendar_list_calendars` MCP tool that wraps `provider.list_calendars()` and returns the calendars available on the connected account in a normalized, butler-aware shape. This backs the dashboard's per-event calendar selector and the sources drawer; it is the source of the `calendar_id` values a caller may pass as an explicit target to `calendar_create_event`.

#### Scenario: List calendars on the connected account

- **WHEN** `calendar_list_calendars` is called
- **THEN** each calendar is returned with `calendar_id`, `summary` (display name), `primary`, `access_role`, `is_butlers_calendar`, and `selectable`
- **AND** the dedicated "Butlers" calendar (`_resolved_calendar_id`) is flagged with `is_butlers_calendar = true`
- **AND** a calendar whose access role is not `writer` or `owner` is marked `selectable = false` so it cannot be offered as a write target

#### Scenario: Provider failure fails open

- **WHEN** `calendar_list_calendars` is called and the provider raises an error
- **THEN** an empty calendar list is returned with error metadata rather than the call raising
