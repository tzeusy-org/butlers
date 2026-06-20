## MODIFIED Requirements

### Requirement: Calendar Event CRUD Tools

The module SHALL register 17 MCP tools total. The core CRUD tools are: `calendar_list_events`, `calendar_get_event`, `calendar_create_event`, `calendar_update_event`, `calendar_delete_event`.

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

- **WHEN** `calendar_update_event` is called with an event_id and partial fields
- **THEN** only non-None fields are sent to the provider's PATCH endpoint
- **AND** timezone changes re-emit start/end boundaries with the new timezone

#### Scenario: Delete event

- **WHEN** `calendar_delete_event` is called with an event_id
- **THEN** the event is deleted from the provider calendar

#### Scenario: Find free slots tool is registered

- **WHEN** the module registers its MCP tools
- **THEN** `calendar_find_free_slots` is registered alongside the existing tools, bringing the total to 17
- **AND** it is the only tool that reads availability without mutating any event

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

## ADDED Requirements

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
