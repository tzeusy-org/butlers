## Why

The calendar create/edit dialog links people to events (`ContactPeoplePicker`,
`entity_ids[]` on create/update — bu-hzi4v / PR #3020), and those links persist
in `calendar_event_entities`. But the workspace **read** only exposed
`entity_ids` on the `proposals` lane, not on regular `UnifiedCalendarEntry`
rows. So the frontend could show linked-people avatars only at creation time —
reopening an existing event showed nothing. This closes the last sub-item of the
calendar contact-link tier (epic bu-l3k0zg) by hydrating linked people on
existing events.

## What Changes

- **Workspace read hydrates linked people.** `GET /api/calendar/workspace?view=user|butler`
  populates a new additive `linked_people: [{entity_id, display_label}]` on each
  `UnifiedCalendarEntry`, resolved from the per-schema `calendar_event_entities`
  junction joined to shared `public.entities` (`canonical_name` as the label).
  Resolution is a **single batch join per targeted schema** keyed on `event_id`,
  scoped to the returned page's events and their producing schemas — no N+1.

- **Honest degraded flag.** A new `people_source_available` boolean on the read
  response (fleet degraded-source convention) is `false` only when a resolution
  query failed for a targeted schema, so people are never silently dropped — the
  FE shows a "people unavailable" indicator instead of misreading empty
  `linked_people` as "no one linked". A link to a missing/tombstoned entity is
  still surfaced with the label `"Unknown"`.

- **Frontend hydration.** Existing event surfaces render the linked people: a
  compact overlapping avatar cluster on agenda pills and full name chips in the
  event detail panel (matching the `ContactPeoplePicker` chip style), plus the
  degraded note when `people_source_available` is false.

## Capabilities

### Modified Capabilities

- `dashboard-api`: the calendar workspace read gains the additive
  `linked_people` field and the `people_source_available` degraded flag on the
  `user`/`butler` views (new requirement "Calendar Workspace Linked-People
  Hydration").

## Impact

- **Read-model** (`src/butlers/api/read_models/calendar_workspace_v1.py`): new
  `query_calendar_entry_people` batch resolver + `CalendarEntryPerson` /
  `CalendarEntryPeopleResult` DTOs, using `fan_out_with_status` for the honest
  degraded signal.
- **Models** (`src/butlers/api/models/calendar_workspace.py`): new
  `CalendarLinkedPerson`; `UnifiedCalendarEntry.linked_people` and
  `CalendarWorkspaceReadResponse.people_source_available` (both additive).
- **Router** (`src/butlers/api/routers/calendar_workspace.py`): `get_workspace`
  resolves people for the returned page and attaches them per entry.
- **FE** (`frontend/src/components/calendar/LinkedPeopleAvatars.tsx`,
  `frontend/src/pages/CalendarWorkspacePage.tsx`, `frontend/src/api/types.ts`):
  avatar cluster + detail-panel chips + degraded note + additive types.
- **No new migration** — `calendar_event_entities` already stores the links.

## Out of Scope

- Pre-filling the create/edit dialog's `people` picker from an existing event's
  linked people (the dialog is create-oriented today; a follow-up).
- Linked-people avatars on the week/day time-grid blocks (agenda pills + detail
  panel cover the read surface; grid-block density is a separate design pass).
- Any change to how links are written (`entity_ids[]` on create/update is
  unchanged).
