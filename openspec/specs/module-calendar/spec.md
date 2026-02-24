# Calendar Module

## Purpose

The Calendar module is a provider-agnostic module that reads and writes calendar events, manages event lifecycle (create, update, reschedule, cancel), enforces conflict detection policies, supports timezone-aware scheduling with all-day and timed event semantics, and projects scheduled tasks and reminders into a unified calendar view.

## ADDED Requirements

### Requirement: Provider-Agnostic Architecture

The module defines an abstract `CalendarProvider` interface with concrete implementations per provider. Currently only Google Calendar is implemented via `_GoogleProvider`.

#### Scenario: Provider selection at startup

- **WHEN** the Calendar module starts up with `provider = "google"` in config
- **THEN** a `_GoogleProvider` instance is created with OAuth credentials resolved from the credential store
- **AND** the calendar ID is resolved from credential store or auto-discovered via shared "Butlers" calendar

#### Scenario: Unsupported provider configured

- **WHEN** a provider not in the `_PROVIDER_CLASSES` dict is configured
- **THEN** startup fails with a descriptive error

### Requirement: CalendarConfig Validation

Configuration is declared under `[modules.calendar]` in `butler.toml` with fields: `provider` (required), `calendar_id` (optional), `timezone` (default `"UTC"`), `conflicts` (policy defaults), `event_defaults` (notification defaults), and `sync` (sync interval settings).

#### Scenario: Valid calendar config

- **WHEN** config is provided with a non-empty `provider` and valid timezone
- **THEN** the config is validated and normalized (provider lowercased, timezone stripped)

#### Scenario: Conflict policy configuration

- **WHEN** `conflicts` is configured with a `default_policy`
- **THEN** valid policies are `suggest`, `fail`, `allow_overlap`
- **AND** legacy aliases `allow` -> `allow_overlap` and `reject` -> `fail` are normalized

### Requirement: Calendar Event CRUD Tools

The module registers 13 MCP tools total. The core CRUD tools are: `calendar_list_events`, `calendar_get_event`, `calendar_create_event`, `calendar_update_event`, `calendar_delete_event`.

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

#### Scenario: Update event with partial patch

- **WHEN** `calendar_update_event` is called with an event_id and partial fields
- **THEN** only non-None fields are sent to the provider's PATCH endpoint
- **AND** timezone changes re-emit start/end boundaries with the new timezone

#### Scenario: Delete event

- **WHEN** `calendar_delete_event` is called with an event_id
- **THEN** the event is deleted from the provider calendar

### Requirement: CalendarEvent Model

The canonical `CalendarEvent` model is provider-neutral with fields: `event_id`, `title`, `start_at`, `end_at`, `timezone`, `description`, `location`, `attendees` (list of `AttendeeInfo`), `recurrence_rule`, `color_id`, `butler_generated`, `butler_name`, `status`, `organizer`, `visibility`, `etag`, `created_at`, `updated_at`.

#### Scenario: Google event parsing

- **WHEN** a Google Calendar API event payload is received
- **THEN** it is parsed into a `CalendarEvent` via `_google_event_to_calendar_event`
- **AND** cancelled events return `None`
- **AND** attendees are parsed into `AttendeeInfo` objects with email, display_name, response_status, optional, organizer, self_, and comment fields
- **AND** recurrence rules are extracted from the `recurrence` array
- **AND** butler-generated metadata is extracted from `extendedProperties.private`

### Requirement: RRULE and Cron Support

The module supports both RFC-5545 RRULE recurrence and cron expressions for event and task projection.

#### Scenario: RRULE occurrence expansion

- **WHEN** an event has a `recurrence_rule` (with or without `RRULE:` prefix)
- **THEN** `_rrule_occurrences_in_window` expands instances within a given time window using dateutil
- **AND** each occurrence gets a `(starts_at, ends_at)` pair with configurable duration

#### Scenario: Cron task projection

- **WHEN** a scheduled task has a cron expression
- **THEN** `_cron_occurrences_in_window` expands firing times within a window using croniter
- **AND** each occurrence gets a default duration of 15 minutes

#### Scenario: Recurrence projection window

- **WHEN** recurring events are projected
- **THEN** a rolling 90-day window (`RECURRENCE_PROJECTION_WINDOW_DAYS`) is used

### Requirement: Conflict Detection and Resolution

The module enforces conflict detection policies when creating or rescheduling events.

#### Scenario: Suggest conflict resolution

- **WHEN** an event creation conflicts with existing events and policy is `suggest`
- **THEN** up to 3 alternative time slots are suggested (default `DEFAULT_CONFLICT_SUGGESTION_COUNT = 3`)

#### Scenario: Fail on conflict

- **WHEN** an event creation conflicts and policy is `fail`
- **THEN** event creation is rejected with a structured error

#### Scenario: Allow overlap with approval gate

- **WHEN** an event creation conflicts and policy is `allow_overlap`
- **THEN** the event is created if no approval enqueuer is set
- **AND** if an approval enqueuer is wired, high-impact overlaps produce `status=approval_required`

### Requirement: Butler Event Management Tools

The module registers MCP tools for managing butler-owned workspace events (scheduled tasks and reminders projected as calendar entries): `calendar_create_butler_event`, `calendar_update_butler_event`, `calendar_delete_butler_event`, `calendar_toggle_butler_event`.

#### Scenario: Create butler event

- **WHEN** `calendar_create_butler_event` is called with title, timing, and source type (reminder or scheduled task)
- **THEN** a butler-managed event is created with recurrence support (RRULE or cron)
- **AND** the event is tagged with butler metadata for unified calendar projection

#### Scenario: Update butler event

- **WHEN** `calendar_update_butler_event` is called with an event ID and partial fields
- **THEN** only the provided fields are updated (timing, recurrence, enabled status)

#### Scenario: Delete butler event

- **WHEN** `calendar_delete_butler_event` is called with an event ID
- **THEN** the butler event is deleted (series-scoped in v1)
- **AND** high-impact mutations require approval gate

#### Scenario: Toggle butler event

- **WHEN** `calendar_toggle_butler_event` is called with an event ID and enabled flag
- **THEN** the butler event is paused or resumed without deletion
- **AND** high-impact mutations require approval gate

### Requirement: Attendee Management Tools

The module registers MCP tools for managing event attendees: `calendar_add_attendees` and `calendar_remove_attendees`.

#### Scenario: Add attendees to event

- **WHEN** `calendar_add_attendees` is called with an event ID and list of email addresses
- **THEN** attendees are added to the event with deduplication
- **AND** notification policy controls whether attendees are notified
- **AND** provider failures return a fail-closed structured error

#### Scenario: Remove attendees from event

- **WHEN** `calendar_remove_attendees` is called with an event ID and list of email addresses
- **THEN** matching attendees are removed (case-insensitive email match)
- **AND** cancellation notifications follow the send_updates policy

### Requirement: Google OAuth and Rate Limiting

The Google provider handles OAuth token refresh and rate-limited retries.

#### Scenario: OAuth token refresh

- **WHEN** the access token expires or is not cached
- **THEN** a refresh-token exchange is performed against `https://oauth2.googleapis.com/token`
- **AND** the new token is cached with an early-expiry safety margin (60s before actual expiry)

#### Scenario: Rate-limit retry

- **WHEN** a Google Calendar API request returns 429 or 503
- **THEN** the request is retried up to 3 times with exponential backoff (base 1.0s)

#### Scenario: Credential redaction in errors

- **WHEN** an error message might contain credential values
- **THEN** patterns like `client_secret=...`, `refresh_token=...`, `access_token=...` are redacted before logging or returning to the caller

### Requirement: Calendar Sync Tools

The module registers MCP tools for sync observability and manual triggering: `calendar_sync_status` and `calendar_force_sync`.

#### Scenario: Query sync status

- **WHEN** `calendar_sync_status` is called
- **THEN** it returns the current sync state: last sync time, sync token validity, pending changes count, and last error
- **AND** if sync is not configured, returns `sync_enabled=False` (fail-open)

#### Scenario: Force immediate sync

- **WHEN** `calendar_force_sync` is called
- **THEN** an immediate sync is triggered outside the normal polling schedule
- **AND** if a background poller is running, it is signaled; otherwise an inline one-off sync runs
- **AND** provider errors are recorded in `last_sync_error` rather than raised (fail-open)

### Requirement: [TARGET-STATE] Calendar Sync and Projection

Provider sync with incremental/full modes and a unified projection table for fast dashboard queries.

#### Scenario: Incremental sync via sync token

- **WHEN** a sync token exists for a calendar
- **THEN** incremental sync fetches only changed events since the last token
- **AND** an expired sync token triggers a full sync fallback

#### Scenario: Internal task projection

- **WHEN** the butler has scheduled tasks with cron expressions
- **THEN** a periodic background task projects them as `SOURCE_KIND_INTERNAL_SCHEDULER` entries

### Requirement: [TARGET-STATE] Unified Calendar View

A dashboard page at `/butlers/calendar` with a view toggle between user events and butler-managed schedules/reminders, backed by an in-app projection table.

#### Scenario: Projection status tracking

- **WHEN** the projection is queried
- **THEN** a staleness status is returned: `fresh`, `stale` (exceeds 2x sync interval), or `failed`
