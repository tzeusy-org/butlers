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

The module registers MCP tools for calendar event lifecycle management: `calendar_list_events`, `calendar_get_event`, `calendar_create_event`, `calendar_update_event`, `calendar_delete_event`.

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

### Requirement: Attendee Management

The module supports adding and removing attendees from calendar events.

#### Scenario: Add attendees to event

- **WHEN** attendees are added to an event via the provider
- **THEN** the attendee list is updated with email-based entries
- **AND** response statuses are tracked (accepted, declined, tentative, needs-action)

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
