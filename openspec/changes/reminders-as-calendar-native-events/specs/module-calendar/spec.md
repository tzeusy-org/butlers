## ADDED Requirements

### Requirement: Calendar event authorship tracking

Every `calendar_events` row SHALL have a `source_butler TEXT NOT NULL` column identifying the butler that created the event, and a `source_session_id TEXT` column (nullable) identifying the session that created it. Provider-synced events (Google Calendar) SHALL have `source_session_id = NULL`. Butler-created events SHALL populate both columns at creation time.

An index SHALL exist on `calendar_events(source_butler)` for efficient filtering.

#### Scenario: Butler creates a reminder event

- **WHEN** the relationship butler creates a reminder via `reminder_create`
- **THEN** the resulting `calendar_events` row SHALL have `source_butler = 'relationship'`
- **AND** `source_session_id` SHALL be set to the current session ID

#### Scenario: Provider-synced event has no session

- **WHEN** a Google Calendar event is synced into `calendar_events` via the provider sync pipeline
- **THEN** `source_butler` SHALL be set to the butler name that owns the calendar module instance performing the sync
- **AND** `source_session_id` SHALL be `NULL`

#### Scenario: Query events by source butler

- **WHEN** a query filters `calendar_events` by `source_butler = 'health'`
- **THEN** only events created by the health butler SHALL be returned

---

### Requirement: Calendar event title and body columns

Every `calendar_events` row SHALL have a `title TEXT NOT NULL` column (concise label, <30 words by convention) and a `body TEXT` column (nullable, longer description). These columns replace the pattern of storing event names in `metadata JSONB`.

#### Scenario: Creating an event with title and body

- **WHEN** `calendar_create_event` is called with `title = "Team standup"` and `body = "Daily sync with engineering team"`
- **THEN** the resulting `calendar_events` row SHALL have `title = 'Team standup'` and `body = 'Daily sync with engineering team'`

#### Scenario: Creating an event with title only

- **WHEN** `calendar_create_event` is called with `title = "Dentist appointment"` and no body
- **THEN** the resulting `calendar_events` row SHALL have `title = 'Dentist appointment'` and `body = NULL`

#### Scenario: Backfill existing events during migration

- **WHEN** the migration runs on existing `calendar_events` rows that lack `title`
- **THEN** `title` SHALL be populated from `metadata->>'title'` or `metadata->>'display_title'` where present
- **AND** rows with no extractable title SHALL receive `title = '(untitled)'`

---

### Requirement: Calendar event entity associations

Calendar events SHALL support association with zero or more entities via a junction table `calendar_event_entities`. This enables linking events to the people, organizations, or places they relate to.

```sql
CREATE TABLE calendar_event_entities (
    event_id UUID NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
    entity_id UUID NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, entity_id)
);
```

An index SHALL exist on `calendar_event_entities(entity_id)` for reverse lookups (find all events for a given entity).

#### Scenario: Reminder linked to one entity

- **WHEN** a reminder "Mom's birthday" is created with `entity_ids = [mom_entity_id]`
- **THEN** one row SHALL be inserted into `calendar_event_entities` with `event_id` and `entity_id = mom_entity_id`

#### Scenario: Meeting linked to multiple entities

- **WHEN** a calendar event "Lunch with Sarah and Tom" is created with `entity_ids = [sarah_id, tom_id]`
- **THEN** two rows SHALL be inserted into `calendar_event_entities`

#### Scenario: Event with no entity association

- **WHEN** a calendar event "Go for a run" is created with no `entity_ids`
- **THEN** no rows SHALL be inserted into `calendar_event_entities`

#### Scenario: Deleting an event cascades to junction

- **WHEN** a calendar event is deleted from `calendar_events`
- **THEN** all associated rows in `calendar_event_entities` SHALL be deleted via CASCADE

#### Scenario: Deleting an entity cascades to junction

- **WHEN** an entity is deleted from `public.entities`
- **THEN** all associated rows in `calendar_event_entities` SHALL be deleted via CASCADE
- **AND** the calendar event itself SHALL NOT be deleted (only the association is removed)

#### Scenario: Query events by entity

- **WHEN** a query joins `calendar_events` with `calendar_event_entities` filtering by `entity_id = X`
- **THEN** all calendar events associated with entity X SHALL be returned regardless of `source_kind`

---

### Requirement: Reminder MCP tools on calendar module

The calendar module SHALL register three reminder-specific MCP tools: `reminder_create`, `reminder_list`, and `reminder_dismiss`. These tools create and manage calendar events with `source_kind = "internal_reminders"`.

#### Scenario: Create a one-time reminder

- **WHEN** `reminder_create` is called with `title = "Call Mom"`, `due_at = "2026-05-01T09:00:00Z"`, and `entity_ids = [mom_id]`
- **THEN** a `calendar_events` row SHALL be created with:
  - `source_kind` resolved from a `calendar_sources` entry with `source_kind = "internal_reminders"` and `lane = "butler"`
  - `title = "Call Mom"`
  - `starts_at = 2026-05-01T09:00:00Z`
  - `ends_at = 2026-05-01T09:15:00Z` (default 15-minute duration)
  - `recurrence_rule = NULL` (one-time)
  - `status = "confirmed"`
  - `source_butler` set to the current butler name
  - `source_session_id` set to the current session ID
- **AND** a `calendar_event_entities` row SHALL link the event to `mom_id`

#### Scenario: Create a recurring yearly reminder

- **WHEN** `reminder_create` is called with `title = "Mom's birthday"`, `due_at = "2026-03-05T08:00:00Z"`, `recurrence = "yearly"`, and `entity_ids = [mom_id]`
- **THEN** a `calendar_events` row SHALL be created with `recurrence_rule = "RRULE:FREQ=YEARLY"`

#### Scenario: Create a recurring monthly reminder

- **WHEN** `reminder_create` is called with `title = "Rent due"`, `due_at = "2026-05-01T08:00:00Z"`, `recurrence = "monthly"`
- **THEN** a `calendar_events` row SHALL be created with `recurrence_rule = "RRULE:FREQ=MONTHLY"`

#### Scenario: List active reminders

- **WHEN** `reminder_list` is called with optional `entity_id` and `due_before` filters
- **THEN** it SHALL return calendar events where:
  - The event's source has `source_kind = "internal_reminders"`
  - `source_butler` matches the calling butler
  - `status != 'cancelled'` (excludes dismissed)
  - If `entity_id` provided: event is linked via `calendar_event_entities`
  - If `due_before` provided: `starts_at <= due_before`
- **AND** results SHALL include entity associations resolved from the junction table

#### Scenario: List reminders without filters

- **WHEN** `reminder_list` is called with no filters
- **THEN** all active (non-cancelled) reminders for the calling butler SHALL be returned

#### Scenario: Dismiss a one-time reminder

- **WHEN** `reminder_dismiss` is called with an event ID for a one-time reminder (no recurrence_rule)
- **THEN** the event's `status` SHALL be set to `'cancelled'`

#### Scenario: Dismiss a recurring reminder

- **WHEN** `reminder_dismiss` is called with an event ID for a recurring reminder
- **THEN** the current instance in `calendar_event_instances` SHALL be marked `status = 'cancelled'`
- **AND** the next instance SHALL remain with `status = 'confirmed'`
- **AND** if no future instances exist within the projection window, the event series SHALL remain active for future projection cycles

---

### Requirement: Default reminder duration

Reminder events created via `reminder_create` SHALL have a default duration of 15 minutes. The `ends_at` timestamp SHALL be set to `starts_at + 15 minutes` unless explicitly overridden.

#### Scenario: Reminder with default duration

- **WHEN** `reminder_create` is called with `due_at = "2026-05-01T09:00:00Z"` and no explicit `ends_at`
- **THEN** `starts_at = 2026-05-01T09:00:00Z` and `ends_at = 2026-05-01T09:15:00Z`

#### Scenario: Reminder with explicit duration

- **WHEN** `reminder_create` is called with `due_at = "2026-05-01T09:00:00Z"` and `ends_at = "2026-05-01T10:00:00Z"`
- **THEN** `starts_at = 2026-05-01T09:00:00Z` and `ends_at = 2026-05-01T10:00:00Z` (override honored)

---

### Requirement: Tick source_butler scoping

The calendar module's `tick()` method SHALL accept a required `source_butler` parameter. When evaluating due events for reminder notification, `tick()` SHALL only consider events where `calendar_events.source_butler` matches the provided `source_butler` value.

#### Scenario: Relationship butler tick evaluates only its reminders

- **WHEN** the relationship butler's daemon calls `calendar_module.tick(source_butler="relationship")`
- **THEN** only calendar events with `source_butler = 'relationship'` SHALL be evaluated for due-reminder notification
- **AND** events with `source_butler = 'health'` or any other butler SHALL be ignored

#### Scenario: Health butler tick evaluates only its reminders

- **WHEN** the health butler's daemon calls `calendar_module.tick(source_butler="health")`
- **THEN** only calendar events with `source_butler = 'health'` SHALL be evaluated

#### Scenario: Due reminder triggers notification

- **WHEN** `tick(source_butler="relationship")` runs
- **AND** a reminder event exists with `source_butler = 'relationship'`, `source_kind = "internal_reminders"`, `status = 'confirmed'`, and `starts_at` is in the past or within the current tick window
- **THEN** a notification SHALL be dispatched via the butler's `notify()` tool
- **AND** the event's metadata SHALL be updated to record the notification timestamp

---

## MODIFIED Requirements

### Requirement: Butler Event Management Tools

The module registers MCP tools for managing butler-owned workspace events (scheduled tasks projected as calendar entries): `calendar_create_butler_event`, `calendar_update_butler_event`, `calendar_delete_butler_event`, `calendar_toggle_butler_event`. Reminder management is handled by the dedicated `reminder_create`, `reminder_list`, and `reminder_dismiss` tools.

#### Scenario: Create butler event

- **WHEN** `calendar_create_butler_event` is called with title, timing, and source type (scheduled task)
- **THEN** a butler-managed event is created with recurrence support (RRULE or cron)
- **AND** the event is tagged with butler metadata for unified calendar projection
- **AND** `source_butler` is set to the calling butler's name

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

---

### Requirement: [TARGET-STATE] Calendar Sync and Projection

Provider sync with incremental/full modes and a unified projection table for fast dashboard queries. Reminders are native calendar events and do not require separate projection logic.

#### Scenario: Incremental sync via sync token

- **WHEN** a sync token exists for a calendar
- **THEN** incremental sync fetches only changed events since the last token
- **AND** an expired sync token triggers a full sync fallback

#### Scenario: Internal task projection

- **WHEN** the butler has scheduled tasks with cron expressions
- **THEN** a periodic background task projects them as `SOURCE_KIND_INTERNAL_SCHEDULER` entries

#### Scenario: Reminder events in projection

- **WHEN** the butler has reminder events in `calendar_events` with `source_kind = "internal_reminders"`
- **THEN** they SHALL be projected into `calendar_event_instances` using the same RRULE/cron expansion logic as other events
- **AND** no separate `_project_reminders_source` pipeline is needed — reminders are projected as regular calendar events

---

### Requirement: Dual-Lane Ownership and Authoritativeness

The projection uses a dual-lane model to separate event authority. Each `calendar_sources` row has a `lane` field: `"user"` or `"butler"`. The lane determines which system is authoritative for an event's state.

- **`lane="user"`** — Provider-synced external events (meetings, appointments created by humans on Google Calendar). Google is the source of truth.
- **`lane="butler"`** — Internal scheduled tasks and reminders managed by the butler. The butler's `calendar_events` table is the source of truth. These are pushed outbound to Google for visibility but Google is never read back as authoritative for them.

#### Scenario: Butler-generated events in provider sync projection

> **SPEC-CODE DIVERGENCE**: The implementation at `_project_provider_changes` (calendar.py:5370-5376) persists ALL provider events including butler-generated ones, noting butler metadata for UI differentiation. The original exclusion behavior described below is not implemented. The rationale in code: butler events created via `calendar_create_event` (workspace mutations) are distinct from internal scheduler items and should appear in the provider projection.

- **WHEN** `_project_provider_changes` processes events returned by an incremental or full sync
- **THEN** all events are persisted to the projection, including butler-generated ones
- **AND** butler-generated metadata (`butler_generated`, `butler_name`) is preserved in the projection row metadata for UI differentiation
- **BECAUSE** butler events created via `calendar_create_event` are user-lane workspace mutations (not internal scheduler items) and should be visible in the provider projection

#### Scenario: Butler overwrites external edits to butler-owned events

- **WHEN** a user manually moves or edits a butler-generated event directly on Google Calendar
- **AND** the next sync cycle runs
- **THEN** the provider sync skips the modified event (butler-generated filter)
- **AND** `_push_internal_events_to_provider` overwrites the Google event with the butler's local state (title, start/end from `calendar_events`)
- **BECAUSE** the butler's database is authoritative for butler-owned events; Google is a read-only mirror for them

#### Scenario: External events faithfully track provider state

- **WHEN** a non-butler event is created or modified on Google Calendar
- **AND** the next sync cycle runs
- **THEN** the event is upserted into the `lane="user"` projection via `_project_provider_changes`
- **AND** cancelled events are marked cancelled in the projection
- **AND** events no longer returned by a full sync are marked stale/cancelled via `_mark_projection_source_stale_events_cancelled`

#### Scenario: Modifying butler events requires the butler

- **WHEN** a user wants to reschedule or edit a butler-managed event
- **THEN** they must use butler MCP tools (`calendar_update_butler_event`, `calendar_update_event` with the event ID, or `reminder_dismiss` for reminders)
- **AND** the butler updates both its local state and the Google Calendar event atomically
- **AND** direct Google Calendar edits will be silently reverted on the next sync cycle

---

## REMOVED Requirements

### Requirement: Separate reminder projection pipeline

**Reason:** Reminders are now native calendar events stored in `calendar_events`. The separate `_project_reminders_source` and `_create_reminder_event` pipeline is no longer needed — reminders are projected using the same mechanism as all other calendar events.

**Migration:** Existing reminder data is migrated to `calendar_events` via an Alembic migration. The `reminders` table is renamed to `_reminders_backup` for one release cycle, then dropped.
