## ADDED Requirements

### Requirement: Explicit Calendar Response Group and Startup Isolation

The Calendar module SHALL expose invitation responses through an explicit
`response` group, separately from `core` and `butler_events`. Omitted or empty
`groups` SHALL retain the existing all-groups behavior. `response_enabled`
SHALL remain an independent opt-in switch; selecting a group does not authorize
an effect or waive any account, source, approval or historical-fence condition.

ID: REQ-module-calendar-032
Source: accepted bu-qozovd.1 slice 2 and unchanged original AC3; module-base tool group contract; REQ-module-calendar-029; REQ-dashboard-api-069
Scope: v1-mandatory

#### Scenario: Response-only registration

- **WHEN** responses are enabled and the explicit groups select only `response`
- **THEN** `calendar_respond` SHALL be registered with its existing public schema and default values, private command bindings excluded
- **AND** core and butler-event tools SHALL NOT be registered
- **AND** the canonical preparer and owning approval gate SHALL retain the actual registered handler captured by the shared executor
- **AND** the descriptor group SHALL be `response` with all other authority and sensitivity declarations preserved

#### Scenario: Explicit groups omit response

- **WHEN** `response_enabled` is true and a nonempty explicit group list omits `response`
- **THEN** response registration SHALL fail with a descriptive configuration error
- **AND** an omitted or empty group list SHALL continue selecting all groups

#### Scenario: Response-only cold startup

- **WHEN** `response` is selected with neither `core` nor `butler_events`
- **THEN** startup SHALL resolve canonical DB-backed credentials for the configured or primary Google account and construct the lazy provider
- **AND** startup SHALL NOT discover or create calendars, restore or persist calendar targets, perform source-ledger hygiene, recover or drain force-sync commands, or start sync, queue or internal-projection workers
- **AND** no provider request or credential-health update SHALL be caused by provider construction
- **AND** response admission SHALL continue requiring the exact existing eligible projected source and verified account, without discovery or backfill as a fallback
- **AND** successful approved response effects SHALL retain their existing projection updates

#### Scenario: Actual roster configuration preserves both historical owners

- **WHEN** Messenger and Relationship load their Calendar configuration
- **THEN** Messenger SHALL select `response` only, and Relationship SHALL select `core` and `response`
- **AND** both SHALL retain their enabled owning response approval gate and exact command dispatcher
- **AND** both SHALL retain the direct non-LLM-presentable `calendar_response_fence_status` read independently of selected groups and response execution enablement
- **AND** neither registration change SHALL select a new owner or retire historical evidence, physical fencing or route-dependent probing

#### Scenario: Normal Calendar startup remains operational

- **WHEN** omitted or empty groups, or explicit `core` or `butler_events`, select normal startup
- **THEN** existing discovery, calendar role resolution, hygiene, sync and internal projection behavior SHALL be preserved
- **AND** force-sync queue recovery and processing SHALL remain available independently of periodic sync enablement

## MODIFIED Requirements

### Requirement: Provider-Agnostic Architecture

The module defines an abstract `CalendarProvider` interface with concrete implementations per provider. Currently only Google Calendar is implemented. The module SHALL track the dedicated "Butlers" calendar id and the user's primary calendar id as distinct roles, and SHALL NOT overwrite the Butlers calendar id when the user changes the default target.

ID: REQ-module-calendar-033
Source: accepted bu-qozovd.1 slice 2; canonical credential and account isolation contracts
Scope: v1-mandatory

#### Scenario: Provider selection at startup with account

- **WHEN** the Calendar module starts up with `provider = "google"` and `account = "work@gmail.com"` in config
- **THEN** a Google provider instance is created with OAuth credentials resolved from the credential store for the specified Google account
- **AND** for normal startup with `core` or `butler_events` selected, the butler calendar ID is resolved from credential store or auto-discovered via shared "Butlers" calendar on that account
- **AND** response-only startup retains the same account-bound credentials but skips calendar discovery and creation

#### Scenario: Provider selection at startup without account (primary)

- **WHEN** the Calendar module starts up with `provider = "google"` and no `account` field in config
- **THEN** credentials are resolved for the primary Google account
- **AND** for normal startup with `core` or `butler_events` selected, behavior is identical to pre-multi-account single-account deployments
- **AND** response-only startup resolves the same primary account without starting normal Calendar discovery or workers

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

With omitted or empty tool groups, the module SHALL register 20 MCP tools before conditional invitation-response and historical-fence tools are added under their own requirements. An explicit group list SHALL select only its named groups: the 16 core tools and 4 butler_events tools retain their existing contracts, while invitation responses belong to the response group. Historical-fence reads retain their independent registration contract. The core CRUD tools are: `calendar_list_events`, `calendar_get_event`, `calendar_create_event`, `calendar_update_event`, `calendar_delete_event`, the occurrence-targeted `calendar_update_event_instance`, `calendar_delete_event_instance`, and the read/utility tools `calendar_find_free_slots` and `calendar_list_calendars`. The remaining tools are enumerated by the Butler Event Management Tools (4: `calendar_create_butler_event`, `calendar_update_butler_event`, `calendar_delete_butler_event`, `calendar_toggle_butler_event`), Reminder Tools (3: `reminder_create`, `reminder_list`, `reminder_dismiss`), and Calendar Sync Tools (3: `calendar_sync_status`, `calendar_force_sync`, `calendar_set_primary`) requirements below, plus the `calendar_propose_event` producer (behavior specified by the `calendar-event-proposals` capability). Butler-authored events SHALL default to the dedicated "Butlers" calendar when no explicit `calendar_id` is given; the user's own events SHALL be edited in place on whichever calendar they live on, resolved by event id. A caller MAY pass an explicit `calendar_id` to target a specific calendar; the available `calendar_id` values SHALL be enumerated by the read-only `calendar_list_calendars` source-listing tool (see the Calendar Source Listing Tool requirement).

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
