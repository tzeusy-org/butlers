## MODIFIED Requirements

### Requirement: Provider-Agnostic Architecture

The module defines an abstract `CalendarProvider` interface with concrete implementations per provider. Currently only Google Calendar is implemented. The module SHALL track the dedicated "Butlers" calendar id and the user's primary calendar id as distinct roles, and SHALL NOT overwrite the Butlers calendar id when the user changes the default target.

#### Scenario: Provider selection at startup with account

- **WHEN** the Calendar module starts up with `provider = "google"` and `account = "work@gmail.com"` in config
- **THEN** a Google provider instance is created with OAuth credentials resolved from the credential store for the specified Google account
- **AND** the butler calendar ID is resolved from credential store or auto-discovered via shared "Butlers" calendar on that account

#### Scenario: Provider selection at startup without account (primary)

- **WHEN** the Calendar module starts up with `provider = "google"` and no `account` field in config
- **THEN** credentials are resolved for the primary Google account
- **AND** behavior is identical to pre-multi-account single-account deployments

#### Scenario: Calendar ID role resolution

- **WHEN** the Calendar module completes startup and calendar discovery
- **THEN** distinct calendar IDs are tracked with distinct roles:
  - the Butlers calendar id: the dedicated "Butlers" group calendar (auto-discovered or created via `discover_or_create_calendar("Butlers")`, persisted to credential key `GOOGLE_CALENDAR_ID`). This is the **default write target for all butler-authored events** and is also the calendar that scheduled tasks and reminders are pushed to on Google.
  - the primary calendar id: the user's primary Google Calendar (the one marked `primary: true` in Google's calendarList). The user's own events live here; the butler edits them in place but does not create new butler-authored events here by default.
- **AND** the "Butlers" calendar id is treated as immutable for the lifetime of the connected account and is NOT overwritten by `calendar_set_primary`

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
- **THEN** the event is visible in the projection tables before the next sync round-trip
- **AND** a background projection sync still runs for reconciliation and freshness metadata
- **AND** failures in eager projection are logged but do not block the mutation response (fail-open)
- **BECAUSE** Google's incremental sync API has indexing latency (1-5s) after writes, and relying on a sync round-trip to project the mutation creates a race condition where the event may never reach the projection tables

#### Scenario: Update event with partial patch

- **WHEN** `calendar_update_event` is called with an event_id and partial fields
- **THEN** only non-None fields are sent to the provider's PATCH endpoint
- **AND** timezone changes re-emit start/end boundaries with the new timezone
- **AND** `all_day=true` serializes Google `start` and `end` as date-only
  boundaries, never `dateTime` boundaries

#### Scenario: Delete event

- **WHEN** `calendar_delete_event` is called with an event_id
- **THEN** the event is deleted from the provider calendar

### Requirement: CalendarEvent Model

The canonical `CalendarEvent` model SHALL be provider-neutral with fields:
`event_id`, `title`, `start_at`, `end_at`, `timezone`, `all_day`,
`description`, `body`, `location`, `attendees` (list of `AttendeeInfo`),
`recurrence_rule`, `color_id`, `butler_generated`, `butler_name`,
`source_butler`, `source_session_id`, `entity_ids`, `status`, `organizer`,
`visibility`, `etag`, `created_at`, and `updated_at`.

- `all_day` is provider-authoritative boolean truth. Google `start.date` and
  `end.date` boundaries set it to `true`, and all-day writes SHALL preserve the
  same date-only representation.

#### Scenario: Google date-only event preserves all-day truth

- **WHEN** a Google event payload has date-only `start.date` and `end.date`
  boundaries
- **THEN** its `CalendarEvent` has `all_day=true`
- **AND** a create or update carrying that truth serializes Google `start.date`
  and `end.date`, never `dateTime` boundaries

#### Scenario: Google event parsing

- **WHEN** a Google Calendar API event payload is received
- **THEN** it is parsed into a `CalendarEvent`
- **AND** cancelled events return `None`
- **AND** attendees are parsed into `AttendeeInfo` objects with email, display_name, response_status, optional, organizer, self_, and comment fields
- **AND** recurrence rules are extracted from the `recurrence` array
- **AND** butler-generated metadata is extracted from `extendedProperties.private`
- **AND** `description` field is mapped to `body` on the model
- **AND** date-only `start.date` and `end.date` boundaries set `all_day=true`

#### Scenario: Authorship annotation on create

- **WHEN** `calendar_create_event` or `calendar_update_event` is called
- **THEN** the resulting event is annotated with `source_butler` (the butler's name) and `source_session_id` (the current runtime session ID)
- **AND** both values are written to the `calendar_events` row in the projection table

#### Scenario: Entity association on create and update

- **WHEN** `calendar_create_event`, `calendar_update_event`, or `calendar_update_butler_event` is called with a non-empty `entity_ids` set
- **THEN** the junction table `calendar_event_entities` is updated
- **AND** existing entity links for the event are replaced with the new set (full replace, not additive)

#### Scenario: Explicitly clear every entity association

- **WHEN** `calendar_update_event` is called with `entity_ids=[]` and `clear_entity_ids=true`
- **THEN** every existing `calendar_event_entities` row for that event is deleted without attempting an empty insert
- **AND** the eager projection write-through carries the same explicit-clear signal so the workspace reflects the removal before the next provider sync
- **AND** `clear_entity_ids=true` with an omitted or non-empty `entity_ids` value is rejected as ambiguous
- **AND** an omitted `entity_ids`, or an empty list without `clear_entity_ids=true`, remains a no-op that preserves existing links

#### Scenario: Entity association on read

- **WHEN** an event is returned from `calendar_get_event`, `calendar_list_events`, or any projection read path
- **THEN** the event's `entity_ids` field is populated from `calendar_event_entities`

### Requirement: calendar_event_entities Junction Table

The `calendar_event_entities` table SHALL be the cross-reference between `calendar_events` rows and entities in the memory module's entity graph. It MUST enable reverse lookup — given an entity, find all calendar events associated with it — and it SHALL be the authoritative source of participant entity membership for downstream retrospective projection by the Chronicler butler.

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
- **BECAUSE** attendee → entity resolution is a write-time deterministic step owned by the calendar module; the chronicler retrospective view must reflect that decision rather than invent its own

### Requirement: RRULE and Cron Support

The module SHALL support both RFC-5545 RRULE recurrence and cron expressions for event and task projection.

#### Scenario: RRULE occurrence expansion

- **WHEN** an event has a `recurrence_rule` (with or without `RRULE:` prefix)
- **THEN** instances are expanded within a given time window using dateutil
- **AND** each occurrence gets a `(starts_at, ends_at)` pair with configurable duration

#### Scenario: Cron task projection

- **WHEN** a scheduled task has a cron expression
- **THEN** firing times are expanded within a window using croniter
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
- **THEN** the suggestion list SHALL NOT contain a slot that starts before the earliest meeting time, ends after the latest meeting time, falls on a disallowed weekday, or overlaps a no-meeting block

#### Scenario: Suggestions with no owner preferences configured

- **WHEN** suggested slots are built and no owner scheduling-availability preferences row exists
- **THEN** slot suggestion behaves as before (forward-stepping from the last conflict), applying no life-availability filtering

### Requirement: Reminder Tools

The module SHALL register three MCP tools for managing butler-owned reminders as native calendar events: `reminder_create`, `reminder_list`, `reminder_dismiss`. Reminders are stored as `calendar_events` rows with `source_kind = 'internal_reminders'` and scoped to the calling butler via `source_butler`. `calendar_events` is the sole authoritative store for reminders.

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
- **THEN** the default target calendar is updated to the specified calendar
- **AND** the choice is persisted to the credential store so it survives restarts
- **AND** the `calendar_id` must be one of the discovered provider calendars

### Requirement: Programmatic Butler-Authored Event Creation

The programmatic inter-module entry point `create_user_event` (used by e.g. health/meal logging) SHALL be a first-class butler-authored write: it targets the dedicated "Butlers" calendar and stamps butler-generated provenance, consistent with `calendar_create_event`.

#### Scenario: Meal/health event lands on the Butlers calendar

- **WHEN** another module calls `create_user_event(title, start_at, end_at, description)`
- **THEN** the event is created on the dedicated "Butlers" calendar
- **AND** it is stamped with butler-generated metadata (`butler_generated=true`, `butler_name`) and `BUTLER:` title branding
- **AND** the `calendar.write` permission grant is enforced via the permissions matrix before the provider write

### Requirement: [TARGET-STATE] Calendar Sync and Projection

The module SHALL provide provider sync with incremental/full modes and a unified projection table for fast dashboard queries.

#### Scenario: Incremental sync via sync token

- **WHEN** a sync token exists for a calendar
- **THEN** incremental sync fetches only changed events since the last token
- **AND** an expired sync token triggers a full sync fallback

#### Scenario: Internal task projection

- **WHEN** the butler has scheduled tasks with cron expressions
- **THEN** a periodic background task projects them as `SOURCE_KIND_INTERNAL_SCHEDULER` entries

#### Scenario: Sync deadman escalates persistent staleness to QA

- **GIVEN** an enabled provider source's `calendar_sync_cursors` has not
  stamped `last_synced_at` within 2x the poll interval
  (`DEFAULT_SYNC_INTERVAL_MINUTES`, absent a per-butler override)
- **WHEN** the dashboard-api's independent sync-deadman check (external to
  each butler's own sync-poller task, so a dead poller loop cannot silence
  its own watchdog) observes the same stale-source composition across two
  consecutive check ticks
- **THEN** it escalates once — a `public.healing_attempts` case in terminal
  `unfixable` status with a human-action `error_detail` — debounced via
  `public.audit_log` first-detected/escalated markers so a single-tick blip
  or an already-escalated composition never re-fires (no notification storm)
- **AND** an operator-disabled source (`sync_enabled: false`) and an internal
  (non-provider) source are never flagged — only genuine provider-sync death
  counts

#### Scenario: Superseded provider instance pruned on time-drifted re-sync

- **GIVEN** a provider (`lane="user"`) event already projected as one
  `calendar_event_instances` row, whose `origin_instance_ref` embeds the event
  start (`f"{event_id}:{start.isoformat()}"`)
- **WHEN** a later sync re-projects the same event at a drifted start (the
  `ON CONFLICT (event_id, origin_instance_ref)` upsert INSERTs a NEW instance
  under a different `origin_instance_ref` rather than updating the prior one)
- **THEN** the sync deletes every other instance of that
  `event_id` (`origin_instance_ref IS DISTINCT FROM` the just-written ref) so the
  ledger converges to exactly one instance per provider event
- **AND** the prune is fail-open — a DB error is logged and never propagates into
  the sync loop
- **BECAUSE** a provider event projects to exactly one instance per sync; without
  the prune, each time-drifted re-sync leaves a stale copy behind and the same
  event renders twice with two different windows

#### Scenario: Probe/sentinel calendar source never registered and purged once

- **GIVEN** a sentinel calendar id used only by credential/health probes
  (`__invalid_check__`), never a real calendar
- **WHEN** any source-registration path is reached
  with that calendar id
- **THEN** registration is refused (returns `None`, no `calendar_sources` row
  written) so no bogus `provider:<name>:__invalid_check__` source can pollute the
  source-freshness ledger
- **AND** on module startup a one-time idempotent purge deletes any residual
  probe source (`DELETE FROM calendar_sources WHERE calendar_id = ANY(...)`,
  cascading to its events/instances/cursors), a no-op once clean
- **BECAUSE** a probe that historically reached the sync path persisted a
  permanent phantom source that showed as a perpetually-stale lane in the
  freshness ledger

#### Scenario: Projection authorship fields normalize missing provenance

- **WHEN** a projection row is written and `source_butler` is null, blank, or the sentinel `"unknown"`
- **THEN** the write SHALL fall back to the module's canonical butler name and finally `DEFAULT_BUTLER_NAME` before inserting into `calendar_events.source_butler`
- **AND** `source_session_id` SHALL be stripped and blank values SHALL be stored as `NULL`
- **BECAUSE** projection authorship columns are part of the durable calendar event provenance contract and must remain canonical while satisfying the non-null database constraint

### Requirement: Dual-Lane Ownership and Authoritativeness

The projection SHALL use a dual-lane model to separate event authority. Each `calendar_sources` row has a `lane` field: `"user"` or `"butler"`. The lane determines which system is authoritative for an event's state.

- **`lane="user"`** — Provider-synced external events (meetings, appointments created by humans on Google Calendar). Google is the source of truth. The local projection faithfully mirrors whatever the provider reports on each sync cycle.
- **`lane="butler"`** — Internal scheduled tasks and reminders managed by the butler. The butler's `calendar_events` rows (for reminders with `source_kind='internal_reminders'`) and `scheduled_tasks` table are the source of truth. These are pushed outbound to Google for visibility but Google is never read back as authoritative for them.

#### Scenario: Butler-generated events in provider sync projection

- **WHEN** the provider sync processes events returned by an incremental or full sync
- **THEN** all events are persisted to the projection, including butler-generated ones
- **AND** butler-generated metadata (`butler_generated`, `butler_name`) is preserved in the projection row metadata for UI differentiation
- **BECAUSE** butler events created via `calendar_create_event` are user-lane workspace mutations (not internal scheduler items) and should be visible in the provider projection

#### Scenario: Butler overwrites external edits to butler-owned events

- **WHEN** a user manually moves or edits a butler-generated event directly on Google Calendar
- **AND** the next sync cycle runs
- **THEN** the provider sync skips the modified event (butler-generated filter)
- **AND** the outbound push of internal events overwrites the Google event with the butler's local state (title, start/end from `scheduled_tasks` or `calendar_events` with `source_kind='internal_reminders'`)
- **BECAUSE** the butler's database is authoritative for butler-owned events; Google is a read-only mirror for them

#### Scenario: External events faithfully track provider state

- **WHEN** a non-butler event is created or modified on Google Calendar
- **AND** the next sync cycle runs
- **THEN** the event is upserted into the `lane="user"` projection
- **AND** cancelled events are marked cancelled in the projection
- **AND** events no longer returned by a full sync are marked stale/cancelled

#### Scenario: Modifying butler events requires the butler

- **WHEN** a user wants to reschedule or edit a butler-managed event
- **THEN** they must use butler MCP tools (`calendar_update_butler_event`, `calendar_update_event` with the event ID)
- **AND** the butler updates both its local state and the Google Calendar event atomically
- **AND** direct Google Calendar edits will be silently reverted on the next sync cycle

### Requirement: Calendar Source Listing Tool

The module SHALL register a read-only `calendar_list_calendars` MCP tool that wraps `provider.list_calendars()` and returns the calendars available on the connected account in a normalized, butler-aware shape. This backs the dashboard's per-event calendar selector and the sources drawer; it is the source of the `calendar_id` values a caller may pass as an explicit target to `calendar_create_event`.

#### Scenario: List calendars on the connected account

- **WHEN** `calendar_list_calendars` is called
- **THEN** each calendar is returned with `calendar_id`, `summary` (display name), `primary`, `access_role`, `is_butlers_calendar`, and `selectable`
- **AND** the dedicated "Butlers" calendar is flagged with `is_butlers_calendar = true`
- **AND** a calendar whose access role is not `writer` or `owner` is marked `selectable = false` so it cannot be offered as a write target

#### Scenario: Provider failure fails open

- **WHEN** `calendar_list_calendars` is called and the provider raises an error
- **THEN** an empty calendar list is returned with error metadata rather than the call raising

### Requirement: Reversible Mutation Pre-State Capture

The calendar module SHALL capture the pre-mutation event state of every
reversible user-lane mutation into the recorded `action_result` so an inverse
is reconstructable. For `workspace_user_update` and `workspace_user_delete`,
the captured pre-image MUST include `all_day` with the existing title, start,
end, timezone, recurrence, calendar, and linked-people fields. The dashboard
undo endpoint SHALL pass the nullable `all_day` truth through the inverse
`calendar_update_event` or `calendar_create_event` payload without an extra
provider read.

#### Scenario: All-day pre-state round-trips through undo

- **WHEN** an applied user-lane update or delete has a captured pre-state with
  `all_day=true` and the dashboard reverses it
- **THEN** the inverse `calendar_update_event` or `calendar_create_event` call
  carries `all_day=true` with the captured start and end boundaries
- **AND** Google receives `start.date` and `end.date`, with no `dateTime`
  boundary in the inverse write
- **AND** the existing `this`, `following`, and `series` recurrence-scope
  semantics remain unchanged

#### Scenario: Update captures the pre-mutation event state

- **WHEN** `calendar_update_event` resolves an existing event and applies a patch
- **THEN** the finalized `action_result` for the `workspace_user_update` row
  includes the pre-mutation event state (at least title, start_at, end_at,
  timezone, `all_day`, location, description, attendees, recurrence_rule, the
  resolved calendar id, and `entity_ids`) under a stable key, alongside the
  existing post-mutation outcome
- **AND** the pre-state reuses the `existing_event` already fetched before the
  PATCH and reads local `entity_ids` from the projection when available, adding
  no extra provider round-trip

#### Scenario: Delete captures the pre-deletion event state

- **WHEN** `calendar_delete_event` removes an existing event
- **THEN** the finalized `action_result` for the `workspace_user_delete` row
  includes the pre-deletion event state (the fields needed to recreate the event)
  under the same stable key
- **AND** the captured pre-image is sufficient for an inverse
  `calendar_create_event` to recreate the event and its linked people on its home
  calendar

#### Scenario: Pre-state is absent for non-reversible or non-applied outcomes

- **WHEN** a mutation finalizes with status `failed` or `noop` (e.g. the target
  event was not found), or the mutation is a create (which has no pre-image)
- **THEN** no pre-mutation state is required in `action_result`
- **AND** the undo endpoint treats the absence of pre-state on an otherwise
  reversible action as a fail-fast condition (it does not guess an inverse)

#### Scenario: Idempotent-replay path is unchanged by capture

- **WHEN** a mutation is replayed under the same `request_id` and resolves to the
  previously recorded projection action
- **THEN** the existing replay behavior is preserved (the prior `action_result` is
  returned with `idempotent_replay=true`)
- **AND** capturing pre-state does not alter the `idempotency_key`, the action
  status transitions, or the replay contract
