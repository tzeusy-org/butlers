## MODIFIED Requirements

### Requirement: Provider-Agnostic Architecture

The module defines an abstract `CalendarProvider` interface with concrete implementations per provider. Currently only Google Calendar is implemented via `_GoogleProvider`. The module SHALL track the dedicated "Butlers" calendar id and the user's primary calendar id as distinct roles, and SHALL NOT overwrite the Butlers calendar id when the user changes the default target.

#### Scenario: Provider selection at startup with account

- **WHEN** the Calendar module starts up with `provider = "google"` and `account = "work@gmail.com"` in config
- **THEN** a `_GoogleProvider` instance is created with OAuth credentials resolved from the credential store for the specified Google account
- **AND** the butler calendar ID is resolved from credential store or auto-discovered via shared "Butlers" calendar on that account

#### Scenario: Provider selection at startup without account (primary)

- **WHEN** the Calendar module starts up with `provider = "google"` and no `account` field in config
- **THEN** credentials are resolved for the primary Google account
- **AND** behavior is identical to pre-multi-account single-account deployments

#### Scenario: Calendar ID role resolution

- **WHEN** the Calendar module completes startup and calendar discovery
- **THEN** distinct calendar IDs are tracked with distinct roles:
  - `_resolved_calendar_id` — the dedicated "Butlers" group calendar (auto-discovered or created via `discover_or_create_calendar("Butlers")`, persisted to credential key `GOOGLE_CALENDAR_ID`). This is the **default write target for all butler-authored events** and is also used by `_push_internal_events_to_provider` to push scheduled tasks and reminders to Google.
  - `_primary_calendar_id` — the user's primary Google Calendar (the one marked `primary: true` in Google's calendarList). The user's own events live here; the butler edits them in place but does not create new butler-authored events here by default.
- **AND** the "Butlers" calendar id (`_resolved_calendar_id`) is treated as immutable for the lifetime of the connected account and is NOT overwritten by `calendar_set_primary`

#### Scenario: Unsupported provider configured

- **WHEN** a provider not in the `_PROVIDER_CLASSES` dict is configured
- **THEN** startup fails with a descriptive error

#### Scenario: Account not connected

- **WHEN** the Calendar module starts with `account = "nonexistent@gmail.com"`
- **AND** no `google_accounts` row exists for that email
- **THEN** startup SHALL fail with a descriptive error directing the user to connect the account via the dashboard OAuth flow

#### Scenario: Account missing required scopes

- **WHEN** the Calendar module starts with an account that does not have `calendar` in its `granted_scopes`
- **THEN** startup SHALL fail with a message directing the user to re-authorize the account with Calendar scope

### Requirement: Calendar Event CRUD Tools

The module registers MCP CRUD tools: `calendar_list_events`, `calendar_get_event`, `calendar_create_event`, `calendar_update_event`, `calendar_delete_event`. Butler-authored events SHALL default to the dedicated "Butlers" calendar when no explicit `calendar_id` is given; the user's own events SHALL be edited in place on whichever calendar they live on, resolved by event id.

#### Scenario: List events with time window

- **WHEN** `calendar_list_events` is called with optional `start_at`, `end_at`, and `limit`
- **THEN** events from the provider are returned as serialized dicts
- **AND** provider failures return a fail-open response with empty events list and error metadata

#### Scenario: Get single event

- **WHEN** `calendar_get_event` is called with an event_id
- **THEN** the full event is returned from the provider
- **AND** a 404 response returns `{"status": "not_found", "event": null}`

#### Scenario: Create butler-authored event defaults to the Butlers calendar

- **WHEN** `calendar_create_event` is called with title, start_at, end_at, and optional fields, and **no** explicit `calendar_id`
- **THEN** the event is created on the dedicated "Butlers" calendar (`_resolved_calendar_id`), NOT the user's primary calendar
- **AND** the event is stamped with butler-generated metadata in `extendedProperties.private` (`butler_generated=true`, `butler_name`) and `BUTLER:` title branding
- **AND** conflict detection runs according to the configured policy (suggest alternatives, fail, or allow with approval gate)
- **AND** the event payload is normalized (timezone, all-day inference, notification defaults)

#### Scenario: Create on the user's primary calendar via explicit override

- **WHEN** `calendar_create_event` is called with an explicit `calendar_id` equal to the user's primary calendar (or any discovered calendar)
- **THEN** the event is created on that calendar
- **AND** the `calendar_id` must be one of the discovered provider calendars, else a validation error is raised

#### Scenario: Eager projection write-through on provider mutations

- **WHEN** a provider mutation succeeds (`calendar_create_event`, `calendar_update_event`, or `calendar_delete_event`)
- **THEN** the event is eagerly projected into the projection tables via `_project_provider_mutation` before the sync round-trip
- **AND** a background `_refresh_user_projection` sync still runs for reconciliation and freshness metadata
- **AND** failures in eager projection are logged but do not block the mutation response (fail-open)
- **BECAUSE** Google's incremental sync API has indexing latency (1-5s) after writes, and relying on a sync round-trip to project the mutation creates a race condition where the event may never reach the projection tables

#### Scenario: Update event resolves the event's home calendar

- **WHEN** `calendar_update_event` is called with an `event_id` and partial fields and **no** explicit `calendar_id`
- **THEN** the calendar the event lives on is resolved via the home-calendar resolver (projection lookup, then bounded search, then primary fallback) and the PATCH is sent to that calendar
- **AND** only non-None fields are sent to the provider's PATCH endpoint
- **AND** timezone changes re-emit start/end boundaries with the new timezone
- **AND** a butler-authored event living on the Butlers calendar is patched on the Butlers calendar, and a user's own event on the primary calendar is patched in place on the primary calendar

#### Scenario: Delete event resolves the event's home calendar

- **WHEN** `calendar_delete_event` is called with an `event_id` and **no** explicit `calendar_id`
- **THEN** the calendar the event lives on is resolved via the home-calendar resolver and the delete is issued against that calendar
- **AND** an explicit `calendar_id` override, when supplied, takes precedence over the resolver

### Requirement: Calendar Sync Tools

The module registers MCP tools for sync observability and target selection: `calendar_sync_status`, `calendar_force_sync`, and `calendar_set_primary`. `calendar_set_primary` SHALL update only the user's default-target selection and MUST NOT mutate the dedicated "Butlers" calendar id.

#### Scenario: Query sync status

- **WHEN** `calendar_sync_status` is called
- **THEN** it returns the current sync state: last sync time, sync token validity, pending changes count, and last error
- **AND** if sync is not configured, returns `sync_enabled=False` (fail-open)

#### Scenario: Force immediate sync

- **WHEN** `calendar_force_sync` is called
- **THEN** an immediate sync is triggered outside the normal polling schedule
- **AND** if a background poller is running, it is signaled; otherwise an inline one-off sync runs
- **AND** provider errors are recorded in `last_sync_error` rather than raised (fail-open)

#### Scenario: Set primary calendar does not clobber the Butlers calendar

- **WHEN** `calendar_set_primary` is called with a `calendar_id`
- **THEN** the in-memory primary/default-target selection (`_primary_calendar_id`) is updated to the specified calendar
- **AND** the dedicated "Butlers" calendar id (`_resolved_calendar_id`) is left unchanged, so butler-authored creates continue to target the Butlers calendar
- **AND** the choice is persisted so it survives restarts under a credential key distinct from `GOOGLE_CALENDAR_ID`
- **AND** the `calendar_id` must be one of the discovered provider calendars

## ADDED Requirements

### Requirement: Event Home-Calendar Resolution

When a mutation (`calendar_update_event`, `calendar_delete_event`) is issued by event id without an explicit `calendar_id`, the module SHALL resolve the calendar the event actually lives on rather than defaulting to a single calendar. This prevents butler-authored events on the Butlers calendar from being mutated against the wrong calendar (404 / silent miss) once they no longer live on primary.

#### Scenario: Home calendar resolved from the projection

- **WHEN** a mutation is issued for an `event_id` whose provider origin reference exists in the `calendar_events` projection
- **THEN** the home calendar is resolved by joining `calendar_events.origin_ref` to its `calendar_sources.calendar_id`
- **AND** the mutation targets that calendar

#### Scenario: Explicit override wins over resolution

- **WHEN** a mutation is issued with an explicit `calendar_id`
- **THEN** the resolver is bypassed and the supplied calendar is used (validated against discovered calendars)

#### Scenario: Fallback when the projection has no record

- **WHEN** a mutation is issued for an `event_id` not present in the projection
- **THEN** the resolver performs a bounded search across discovered calendars (Butlers calendar and primary at minimum) to locate the event
- **AND** if the event cannot be located, the mutation falls back to the primary calendar and a not-found response is surfaced fail-open rather than raising

### Requirement: Programmatic Butler-Authored Event Creation

The programmatic inter-module entry point `create_user_event` (used by e.g. health/meal logging) SHALL be a first-class butler-authored write: it targets the dedicated "Butlers" calendar and stamps butler-generated provenance, consistent with `calendar_create_event`.

#### Scenario: Meal/health event lands on the Butlers calendar

- **WHEN** another module calls `create_user_event(title, start_at, end_at, description)`
- **THEN** the event is created on the dedicated "Butlers" calendar (`_resolved_calendar_id`)
- **AND** it is stamped with butler-generated metadata (`butler_generated=true`, `butler_name`) and `BUTLER:` title branding
- **AND** the `calendar.write` permission grant is enforced via the permissions matrix before the provider write
