# Calendar Module

## Purpose

The Calendar module is a provider-agnostic module that reads and writes calendar events, manages event lifecycle (create, update, reschedule, cancel), enforces conflict detection policies, supports timezone-aware scheduling with all-day and timed event semantics, and projects scheduled tasks and reminders into a unified calendar view.

## ADDED Requirements

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

### Requirement: CalendarConfig Validation

Configuration is declared under `[modules.calendar]` in `butler.toml` with fields: `provider` (required), `account` (optional, email string — Google account to use), `calendar_id` (optional), `timezone` (default `"UTC"`), `conflicts` (policy defaults), `event_defaults` (notification defaults), and `sync` (sync interval settings).

#### Scenario: Valid calendar config with account

- **WHEN** config is provided with `provider = "google"`, `account = "work@gmail.com"`, and valid timezone
- **THEN** the config is validated and normalized (provider lowercased, timezone stripped, account stripped)

#### Scenario: Valid calendar config without account

- **WHEN** config is provided with `provider = "google"` and no `account` field
- **THEN** the config is valid and the module SHALL use the primary Google account at startup

#### Scenario: Conflict policy configuration

- **WHEN** `conflicts` is configured with a `default_policy`
- **THEN** valid policies are `suggest`, `fail`, `allow_overlap`

### Requirement: Calendar Event CRUD Tools

The module registers 22 MCP tools total. The core CRUD tools are: `calendar_list_events`, `calendar_get_event`, `calendar_create_event`, `calendar_update_event`, `calendar_delete_event`, the occurrence-targeted `calendar_update_event_instance`, `calendar_delete_event_instance`, and the read/utility tools `calendar_find_free_slots` and `calendar_list_calendars`. The remaining tools are enumerated by the Butler Event Management Tools (4: `calendar_create_butler_event`, `calendar_update_butler_event`, `calendar_delete_butler_event`, `calendar_toggle_butler_event`), Attendee Management Tools (2: `calendar_add_attendees`, `calendar_remove_attendees`), Reminder Tools (3: `reminder_create`, `reminder_list`, `reminder_dismiss`), and Calendar Sync Tools (3: `calendar_sync_status`, `calendar_force_sync`, `calendar_set_primary`) requirements below, plus the `calendar_propose_event` producer (behavior specified by the `calendar-event-proposals` capability). Butler-authored events SHALL default to the dedicated "Butlers" calendar when no explicit `calendar_id` is given; the user's own events SHALL be edited in place on whichever calendar they live on, resolved by event id.

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

### Requirement: CalendarEvent Model

The canonical `CalendarEvent` model is provider-neutral with fields: `event_id`, `title`, `start_at`, `end_at`, `timezone`, `description`, `body`, `location`, `attendees` (list of `AttendeeInfo`), `recurrence_rule`, `color_id`, `butler_generated`, `butler_name`, `source_butler`, `source_session_id`, `entity_ids`, `status`, `organizer`, `visibility`, `etag`, `created_at`, `updated_at`.

- `body` — a longer freeform description to complement the short `title` (nullable; maps to Google Calendar `description` field on parse)
- `source_butler` — the butler name that created or owns the event (NOT NULL; backfilled from `metadata.butler_name` on migration)
- `source_session_id` — session identifier of the creating LLM session (nullable)
- `entity_ids` — list of entity UUIDs linked to this event via the `calendar_event_entities` junction table; populated on read, accepted on create/update

#### Scenario: Google event parsing

- **WHEN** a Google Calendar API event payload is received
- **THEN** it is parsed into a `CalendarEvent` via `_google_event_to_calendar_event`
- **AND** cancelled events return `None`
- **AND** attendees are parsed into `AttendeeInfo` objects with email, display_name, response_status, optional, organizer, self_, and comment fields
- **AND** recurrence rules are extracted from the `recurrence` array
- **AND** butler-generated metadata is extracted from `extendedProperties.private`
- **AND** `description` field is mapped to `body` on the model

#### Scenario: Authorship annotation on create

- **WHEN** `calendar_create_event` or `calendar_update_event` is called
- **THEN** the resulting event is annotated with `source_butler` (the butler's name) and `source_session_id` (the current runtime session ID)
- **AND** both values are written to the `calendar_events` row in the projection table

#### Scenario: Entity association on create and update

- **WHEN** `calendar_create_event`, `calendar_update_event`, or `calendar_update_butler_event` is called with `entity_ids`
- **THEN** the junction table `calendar_event_entities` is updated via `_upsert_event_entities`
- **AND** existing entity links for the event are replaced with the new set (full replace, not additive)

#### Scenario: Entity association on read

- **WHEN** an event is returned from `calendar_get_event`, `calendar_list_events`, or any projection read path
- **THEN** the event's `entity_ids` field is populated from `calendar_event_entities` via `_fetch_event_entity_ids`

### Requirement: calendar_event_entities Junction Table

The `calendar_event_entities` table SHALL be the cross-reference between `calendar_events` rows and entities in the memory butler's entity graph. It MUST enable reverse lookup — given an entity, find all calendar events associated with it — and it SHALL be the authoritative source of participant entity membership for downstream retrospective projection by the Chronicler butler.

Schema: `(event_id UUID REFERENCES calendar_events(id) ON DELETE CASCADE, entity_id UUID REFERENCES public.entities(id) ON DELETE CASCADE)` with a UNIQUE constraint on `(event_id, entity_id)`.

#### Scenario: Entity merge re-pointing

- **WHEN** two entities are merged via `entity_merge()` in the memory module
- **THEN** `calendar_event_entities` rows referencing the source entity are re-pointed to the target entity
- **AND** any duplicates created by the re-point are deleted (deduplication on `(event_id, entity_id)`)
- **AND** failures in the re-pointing step are swallowed gracefully if the table does not exist
- **AND** the parallel chronicler join table `chronicler.episode_entities` is re-pointed in the same `entity_merge()` flow so the two surfaces do not drift; see `butler-chronicler` for the chronicler-side requirement

#### Scenario: Authoritative source for chronicler participant resolution

- **WHEN** the Chronicler `CalendarCompletedAdapter` projects a completed calendar instance into a `chronicler.episodes` row
- **THEN** the adapter SHALL read the upstream attendee → entity resolution from `{schema}.calendar_event_entities` joined through `calendar_events.id` (the upstream event row), NOT from the raw Google Calendar attendee payload
- **AND** the calendar module SHALL remain the sole writer to `calendar_event_entities`; chronicler SHALL NOT mutate or re-resolve attendees on its own
- **AND** when the upstream `calendar_event_entities` table is absent in a deployment (calendar module disabled), the chronicler adapter SHALL degrade gracefully by writing only the owner row into `chronicler.episode_entities` (see `butler-chronicler`)
- **BECAUSE** attendee → entity resolution is a write-time deterministic step owned by the calendar module's `_upsert_event_entities`; the chronicler retrospective view must reflect that decision rather than invent its own

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

The module enforces conflict detection policies when creating or rescheduling events. When the policy is `suggest`, suggested alternative slots SHALL respect the owner's scheduling-availability preferences so that no suggestion falls outside the owner's allowed hours/days or inside a no-meeting block.

#### Scenario: Suggest conflict resolution

- **WHEN** an event creation conflicts with existing events and policy is `suggest`
- **THEN** up to 3 alternative time slots are suggested (default `DEFAULT_CONFLICT_SUGGESTION_COUNT = 3`)
- **AND** each suggested slot lies within the owner's scheduling-availability preferences when such preferences are configured

#### Scenario: Fail on conflict

- **WHEN** an event creation conflicts and policy is `fail`
- **THEN** event creation is rejected with a structured error

#### Scenario: Allow overlap with approval gate

- **WHEN** an event creation conflicts and policy is `allow_overlap`
- **THEN** the event is created if no approval enqueuer is set
- **AND** if an approval enqueuer is wired, high-impact overlaps produce `status=approval_required`

#### Scenario: Suggestions respect owner scheduling preferences

- **WHEN** suggested slots are built and owner scheduling-availability preferences are configured (earliest/latest meeting time, allowed days, no-meeting blocks)
- **THEN** `_build_suggested_slots` SHALL NOT emit a slot that starts before the earliest meeting time, ends after the latest meeting time, falls on a disallowed weekday, or overlaps a no-meeting block

#### Scenario: Suggestions with no owner preferences configured

- **WHEN** suggested slots are built and no owner scheduling-availability preferences row exists
- **THEN** slot suggestion behaves as before (forward-stepping from the last conflict), applying no life-availability filtering

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

The Google provider handles OAuth token refresh and rate-limited retries, resolving credentials for the configured account.

#### Scenario: OAuth token refresh for specific account

- **WHEN** the access token expires or is not cached
- **THEN** a refresh-token exchange is performed against `https://oauth2.googleapis.com/token` using the refresh token for the configured Google account
- **AND** the new token is cached with an early-expiry safety margin (60s before actual expiry)
- **AND** on successful refresh, `google_accounts.last_token_refresh_at` SHALL be updated

#### Scenario: Rate-limit retry

- **WHEN** a Google Calendar API request returns 429 or 503
- **THEN** the request is retried up to 3 times with exponential backoff (base 1.0s)

#### Scenario: Credential redaction in errors

- **WHEN** an error message might contain credential values
- **THEN** patterns like `client_secret=...`, `refresh_token=...`, `access_token=...` are redacted before logging or returning to the caller

### Requirement: Reminder Tools

The module registers three MCP tools for managing butler-owned reminders as native calendar events: `reminder_create`, `reminder_list`, `reminder_dismiss`. Reminders are stored as `calendar_events` rows with `source_kind = 'internal_reminders'` and scoped to the calling butler via `source_butler`. The legacy `reminders` SPO fact table used by the relationship butler has been migrated to `calendar_events` and is no longer authoritative.

#### Scenario: Create reminder as calendar event

- **WHEN** `reminder_create` is called with `title`, `due_at`, and optional `body`, `ends_at`, `recurrence`, `entity_ids`, `timezone`
- **THEN** a row is inserted into `calendar_events` with `source_kind = 'internal_reminders'`, `source_butler = <calling butler>`, `status = 'confirmed'`
- **AND** `ends_at` defaults to `due_at + 15 minutes` when not provided
- **AND** `recurrence` accepts `"daily"`, `"weekly"`, `"monthly"`, or `"yearly"` and is mapped to an RRULE string
- **AND** `entity_ids` are stored in `calendar_event_entities` for reverse lookup
- **AND** the response includes `event_id`, `title`, `starts_at`, `ends_at`, `recurrence_rule`, `entity_ids`, and `source_butler`

#### Scenario: List reminders

- **WHEN** `reminder_list` is called with optional `entity_id`, `due_before`, and `include_dismissed` filters
- **THEN** reminders are fetched from `calendar_events` where `source_kind = 'internal_reminders'` and `source_butler = <calling butler>`
- **AND** entity associations are resolved in a single batch fetch from `calendar_event_entities`
- **AND** dismissed reminders (status = 'cancelled') are excluded unless `include_dismissed=True`
- **AND** an unavailable DB pool returns an empty list (fail-open)

#### Scenario: Dismiss one-time reminder

- **WHEN** `reminder_dismiss` is called with an `event_id` that has no `recurrence_rule`
- **THEN** the `calendar_events` row `status` is set to `'cancelled'`

#### Scenario: Dismiss recurring reminder occurrence

- **WHEN** `reminder_dismiss` is called with an `event_id` that has a `recurrence_rule`
- **THEN** the earliest non-cancelled instance in `calendar_event_instances` has its `status` set to `'cancelled'`
- **AND** the series event row remains active so future occurrences continue to be projected

#### Scenario: Migration from relationship butler reminders

- **WHEN** the relationship butler's migration `007_reminders_to_calendar_events` runs
- **THEN** all existing reminder facts (predicate = 'reminder') from the relationship butler are migrated to `calendar_events` with RRULE recurrence mapping
- **AND** `calendar_event_entities` rows are populated from contact entity resolution
- **AND** the legacy `reminders` table is renamed to `_reminders_backup`
- **AND** reminder MCP tools (`reminder_create`, `reminder_list`, `reminder_dismiss`) are removed from the relationship butler's tool surface

### Requirement: Reminder Dispatch via tick()

The module exposes an async `tick(source_butler, notify_fn=None)` method that the butler core can call periodically to evaluate and dispatch due reminders.

#### Scenario: One-time reminder dispatch

- **WHEN** `tick(source_butler)` is called
- **AND** a one-time reminder (no `recurrence_rule`) has `starts_at <= now` and `metadata->>'last_notified_at'` is absent
- **THEN** `notify_fn` is called with a `notify.v1` envelope containing the reminder title and `due_at`
- **AND** `metadata.last_notified_at` is updated on the event row to prevent duplicate delivery

#### Scenario: Recurring reminder per-occurrence dedup

- **WHEN** `tick(source_butler)` evaluates recurring reminders
- **THEN** it joins `calendar_event_instances` to find instances where `starts_at <= now` and `metadata->>'notified_at'` is absent
- **AND** each instance fires exactly once: after dispatch, `notified_at` is stamped on the instance row (not the series event)
- **AND** cancelled instances (status = 'cancelled') are skipped
- **BECAUSE** stamping dedup metadata on the instance ensures each occurrence of a recurring series fires independently

#### Scenario: Scoping to a butler

- **WHEN** `tick(source_butler)` is called with a butler name
- **THEN** only `calendar_events` owned by that butler (via `calendar_sources.butler_name = source_butler`) are evaluated
- **AND** each butler's due reminder evaluation is isolated from other butlers

#### Scenario: tick() with no notify_fn

- **WHEN** `tick(source_butler, notify_fn=None)` is called
- **THEN** due reminders are identified and logged but not dispatched
- **AND** dedup metadata is NOT written (so they will fire again when a real notify_fn is provided)

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

### Requirement: [TARGET-STATE] Calendar Sync and Projection

Provider sync with incremental/full modes and a unified projection table for fast dashboard queries.

#### Scenario: Incremental sync via sync token

- **WHEN** a sync token exists for a calendar
- **THEN** incremental sync fetches only changed events since the last token
- **AND** an expired sync token triggers a full sync fallback

#### Scenario: Internal task projection

- **WHEN** the butler has scheduled tasks with cron expressions
- **THEN** a periodic background task projects them as `SOURCE_KIND_INTERNAL_SCHEDULER` entries

#### Scenario: Projection authorship fields normalize missing provenance

- **WHEN** `_upsert_projection_event` writes a projection row and `source_butler` is null, blank, or the sentinel `"unknown"`
- **THEN** the write SHALL fall back to the module's canonical butler name and finally `DEFAULT_BUTLER_NAME` before inserting into `calendar_events.source_butler`
- **AND** `source_session_id` SHALL be stripped and blank values SHALL be stored as `NULL`
- **BECAUSE** projection authorship columns are part of the durable calendar event provenance contract and must remain canonical while satisfying the non-null database constraint

### Requirement: Dual-Lane Ownership and Authoritativeness

The projection uses a dual-lane model to separate event authority. Each `calendar_sources` row has a `lane` field: `"user"` or `"butler"`. The lane determines which system is authoritative for an event's state.

- **`lane="user"`** — Provider-synced external events (meetings, appointments created by humans on Google Calendar). Google is the source of truth. The local projection faithfully mirrors whatever the provider reports on each sync cycle.
- **`lane="butler"`** — Internal scheduled tasks and reminders managed by the butler. The butler's `calendar_events` rows (for reminders with `source_kind='internal_reminders'`) and `scheduled_tasks` table are the source of truth. These are pushed outbound to Google for visibility but Google is never read back as authoritative for them.

#### Scenario: Butler-generated events in provider sync projection

> **SPEC-CODE DIVERGENCE**: The implementation at `_project_provider_changes` (calendar.py:5370-5376) persists ALL provider events including butler-generated ones, noting butler metadata for UI differentiation. The original exclusion behavior described below is not implemented. The rationale in code: butler events created via `calendar_create_event` (workspace mutations) are distinct from internal scheduler events and should appear in the provider projection.

- **WHEN** `_project_provider_changes` processes events returned by an incremental or full sync
- **THEN** all events are persisted to the projection, including butler-generated ones
- **AND** butler-generated metadata (`butler_generated`, `butler_name`) is preserved in the projection row metadata for UI differentiation
- **BECAUSE** butler events created via `calendar_create_event` are user-lane workspace mutations (not internal scheduler items) and should be visible in the provider projection

#### Scenario: Butler overwrites external edits to butler-owned events

- **WHEN** a user manually moves or edits a butler-generated event directly on Google Calendar
- **AND** the next sync cycle runs
- **THEN** the provider sync skips the modified event (butler-generated filter)
- **AND** `_push_internal_events_to_provider` overwrites the Google event with the butler's local state (title, start/end from `scheduled_tasks` or `calendar_events` with `source_kind='internal_reminders'`)
- **BECAUSE** the butler's database is authoritative for butler-owned events; Google is a read-only mirror for them

#### Scenario: External events faithfully track provider state

- **WHEN** a non-butler event is created or modified on Google Calendar
- **AND** the next sync cycle runs
- **THEN** the event is upserted into the `lane="user"` projection via `_project_provider_changes`
- **AND** cancelled events are marked cancelled in the projection
- **AND** events no longer returned by a full sync are marked stale/cancelled via `_mark_projection_source_stale_events_cancelled`

#### Scenario: Modifying butler events requires the butler

- **WHEN** a user wants to reschedule or edit a butler-managed event
- **THEN** they must use butler MCP tools (`calendar_update_butler_event`, `calendar_update_event` with the event ID)
- **AND** the butler updates both its local state and the Google Calendar event atomically
- **AND** direct Google Calendar edits will be silently reverted on the next sync cycle

### Requirement: [TARGET-STATE] Unified Calendar View

A dashboard page at `/butlers/calendar` with a view toggle between user events and butler-managed schedules/reminders, backed by an in-app projection table.

#### Scenario: Projection status tracking

- **WHEN** the projection is queried
- **THEN** a staleness status is returned: `fresh`, `stale` (exceeds 2x sync interval), or `failed`

### Requirement: Calendar Event Full-Text Search Index

The calendar projection SHALL support index-backed substring search over the human-readable event text. A core Alembic migration (next in the `core_*` chain) SHALL ensure the `pg_trgm` extension and create a GIN trigram index over `calendar_events(title, description, location)` in each butler schema, so free-text lookups do not require a sequential scan of the projection.

#### Scenario: Trigram index migration is idempotent and reversible
- **WHEN** the search-index core migration runs against a butler schema
- **THEN** it executes `CREATE EXTENSION IF NOT EXISTS pg_trgm` and creates a GIN trigram index (`gin_trgm_ops`) over `calendar_events(title, description, location)` with `IF NOT EXISTS`
- **AND** re-running the migration is a no-op (no duplicate index, no error)
- **AND** the migration `downgrade()` drops the index (`DROP INDEX IF EXISTS`) while leaving the shared `pg_trgm` extension installed

#### Scenario: Search index covers the searchable projection columns
- **WHEN** the projection stores a `calendar_events` row with `title`, optional `description`, and optional `location`
- **THEN** all three columns are covered by the trigram index so a substring query against any of them is index-eligible
- **AND** the index is per-schema, consistent with the projection's per-butler-schema layout

### Requirement: Calendar Event Full-Text Search Query

The module SHALL expose a fan-out search over the `calendar_events` projection that matches a free-text query against `title`, `description`, and `location`, returns matches ranked by trigram relevance with each match's date(s), and degrades fail-open when the trigram index or extension is unavailable. This is the contract behind the `GET /api/calendar/workspace/search` endpoint (see `dashboard-api`).

#### Scenario: Ranked match across title, description, and location
- **WHEN** a non-empty query is searched against the projection
- **THEN** `calendar_events` rows whose `title`, `description`, or `location` match the query (trigram similarity / substring) are returned
- **AND** results are ranked by trigram relevance and carry each match's event date(s) so callers can group by day and jump-to
- **AND** the search is fanned out across butler schemas and honors lane (`view`) and `butlers`/`sources` scoping

#### Scenario: Empty query returns no matches
- **WHEN** the search is invoked with a missing or blank query string
- **THEN** an empty result set is returned (the search SHALL NOT return the entire projection)
- **AND** no error is raised

#### Scenario: Degraded search when the trigram index is unavailable
- **WHEN** a probed butler schema lacks the `pg_trgm` extension or the trigram index
- **THEN** the search degrades fail-open — it falls back to a substring (`ILIKE`) match for that schema or skips it — rather than raising a 500
- **AND** results from schemas where the index is present are still returned

### Requirement: Free/Busy Availability Query

The `CalendarProvider` interface SHALL expose a windowed, multi-calendar free/busy query `get_free_busy(calendar_ids, start_at, end_at)` that returns merged busy windows. This generalizes the existing single-calendar, candidate-window free/busy lookup that previously lived only inside conflict detection. `find_conflicts` SHALL be implemented in terms of `get_free_busy` so the provider's `/freeBusy` request/response handling exists in exactly one place.

#### Scenario: Free/busy across multiple calendars over an arbitrary window

- **WHEN** `get_free_busy` is called with a list of `calendar_ids` and a `start_at`/`end_at` window
- **THEN** the provider returns the busy windows for all requested calendars merged into a single list bounded by the requested window
- **AND** for the Google provider this reuses the existing `/freeBusy` request body (`timeMin`, `timeMax`, `timeZone`, `items`) and the existing `calendars` → `busy[]` parsing, with `items` carrying every requested calendar id

#### Scenario: Empty result when no busy windows

- **WHEN** `get_free_busy` is called and no calendar reports any busy window in the requested range
- **THEN** an empty list of busy windows is returned

#### Scenario: find_conflicts delegates to get_free_busy

- **WHEN** `find_conflicts` is called for a candidate event on a single calendar
- **THEN** it resolves conflicts via `get_free_busy(calendar_ids=[calendar_id], start_at=candidate.start_at, end_at=candidate.end_at)`
- **AND** its return shape (a list of synthetic `(busy)` `CalendarEvent`s) and signature are unchanged from before the refactor

#### Scenario: Free/busy uses the existing calendar OAuth scope

- **WHEN** the Google provider issues a free/busy query
- **THEN** it authorizes the request with the already-granted `calendar` scope and requires no additional OAuth scope

### Requirement: Find Free Slots Tool

The module SHALL register an MCP tool `calendar_find_free_slots` that turns free/busy data into ranked open time slots. It is a read-only availability tool: it proposes slots and never creates, updates, or deletes an event.

#### Scenario: Rank open slots over a search window

- **WHEN** `calendar_find_free_slots` is called with a `duration_minutes`, a `search_start`/`search_end` window, optional `calendar_ids`, and optional structured `constraints`
- **THEN** it queries `get_free_busy` over the window, subtracts the busy windows to obtain free gaps, splits each gap into `duration_minutes`-sized candidate slots, and returns them ranked earliest-first (constraint-matching slots preferred)
- **AND** at most `limit` slots are returned
- **AND** no event is created, updated, or deleted as a side effect

#### Scenario: Slots respect owner scheduling preferences

- **WHEN** `calendar_find_free_slots` runs and owner scheduling-availability preferences are configured
- **THEN** returned slots lie within the owner's allowed meeting hours and days and do not overlap any no-meeting block
- **AND** when no owner preferences row exists, only busy-window subtraction and the search window constrain the results

#### Scenario: Natural-language constraints are pre-parsed into structured form

- **WHEN** a caller wants constraints like "mornings only" or "avoid Fridays"
- **THEN** the caller passes them as structured `constraints` (e.g. part-of-day, avoided weekdays) and the deterministic finder applies them
- **AND** the finder itself performs no LLM call

#### Scenario: Fully busy window returns no slots

- **WHEN** `calendar_find_free_slots` is called and the search window contains no gap long enough for `duration_minutes`
- **THEN** an empty slots list is returned (fail-open, not an error)

## Source References

- Non-Negotiable Rule 1 (single-owner data sovereignty)
- Non-Negotiable Rule 4 (LLM reasoning is ephemeral)
- RFC 0006 (Database schema isolation)
- RFC 0010 (Cross-Butler Briefing Exception)
- RFC 0014 (Chronicler Time Butler) §D3 Adapter Contract
