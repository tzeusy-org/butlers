## MODIFIED Requirements

### Requirement: Calendar Event CRUD Tools

The module registers 20 MCP tools total. The core CRUD tools are: `calendar_list_events`, `calendar_get_event`, `calendar_create_event`, `calendar_update_event`, `calendar_delete_event`, and the occurrence-targeted `calendar_update_event_instance`, `calendar_delete_event_instance`. The full count additionally includes the Butler Event Management (4), Attendee Management (2), Reminder (3), and Calendar Sync (3) tools plus the `calendar_propose_event` producer (behavior specified by the `calendar-event-proposals` capability). `calendar_update_event` and `calendar_delete_event` SHALL accept a `recurrence_scope` of `this`, `following`, or `series` (default `series`); `this` and `following` operate on a single occurrence or an occurrence-and-onward split of a recurring event rather than the whole series.

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

#### Scenario: Eager projection write-through on provider mutations

- **WHEN** a provider mutation succeeds (`calendar_create_event`, `calendar_update_event`, or `calendar_delete_event`)
- **THEN** the event is eagerly projected into the projection tables via `_project_provider_mutation` before the sync round-trip
- **AND** a background `_refresh_user_projection` sync still runs for reconciliation and freshness metadata
- **AND** failures in eager projection are logged but do not block the mutation response (fail-open)
- **BECAUSE** Google's incremental sync API has indexing latency (1-5s) after writes, and relying on a sync round-trip to project the mutation creates a race condition where the event may never reach the projection tables

#### Scenario: Update event with partial patch

- **WHEN** `calendar_update_event` is called with an event_id and partial fields and no `recurrence_scope` (defaulting to `series`)
- **THEN** only non-None fields are sent to the provider's PATCH endpoint
- **AND** timezone changes re-emit start/end boundaries with the new timezone
- **AND** the whole recurring series is updated

#### Scenario: Update a single occurrence of a recurring event

- **WHEN** `calendar_update_event` is called with `recurrence_scope="this"` (or `calendar_update_event_instance` is called) for a base recurring `event_id` and an `instance_start_at`
- **THEN** only the named occurrence reflects the edited fields; the rest of the series is unchanged
- **AND** the occurrence is detached as an exception (its original slot EXDATE-d from the series recurrence) and the matching `calendar_event_instances` row is marked `is_exception = true`

#### Scenario: Update this-and-following occurrences of a recurring event

- **WHEN** `calendar_update_event` is called with `recurrence_scope="following"` for a base recurring `event_id` and an `instance_start_at`
- **THEN** the original series RRULE is bounded with an `UNTIL` just before `instance_start_at`, and the named occurrence and every later occurrence reflect the edited fields
- **AND** occurrences before the boundary are unchanged

#### Scenario: Delete the whole recurring series

- **WHEN** `calendar_delete_event` is called with an event_id and `recurrence_scope="series"` (the default)
- **THEN** the entire recurring series is deleted from the provider calendar

#### Scenario: Delete a single occurrence of a recurring event

- **WHEN** `calendar_delete_event` is called with `recurrence_scope="this"` (or `calendar_delete_event_instance` is called) for a base recurring `event_id` and an `instance_start_at`
- **THEN** a timezone-correct `EXDATE` for that occurrence is appended to the series recurrence array, removing only that occurrence
- **AND** the rest of the series remains intact and the matching `calendar_event_instances` row is marked `is_exception = true`
- **AND** a non-existent occurrence surfaces a not-found response fail-open rather than raising

#### Scenario: Delete this-and-following occurrences of a recurring event

- **WHEN** `calendar_delete_event` is called with `recurrence_scope="following"` for a base recurring `event_id` and an `instance_start_at`
- **THEN** the original series RRULE is bounded with an `UNTIL` just before `instance_start_at`, removing the named occurrence and every later one
- **AND** occurrences before the boundary remain intact

### Requirement: Butler Event Management Tools

The module registers MCP tools for managing butler-owned workspace events (scheduled tasks and reminders projected as calendar entries): `calendar_create_butler_event`, `calendar_update_butler_event`, `calendar_delete_butler_event`, `calendar_toggle_butler_event`. Deletion of a butler workspace event SHALL be scope-aware (`this`, `following`, or the default `series`) rather than series-only.

#### Scenario: Create butler event

- **WHEN** `calendar_create_butler_event` is called with title, timing, and source type (reminder or scheduled task)
- **THEN** a butler-managed event is created with recurrence support (RRULE or cron)
- **AND** the event is tagged with butler metadata for unified calendar projection

#### Scenario: Update butler event

- **WHEN** `calendar_update_butler_event` is called with an event ID and partial fields
- **THEN** only the provided fields are updated (timing, recurrence, enabled status)

#### Scenario: Delete butler event

- **WHEN** `calendar_delete_butler_event` is called with an event ID
- **THEN** the butler event is deleted with scope-aware semantics (`this`, `following`, or the default `series`)
- **AND** high-impact mutations require approval gate

#### Scenario: Toggle butler event

- **WHEN** `calendar_toggle_butler_event` is called with an event ID and enabled flag
- **THEN** the butler event is paused or resumed without deletion
- **AND** high-impact mutations require approval gate

## ADDED Requirements

### Requirement: Recurrence-Scoped Occurrence Mutation

When a mutation targets a recurring event, the module SHALL support operating on a single occurrence (`this`) or the occurrence-and-onward remainder (`following`) in addition to the whole series (`series`), via the `recurrence_scope` argument on `calendar_update_event` / `calendar_delete_event` and the dedicated `calendar_update_event_instance` / `calendar_delete_event_instance` tools. Occurrence-scoped mutations SHALL persist provider EXDATE/RDATE recurrence entries and mark the affected `calendar_event_instances` rows `is_exception = true`.

#### Scenario: Single occurrence detached as an exception

- **WHEN** an occurrence-scoped mutation (`recurrence_scope="this"` or `calendar_*_event_instance`) is applied to a base recurring `event_id` and an `instance_start_at`
- **THEN** the original occurrence slot is EXDATE-d from the series recurrence array on the provider
- **AND** the matching `calendar_event_instances` row (keyed by `event_id` + occurrence start) is marked `is_exception = true`
- **AND** occurrences other than the named one are unaffected

#### Scenario: This-and-following split at the boundary

- **WHEN** a mutation is applied with `recurrence_scope="following"` for a base recurring `event_id` and an `instance_start_at`
- **THEN** the original series RRULE is bounded with an `UNTIL` just before `instance_start_at`
- **AND** the mutation applies to the named occurrence and every later occurrence
- **AND** occurrences before the boundary remain unchanged

#### Scenario: Impact preview reports occurrences touched

- **WHEN** a recurrence-scoped mutation is evaluated before the provider write
- **THEN** an impact preview reports the number of occurrences the mutation will touch: 1 for `this`, the remaining-from-boundary count for `following`, and the whole-series occurrence count for `series`
- **AND** the count feeds the high-impact approval gate so large `series` / `following` mutations are gated while a single-occurrence `this` mutation is not

#### Scenario: Unknown recurrence scope rejected

- **WHEN** `calendar_update_event` or `calendar_delete_event` is called with a `recurrence_scope` other than `this`, `following`, or `series`
- **THEN** the call is rejected with a validation error and no provider mutation is issued
