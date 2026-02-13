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
calendar_id = "<butler_subcalendar_id>"
timezone = "America/Los_Angeles"
credentials_env = ["BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON"]
default_conflict_policy = "suggest" # suggest | fail | allow_overlap
default_event_color_id = "5"
default_notifications = "popup:10,email:30" # optional convenience config
```

## Calendar Topology (Decision)

Use a dedicated Google Calendar subcalendar for all Butler-managed events.

- Default behavior: calendar tools write to the configured subcalendar ID.
- Rationale:
  - clean separation from personal/manual events,
  - easier filtering, export, and bulk cleanup,
  - lower risk of accidental overlap with private events when reviewing Butler actions.

Recommended naming:
- Calendar name: `Butler`
- Calendar ID: copied from Google Calendar settings and used as `modules.calendar.calendar_id`.

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

## Phase 5: Routing + Butler Prompt Updates

Files:
- `roster/switchboard/tools/routing/classify.py`
- `roster/switchboard/CLAUDE.md`
- `roster/general/CLAUDE.md`
- `roster/health/CLAUDE.md`
- `roster/relationship/CLAUDE.md`
- relevant `roster/*/butler.toml`

Tasks:
- Add calendar-capability routing hints.
- Add per-butler calendar usage instructions.

## Phase 6: Tests

Files:
- `tests/modules/test_calendar_module.py` (new)
- `tests/tools/test_decomposition.py` (update)
- possibly `tests/config/test_config.py` (if module config defaults/validation changes)

Test coverage:
- `BUTLER:` prefix enforcement for create/update.
- Conflict policies (`fail/suggest/allow_overlap`).
- Conflict override approval path (`status=approval_required`, pending action created).
- Recurring event create/update.
- Event option mapping (notification/color/location/description/timezone).
- OAuth credential parse failures.
- Switchboard classification preference for calendar-enabled targets.

## Phase 7: Documentation

Files:
- `README.md`
- `docs/PROJECT_PLAN.md`
- this file

Tasks:
- Add calendar module setup section.
- Add env var and OAuth provisioning instructions.
- Add operator notes for conflict policies.

---

## Google Calendar Provisioning Runbook

1. Create/select a Google Cloud project.
2. Enable Google Calendar API.
3. Configure OAuth consent screen.
4. Create OAuth client credentials.
5. Authorize with the target Google user account and request offline access to get refresh token.
6. Store JSON in `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON`.
7. Create a dedicated Google subcalendar for Butler-managed events (for example `Butler`).
8. Set `modules.calendar.calendar_id` to that subcalendar ID for each calendar-enabled butler.
9. Enable `[modules.calendar]` for target butler(s).
10. Restart daemon and validate with a `calendar_list_events` call.

Security notes:
- Never commit credential JSON.
- Keep env var in local secret management.
- Rotate refresh token if compromised.

---

## Risks and Mitigations

- Risk: accidental schedule overlap.
  - Mitigation: default `conflict_policy=suggest`, explicit user confirmation before overlap.
- Risk: AI writes to wrong calendar.
  - Mitigation: explicit `calendar_id` config; return calendar id in tool responses for audit.
- Risk: indistinguishable AI events.
  - Mitigation: hard `BUTLER:` prefix + metadata flags.
- Risk: stale update overwrite.
  - Mitigation: etag-based conditional update flow.
- Risk: mixing Butler events with personal events reduces trust/readability.
  - Mitigation: dedicated Butler subcalendar as default deployment pattern.

---

## Decisions Confirmed

1. Approval gating: only conflict overrides are gated; normal writes are not. This is implemented with conditional logic inside the calendar module (not separate overlap tools).
2. Default conflict policy: `suggest`.
3. Recurrence: supported in v1.
4. Attendee invites: deferred.
5. Access scope: all calendar-enabled butlers can read/write calendar events.
6. Event placement: Butler-managed events use a dedicated Google subcalendar.
