# Calendar Module Plan

## Goal

Add `modules/calendar/` functionality so any butler with the calendar module enabled can read/write a configured user's Google Calendar, with safe conflict handling and explicit AI-authored event labeling.

Core requirement: every AI-generated event title must start with `BUTLER:`.
Access model: all butlers with `[modules.calendar]` enabled are allowed to use calendar tools.

---

## Desired End-to-End Flow

1. User sends a message to Switchboard, or email comes in.
2. Switchboard classifies/routes to a specialist butler (for example: `relationship`, `health`, or `general`).
3. Target butler's Claude runtime decides that the message implies creating/updating a calendar event.
4. Butler calls the calendar module tool (`calendar_create_event` or `calendar_update_event`).
5. Tool checks conflict status, enforces `BUTLER:` prefix, and applies write policy.
6. If the user asks to force an overlap, conflict override is approval-gated before write.
7. Tool returns outcome (created/updated/conflict/error) to the butler.
8. Butler responds to the user and asks for clarification/confirmation if conflict policy requires it.

---

## API Research Summary (Google Calendar)

Primary API: Google Calendar API v3.

Endpoints needed for v1:
- `events.list` (read events)
- `events.get` (fetch specific event for update safety/etag)
- `events.insert` (create event)
- `events.patch` (partial update)
- `freeBusy.query` (detect conflicts)

Auth model for this project:
- User OAuth with offline access (refresh token), tied to the specific Google account/calendar owner.
- Service account support can be deferred unless Google Workspace domain-wide delegation is needed.

Scopes:
- Read/write scope for v1: `https://www.googleapis.com/auth/calendar.events`
- Optional read-only mode future: `https://www.googleapis.com/auth/calendar.readonly`

Provider decision for v1:
- Google Calendar only.

Useful docs:
- https://developers.google.com/workspace/calendar/api/guides/overview
- https://developers.google.com/workspace/calendar/api/auth
- https://developers.google.com/workspace/calendar/api/v3/reference/events
- https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query

---

## Module Design

## Design Constraint: Provider-Agnostic Calendar Layer

Calendar functionality must be API-agnostic at the module boundary.

- Tool contracts (`calendar_create_event`, `calendar_update_event`, etc.) must be provider-neutral.
- Internal implementation should use a provider adapter interface (for example: `CalendarProvider`) so additional backends can be added without changing MCP tool signatures.
- Provider-specific field mapping (Google `colorId`, reminders format, recurrence quirks, etc.) must stay inside the provider adapter.

v1 implementation scope:
- Only one concrete adapter is implemented: Google Calendar.

## New Module

Create `src/butlers/modules/calendar.py` with:
- `CalendarConfig` (Pydantic config schema)
- `CalendarModule(Module)` implementation
- Google API client helpers (token refresh + request wrappers)
- conflict detection helpers
- event title normalization helper (`BUTLER:` prefix)

## Proposed Module Config

```toml
[modules.calendar]
provider = "google"
calendar_id = "<shared_butler_calendar_id>"
timezone = "America/Los_Angeles"
credentials_env = ["BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON"]
default_conflict_policy = "suggest" # suggest | fail | allow_overlap
default_event_color_id = "5"
default_notifications = "popup:10,email:30" # optional convenience config
```

## Calendar Topology (Decision)

Use a single shared Google Calendar for all Butler-managed events across all calendar-enabled butlers.

- Default behavior: all calendar tools write to the shared calendar configured by `modules.calendar.calendar_id`.
- Per-butler attribution: events retain `butler_name` and `butler_generated` tags in `extendedProperties.private` to identify which butler created them.
- Rationale:
  - reduces operational overhead (one calendar instead of three),
  - simplifies setup for new calendar-enabled butlers,
  - per-butler attribution is preserved through event metadata,
  - clean separation from personal/manual events,
  - easier filtering, export, and bulk cleanup.

Recommended naming:
- Calendar name: `Butler`
- Calendar ID: copied from Google Calendar settings and used as `modules.calendar.calendar_id` (e.g., `butler@group.calendar.google.com`).

## Required Environment Variable

`BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON` (required)

Expected JSON payload (single env var by design):
- `client_id`
- `client_secret`
- `refresh_token`

Example:

```json
{
  "client_id": "...apps.googleusercontent.com",
  "client_secret": "...",
  "refresh_token": "1//..."
}
```

---

## Tool Surface (v1)

Minimum MCP tools:
- `calendar_list_events(start, end, calendar_id?)`
- `calendar_create_event(summary, start, end, timezone?, all_day?, description?, location?, notification?, color_id?, recurrence?, conflict_policy?)`
- `calendar_update_event(event_id, summary?, start?, end?, timezone?, all_day?, description?, location?, notification?, color_id?, recurrence?, conflict_policy?)`
- `calendar_get_event(event_id, calendar_id?)` (optional but recommended for debugging/manual workflows)

Return structure should be explicit and machine-usable:
- `status`: `created|updated|conflict|error`
- `event_id` when successful
- `html_link` for user-visible calendar link
- `conflicts` list (if any)
- `suggested_slots` list (if conflict policy is `suggest`)

Event option support in v1:
- `date` / all-day events and `dateTime` / timed events
- `timezone`
- `location`
- `description`
- `notification` (mapped to Google reminders overrides)
- `color_id`
- `recurrence` (RFC5545 RRULE strings)

Not in v1:
- attendee invites (deferred)

---

## `BUTLER:` Labeling Contract

Hard rule at tool layer (not prompt-only):
- On create: always normalize `summary` to start with `BUTLER: `.
- On update:
  - If event was originally Butler-generated, preserve/repair `BUTLER:` prefix.
  - If updating a non-Butler event, only prefix if the update turns it into an AI-authored scheduling action.

Recommended metadata:
- `extendedProperties.private.butler_generated = "true"`
- `extendedProperties.private.butler_name = "<name>"`

This gives a reliable marker beyond title text.

---

## Conflict and UX Flows

Use `freeBusy.query` before write operations.

## Create Event

- No conflict:
  - Create event immediately.
  - Return `status=created`.
- Conflict detected:
  - `conflict_policy=fail`: return `status=conflict` with details; no write.
  - `conflict_policy=suggest` (default): return `status=conflict` + nearest candidate slots.
  - `conflict_policy=allow_overlap`: requires approval, then create and return warning details.

## Update Event

- Existing event fetched first.
- If update changes time window, run conflict check.
- Use etag/conditional update semantics to avoid stale overwrite behavior.

## User-facing behavior

When conflicts exist, assistant response should:
- explicitly mention overlap,
- list conflicting event time windows (not sensitive details unless allowed),
- ask confirmation before forcing overlap when policy is not `allow_overlap`.

Approval-gating rule:
- Normal calendar writes are not approval-gated.
- Only conflict overrides (`conflict_policy=allow_overlap`) are approval-gated.
- Implementation approach: conditional gating inside calendar tools. When an overlap override is requested, the calendar module enqueues an approval action and returns `status=approval_required` instead of writing immediately.

---

## Switchboard + Routing Changes

Current classifier prompt includes butler name/description. For calendar-aware routing, include module capabilities in classifier context.

Suggested adjustment in `roster/switchboard/tools/routing/classify.py` prompt context:
- For each butler include modules list (for example `modules: ["calendar", "email"]`).
- Add routing rule:
  - Scheduling intents should prefer butlers with `calendar` module enabled.
  - Domain ownership still applies (health appointment -> `health`, family dinner -> `relationship`, general meeting -> `general`).

No new Switchboard tool is required for v1; this is a prompt/context refinement plus regression tests.

---

## Changes Required Per Custom Butler

Any butler that should schedule events must:
- enable `[modules.calendar]` in its `butler.toml`,
- have `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON` available in runtime env,
- include calendar usage guidance in its `CLAUDE.md`.

## CLAUDE.md Guidance Pattern

Add a short section to each participating butler:
- when to call calendar tools,
- default conflict behavior (do not force overlap unless user explicitly asks),
- requirement that created events are Butler-authored and prefixed.

Suggested heuristics:
- `relationship`: social plans, birthdays, follow-up meetings.
- `health`: appointments, medication follow-ups, screenings.
- `general`: catch-all scheduling and reminders.

---

## Implementation Plan (Phased)

## Phase 1: Module Skeleton

Files:
- `src/butlers/modules/calendar.py` (new)

Tasks:
- Add `CalendarConfig` + `CalendarModule`.
- Register tool functions.
- Wire `credentials_env` default to `["BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON"]`.

## Phase 2: Google Auth + API Client

Files:
- `src/butlers/modules/calendar.py`

Tasks:
- Parse credential JSON from env.
- Implement token refresh via OAuth token endpoint.
- Add request helper for Calendar API calls with bearer token.
- Define provider adapter boundary and wire Google adapter as the only active provider.

## Phase 3: Read/Write Tools

Files:
- `src/butlers/modules/calendar.py`

Tasks:
- Implement list/create/update/get tools.
- Enforce `BUTLER:` title contract.
- Add extendedProperties metadata.
- Add event option handling: date/time, timezone, location, description, notification, color.

## Phase 4: Conflict Engine

Files:
- `src/butlers/modules/calendar.py`

Tasks:
- Implement `freeBusy.query` preflight for create/update.
- Implement suggested next slots logic.
- Integrate conditional approval flow for `allow_overlap` inside calendar tools (module-level decision point).
- If approvals module is enabled, create pending action records for overlap overrides; if not enabled, return an explicit error instructing safe fallback (`suggest`/`fail`).
- Return structured conflict payload.

## Phase 4.5: Recurrence Support

Files:
- `src/butlers/modules/calendar.py`

Tasks:
- Accept and validate RRULE recurrence payloads.
- Support recurring create and recurring update semantics (series-level in v1).
- Add guardrails for timezone + recurrence interactions.
- Document and enforce `calendar_update_event(recurrence_scope="series")` as the only
  supported recurring update scope in v1.

## Phase 5: Routing + Butler Prompt Updates

Files:
- `roster/switchboard/tools/routing/classify.py`
- `roster/general/CLAUDE.md`
- `roster/health/CLAUDE.md`
- `roster/relationship/CLAUDE.md`

Tasks:
- Refine Switchboard classifier context to include module capabilities.
- Update CLAUDE.md with calendar usage guidelines for each butler.

## Phase 6: Regression Tests

Files:
- `tests/config/test_roster_calendar_rollout.py`

Tasks:
- Assert all calendar-enabled butlers use the same shared calendar ID (not unique per-butler).
- Validate calendar_id format and confirm it's not `primary`.
- Assert CLAUDE guidance includes conflict and scope constraints.

## Rollout Approach

1. Calendar module is production-ready.
2. Configure shared calendar ID in all three butler.toml files.
3. Deploy runbook documents single-calendar setup.
4. Validate via regression tests.
