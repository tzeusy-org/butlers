## MODIFIED Requirements

### Requirement: Calendar Event CRUD Tools

The module SHALL register 20 MCP tools before conditional invitation-response and historical-fence tools are added under their own requirements. The core CRUD tools are: `calendar_list_events`, `calendar_get_event`, `calendar_create_event`, `calendar_update_event`, `calendar_delete_event`, the occurrence-targeted `calendar_update_event_instance`, `calendar_delete_event_instance`, and the read/utility tools `calendar_find_free_slots` and `calendar_list_calendars`. The remaining tools are enumerated by the Butler Event Management Tools (4: `calendar_create_butler_event`, `calendar_update_butler_event`, `calendar_delete_butler_event`, `calendar_toggle_butler_event`), Reminder Tools (3: `reminder_create`, `reminder_list`, `reminder_dismiss`), and Calendar Sync Tools (3: `calendar_sync_status`, `calendar_force_sync`, `calendar_set_primary`) requirements below, plus the `calendar_propose_event` producer (behavior specified by the `calendar-event-proposals` capability). Butler-authored events SHALL default to the dedicated "Butlers" calendar when no explicit `calendar_id` is given; the user's own events SHALL be edited in place on whichever calendar they live on, resolved by event id. A caller MAY pass an explicit `calendar_id` to target a specific calendar; the available `calendar_id` values SHALL be enumerated by the read-only `calendar_list_calendars` source-listing tool (see the Calendar Source Listing Tool requirement).

The retired `attendees` group SHALL register no tools, including when explicitly requested. The standalone `calendar_add_attendees` and `calendar_remove_attendees` tools SHALL NOT be registered. Event attendee fields, attendee arguments on retained create/update tools and approval-bound self invitation responses retain their separate contracts.

ID: REQ-module-calendar-031
Source: accepted bu-qozovd.1 slice 1 design and unchanged AC5
Scope: v1-mandatory

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

## REMOVED Requirements

### Requirement: Attendee Management Tools

**Reason**: Accepted bu-qozovd.1 slice 1 and AC5 retire the attendees MCP group and both standalone tools.

**Migration**: No replacement standalone attendee tool is introduced. Retained event create/update attendee fields and approval-bound invitation responses keep their existing contracts.
