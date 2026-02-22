# Calendar Module: Permanent Definition

Status: Normative (Target State)
Last updated: 2026-02-22
Primary owner: Platform/Modules

## 1. Module

The Calendar module is a reusable module that relevant butlers load locally.

It is responsible for:
- Reading and writing calendar events through a provider-agnostic interface (Google Calendar v1, extensible to iCloud Calendar and others).
- Managing event lifecycle: creation, updates, rescheduling, cancellation, and RSVP state tracking.
- Enforcing conflict detection and resolution policies (suggest alternatives, fail, or allow with approval gate).
- Supporting timezone-aware scheduling with all-day and timed event semantics.
- Tracking invitee/attendee state (accepted, declined, tentative, needs-action) and response changes.
- Providing event documentation through structured descriptions and private notes.
- Keeping the hosting butler's calendar view current through polling-based sync (v1) with a path toward push/subscription (v2+).
- Powering a Google Calendar-like dashboard page at `/butlers/calendar` (for example `https://tzeusy.parrot-hen.ts.net/butlers/calendar`) with a view toggle between user events and butler-managed schedules/reminders.
- Persisting normalized calendar data in-app so the dashboard can query fast, consistent calendar state across sources and butlers.

This document is the authoritative target-state contract for calendar behavior when the module is enabled.

## 2. Design Goals

- Provider-agnostic core: all tool semantics and data models are provider-neutral. Provider adapters translate to/from canonical shapes.
- Butler-owned subcalendar isolation: butlers write events to a dedicated subcalendar, never the user's primary calendar, unless explicitly configured otherwise.
- Conflict-aware by default: every create/update checks for overlapping events and applies the configured conflict policy before writing.
- Timezone-first: all event boundaries carry explicit IANA timezone information. Naive datetimes are rejected or resolved against the butler's configured default timezone.
- Fail-open for reads, fail-closed for writes: list/get failures log and return partial results; create/update/delete failures must not silently drop mutations.
- Approval-integrated: overlap overrides and high-impact scheduling actions (e.g., cancelling events with external attendees) can be routed through the approvals module when enabled.
- Dual-view workspace: a single calendar surface supports `user` and `butler` modes with shared interaction patterns (create/edit/delete/drag/reschedule/sync).
- App-native persistence: external provider events and internal butler schedules/reminders are normalized into durable local records for rendering, analytics, and deterministic reconciliation.

## 3. Applicability and Boundaries

### In scope
- Module configuration and tool registration contract.
- Provider-neutral event CRUD and conflict detection.
- Event RSVP/attendee state tracking and response propagation.
- Recurrence rule handling (RRULE creation, series vs. instance scope).
- Polling-based calendar sync and freshness semantics.
- Event documentation (description, private notes, attachments metadata).
- Butler-generated event tagging and identification.
- Conflict policy enforcement and suggested-slot generation.
- Approval integration for overlap overrides.
- Dashboard calendar UX contract for `/butlers/calendar`, including `user` and `butler` views.
- In-app calendar storage/projection model for user calendars plus butler schedule/reminder events.

### Out of scope
- Direct CalDAV/iCal protocol implementation (providers wrap their native APIs).
- Calendar sharing or ACL management.
- Video conferencing link generation (Google Meet, Zoom, etc.).
- Cross-butler execution coupling (the dashboard may read all butlers, but execution ownership remains per butler and goes through existing APIs/tools).
- Full parity with every native provider feature (color palettes, offline editing, conference lifecycle, resource booking, etc.).

## 4. Runtime Architecture Contract

### 4.1 Local components (per hosting butler)
- `Calendar tools`: module-registered MCP tools on the hosting butler MCP server.
- `Calendar provider`: adapter instance (e.g., `_GoogleProvider`) managing authenticated API access and response translation.
- `OAuth client`: credential manager with refresh-token exchange and access-token caching (Google v1).
- `Sync poller`: scheduled task that pulls recent changes and updates local event cache (when local cache is enabled).
- `Conflict engine`: pre-write conflict detection using provider free/busy APIs and local event data.
- `Approval enqueuer`: optional callback wired by the daemon when the approvals module is co-loaded.
- `Calendar projection store`: normalized local tables for both external user events and internal butler schedule/reminder events.
- `Calendar workspace API`: backend query layer serving `/butlers/calendar` range/window requests.
- `Butler schedule projector`: adapter that maps `scheduled_tasks` plus butler-specific reminder tables into unified calendar entries.

### 4.2 Mandatory runtime flows

1. `Startup`
   - Module validates config from `[modules.calendar]` in `butler.toml`.
   - Provider adapter is instantiated and credentials are verified (OAuth token refresh for Google).
   - If approvals module is co-loaded, daemon wires the overlap-approval enqueuer via `set_approval_enqueuer(...)`.

2. `Event read (list/get)`
   - Tool resolves `calendar_id` from config default or explicit override.
   - Provider adapter queries the external calendar API with time window, pagination, and filter parameters.
   - Response is translated to canonical `CalendarEvent` shapes and returned.

3. `Event write (create/update)`
   - Tool normalizes input payload (timezone resolution, all-day detection, recurrence validation).
   - Conflict engine checks for overlapping events via provider free/busy or event-list queries.
   - Conflict policy determines outcome: `suggest` returns alternatives, `fail` rejects, `allow_overlap` proceeds (with optional approval gate).
   - If approved/clear, provider adapter writes the event and returns the canonical result.
   - Butler-generated events are tagged with `BUTLER:` title prefix and private metadata.

4. `Event delete/cancel`
   - Tool verifies event existence and butler-generated status.
   - Provider adapter cancels or deletes the event (cancel preferred for events with attendees to send notifications).

5. `Polling sync` (when local cache enabled)
   - Scheduled task polls for changes since last sync token.
   - New, updated, and cancelled events are reflected in local cache.
   - Sync failures are logged but do not block butler operation.

6. `Workspace projection refresh`
   - A projector pass materializes/updates unified entries used by `/butlers/calendar`.
   - Provider-origin events are upserted from sync payloads.
   - Butler-origin events are upserted from scheduler/reminder sources and carry source pointers for bidirectional edits.
   - Projection lag is tracked and exposed via sync/status APIs.

### 4.3 Determinism and isolation
- Provider writes always flow through the configured provider API (Google v1 today).
- Dashboard reads are federated: per-butler stores can be queried and merged in a single UI response.
- Cross-butler visibility is read-only at the aggregation layer; mutations are routed to the owning butler/source adapter.
- Calendar reads for `/butlers/calendar` are served from local projection tables first, with provider fallback when the projection is stale/unavailable.
- Conflict detection is point-in-time; concurrent external changes may create races that are acceptable for v1.
- Butler-generated event metadata uses provider-specific private properties (Google `extendedProperties.private`) to avoid polluting user-visible fields.

### 4.4 Reliability
- Read failures (list/get) are fail-open: return empty results with error metadata, log the failure.
- Write failures (create/update/delete) are fail-closed: raise structured errors with provider error context.
- OAuth token refresh failures are retried once before raising `CalendarTokenRefreshError`.
- Provider HTTP errors include status code and sanitized error message (no credential leakage).

## 5. Data Model Contract

### 5.1 CalendarEvent (canonical read model)

Purpose: provider-neutral representation of a calendar event returned by all read operations.

Required fields:
- `event_id` (str): provider-assigned unique identifier.
- `title` (str): event summary/title. Butler-generated events carry the `BUTLER:` prefix.
- `start_at` (datetime): event start, always timezone-aware.
- `end_at` (datetime): event end, always timezone-aware.
- `timezone` (str): IANA timezone identifier for the event's primary timezone.

Optional fields:
- `description` (str | None): free-text event description/body.
- `location` (str | None): event location (physical address, room name, or virtual link).
- `attendees` (list[AttendeeInfo]): structured attendee list with RSVP state (see 5.4).
- `recurrence_rule` (str | None): RRULE string for recurring events.
- `status` (EventStatus): event lifecycle status (see 5.3).
- `color_id` (str | None): provider color identifier.
- `organizer` (str | None): email address of the event organizer.
- `visibility` (EventVisibility): event visibility level (see 5.5).
- `butler_generated` (bool): whether this event was created by a butler.
- `butler_name` (str | None): which butler created the event.
- `notes` (str | None): butler-private notes stored in provider extended properties, invisible to attendees.
- `etag` (str | None): provider-assigned version tag for optimistic concurrency.
- `created_at` (datetime | None): when the event was created.
- `updated_at` (datetime | None): when the event was last modified.

### 5.2 CalendarEventCreate / CalendarEventUpdate (write models)

`CalendarEventCreate` required fields:
- `title` (str): event summary.
- `start_at` (datetime | date): event start (date for all-day events).
- `end_at` (datetime | date): event end (date for all-day events, exclusive).
- `timezone` (str | None): IANA timezone; falls back to butler config default.

`CalendarEventCreate` optional fields:
- `all_day` (bool | None): explicit all-day flag; inferred from date-only boundaries when omitted.
- `description` (str | None): event body text.
- `location` (str | None): event location.
- `attendees` (list[str]): email addresses of invitees.
- `recurrence_rule` (str | None): RRULE string (must include `FREQ=`, must not include `DTSTART`/`DTEND`).
- `notification` (CalendarNotificationInput | bool | int | None): reminder configuration.
- `color_id` (str | None): provider color identifier.
- `status` (EventStatus | None): initial status (defaults to `confirmed`).
- `visibility` (EventVisibility | None): visibility level (defaults to provider default).
- `notes` (str | None): butler-private notes (stored in extended properties).
- `private_metadata` (dict[str, str]): provider extended properties for butler tagging.

`CalendarEventUpdate` is a partial patch model where all fields are optional. Additionally:
- `recurrence_scope` (Literal["series", "this_instance", "this_and_following"]): scope of recurrence updates. v1 supports `series` only; `this_instance` and `this_and_following` are target-state.
- `send_updates` (SendUpdatesPolicy | None): whether to notify attendees of the change (see 5.7).

### 5.3 EventStatus

Event lifecycle states as tracked by the provider.

| Status | Meaning |
|--------|---------|
| `confirmed` | Event is confirmed and active (default for new events). |
| `tentative` | Event is tentatively scheduled (organizer has not confirmed). |
| `cancelled` | Event has been cancelled. |

State transitions:
- `confirmed -> tentative`: organizer marks event as tentative.
- `confirmed -> cancelled`: organizer cancels the event.
- `tentative -> confirmed`: organizer confirms the event.
- `tentative -> cancelled`: organizer cancels the tentative event.
- `cancelled` is terminal for the event instance.

### 5.4 AttendeeInfo

Structured attendee representation with RSVP tracking.

| Field | Type | Description |
|-------|------|-------------|
| `email` | str | Attendee email address. |
| `display_name` | str \| None | Attendee display name if available. |
| `response_status` | AttendeeResponseStatus | Current RSVP state. |
| `optional` | bool | Whether attendance is optional (default false). |
| `organizer` | bool | Whether this attendee is the organizer. |
| `self_` | bool | Whether this attendee is the authenticated calendar user. |
| `comment` | str \| None | Attendee's RSVP comment. |

### 5.5 AttendeeResponseStatus

| Status | Meaning |
|--------|---------|
| `needs_action` | Invitee has not responded (default for new invitees). |
| `accepted` | Invitee accepted the invitation. |
| `declined` | Invitee declined the invitation. |
| `tentative` | Invitee tentatively accepted. |

These states are read-only from the butler's perspective — they reflect the invitee's response as reported by the provider. The organizer (butler) cannot set an attendee's response status; they can only add/remove attendees or change their optional flag.

### 5.6 EventVisibility

| Visibility | Meaning |
|------------|---------|
| `default` | Use the calendar's default visibility. |
| `public` | Event details are visible to anyone with calendar access. |
| `private` | Only attendees can see event details; others see only free/busy. |
| `confidential` | Only the organizer sees full details; others see free/busy. |

### 5.7 SendUpdatesPolicy

Controls whether attendees receive email notifications for event changes.

| Policy | Meaning |
|--------|---------|
| `all` | Send notifications to all attendees. |
| `external_only` | Send notifications only to non-organizer-domain attendees. |
| `none` | Do not send any notifications. |

Butler default: `none` for butler-managed events (to avoid spam). Overridable per-tool-call.

### 5.8 Workspace Projection Persistence (v1)

The calendar workspace uses normalized persistence tables for source metadata, unified events, expanded instances, sync checkpoints, and mutation audit/idempotency tracking.

| Table | Purpose | Required columns | Key constraints/indexes |
|------|---------|------------------|-------------------------|
| `calendar_sources` | Source registry for user/provider and butler lanes. | `id`, `source_key`, `source_kind`, `lane`, `metadata`, `created_at`, `updated_at` | `UNIQUE(source_key)`; `lane IN ('user','butler')`; source lookup index on `(lane, source_kind)` |
| `calendar_events` | Canonical projected event rows. | `id`, `source_id`, `origin_ref`, `title`, `timezone`, `starts_at`, `ends_at`, `status`, `metadata` | `UNIQUE(source_id, origin_ref)`; `ends_at > starts_at`; status enum check; source/time index `(source_id, starts_at)`; GiST range index on `tstzrange(starts_at, ends_at, '[)')` |
| `calendar_event_instances` | Expanded recurrence/event instances. | `id`, `event_id`, `source_id`, `origin_instance_ref`, `timezone`, `starts_at`, `ends_at`, `status`, `metadata` | `UNIQUE(event_id, origin_instance_ref)`; `ends_at > starts_at`; status enum check; source/event time indexes and GiST range index for window overlap |
| `calendar_sync_cursors` | Incremental/full sync checkpoints per source. | `source_id`, `cursor_name`, `sync_token`, `checkpoint`, `last_synced_at`, `created_at`, `updated_at` | `PRIMARY KEY(source_id, cursor_name)`; non-empty cursor name check; stale-sync lookup index on `last_synced_at` |
| `calendar_action_log` | Mutation audit trail + idempotency guardrail. | `id`, `idempotency_key`, `action_type`, `action_status`, `action_payload`, `created_at`, `updated_at` | `UNIQUE(idempotency_key)`; action status enum check; request/source/event/instance audit lookup indexes |

Provider sync and workspace mutation handlers MUST use `calendar_action_log.idempotency_key` to prevent duplicate side effects during retries/replays, and MUST maintain deterministic source linkage via `calendar_events(source_id, origin_ref)` and `calendar_event_instances(event_id, origin_instance_ref)`.

### 5.9 UnifiedCalendarEntry (local workspace read model)

Purpose: single shape returned to `/butlers/calendar` regardless of source type.

Required fields:
- `entry_id` (uuid): local primary key.
- `view` (Literal["user", "butler"]): which workspace mode the entry belongs to.
- `source_type` (Literal["provider_event", "scheduled_task", "butler_reminder", "manual_butler_event"]): source category.
- `title` (str): display title shown on calendar grid/list.
- `start_at` (datetime): timezone-aware start boundary.
- `end_at` (datetime): timezone-aware end boundary.
- `timezone` (str): canonical IANA timezone.
- `all_day` (bool): all-day flag.

Optional fields:
- `calendar_id` (str | None): external provider calendar identifier when applicable.
- `provider_event_id` (str | None): upstream provider event identifier.
- `butler_name` (str | None): owning butler (required for `view="butler"`).
- `schedule_id` (uuid | None): pointer to `scheduled_tasks.id` when source is a scheduler row.
- `reminder_id` (uuid | None): pointer to a module reminder row when source is reminder-backed.
- `rrule` (str | None): recurrence rule for rendering and editing.
- `cron` (str | None): cron expression for scheduler-backed entries.
- `until_at` (datetime | None): end-of-series boundary (e.g., "every day until March 1").
- `status` (str): `active`, `paused`, `completed`, `cancelled`, or `error`.
- `sync_state` (str | None): projection freshness marker (`fresh`, `stale`, `syncing`, `failed`).
- `editable` (bool): whether UI can edit in current context.
- `metadata` (dict[str, Any]): source-specific metadata for detail drawers.

### 5.10 App-native storage tables (target state)

The module must persist calendar state in-app (not only in provider APIs) using the following logical tables.

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `calendar_sources` | Connected calendars and internal source adapters | `id`, `kind` (`google`, `outlook`, `internal_scheduler`, `internal_reminders`), `owner_scope`, `status`, `last_synced_at`, `last_error` |
| `calendar_events` | Canonical event records (one row per series/master entity) | `id`, `view`, `source_id`, `source_external_id`, `title`, `description`, `timezone`, `all_day`, `rrule`, `cron`, `until_at`, `status`, `butler_name`, `origin_ref` (JSONB) |
| `calendar_event_instances` | Expanded occurrences for fast range queries | `id`, `event_id`, `instance_start_at`, `instance_end_at`, `instance_status`, `is_exception` |
| `calendar_sync_cursors` | Incremental sync and projection checkpoints per source | `source_id`, `cursor_type`, `cursor_value`, `updated_at` |
| `calendar_action_log` | Auditable record of UI/API mutations and reconciliation actions | `id`, `event_id`, `action`, `actor`, `request_id`, `payload`, `created_at` |

`origin_ref` contract:
- Provider event: `{"provider":"google","calendar_id":"...","event_id":"..."}`
- Scheduler event: `{"table":"scheduled_tasks","id":"<uuid>"}`
- Reminder event: `{"table":"reminders","id":"<uuid>","module":"relationship|health|..."}`.

### 5.11 Required changes to existing scheduler/reminder models

To support full calendar editing in `butler` view, the scheduler/reminder source models need additional fields:
- `scheduled_tasks`: add `timezone`, `start_at`, `end_at`, `until_at`, `display_title`, and `calendar_event_id` (nullable FK to `calendar_events.id`).
- Reminder-capable modules (for example relationship reminders): expose `timezone`, `next_trigger_at`, and optional `until_at` consistently for projection.
- All butler-origin schedules/reminders must store stable IDs and last-updated timestamps so the projector can perform deterministic upserts.

This allows flows like:
- "Health butler: remind me at 9:00 AM every day until 2026-03-01 to take medicines"
- UI writes one `calendar_events` row (`view="butler"`, `rrule=RRULE:FREQ=DAILY;UNTIL=20260301T140000Z`, `butler_name="health"`)
- Projector/materializer emits/updates underlying execution rows (`scheduled_tasks` or module reminder rows) while preserving linkage via `calendar_event_id`.

## 6. Timezone Contract

### 6.1 Storage and representation
- All event boundaries (`start_at`, `end_at`) MUST be timezone-aware `datetime` objects.
- Naive datetimes in tool input are resolved against the event's explicit `timezone` parameter, falling back to the butler's configured `timezone` in `butler.toml`.
- IANA timezone identifiers are the canonical format (e.g., `America/New_York`, `Europe/London`). UTC offsets alone are not accepted as timezone parameters.

### 6.2 All-day events
- All-day events use `date` objects (not `datetime`) for `start_at` and `end_at`.
- `end_at` for all-day events is exclusive (a single-day event has `end_at = start_at + 1 day`).
- All-day events do not carry timezone on their boundaries; the calendar's configured timezone governs display.

### 6.3 Timezone display
- Tools return event times in the event's stored timezone by default.
- A `display_timezone` parameter on read tools allows converting event times for display without modifying the stored timezone.
- Recurring events preserve the timezone of the original event; individual occurrences are expanded in that timezone (respecting DST transitions).

### 6.4 Cross-timezone scheduling
- When creating events with attendees in different timezones, the event's timezone is the organizer's timezone.
- Attendee-facing times are converted by the provider for each attendee's local timezone in their calendar view.
- Free/busy queries for conflict detection use UTC-normalized windows regardless of event timezone.

## 7. Conflict Detection and Resolution Contract

### 7.1 Conflict policies

| Policy | Behavior |
|--------|----------|
| `suggest` | Detect conflicts, reject the write, and return up to N alternative time slots. Default policy. |
| `fail` | Detect conflicts and reject the write with conflict details. No suggestions. |
| `allow_overlap` | Detect conflicts but proceed with the write. Optionally gates through the approval queue when `require_approval_for_overlap=true`. |

### 7.2 Conflict detection mechanism
- For Google Calendar v1: uses the Calendar freeBusy API to query busy windows for the target calendar.
- Conflict windows are compared against the candidate event's `[start_at, end_at)` interval.
- Self-conflicts (updating an event to a new time that overlaps its own original time) are excluded.

### 7.3 Suggested slot generation
- When policy is `suggest`, the module generates up to `suggestion_count` (default 3) alternative slots.
- Slots are placed after the last conflicting event's end time, with 15-minute gaps between suggestions.
- Suggested slots preserve the candidate event's duration.
- Suggestions are best-effort and do not re-check for conflicts (they may still overlap other events).

### 7.4 Approval-gated overlaps
- When `require_approval_for_overlap=true` (default) and the approvals module is co-loaded, overlap-override writes are enqueued as pending approval actions instead of executing immediately.
- The approval action includes conflict details and the full tool arguments for replay after approval.
- If the approvals module is not co-loaded, overlap overrides return `approval_unavailable` status with guidance.

## 8. Attendee and Invitation Contract

### 8.1 Reading attendee state
- `calendar_list_events` and `calendar_get_event` return full `AttendeeInfo` for each attendee on an event.
- Attendee response status reflects the latest state from the provider.
- The butler can query events filtered by attendee response status (e.g., "show events where someone declined").

### 8.2 Managing attendees (target state)
- `calendar_add_attendees`: add one or more attendees to an existing event. Requires `send_updates` policy.
- `calendar_remove_attendees`: remove attendees from an event. Removed attendees receive a cancellation if `send_updates` is `all` or `external_only`.
- `calendar_update_attendee`: change an attendee's optional flag or other metadata (cannot change their response status).

### 8.3 RSVP monitoring
- The polling sync (section 10) detects attendee response changes between sync cycles.
- When an attendee's response status changes, the module emits a structured change record for the butler to act on (e.g., log, notify the user, reschedule).
- Target state: `calendar_attendee_changes` tool returns recent RSVP state transitions for a given event or time window.

### 8.4 Invitation policies
- Butler-generated events default to `send_updates="none"` to prevent spam.
- The butler CLAUDE.md should define invitation policies appropriate to the butler's role (e.g., general butler may not send invitations; a scheduling butler may send them freely).
- Approval integration: adding external attendees can be gated through the approvals module as a high-impact output action.

## 9. Event Documentation Contract

### 9.1 Event description
- The `description` field is the primary user-visible documentation on an event.
- Butlers should use the description for context that attendees need: agenda, preparation notes, links, dial-in information.
- Description supports plain text (Google Calendar renders limited HTML but butlers should write plain text for portability).

### 9.2 Butler-private notes
- The `notes` field stores butler-internal context in provider extended properties (`extendedProperties.private`).
- Private notes are invisible to attendees and only accessible through the calendar API (not the calendar UI).
- Use cases: scheduling rationale, conflict resolution history, user instructions that led to event creation, linked entity/memory references.

### 9.3 Butler-generated event tagging
- Butler-created events carry:
  - Title prefix: `BUTLER:` (e.g., `BUTLER: Weekly sync`).
  - Private metadata: `butler_generated=true`, `butler_name=<name>`.
- These tags allow the module to identify and filter butler-managed events vs. human-created events.
- Update operations on butler-generated events preserve the title prefix and private metadata.

## 10. Calendar Sync Contract

### 10.1 Polling model (v1)
- Each butler with the calendar module runs a scheduled polling task (configured in `butler.toml` schedule).
- The poller calls the provider's incremental sync endpoint (Google Calendar `syncToken` / `nextSyncToken`).
- Changed events since the last sync are fetched and used to update local cache/projection tables and trigger butler actions.
- Poll interval is configurable; recommended default is 5 minutes for active butlers.

### 10.2 Sync state
- The module persists provider cursors/checkpoints in `calendar_sync_cursors` (and may mirror to state KV for backward compatibility).
- On first sync or token invalidation, a full sync is performed for the configured calendar(s).
- Sync tokens are calendar-scoped; each configured `calendar_id` has its own token.

### 10.3 Change detection
- The poller compares incoming events against the previous known state to detect:
  - New events (not previously seen).
  - Updated events (etag/updated_at changed).
  - Cancelled events (status changed to `cancelled`).
  - Attendee response changes (response_status deltas).
  - Time changes (start_at/end_at deltas).
- Detected changes are emitted as structured change records for butler processing.
- Detected changes also drive upsert/delete in `calendar_events` and `calendar_event_instances` for `/butlers/calendar` queries.

### 10.4 Push/subscription model (target state, v2+)
- Google Calendar supports push notifications via webhook channels.
- Target state: the module registers a webhook channel during startup and receives push notifications for calendar changes.
- Push notifications trigger immediate sync instead of waiting for the next poll interval.
- Webhook channels require a publicly-accessible HTTPS endpoint; deployment topology determines feasibility.
- Fallback: if push registration fails or is not configured, the module falls back to polling.

### 10.5 iCloud Calendar sync considerations
- iCloud Calendar uses CalDAV with `ctag`/`etag` change tracking.
- Polling model is similar: check `ctag` for calendar-level changes, then fetch changed events by `etag`.
- No push notification support from iCloud; polling is the only option.
- Sync interval may need to be longer to respect iCloud rate limits.

## 11. Recurrence Contract

### 11.1 Recurrence rule format
- Events can carry one or more RRULE strings conforming to RFC 5545.
- Rules must include a `FREQ` component and must not include `DTSTART`/`DTEND` (these are derived from the event boundaries).
- Common patterns: `RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR`, `RRULE:FREQ=MONTHLY;BYMONTHDAY=15`, `RRULE:FREQ=DAILY;COUNT=10`.

### 11.2 Recurrence expansion
- List operations with `singleEvents=true` (Google) return expanded individual occurrences, each with its own `event_id` (base ID + instance timestamp).
- The canonical `CalendarEvent` model represents individual occurrences, not the abstract series.

### 11.3 Recurrence modification scope
- v1: updates to recurring events apply to the entire series (`recurrence_scope="series"`).
- Target state: support `this_instance` (modify only one occurrence, creating an exception) and `this_and_following` (modify this and all future occurrences, splitting the series).
- Deletion scope follows the same model: delete the series, this instance, or this and following.

### 11.4 Timezone and DST in recurrence
- Recurring events store their timezone and expand occurrences in that timezone.
- DST transitions are handled by the provider: a weekly meeting at 10:00 AM Eastern stays at 10:00 AM Eastern regardless of DST changes (the UTC offset shifts).
- When updating a recurring event's timezone, all future occurrences shift to the new timezone.

## 12. Module Configuration Contract

Module config is declared under `[modules.calendar]` in each hosting butler's `butler.toml`.

### 12.1 Required settings
- `provider` (str): calendar provider identifier. v1 supports `"google"`. Future: `"icloud"`, `"caldav"`, `"outlook"`.
- `calendar_id` (str): default calendar identifier for read/write operations. For Google, this is the calendar email address.

### 12.2 Optional settings
- `timezone` (str, default `"UTC"`): default IANA timezone for the butler's calendar operations.
- `read_calendars` (list[str], default `[]`): additional calendar IDs to include in read queries (for cross-calendar visibility without write access).
- `workspace_enabled` (bool, default `true`): whether this butler contributes to `/butlers/calendar` projection data.
- `workspace_source_priority` (int, default `100`): merge priority when two sources produce overlapping canonical entries.

### 12.3 Conflict settings (`[modules.calendar.conflicts]`)
- `policy` (CalendarConflictPolicy, default `"suggest"`): default conflict policy for create/update operations.
- `require_approval_for_overlap` (bool, default `true`): whether overlap overrides require approval when the approvals module is enabled.
- `suggestion_count` (int, default `3`): number of alternative time slots to suggest when policy is `"suggest"`.

### 12.4 Event defaults (`[modules.calendar.event_defaults]`)
- `enabled` (bool, default `true`): whether default reminders are added to new events.
- `minutes_before` (int, default `15`): default reminder time in minutes.
- `color_id` (str | None, default `None`): default color for butler-generated events.
- `send_updates` (SendUpdatesPolicy, default `"none"`): default notification policy for event writes.
- `visibility` (EventVisibility, default `"default"`): default visibility for new events.

### 12.5 Sync settings (`[modules.calendar.sync]`)
- `enabled` (bool, default `false`): whether polling sync is active.
- `interval_minutes` (int, default `5`): polling interval.
- `full_sync_window_days` (int, default `30`): time window for full sync on first run or token invalidation.

### 12.6 Workspace settings (`[modules.calendar.workspace]`, target state)
- `default_view` (Literal["user", "butler"], default `"user"`): initial mode when opening `/butlers/calendar`.
- `enable_butler_lanes` (bool, default `true`): show per-butler lane split in butler view.
- `max_range_days` (int, default `90`): maximum range window accepted by workspace APIs.
- `projection_refresh_seconds` (int, default `60`): target maximum staleness for background projection refresh.

### 12.7 Example configuration

```toml
[modules.calendar]
provider = "google"
calendar_id = "butler-general@group.calendar.google.com"
timezone = "America/New_York"
read_calendars = ["user@gmail.com"]
workspace_enabled = true
workspace_source_priority = 100

[modules.calendar.conflicts]
policy = "suggest"
require_approval_for_overlap = true
suggestion_count = 3

[modules.calendar.event_defaults]
enabled = true
minutes_before = 15
color_id = "9"
send_updates = "none"
visibility = "default"

[modules.calendar.sync]
enabled = true
interval_minutes = 5
full_sync_window_days = 30

[modules.calendar.workspace]
default_view = "user"
enable_butler_lanes = true
max_range_days = 90
projection_refresh_seconds = 60
```

## 13. MCP Tool Surface Contract

Calendar tools are registered on each hosting butler MCP server when the module is enabled.

### 13.1 Read tools
- `calendar_list_events(calendar_id?, start_at?, end_at?, limit?, display_timezone?, attendee_filter?)`: List events in a time window with optional timezone conversion and attendee filtering.
- `calendar_get_event(event_id, calendar_id?, display_timezone?)`: Fetch a single event by ID with full attendee and recurrence detail.
- `calendar_search_events(query, calendar_id?, start_at?, end_at?, limit?)`: Full-text search across event titles and descriptions.
- `calendar_check_availability(start_at, end_at, calendar_ids?)`: Check free/busy status across one or more calendars.

### 13.2 Write tools
- `calendar_create_event(title, start_at, end_at, timezone?, description?, location?, attendees?, recurrence_rule?, notification?, color_id?, calendar_id?, conflict_policy?, status?, visibility?, notes?, send_updates?)`: Create a new event with conflict detection and butler tagging.
- `calendar_update_event(event_id, title?, start_at?, end_at?, timezone?, description?, location?, attendees?, recurrence_rule?, recurrence_scope?, color_id?, calendar_id?, conflict_policy?, status?, visibility?, notes?, send_updates?)`: Patch an existing event with conflict detection for time changes.
- `calendar_delete_event(event_id, calendar_id?, recurrence_scope?, send_updates?)`: Delete or cancel an event (or occurrence/series for recurring events).

### 13.3 Attendee management tools (target state)
- `calendar_add_attendees(event_id, attendees, optional?, calendar_id?, send_updates?)`: Add attendees to an event.
- `calendar_remove_attendees(event_id, attendees, calendar_id?, send_updates?)`: Remove attendees from an event.
- `calendar_attendee_changes(event_id?, start_at?, end_at?, calendar_id?)`: Query recent attendee RSVP state changes.

### 13.4 Scheduling tools (target state)
- `calendar_find_free_slots(duration, start_at, end_at, calendar_ids?, timezone?, constraints?)`: Find available time slots of a given duration within a window, optionally constrained by working hours or day-of-week preferences.
- `calendar_propose_meeting(title, duration, attendee_calendars, start_at, end_at, timezone?, constraints?)`: Find mutually available times across multiple calendars and propose a meeting.

### 13.5 Sync tools
- `calendar_sync_status()`: Return sync state (last sync time, sync token validity, pending changes count).
- `calendar_force_sync(calendar_id?)`: Trigger an immediate sync outside the normal polling schedule.

### 13.6 Butler-view scheduling tools (target state)
- `calendar_list_butler_events(start_at, end_at, butlers?, include_disabled?)`: List butler schedule/reminder entries in unified form for lane rendering.
- `calendar_create_butler_event(butler_name, title, start_at, end_at?, timezone?, recurrence_rule?, cron?, until_at?, action, action_args?, source_hint?)`: Create a butler-owned scheduled event/reminder from calendar UI.
- `calendar_update_butler_event(event_id, title?, start_at?, end_at?, timezone?, recurrence_rule?, cron?, until_at?, enabled?)`: Update an existing butler-owned event.
- `calendar_delete_butler_event(event_id, scope?)`: Cancel/delete a butler-owned one-off or recurring series.
- `calendar_toggle_butler_event(event_id, enabled)`: Pause/resume recurring butler events.

### 13.7 Workspace query tools (target state)
- `calendar_workspace_query(view, start_at, end_at, timezone?, butlers?, calendars?, include_metadata?)`: Backing query for `/butlers/calendar` month/week/day/list surfaces.
- `calendar_workspace_mutate(action, payload)`: Thin action gateway for UI interactions that need routing to provider, scheduler, or reminder adapters.

### 13.8 Tool identity and I/O model
Calendar tools operate on the butler's configured calendar credentials. Following the I/O model contract:
- Calendar tools that only read data are inputs with `approval_default="none"`.
- Calendar tools that create, update, or delete events are outputs with `approval_default="conditional"`.
- Overlap overrides are additionally gated through the approvals module when configured (see section 7.4).
- v1 does not distinguish user-identity vs. bot-identity for calendar operations (the butler acts through a single set of OAuth credentials). Target state: support `user_calendar_*` and `bot_calendar_*` tool prefixes when user-delegated calendar access is available.
- Butler-view mutations target explicit `butler_name` ownership and translate to `scheduled_tasks` and/or module reminder records.

### 13.9 Lineage propagation
- All tools accept optional `request_context` metadata.
- If `request_context.request_id` is present, the value is preserved in audit/event surfaces for trace correlation.

## 14. Provider Adapter Contract

### 14.1 CalendarProvider interface

Every provider adapter must implement the `CalendarProvider` abstract base class:

- `name` (property): provider identifier string.
- `list_events(calendar_id, start_at?, end_at?, limit?)`: return canonical `CalendarEvent` list.
- `get_event(calendar_id, event_id)`: return a single `CalendarEvent` or `None`.
- `create_event(calendar_id, payload)`: create and return the canonical event.
- `update_event(calendar_id, event_id, patch)`: update and return the canonical event.
- `delete_event(calendar_id, event_id)`: delete or cancel the event.
- `find_conflicts(calendar_id, candidate)`: return overlapping events for a candidate.
- `shutdown()`: release provider resources (HTTP clients, connections).

### 14.2 Google Calendar adapter (v1)
- Authentication: OAuth 2.0 refresh-token flow using DB-first credential resolution.
  Credentials are resolved at startup via `resolve_google_credentials(pool)`:
  1. DB lookup (from `butler_secrets` using keys `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`).
  2. No runtime env-var fallback; runtime resolution is DB-backed only.
  Legacy `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON` is not used by runtime credential resolution.
- API base: `https://www.googleapis.com/calendar/v3`.
- Event mapping: Google event payloads are translated to/from canonical `CalendarEvent` shapes, including timezone resolution, attendee extraction, recurrence rule parsing, and extended property mapping.
- Conflict detection: uses Google freeBusy API for efficient busy-window queries.
- Rate limiting: respects Google Calendar API quotas (default 1M queries/day, 500 queries/100s per user). Implements exponential backoff on 429/503.

### 14.3 iCloud Calendar adapter (target state)
- Authentication: Apple-specific auth (app-specific password or Sign in with Apple token).
- Protocol: CalDAV over HTTPS.
- Event mapping: iCalendar (RFC 5545) VEVENT components translated to/from canonical shapes.
- Conflict detection: client-side overlap check against fetched events (no native free/busy API equivalent to Google).
- Limitations: no push notifications, limited API documentation, stricter rate limits.

### 14.4 Provider registration
- Providers are registered in `CalendarModule._PROVIDER_CLASSES` keyed by provider name.
- New providers are added by implementing `CalendarProvider` and registering the class.
- Provider selection is determined by the `provider` config value at module startup.

## 15. Error Handling Contract

### 15.1 Error hierarchy
- `CalendarAuthError`: base class for authentication/request failures.
  - `CalendarCredentialError`: missing or invalid credentials.
  - `CalendarTokenRefreshError`: OAuth token refresh failure.
  - `CalendarRequestError`: provider API request failure (carries `status_code` and `message`).

### 15.2 Error response format
All tool errors return structured dictionaries:
```python
{
    "status": "error",
    "error": "human-readable error description",
    "error_type": "CalendarRequestError",
    "provider": "google",
    "calendar_id": "...",
}
```

### 15.3 Sensitive data in errors
- Error messages from providers are sanitized: truncated to 200 chars, whitespace-normalized.
- Credential values, access tokens, and refresh tokens are never included in error messages or logs.
- Provider error payloads may contain user data; these are passed through only in the `message` field after sanitization.

## 16. Calendar Workspace UX Contract (`/butlers/calendar`)

### 16.1 Route and visual model
- Canonical route: `/butlers/calendar` (for example `https://tzeusy.parrot-hen.ts.net/butlers/calendar`).
- UX target: Google Calendar-like interaction model (month/week/day/list switcher, drag-to-create, drag-to-reschedule, side panel details, quick-add).
- The page must feel like one calendar workspace with a primary mode switch, not two disconnected screens.

### 16.2 Required view toggle
- A top-level segmented control toggles between:
  - `user` view: user upcoming events across connected user calendars.
  - `butler` view: butler-managed schedules/reminders, split by butler lane.
- The selected view is persisted in URL query state (`?view=user|butler`) and restored on reload/share.

### 16.3 `user` view behavior
- Show upcoming user events in calendar grid/list with provider metadata (calendar/source labels, organizer, attendee state).
- Provide inline actions:
  - Create event on a selected connected calendar.
  - Edit/move/delete user events (subject to provider permissions).
  - Trigger sync now for one source or all connected sources.
  - Filter by connected source/calendar.
- Writes route to provider-backed tools (`calendar_create_event`, `calendar_update_event`, `calendar_delete_event`) and then refresh projection.

### 16.4 `butler` view behavior
- Show events as per-butler lanes (for example `general`, `relationship`, `health`, `switchboard`) in week/day views and grouped-by-butler in list/month views.
- Include all scheduled butler items:
  - Cron/job schedules (`scheduled_tasks` projections).
  - One-off reminders.
  - Recurring reminders (including RRULE- or cron-backed reminders with end boundaries).
- Provide inline actions:
  - Create a new butler reminder/schedule from cell selection or quick-add.
  - Edit recurrence/timezone/start/end/until boundaries.
  - Pause/resume recurring schedules.
  - Delete one-off or entire recurring series.
- Required example flow: user creates "Health: remind me at 9:00 AM every day until 2026-03-01 to take medicines"; UI must persist a recurring butler event with explicit `until_at`, bind it to `health`, and project it into executable scheduler/reminder rows.

### 16.5 Interaction and safety rules
- Drag-reschedule must preserve duration and timezone unless explicitly edited.
- Recurrence edits require explicit scope selection (`this_instance`, `this_and_following`, `series`) when applicable.
- Mutations emit optimistic UI updates only when local validation passes; hard failures roll back and surface actionable error details.
- Butler-view writes use existing approval policies where actions are high impact.

## 17. Calendar API Surface for the Workspace (target state)

Required backend endpoints powering `/butlers/calendar`:
- `GET /api/calendar/workspace?view=...&start=...&end=...&timezone=...&butlers=...&sources=...`
  - Returns normalized `UnifiedCalendarEntry[]` and per-source freshness.
- `POST /api/calendar/workspace/sync`
  - Triggers projection refresh and optional provider pull (`source_id` or `all=true`).
- `POST /api/calendar/workspace/user-events`
  - Create/update/delete user-view provider events.
- `POST /api/calendar/workspace/butler-events`
  - Create/update/delete/pause/resume butler-view events.
- `GET /api/calendar/workspace/meta`
  - Returns view capabilities, connected sources, writable calendars, butler lanes, and default timezone.

API behavior contract:
- Query path is range-based and returns fully normalized entries (no frontend source-specific joins required).
- Writes are source-routed and idempotent by `request_id`.
- Every mutation response includes refreshed projection metadata (`projection_version`, `staleness_ms`).

Current implementation details (v1 mutation surface):
- `POST /api/calendar/workspace/user-events` accepts `{butler_name, action, request_id?, payload}` where `action` is `create|update|delete` and routes to provider-backed MCP tools (`calendar_create_event`, `calendar_update_event`, `calendar_delete_event`).
- `POST /api/calendar/workspace/butler-events` accepts `{butler_name, action, request_id?, payload}` where `action` is `create|update|delete|toggle` and routes to butler-event MCP tools (`calendar_create_butler_event`, `calendar_update_butler_event`, `calendar_delete_butler_event`, `calendar_toggle_butler_event`).
- Mutation responses include both the underlying tool result and projection freshness metadata (`projection_version`, `staleness_ms`, `projection_freshness`) for workspace refresh logic.
- Recurrence/edit scope behavior: provider recurring update/delete requests currently support `recurrence_scope="series"` only in v1; `this_instance` and `this_and_following` remain target-state and should be treated as unsupported in workspace payloads.
- Idempotency/audit behavior: when a mutation includes `request_id`, the same value is reused as the action-log correlation key for replay protection (`calendar_action_log.idempotency_key`) and operator traceability.
- Operational sync/telemetry behavior: `POST /api/calendar/workspace/sync` returns per-target status/detail/error outcomes, while read/mutation responses expose source/projection freshness (`sync_state`, `staleness_ms`, `projection_freshness`) as the canonical dashboard staleness signal.

## 18. Non-Goals

- Full replacement of native provider applications (settings/ACL UX, rooms/resources, conference lifecycle, offline merge semantics).
- Implementing a standalone scheduling/booking platform.
- Managing calendar ACLs, sharing settings, or calendar creation/deletion.
- Generating video conferencing links (Google Meet, Zoom, Teams).
- Sending calendar invitations outside of the provider's native invitation system.
- Joint multi-butler execution semantics (shared rendering is in scope; execution ownership remains per butler).
- Real-time collaborative event editing with CRDT-style merge.
