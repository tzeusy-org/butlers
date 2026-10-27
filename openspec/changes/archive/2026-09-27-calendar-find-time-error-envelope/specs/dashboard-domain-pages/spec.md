## MODIFIED Requirements

### Requirement: Calendar workspace page with dual-view architecture

The dashboard SHALL render a Calendar Workspace page at `/calendar` providing a unified view of user calendar events and butler-managed events through a dual-view architecture.

The page MUST support two views controlled by URL search parameters:
- **User view** (`?view=user`) -- displays events from provider-synced calendars (Google Calendar, etc.) with source/calendar filtering.
- **Butler view** (`?view=butler`) -- displays events organized by butler lane (one lane per butler with its scheduled tasks and reminders).

URL parameters MUST include: `view` (user/butler), `range` (month/week/day/list), `anchor` (ISO date), `source` (source key filter), `calendar` (calendar ID filter). All parameters MUST be persisted in the URL and synchronized via `useSearchParams`.

The page MUST contain:
- A header with title "Calendar Workspace", timezone badge, entry count badge, "Sync now" button, and context-appropriate create buttons ("Create event" in user view, "Create butler event" in butler view).
- A toolbar card with: View toggle (User/Butler), Range selector (Month/Week/Day/List), navigation controls (Prev/Today/Next), and calendar/source filter dropdowns (user view only).
- A main content area rendering the appropriate view mode.
- A read-only "Find time" panel that consumes `POST /api/calendar/workspace/find-time` and distinguishes unavailable free/busy from a successful empty slot result.

#### Scenario: View/range state survives page reload

- **WHEN** the user navigates to `?view=butler&range=month&anchor=2025-06-01`
- **AND** refreshes the page
- **THEN** the page MUST restore butler view, month range, and June 2025 anchor

#### Scenario: Source filters only shown in user view

- **WHEN** the user switches to butler view
- **THEN** the calendar and source filter dropdowns MUST be hidden
- **AND** the `source` and `calendar` URL parameters MUST be removed

#### Scenario: Find-time provider failure is visibly unavailable

- **WHEN** the find-time response has `available=false`, an empty `slots` list, and a safe `reason`
- **THEN** the panel MUST render a named free/busy-unavailable state using that reason
- **AND** it MUST NOT render the successful "No open slots match those constraints in the selected window" empty state
- **AND** it MUST NOT offer a slot-selection or event-creation affordance for that result

#### Scenario: Find-time successful zero-slot result remains empty, not degraded

- **WHEN** the find-time response has `available=true` and an empty `slots` list
- **THEN** the panel MUST render the existing "No open slots match those constraints in the selected window" empty state
- **AND** it MUST NOT render the free/busy-unavailable state
