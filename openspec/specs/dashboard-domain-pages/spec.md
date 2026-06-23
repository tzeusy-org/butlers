# Dashboard Domain Pages

## Purpose

The Butlers dashboard exposes domain-specific pages that surface data managed by individual butlers through read-only (and occasionally mutating) views. These pages turn raw butler data into actionable surfaces: health measurements become trend charts, contacts become an identity-aware CRM, calendar entries merge into a unified workspace, memory tiers become inspectable knowledge graphs, and session costs become budget visibility.

This spec codifies the requirements for six domain page groups: Health, Relationship/Contacts, General/Entities, Calendar, Memory, and Costs -- plus the cross-butler global search that ties them together.

---

## Requirements

### Requirement: [TARGET-STATE] Health Overview landing page

The dashboard SHALL render a Health Overview page at `/health` as the health surface's landing
page (absent today — the bare `/health` URL currently has nowhere to go). The Overview is a
two-column editorial composition (`grid-template-columns: 1.4fr 1fr`): the left column is the
Voice briefing plus a KPI strip; the right column is a quiet attention index. On narrow viewports
the grid MUST collapse to a single column with the attention index below the briefing.

The Overview MUST follow the Dispatch language: Display headline (not bold), the butler hue
(`--category-4` teal) only on the health letter-mark (`ButlerMark`), surfaces-not-cards, and state
color (`--red`/`--amber`/`--green`) reserved for genuine health signal, never decoration.

The Overview MUST contain, in the left column:

- A **DateEyebrow** and a **Display** headline that names the single most important thing about the
  owner's health right now in one sentence.
- A **Voice briefing** (serif elaboration) sourced from `GET /api/health/briefing`, carrying a
  **BriefingStatus pill** that reads `llm · cached` when the line was model-written and `templated`
  when deterministic, so the owner always knows whether a line was computed or model-written.
- A **KPI strip** of exactly four cells, each a mono eyebrow over the latest value, for: latest
  `weight`, latest `blood_pressure`, latest `heart_rate`, latest `blood_sugar` — sourced from
  `GET /api/health/measurements/latest`. No fabricated or placeholder values; an absent reading
  renders a single em-dash, never a fake number.
- A **data-freshness indicator** sourced from `GET /api/health/measurements/sources` (one of the
  wire-orphaned reads this redesign consumes), shown as a quiet mono chip (e.g. "synced 2h ago" per
  source). It MUST state real last-sample times only; when no source data exists the chip is omitted,
  never faked.

The Overview MUST contain, in the right column, an **AttentionList** sourced from the Switchboard
insight reader (`GET /api/switchboard/insights?butler=health&status=pending`). Each attention item MUST link to
the concerning signal (missed doses, severe symptom, drifting measurement) so it is reachable in one
click. When no insight candidate is pending, the attention index MUST collapse to a single
serif-italic line, with no empty-state decoration.

#### Scenario: Overview lands the owner on the most important thing

- **WHEN** the owner navigates to `/health`
- **THEN** the page MUST render the two-column editorial layout with the Voice briefing headline,
  the four-cell KPI strip, and the attention index
- **AND** the briefing headline MUST state the single most important current health fact in one
  sentence

#### Scenario: KPI strip shows the four canonical cells

- **WHEN** the Overview renders the KPI strip
- **THEN** the four cells MUST be latest `weight`, `blood_pressure`, `heart_rate`, and `blood_sugar`
- **AND** a cell with no available reading MUST render an em-dash, never a fabricated value

#### Scenario: Voice line carries the honesty pill

- **WHEN** the Voice briefing renders a model-written elaboration served from cache
- **THEN** the BriefingStatus pill MUST read `llm · cached`
- **WHEN** the briefing falls back to the deterministic templated paragraph
- **THEN** the pill MUST read `templated`

#### Scenario: Empty attention index is one quiet line

- **WHEN** `GET /api/switchboard/insights?butler=health&status=pending` returns zero candidates
- **THEN** the attention index MUST collapse to a single serif-italic line
- **AND** it MUST NOT render placeholder cards, confetti, or celebratory styling

---

### Requirement: Health measurements page with trend charting

The dashboard SHALL render a Measurements page at `/measurements` (reachable from the `/health`
Overview) reframed from "data entry" to "trajectory": the page MUST lead with the trend rule-list
(mono-time / status-dot / value / `→`), not the input box. It displays health measurement data as
interactive line charts with a supporting raw-data view.

The page MUST contain:
- A type selector displaying tabs for measurement types: `weight`, `blood_pressure`, `heart_rate`,
  `blood_sugar`, `temperature`, `spo2`, `steps`. The tabs MUST use only predicates the system can
  actually produce; the legacy tabs `glucose`, `sleep`, and `oxygen` (which the create form can
  never produce, yielding a perpetual "No data" state) MUST NOT appear. Clicking a type SHALL filter
  the chart and rule-list to that type.
- Date range filters (`since`/`until`) using date inputs, with a Clear button when any filter is
  active.
- A Recharts `LineChart` in a `ResponsiveContainer`. For `blood_pressure`, the chart MUST render two
  lines (systolic and diastolic). For all other types, a single line plotting the primary numeric
  value. The line palette MUST be driven by the health hue token `--category-4` (bridged to a literal
  color for recharts via a read of the computed CSS variable), not a hardcoded hex. Where two lines
  are shown (systolic/diastolic), the second line MUST use a distinguishable shade derived from
  `--category-4` (e.g. a reduced-opacity or lightened variant of the same hue) so the two lines remain
  visually separable while staying within the single health hue.
- The trend rule-list as the primary surface, sourced from `GET /api/health/measurements/trend`
  (the bucketed mean/min/max aggregation — one of the wire-orphaned reads this redesign consumes), and
  a "Show/Hide raw data" affordance for the full table (Date, Type, Value, Notes). Measurement values
  are compound JSONB objects (e.g., `{"systolic": 120, "diastolic": 80}`); the chart MUST extract
  numeric values with key-aware parsing and the table MUST format them as `key: value` pairs.

#### Scenario: Chart tabs use only producible predicates

- **WHEN** the measurements page renders the type selector
- **THEN** the tabs MUST be exactly `weight`, `blood_pressure`, `heart_rate`, `blood_sugar`,
  `temperature`, `spo2`, `steps`
- **AND** the tabs `glucose`, `sleep`, and `oxygen` MUST NOT appear

#### Scenario: Page leads with the trend, not the form

- **WHEN** the measurements page loads
- **THEN** the trend rule-list (or chart) MUST be the leading surface
- **AND** the create/input affordance MUST NOT be the first element

#### Scenario: Blood pressure dual-line chart

- **WHEN** the user selects `blood_pressure` as the measurement type
- **AND** there are measurements with `value` containing `systolic` and `diastolic` keys
- **THEN** the chart MUST render two lines for systolic and diastolic
- **AND** the two lines MUST use distinguishable shades of `--category-4` (the diastolic line a
  reduced-opacity or lightened variant) so they are not the same indistinguishable color
- **AND** the chart tooltip MUST label them "Systolic" and "Diastolic"

#### Scenario: Empty state for type with no data

- **WHEN** the user selects a measurement type with zero records in the selected date range
- **THEN** the page MUST display a single serif-italic empty line rather than decorated empty-state
  chrome

---

### Requirement: Medications page with adherence tracking

The dashboard SHALL render a Medications page at `/medications` reframed to the Dispatch language:
medications render as a rule-list (status-dot / med+dose / adherence delta / `→`) — NOT a card grid
— with a right-column "Next doses" list. Dose history is reached via a per-row detail/expand
affordance rather than a nested table.

The page MUST contain:
- An Active/All filter toggle. When "Active" is selected, the API query MUST include `active=true`.
- A rule-list row per medication showing a status dot, the medication name and dosage, and the
  adherence delta. Adherence MUST be sourced from `GET /api/health/medications/{id}/adherence`
  (frequency-expected), NOT a client-side naive taken/total ratio.
- A **dashboard dose-logging affordance**: the owner MUST be able to log a dose for a medication
  directly from the dashboard via `POST /api/health/medications/{id}/doses`, writing the same
  `took_dose` fact the butler MCP tool writes. Adherence MUST be stated as a fact ("12 of 14 doses
  taken"), never rewarded with celebration or a green-check.
- A per-row detail/expand affordance showing recent dose history (Date, Taken/Skipped status, Notes).
  Severity/state color MUST appear only when adherence is genuinely falling, never as decoration.

#### Scenario: Adherence is frequency-expected, not a naive ratio

- **WHEN** a medication row renders its adherence
- **THEN** the value MUST be sourced from `GET /api/health/medications/{id}/adherence`
- **AND** it MUST NOT be computed as a naive taken/total client-side ratio

#### Scenario: Dashboard dose logging

- **WHEN** the owner logs a dose from a medication row
- **THEN** the dashboard MUST call `POST /api/health/medications/{id}/doses`
- **AND** the dose MUST be recorded as a `took_dose` fact (no new table)
- **AND** the adherence figure MUST be stated plainly, never rewarded with celebratory styling

#### Scenario: Dose history is a detail affordance, not a card grid

- **WHEN** the medications page renders
- **THEN** medications MUST render as a Dispatch rule-list, not a responsive card grid
- **AND** dose history MUST be reached via a per-row detail/expand affordance

#### Scenario: Inactive medications hidden by default

- **WHEN** the page loads with the "Active" filter selected
- **THEN** only medications with `active = true` MUST be fetched
- **AND** switching to "All" MUST fetch all medications regardless of active status

---

### Requirement: Conditions page with status badges

The dashboard SHALL render a Conditions page at `/conditions` reframed to the Dispatch language:
conditions render as a rule-list (status-dot / condition+status / onset / `→`), not a paginated card
or badge-heavy table. The page MUST lead with the condition list and use state color only when a
condition status genuinely demands it.

Each row MUST display: a status dot, the condition name and status, the onset/diagnosed date, and a
navigation affordance. Status colors MUST follow the existing convention (`active`, `resolved`,
`managed`) but render as a single status dot rather than a filled badge. Pagination MUST follow the
consistent pagination pattern.

#### Scenario: Conditions render as a Dispatch rule-list

- **WHEN** the conditions page loads with at least one condition
- **THEN** conditions MUST render as rule-list rows with a status dot, name+status, and onset date
- **AND** the page MUST NOT render shadcn `Card` shells around each condition

#### Scenario: Empty conditions list

- **WHEN** no conditions exist
- **THEN** the page MUST render a single serif-italic empty line, not decorated empty-state chrome

---

### Requirement: Symptoms page with severity visualization

The dashboard SHALL render a Symptoms page at `/symptoms` reframed to the Dispatch language:
symptoms render as a rule-list (6px **severity glyph** / symptom+frequency / severity / `→`), not a
progress-bar table. The page retains its name and date-range filters.

The page MUST contain:
- Filter controls: name text input, date range (From/To), Clear button when any filter is active.
  Changing any filter MUST reset pagination to page 0.
- A rule-list row per symptom showing a 6px severity glyph (using `--severity-low`/`-medium`/`-high`
  for the owner's own 1-10 severity value), the symptom name and frequency, the severity, and a
  navigation affordance. No clinical adjectives are added to the owner's own severity value.

#### Scenario: Severity renders as a 6px glyph, not a bar

- **WHEN** a symptom has severity 8
- **THEN** the row MUST render a 6px severity glyph colored `--severity-high`
- **AND** the row MUST NOT render a wide progress bar

#### Scenario: Severity color mapping by band

- **WHEN** a symptom has severity 2
- **THEN** the glyph MUST be `--severity-low`
- **WHEN** a symptom has severity 5
- **THEN** the glyph MUST be `--severity-medium`

---

### Requirement: Meals page with day-grouped display

The dashboard SHALL render a Meals page at `/meals` reframed to the Dispatch language: meals render
as a rule-list (mono-time / meal+nutrition / delta / `→`) grouped by day, with a right-column "Daily
totals" mini-KPI sourced from `GET /api/health/nutrition/summary`. Cards are removed.

The page MUST contain:
- Meal-type filters: All, breakfast, lunch, dinner, snack. Selecting a type SHALL filter; re-selecting
  SHALL deselect.
- Date range filters (From/To) and a Clear button.
- Meals grouped by date under a mono day-header, each row showing the time, the meal description and
  nutrition summary, and a navigation affordance. The right column MUST show the day's nutrition
  totals (calories/macros) from the nutrition summary endpoint.

#### Scenario: Meals render as a day-grouped rule-list

- **WHEN** meals exist across multiple dates
- **THEN** meals MUST render as rule-list rows grouped under mono day-header rules
- **AND** the page MUST NOT render shadcn `Card` shells per meal

#### Scenario: Daily totals from the nutrition summary endpoint

- **WHEN** the meals page renders the right-column daily totals
- **THEN** the totals MUST be sourced from `GET /api/health/nutrition/summary`
- **AND** they MUST NOT be re-computed client-side from individual meal rows

---

### Requirement: Research page with expandable content

The dashboard SHALL render a Research page at `/research` reframed to the Dispatch language: research
notes render as a rule-list (time / topic+source-tag / excerpt / `→`) with in-place expansion and a
right-column "Topics" index, not a badge-heavy table.

The page MUST contain:
- Search input and tag filter affordances. Tags MUST be derived from the current result set.
- A rule-list row per note showing the time, the topic and source tag, and an excerpt. Clicking a row
  SHALL expand its full `content` in place. Source URLs MUST open in a new tab
  (`target="_blank"`, `rel="noopener noreferrer"`).

#### Scenario: Research renders as a rule-list with in-place expansion

- **WHEN** the user clicks a research note row
- **THEN** the row MUST expand in place to show the full content
- **AND** clicking it again MUST collapse it

---

### Requirement: Health data hooks with auto-refresh

Deterministic health domain hooks (CRUD lists, KPI/latest reads, and trend reads) MUST use TanStack
Query hooks that auto-refresh every 30 seconds (`refetchInterval: 30_000`). The following
deterministic hooks MUST be provided and MUST auto-refresh:

| Hook | Query Key Prefix | API Function |
|---|---|---|
| `useMeasurements(params)` | `health-measurements` | `getMeasurements` |
| `useMedications(params)` | `health-medications` | `getMedications` |
| `useMedicationDoses(id, params)` | `health-medication-doses` | `getMedicationDoses` |
| `useMedicationAdherence(id, params)` | `health-medication-adherence` | `getMedicationAdherence` |
| `useConditions(params)` | `health-conditions` | `getConditions` |
| `useSymptoms(params)` | `health-symptoms` | `getSymptoms` |
| `useMeals(params)` | `health-meals` | `getMeals` |
| `useResearch(params)` | `health-research` | `getResearch` |
| `useNutritionSummary(params)` | `health-nutrition-summary` | `getNutritionSummary` |

The `useMedicationDoses` and `useMedicationAdherence` hooks MUST be conditionally enabled
(`enabled: !!medicationId`).

**Auto-refresh carve-out (binds the universal 30s rule):** the LLM Voice briefing hook
(`use-health-briefing`, sourced from `GET /api/health/briefing`) and the insight feed hook (sourced
from `GET /api/switchboard/insights?butler=health`) MUST be **EXCLUDED** from the 30s auto-refresh. They MUST NOT
set a `refetchInterval`; instead they rely on the briefing's per-owner 5-minute TTL cache and a
**manual** refresh triggered via the BriefingStatus pill. This is a permanent cost guard: an
auto-refreshing LLM endpoint would multiply spawn cost.

#### Scenario: Deterministic hooks auto-refresh every 30s

- **WHEN** a deterministic health hook (e.g. `useMeasurements`) is mounted
- **THEN** it MUST set `refetchInterval: 30_000`

#### Scenario: LLM briefing and insight feed are excluded from auto-refresh

- **WHEN** the `use-health-briefing` hook or the insight feed hook is mounted
- **THEN** it MUST NOT set any `refetchInterval`
- **AND** a fresh briefing MUST be obtained only on manual refresh or after the 5-minute TTL elapses

---

### Requirement: Contacts page with search, label filtering, and Google sync

The dashboard SHALL render a Contacts page at `/contacts` displaying contacts in a searchable, label-filterable, paginated table with a Google sync action.

The page MUST contain:
- A heading area with title "Contacts", description, and a "Sync from Google" button that triggers incremental Google Contacts sync. The button MUST be disabled while syncing and display "Syncing..." during the operation.
- On successful sync, a toast MUST display the sync summary (created, updated, skipped, errors counts). On failure, a toast MUST display the error message.
- A `ContactTable` component with: search input (placeholder "Search contacts..."), label filter badges (All + one per label, with deterministic hash-based coloring for labels without explicit colors), and a table with columns: Name (with optional nickname in parentheses), Email, Phone, Labels (colored badges), Last Interaction (relative time via `formatDistanceToNow`).
- Each contact row MUST be clickable, navigating to `/contacts/:id`.
- Pagination with page size 50.

#### Scenario: Label color determinism

- **WHEN** a label named "family" has no explicit `color` set
- **THEN** its badge color MUST be deterministically derived from a hash of "family" using the categorical palette: `var(--category-1)`, `var(--category-2)`, `var(--category-3)`, `var(--category-4)`, `var(--category-5)`, `var(--category-6)`, `var(--category-7)`, `var(--category-8)`
- **AND** the same label MUST always render with the same color

#### Scenario: Google sync with mixed results

- **WHEN** the user clicks "Sync from Google" and the sync returns `{created: 5, updated: 12, skipped: 3, errors: 1}`
- **THEN** a success toast MUST display "Google sync complete: 5 created, 12 updated, 3 skipped, 1 errors"

---

### Requirement: Contact detail page with tabbed sub-resources

The dashboard SHALL render a contact detail page at `/contacts/:contactId` displaying the full contact record with breadcrumb navigation and tabbed sub-resource views.

The page MUST contain:
- Breadcrumbs: "Contacts" (linked to `/contacts`) followed by the contact's name.
- A header card displaying: full name (with nickname in parentheses), job title and company (formatted as "job_title at company"), and colored label badges.
- An info section with labeled rows for: Email, Phone, Address, Birthday.
- A tabbed content area with five tabs:
  - **Notes** -- displaying note cards with content (pre-wrap), and relative timestamp.
  - **Interactions** -- displaying interaction entries with type badge, date, summary, and optional details, rendered as a timeline with a left border accent.
  - **Gifts** -- displaying a table with columns: Description, Direction (given/received badge), Occasion, Date, Value (right-aligned currency formatting).
  - **Loans** -- displaying a table with columns: Description, Direction (lent/borrowed badge), Amount (with currency code), Status (active/repaid/forgiven badge), Date, Due Date.
  - **Activity** -- displaying a feed of activity entries with action badge, details JSON, and relative timestamp.

Each tab MUST independently fetch its data via dedicated hooks and display loading skeletons or empty states as appropriate.

#### Scenario: Contact not found

- **WHEN** the user navigates to `/contacts/nonexistent-id`
- **THEN** the page MUST display an error message: "Failed to load contact." with the error details

---

### Requirement: Groups page

The dashboard SHALL render a Groups page at `/groups` displaying contact groups in a paginated table.

The table MUST display columns: Name (bold), Description (truncated), Members (count), Labels (colored badges with deterministic hashing), Created (date).

The Groups page is a read-and-label surface: groups and their membership are created and maintained through the relationship butler's tools (`group_create`, `group_add_member`), not from this page. The page additionally supports creating labels and assigning/removing labels on a group.

The Groups page MUST NOT be surfaced in the primary sidebar navigation; it remains routable at `/groups` and is reachable via the relationship butler's CRM tab Quick Links.

#### Scenario: Group with no labels

- **WHEN** a group has zero associated labels
- **THEN** the Labels column MUST display an em-dash

#### Scenario: Not in sidebar navigation

- **WHEN** the sidebar renders
- **THEN** no Groups nav link (`/groups`) and no Relationships nav group appear
- **AND** navigating directly to `/groups` still renders the Groups page

---

### Requirement: Contact hooks with conditional fetching

Contact-related hooks MUST be provided with the following behaviors:

| Hook | Conditional | Behavior |
|---|---|---|
| `useContacts(params)` | No | Fetch paginated contacts |
| `useContact(id)` | `enabled: !!contactId` | Fetch single contact detail |
| `useContactNotes(id)` | `enabled: !!contactId` | Fetch notes for a contact |
| `useContactInteractions(id)` | `enabled: !!contactId` | Fetch interactions |
| `useContactGifts(id)` | `enabled: !!contactId` | Fetch gifts |
| `useContactLoans(id)` | `enabled: !!contactId` | Fetch loans |
| `useContactFeed(id)` | `enabled: !!contactId` | Fetch activity feed |
| `useGroups(params)` | No | Fetch paginated groups |
| `useLabels()` | No | Fetch all labels |
| `useUpcomingDates(days)` | No | Fetch upcoming important dates |

---

### Requirement: Entity browser for general butler data

The dashboard SHALL render an Entity Browser component that displays structured JSONB entities from the General butler in a searchable, filterable table with expandable JSON viewer.

The component MUST accept props for: entities array, loading state, search query, collection filter, tag filter, and available collections/tags.

The table MUST display columns: Collection (badge), Tags (secondary badges), Data (truncated JSON preview or expanded JsonViewer), Created (date).

- The JSON preview MUST truncate at 80 characters with an ellipsis.
- Clicking a row SHALL toggle expansion, replacing the truncated preview with a full `JsonViewer` component in a muted background panel.
- Filters MUST include: text search input, collection dropdown (Select component with "All collections" sentinel value `__all__`), tag dropdown (Select component with "All tags" sentinel), and a "Clear filters" button.

#### Scenario: Entity row expansion shows full JSON

- **WHEN** the user clicks an entity row with data `{"blood_type": "A+", "height_cm": 175, "notes": "..."}`
- **THEN** the Data cell MUST expand to show a full recursive `JsonViewer` with syntax highlighting
- **AND** clicking the same row again MUST collapse back to the truncated preview

---

### Requirement: Recursive JSON viewer component

The dashboard SHALL provide a reusable `JsonViewer` component that renders any JSON value as a collapsible tree with syntax highlighting and copy-to-clipboard.

The component MUST support:
- Recursive rendering of objects and arrays with collapsible nodes (triangle toggle).
- Syntax coloring: keys in violet, strings in emerald, numbers in sky, booleans in amber, null in rose italic.
- A "Copy JSON" button at root level that copies the pretty-printed JSON (2-space indent) to the clipboard.
- A `defaultCollapsed` prop that controls whether child nodes start collapsed (depth > 0).
- Indentation at 16px per depth level.

#### Scenario: Copy to clipboard

- **WHEN** the user clicks "Copy JSON" on a viewer displaying `{"name": "Alice"}`
- **THEN** the clipboard MUST contain `{\n  "name": "Alice"\n}`
- **AND** the button text MUST change to "Copied!" for 2 seconds

---

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

#### Scenario: View/range state survives page reload

- **WHEN** the user navigates to `?view=butler&range=month&anchor=2025-06-01`
- **AND** refreshes the page
- **THEN** the page MUST restore butler view, month range, and June 2025 anchor

#### Scenario: Source filters only shown in user view

- **WHEN** the user switches to butler view
- **THEN** the calendar and source filter dropdowns MUST be hidden
- **AND** the `source` and `calendar` URL parameters MUST be removed

---

### Requirement: Calendar user view with grid and list layouts

In user view, the calendar MUST support four range modes:

1. **Month** -- a 6x7 grid (42 cells starting from Monday). Each cell MUST display: the day number (dimmed if outside current month, highlighted with a ring if today), and truncated event titles. Clicking a day cell SHALL create a new event on that date (if writable calendars exist).
2. **Week** -- a 7-column grid displaying events for each day of the week.
3. **Day** -- a single-column display for the selected date.
4. **List** -- a table spanning 30 days from the anchor with columns: Time (formatted window or "All day"), Title, Source (badge from source key).

Editable entries (those with `view=user`, `source_type=provider_event`, a `provider_event_id`, and `editable=true`) MUST display edit and delete buttons.

#### Scenario: Month grid highlights today

- **WHEN** the current month is displayed and today is March 15
- **THEN** the cell for March 15 MUST have a distinct ring/highlight style
- **AND** cells for days outside the current month MUST have muted text

---

### Requirement: Calendar butler view with lane-based display

In butler view, the calendar MUST display events grouped by butler lane. Each lane MUST show:
- Lane header with butler name (titleized), event count, and an "Add event" button.
- A table with columns: Time (formatted window), Title, Type ("Schedule" or "Reminder"), Status (badge), Actions (Edit, Toggle pause/resume, Delete buttons).

Recurring events from the same parent MUST be capped at 10 instances per day per lane. When instances are capped, an overflow row MUST display: "... and N more instances of 'Event Title'".

#### Scenario: Butler event toggle pause/resume

- **WHEN** the user clicks the toggle button on an active butler event
- **THEN** the mutation MUST send `action: "toggle"` with `enabled: false`
- **AND** a success toast "Event paused" MUST appear
- **WHEN** the user clicks toggle on a paused event
- **THEN** the mutation MUST send `enabled: true`
- **AND** a success toast "Event resumed" MUST appear

---

### Requirement: Calendar event creation and editing dialogs

The calendar workspace MUST provide two dialog types for event mutations:

1. **User event dialog** -- for creating/editing provider calendar events. Fields: source selector (writable calendars only), title (required), start datetime, end datetime, timezone, description (textarea), location. Validation: title required, start/end must be valid, end must be after start.

2. **Butler event dialog** -- for creating/editing scheduled tasks and reminders. Fields: butler lane selector, event kind (scheduled_task / butler_reminder), title (required), start datetime, end datetime (required for scheduled_task), timezone, recurrence frequency (None/Daily/Weekly/Monthly/Yearly), optional until-at boundary, cron expression (for scheduled tasks without RRULE). Validation: title required, start required, scheduled tasks require either recurrence frequency or cron, end must be after start for scheduled tasks.

Both dialogs MUST support create and edit modes, determined by the presence of an existing entry.

#### Scenario: Create user event on specific date

- **WHEN** the user clicks a day cell in month view on March 20
- **THEN** the user event dialog MUST open in create mode
- **AND** the start time MUST default to the next full hour on March 20
- **AND** the end time MUST default to 30 minutes after the start

#### Scenario: Delete user event with confirmation

- **WHEN** the user clicks delete on a user calendar event
- **THEN** a confirmation dialog MUST appear with the event title
- **AND** confirming MUST send a `delete` mutation with the `provider_event_id`

---

### Requirement: Calendar source freshness indicators

The calendar workspace MUST display source freshness information alongside each connected source. Each source MUST show:
- A source name derived from: butler lane sources as "[Butler] Butler Name", Google sources as "[Google] Calendar Name", other sources titleized.
- A sync state badge (variant: `fresh` = secondary, `failed` = destructive, `syncing` = default, `stale` = outline).
- Staleness text: "<1s" = "fresh", seconds/minutes/hours/days formatted (e.g., "5m stale", "2h stale", "3d stale").
- Last synced timestamp (formatted as "MMM d, HH:mm").
- A per-source "Sync" button that triggers sync for that individual source.

#### Scenario: Hashed Google Calendar IDs are truncated

- **WHEN** a source has a source_key matching the pattern `[a-f0-9]{20+}@group.calendar.google.com`
- **THEN** the display name MUST be truncated to the first 8 characters followed by an ellipsis

---

### Requirement: Calendar workspace hooks

The calendar workspace MUST use the following TanStack Query hooks:

| Hook | Type | Auto-Refresh |
|---|---|---|
| `useCalendarWorkspace(params)` | Query | 30s |
| `useCalendarWorkspaceMeta()` | Query | 60s |
| `useSyncCalendarWorkspace()` | Mutation | Invalidates workspace + meta |
| `useMutateCalendarWorkspaceUserEvent()` | Mutation | Invalidates workspace + meta |
| `useMutateCalendarWorkspaceButlerEvent()` | Mutation | Invalidates workspace + meta |

All mutation hooks MUST invalidate both `calendar-workspace` and `calendar-workspace-meta` query keys on success.

---

### Requirement: Memory page band layout

The dashboard SHALL render a Memory page at `/memory` as a single column
(max-width 1280px) composed of four vertically stacked bands, in this order:

1. **Overture band** — eyebrow, display headline, one Voice sentence, KPI strip.
2. **Pipeline band** — the memory lifecycle as a single mono line.
3. **Registers + rail band** — `grid-template-columns: 1.4fr 1fr` (gap 56px):
   the search input, kind pills, and the focused register on the left; the
   attention rail and recent activity on the right.
4. **Housekeeping band** — a quiet bottom band (retention policies, compaction
   log, embeddings).

The layout MUST NOT render any tier-card grid, any tabbed table browser, or any
right-sidebar activity card; those are retired. On narrow viewports the
`1.4fr 1fr` grid MUST collapse to a single column with the rail below the
register.

State color (`--red` / `--amber` / `--green`) MUST appear in only two places on
this page: the attention rail, and the pipeline band's dead-letter numeral when
it is non-zero. `--green` MUST NOT appear anywhere on this page (a healthy
pipeline is the absence of alarm, not a celebration). Butler category hues MAY
appear only on ButlerMark letter-marks (daybook gutter, recent-activity rows).

The register selection (`register`, default `facts`), search query (`q`), kind
filters (`kind` / `validity` / `status`), and pagination `offset` are URL query
params so the browser back button and deep-links work; default values are NOT
written to the URL so deep-links round-trip. The search text input itself is
local state until submitted.

#### Scenario: Healthy day renders no alarm color

- **WHEN** there are zero dead-letter episodes, no overdue write-up, no
  anti-pattern rules, no high-importance fading facts, and no stale embeddings
- **THEN** the page MUST render zero `--red`, `--amber`, or `--green` pixels
- **AND** the attention rail body MUST collapse to one serif-italic line reading
  "Nothing waiting."

#### Scenario: Deep-link round-trips through defaults

- **WHEN** the page loads at `/memory` with no query params
- **THEN** the Facts register MUST be the focused register
- **AND** the URL MUST NOT be rewritten to add `register=facts` or any other
  default param

---

### Requirement: Memory overture band

The overture band MUST contain, top to bottom:

1. A mono **eyebrow** reading "MEMORY".
2. A **display headline** (44px) reading "What the house believes."
3. One **Voice sentence** (serif) narrating the system's own process — cadence,
   last run, and output — in the third person, never first person, never
   narrating content. Example: "Forty-one observations await the evening
   write-up; the last ran at 06:00 and produced twelve facts." The Voice
   sentence MUST be templated from `/api/memory/stats` fields (it is NOT
   produced by an LLM).
4. A **KPI strip** of exactly four hairline-divided cells, each a mono eyebrow
   over a mega-number, with no fills, bars, or badges: **PENDING**
   (`unconsolidated_episodes`), **ACTIVE FACTS** (active fact count),
   **PROVEN RULES** (proven rule count), **LAST WRITE-UP**
   (`last_consolidation_at`, formatted, with `last_consolidation_facts_produced`).

All numerals in the band MUST use `tabular-nums`.

#### Scenario: Voice sentence is templated, not generated

- **WHEN** the overture band renders the Voice sentence
- **THEN** the sentence MUST be produced by string templating over
  `/api/memory/stats` fields
- **AND** the page MUST NOT issue any LLM inference call to produce it

#### Scenario: Last write-up cell shows time and facts produced

- **WHEN** `last_consolidation_at` is "2026-06-12T06:00:00Z" and
  `last_consolidation_facts_produced` is 12
- **THEN** the LAST WRITE-UP cell MUST display the formatted time and "12 facts"

---

### Requirement: Memory pipeline band

The pipeline band MUST render the memory lifecycle as a single line of mono
tabular numerals joined by `─→` connectors, reading left to right as the flow
of observation into durable knowledge: episodes → pending → facts (with a
fading count) → rules (with a proven count), and a terminal dead-letters count.

The dead-letter numeral MUST render in `--red` when, and only when, it is
greater than zero; at zero it MUST render in the neutral foreground like every
other numeral in the band. No other numeral in the band may take state color.
The band MUST NOT render progress bars, gauges, sparklines, or a composite
health score.

#### Scenario: Dead letters earn red only when non-zero

- **WHEN** `dead_letter_episodes` is 0
- **THEN** the dead-letters numeral MUST render in the neutral foreground
- **WHEN** `dead_letter_episodes` is 3
- **THEN** the dead-letters numeral MUST render in `--red`

#### Scenario: Consolidation health readable without scrolling

- **WHEN** the page loads
- **THEN** the pending count, last write-up time, and dead-letter count MUST all
  be visible in the overture and pipeline bands before any scroll

---

### Requirement: Memory registers — three shapes

The register area MUST render exactly one focused register at a time, selected
by single-select kind pills (`Facts` default, `Rules`, `Episodes`) bound to the
`register` URL param. The three registers MUST use three distinct row shapes;
the page MUST NOT render the three kinds through one shared table shape. UI
labels MUST remain "Facts", "Rules", "Episodes" — the metaphor nouns
("ledger", "standing orders", "daybook") MUST NOT appear as labels.

**The ledger (Facts)** — hairline-separated grid rows, three columns:
`subject · predicate` (subject sans; entity-anchored subjects link to
`/entities/:id`; predicate mono muted, joined with `·`), `content` (sans,
single line, truncated; the whole row is the hit target opening
`/memory/facts/:id`), and a right-aligned mono `belief` column (effective
confidence to two decimal places followed by a two-letter permanence tag —
`pm` permanent · `st` stable · `sd` standard · `vo` volatile · `ep` ephemeral).
A `derived_from` glyph (`↳`, mono, muted) MUST appear at row end when the fact
has a `source_episode_id`. Fading rows MUST dim their entire foreground
(including content) to `--dim`; the default ledger view MUST NOT render
`superseded`, `expired`, or `retracted` facts unless an explicit validity
filter selects them.

**Standing orders (Rules)** — numbered directives with generous row padding:
a zero-padded `§NN` mono gutter (ordered by maturity rank then confidence), the
directive content (sans, wrapping, clamped to 2 lines in the register), a mono
tally line `applied N · helpful N · harmful N`, and the maturity as a plain
lowercase mono word (`candidate` · `established` · `proven` · `anti_pattern`).
The word `harmful` and its numeral MUST take `--red` only when harmful > 0;
anti-pattern rules MUST additionally carry a 2px left sliver in `--red`. No
colored maturity chips or pills.

**The daybook (Episodes)** — a journal feed grouped by day under mono
day-header rules (TODAY / YESTERDAY / dated): a 50px mono time gutter, a butler
letter-mark (the only place butler hue appears in the register), content (sans,
clamped to 2 lines, expandable in place), and a single consolidation glyph at
row end — `◦` pending (hollow), `•` consolidated (filled), `✕` dead-letter
(`--red`). Importance ≥ 8 MUST render the time gutter in `--fg` instead of
muted. The glyph MUST NOT be replaced by a word badge or chip.

Each ledger and standing-orders and daybook row MUST be clickable and MUST
navigate to the corresponding detail page (`/memory/facts/{id}`,
`/memory/rules/{id}`, `/memory/episodes/{id}`), with a `cursor-pointer` hover
affordance.

#### Scenario: Confidence renders as a mono numeral, never a bar

- **WHEN** a fact has effective confidence 0.94 and permanence "stable"
- **THEN** the belief column MUST read "0.94" as a mono tabular numeral followed
  by the tag "st"
- **AND** the row MUST NOT render a progress bar, donut, gauge, or percent sign
  for confidence

#### Scenario: Fading fact dims rather than colors

- **WHEN** a fact's validity is "fading"
- **THEN** the entire row foreground (subject, content, belief) MUST be rendered
  at `--dim`
- **AND** the row MUST NOT use color, strikethrough, or an opacity animation to
  signal decay

#### Scenario: Default ledger hides non-active validities

- **WHEN** the ledger renders with no validity filter
- **THEN** only `active` (and `fading`) facts MUST appear
- **AND** `superseded`, `expired`, and `retracted` facts MUST be omitted until a
  validity filter selects them

#### Scenario: Anti-pattern rule sliver is the only register state color

- **WHEN** a rule's maturity is `anti_pattern` and its harmful count is 4
- **THEN** the rule MUST render a 2px left sliver in `--red` and the `harmful 4`
  fragment of the tally in `--red`
- **AND** the maturity MUST render as the lowercase mono word "anti_pattern"
  with no colored chip

#### Scenario: Episode consolidation state is a glyph

- **WHEN** an episode is pending, consolidated, or dead-lettered
- **THEN** its row MUST render `◦`, `•`, or `✕` respectively at row end
- **AND** it MUST NOT render a word badge such as "Consolidated" or a colored
  chip

#### Scenario: Derived-from glyph links provenance

- **WHEN** a fact has a non-null `source_episode_id`
- **THEN** the ledger row MUST render a muted mono `↳` glyph at row end
- **AND** clicking the row MUST open `/memory/facts/{id}` (the detail page
  carries the episode link)

---

### Requirement: Belief typography

Every memory belief signal MUST render per the following table; the listed
"Never" renderings are prohibited on the `/memory` page and its detail pages:

| Signal | Rendering | Never |
|---|---|---|
| Effective confidence | mono tabular numeral, 2 decimal places | progress bar, donut, gauge, percent sign |
| Decay | foreground dims to `--dim` at the fading threshold | color, strikethrough, opacity animation |
| Permanence | two-letter mono tag, muted | colored chip, icon |
| Confirmation | detail-page mono stamp (`confirmed <date> · healthy`) | green check, toast celebration |
| Consolidation state | glyph `{◦ • ✕}` | word badge ("Consolidated") |
| Rule maturity | lowercase mono word | colored pill, star rating |
| Rule harm | `--red` on the harmful tally + 2px left sliver when anti-pattern | red row background |
| Importance | ink weight (muted → `--fg`) | flame icons, numbered badges |

The fading threshold MUST be computed from **effective (decayed)** confidence,
not raw stored confidence. There MUST be no composite "memory health score" or
any aggregate letter/colour grade anywhere on the memory surface.

#### Scenario: No health score anywhere

- **WHEN** any memory page or detail page renders
- **THEN** it MUST NOT display a composite health score, grade, or traffic-light
  summary of memory health

---

### Requirement: Memory unified search

The memory page MUST expose exactly one search affordance: a single input at the
top of the register area, scoped by the kind pills, backed by
`GET /api/memory/inspect`. There MUST NOT be a second search box anywhere on the
page (no per-register or per-tab search inputs).

Pressing `/` MUST focus the input; pressing Enter MUST submit the query and kind
to the `q` and `register`/`kind` URL params. Results MUST render in the register
shape of their kind (under mono kind-group headers when the search spans kinds);
search MUST NOT introduce a fourth row shape. Clearing the query MUST restore
the browsing register. An empty result set MUST render one serif-italic line —
"Nothing in the books."

Register pagination MUST be offset-based with page size **50**, rendered as a
footer `1–50 of N` with prev/next pills.

#### Scenario: Single search affordance

- **WHEN** the page renders any register
- **THEN** there MUST be exactly one search input on the page
- **AND** no per-register or per-tab search input MUST be rendered

#### Scenario: Page size is 50

- **WHEN** a register has more than 50 rows
- **THEN** the register MUST show the first 50 rows and a pagination footer
  reading "1–50 of N" with prev/next pills bound to the `offset` URL param

#### Scenario: Empty search result

- **WHEN** a submitted search returns no rows
- **THEN** the register area MUST render the serif-italic line "Nothing in the
  books."

---

### Requirement: Memory attention rail and recent activity

The registers+rail band MUST render an **attention rail** in its right column as
the only surface (besides the pipeline dead-letter numeral) where state color
appears. The rail MUST render at most these five condition rows, each only when
its state exists, each carrying at most one commit-class action:

| Condition | Severity | Reads | Action target |
|---|---|---|---|
| dead-letter episodes > 0 | red | "N episodes dead-lettered" | `/memory?register=episodes&status=dead_letter` |
| consolidation stalled (last run > 2× cadence) | amber | "write-up overdue · last <time>" | **none — action-less** |
| rule turned anti-pattern / harmful streak | red | "§NN harmful ×N" | rule detail page |
| high-importance fact entering fading | amber | "N important facts fading" | `/memory?register=facts&validity=fading` |
| stale embeddings (model drift) | amber | "N rows on old embedding" | housekeeping band |

The **"write-up overdue" row MUST be action-less**: it MUST NOT carry a "run
consolidation now" affordance, nor any other control that triggers a
consolidation run. This is a permanent cost guard — consolidation is a
pre-existing scheduled cron, and a run-now affordance is the only place a future
change could multiply that spawn cost. `--amber` MUST appear only in the rail.

When no condition holds, the rail header MUST remain and the body MUST collapse
to one serif-italic line — "Nothing waiting."

Below the attention rail, a **Recent activity** sub-surface MUST render the
most recent memory events as a quiet list (mono time · ButlerMark · sans
summary), with no color, no type badges, and no card chrome. It MUST default to
20 rows and MUST NOT render a decorative vertical-line-with-dots timeline.

#### Scenario: Write-up overdue row has no run-now affordance

- **WHEN** the rail renders the "write-up overdue" row
- **THEN** the row MUST NOT contain a "run consolidation now" button or any
  control that triggers a consolidation run

#### Scenario: Empty rail collapses to a serif line

- **WHEN** no rail condition holds
- **THEN** the rail header MUST remain and the body MUST read "Nothing waiting."
  in serif italic

---

### Requirement: MemoryBrowser is the /memory house-ledger registers host

`MemoryBrowser` MUST be the Band-3 left-column registers host of the redesigned
`/memory` page: it MUST compose the single unified-search affordance
(`MemorySearch`), the Facts/Rules/Episodes register pills, and the focused
register (`FactsRegister` / `RulesRegister` / `EpisodesRegister`) in browse
mode, or the grouped `SearchResults` in results mode. The new `/memory` page
MUST render `MemoryBrowser` as that column. (The old tabbed/card/badge browser
chrome described by the MODIFIED `Requirement: Memory browser with tabbed tier
navigation` is fully retired inside this rewritten component.)

`MemoryBrowser` retains an optional `butlerScope` prop that filters all of its
register queries to a single butler, so a future change MAY mount the
house-ledger registers (via `MemoryBrowser` with `butlerScope`) on
`ButlerMemoryTab`. That migration is out of scope for this redesign.

`ButlerMemoryTab` on butler detail pages is self-contained: it does NOT depend
on `MemoryBrowser` or any `components/memory/*` module, drawing instead from its
own per-butler hooks (`useButlerMemoryStats`, `useMemoryRecentWrites`). This
keeps the butler-scoped tab decoupled, so restyling or relocating
`MemoryBrowser` cannot silently break it.

#### Scenario: /memory renders MemoryBrowser as the registers host

- **WHEN** the redesigned `/memory` page renders Band 3
- **THEN** it MUST render `MemoryBrowser` as the left-column registers host
  (single search affordance + register pills + focused register / results)

#### Scenario: ButlerMemoryTab is decoupled from MemoryBrowser

- **WHEN** a butler detail page renders its memory tab
- **THEN** `ButlerMemoryTab` MUST source its data from its own per-butler hooks
  and MUST NOT import `MemoryBrowser` or any `components/memory/*` module
- **AND** restyling or moving `MemoryBrowser` MUST NOT break the butler tab

---

### Requirement: Memory hooks (house-ledger)

The redesigned memory domain MUST use TanStack Query hooks for stats, the three
registers, the unified search, recent activity, and the three detail records.
Register and stats queries MUST be parameterised by the URL state
(`register` / `q` / `kind` / `validity` / `status` / `offset`). The fact detail
mutations MUST be exposed as `useConfirmFact()` and `useRetractFact()` hooks
that invalidate the affected fact and stats query keys on success, and these
hooks MUST only render their corresponding commit pills when the backend
confirm/retract endpoints are present (no dead buttons).

#### Scenario: Confirm/Retract gated on backend

- **WHEN** the confirm and retract endpoints are unavailable
- **THEN** the fact detail page MUST NOT render the Confirm or Retract commit
  pill (rather than rendering a non-functional button)

---

### Requirement: Fact detail page

The dashboard SHALL render a Fact detail page at `/memory/facts/:factId` using
the editorial detail skeleton (eyebrow / content-as-heading / state line / KV
band / kind section / provenance / commit footer), not a card-and-badge stack.

The page MUST display breadcrumb navigation: Memory > Facts > {subject}.

The page MUST display:

- **Heading region:** a mono eyebrow ("FACT"), the fact `content` rendered as
  the editorial heading, the `subject` and `predicate` as supporting identity
  (subject links to `/entities/:id` when entity-anchored).
- **Belief state line:** one mono line stating the decay arithmetic honestly —
  `confidence <raw> · decays <decay_rate>/day · last confirmed <relative> ·
  effective <effective>` — plus the two-letter permanence tag, the validity,
  and the scope. Confidence and effective confidence MUST be mono numerals
  (never bars); a fading fact's state line dims to `--dim`. There MUST be no
  confidence progress bar and no colored permanence/validity/scope word badges.
- **Provenance:** Source butler (when present), Source episode (a link to
  `/memory/episodes/{source_episode_id}` when present), Supersedes (a link to
  `/memory/facts/{supersedes_id}` when present), and Superseded-by (a link to
  `/memory/facts/{superseded_by}` when the reverse lookup returns one). When the
  fact has no provenance at all, the provenance section AND its eyebrow MUST be
  omitted (no empty section).
- **KV band:** Reference count, tags, and metadata (metadata as a mono code
  block when non-empty) and timestamps (Created at, Last referenced at, Last
  confirmed at).
- **Commit footer:** a primary **Confirm** pill (the sole commit-class action)
  and a secondary **Retract** pill, each with a 5s pill-morph confirm, **gated
  on the backend `confirm` / `retract` endpoints** — when an endpoint is
  absent the corresponding pill MUST NOT render (never a dead button).

The page MUST delegate loading and error states to the detail-page shell.

#### Scenario: Fact decay arithmetic line

- **WHEN** a fact has confidence 0.94, decay_rate 0.002, was last confirmed 12
  days ago, and has effective confidence 0.92
- **THEN** the belief state line MUST read "confidence 0.94 · decays 0.002/day ·
  last confirmed 12d ago · effective 0.92" in a mono line
- **AND** it MUST NOT render a confidence progress bar

#### Scenario: Fact with source episode link

- **WHEN** a fact has `source_episode_id` set
- **THEN** the provenance section MUST render a clickable link to
  `/memory/episodes/{source_episode_id}`

#### Scenario: Fact superseded-by reverse link

- **WHEN** `GET /api/memory/facts/:id` returns a non-null `superseded_by`
- **THEN** the provenance section MUST render a "Superseded by" link to
  `/memory/facts/{superseded_by}`

#### Scenario: Empty provenance omits its eyebrow

- **WHEN** a fact has no source butler, no source episode, no supersedes, and no
  superseded-by
- **THEN** the provenance section AND its eyebrow MUST both be omitted

#### Scenario: Confirm/Retract pills gated on backend

- **WHEN** the `POST /api/memory/facts/:id/confirm` endpoint is available
- **THEN** the Confirm commit pill MUST render and dispatch to it on confirm
- **WHEN** the endpoint is unavailable
- **THEN** the Confirm pill MUST NOT render

---

### Requirement: Fact detail page conforms to the detail-page archetype

The Fact detail page at `/memory/facts/:factId` SHALL conform to the detail-page
archetype defined in the `detail-page-archetype` spec.

**Changes from the existing requirement (§Requirement: Fact detail page):**

1. **Shell adoption.** The page MUST use `<Page archetype="detail">` as its outer
   shell. The existing `breadcrumbs`, `isLoading`, and `error` handling MUST be
   delegated to the shell's `breadcrumbs`, `loading`, and `error` props respectively.
   The inline three-skeleton loading block and the inline destructive-text error block
   MUST be removed from the page body.

2. **Title.** The `title` prop on `<Page>` MUST be the fact's `subject` field (its
   record identity). The existing `<CardTitle>` is the correct source; it MUST be
   lifted to the `title` prop. (This was already specified as "Subject as page title"
   in the header sub-requirement — this requirement formalises the mechanism.)

3. **Subtitle.** The `description` prop on `<Page>` MUST carry the fact's `predicate`
   field, rendered as a plain-text subtitle below the H1.

4. **Body layout.** The existing card sections (Content, Status row, Metrics,
   Provenance, Tags, Metadata, Timestamps) become the `primary` body slot inside the
   shell.

#### Scenario: Fact detail page uses shell loading state

- **WHEN** `GET /api/memory/facts/:id` is in flight
- **THEN** the `<Page>` shell MUST show the `DetailSkeleton` (card + two block skeletons)
- **AND** the page MUST NOT render inline `<Skeleton>` blocks outside the shell
- **AND** breadcrumbs MUST still be visible during the loading state

#### Scenario: Fact detail page uses shell error state

- **WHEN** `GET /api/memory/facts/:id` fails
- **THEN** the `<Page>` shell MUST render the destructive error card
- **AND** the page MUST NOT render an inline `text-destructive text-center` block

#### Scenario: Fact detail page title shows subject

- **WHEN** a fact has `subject = "Tze"` and `predicate = "preferred contact channel"`
- **THEN** the `<h1>` MUST read "Tze"
- **AND** the subtitle line below the H1 MUST read "preferred contact channel"

---

### Requirement: Rule detail page

The dashboard SHALL render a Rule detail page at `/memory/rules/:ruleId` using
the editorial detail skeleton, not a card-and-badge stack.

The page MUST display breadcrumb navigation: Memory > Rules > Rule.

The page MUST display:

- **Heading region:** a mono eyebrow ("RULE"), the rule `content` rendered as
  the editorial heading (this is the record identity; "Rule" MUST NOT be used as
  the title).
- **State line:** maturity as a lowercase mono word, scope, and permanence as a
  two-letter mono tag, in one line; no colored maturity/scope/permanence word
  badges. Anti-pattern rules MUST carry the `--red` left sliver and the `harmful`
  tally fragment in `--red`.
- **Outcome record:** a mono tally `applied N · helpful N · harmful N`, the
  effectiveness as a mono numeral (never a progress bar), and the confidence /
  decay arithmetic line as on the fact page.
- **Provenance:** Source butler and Source episode (link to
  `/memory/episodes/{id}`) when present; the section and its eyebrow MUST be
  omitted when no provenance exists.
- **KV band:** tags, metadata (mono code block when non-empty), and timestamps
  (Created at, Last applied at, Last evaluated at).

The page MUST delegate loading and error states to the detail-page shell.

#### Scenario: Rule detail page title shows content summary

- **WHEN** a rule has `content = "Always acknowledge messages within 24 hours of receipt"`
- **THEN** the editorial heading MUST read that content
- **AND** it MUST NOT read "Rule"

#### Scenario: Rule effectiveness renders as a numeral

- **WHEN** a rule has an effectiveness score
- **THEN** the outcome record MUST render it as a mono numeral
- **AND** it MUST NOT render an effectiveness progress bar

---

### Requirement: Rule detail page conforms to the detail-page archetype

The Rule detail page at `/memory/rules/:ruleId` SHALL conform to the detail-page
archetype defined in the `detail-page-archetype` spec.

**Changes from the existing requirement (§Requirement: Rule detail page):**

1. **Shell adoption.** Same as Fact: inline L/E blocks delegated to `<Page>` props.

2. **Title — record-identity correction.** The existing requirement specifies
   `"Rule" as page title`. This violates the archetype's record-identity requirement
   (detail-page-archetype spec §Requirement: Detail-page title is record-identity).
   The `title` prop on `<Page>` MUST be the first 80 characters of `rule.content`,
   truncated with an ellipsis (`…`) if the content exceeds 80 characters.
   `"Rule"` as a title is explicitly disallowed.

3. **Subtitle.** The `description` prop on `<Page>` MUST carry a `Maturity: {badge}`
   status summary or be omitted. The Maturity badge itself belongs in the `status`
   prop (see point 4).

4. **Status pills.** The Maturity badge MUST be passed via the `status` prop so it
   appears adjacent to the title row rather than inside `<CardContent>`. This requires
   adding a `status?: React.ReactNode` slot to `PageProps` (see detail-page-archetype
   spec §Requirement: Status pills on the title row — implementation note).

5. **Body layout.** The existing card sections (Content, Status row, Effectiveness,
   Confidence, Provenance, Tags, Metadata, Timestamps) become the `primary` body slot.

#### Scenario: Rule detail page title shows content summary

- **WHEN** a rule has `content = "Always acknowledge messages within 24 hours of receipt"`
- **THEN** the `<h1>` MUST read "Always acknowledge messages within 24 hours of receipt"
- **AND** it MUST NOT read "Rule"

#### Scenario: Rule content truncated to 80 chars

- **WHEN** a rule has content longer than 80 characters
- **THEN** the `<h1>` MUST show the first 80 characters followed by "…"

#### Scenario: Rule detail page uses shell loading state

- **WHEN** `GET /api/memory/rules/:id` is in flight
- **THEN** the `<Page>` shell MUST show `DetailSkeleton`
- **AND** no inline skeleton blocks MUST be rendered by the page

#### Scenario: Rule detail page uses shell error state

- **WHEN** `GET /api/memory/rules/:id` fails
- **THEN** the `<Page>` shell MUST render the destructive error card
- **AND** no inline destructive-text error block MUST be rendered by the page

---

### Requirement: Episode detail page

The dashboard SHALL render an Episode detail page at `/memory/episodes/:episodeId`
using the editorial detail skeleton, not a card-and-badge stack.

The page MUST display breadcrumb navigation: Memory > Episodes > Episode.

The page MUST display:

- **Heading region:** a mono eyebrow ("EPISODE"), the episode `content` rendered
  as the editorial heading; the record-identity subtitle is the `session_id`
  when present, otherwise `Episode {id.slice(0,8)}`. A butler letter-mark
  (ButlerMark) carries the only butler hue on the page.
- **State line:** a single consolidation glyph `{◦ • ✕}` (never a word badge),
  the importance conveyed by ink weight (importance ≥ 8 in `--fg`), and the
  reference count.
- **Derived facts:** a list of facts whose `source_episode_id` equals this
  episode (fetched via the facts `source_episode_id` filter), each linking to
  `/memory/facts/{id}`; the section and its eyebrow MUST be omitted when empty.
- **KV band:** Session ID, Expires at (when present), metadata (mono code block
  when non-empty), and timestamps (Created at, Last referenced at).

The page MUST delegate loading and error states to the detail-page shell.

#### Scenario: Episode consolidation state is a glyph, not a badge

- **WHEN** an episode is consolidated
- **THEN** the state line MUST render the `•` glyph
- **AND** it MUST NOT render a "Consolidated" word badge or colored chip

#### Scenario: Episode shows its derived facts

- **WHEN** facts exist with `source_episode_id` equal to this episode's id
- **THEN** the page MUST list those facts, each linking to `/memory/facts/{id}`
- **WHEN** no such facts exist
- **THEN** the derived-facts section AND its eyebrow MUST be omitted

---

### Requirement: Episode detail page conforms to the detail-page archetype

The Episode detail page at `/memory/episodes/:episodeId` SHALL conform to the
detail-page archetype defined in the `detail-page-archetype` spec.

**Changes from the existing requirement (§Requirement: Episode detail page):**

1. **Shell adoption.** Inline L/E blocks delegated to `<Page>` props.

2. **Title — record-identity correction.** The existing requirement specifies
   `"Episode" as page title`. This violates the archetype's record-identity requirement.
   The `title` prop on `<Page>` MUST be:
   - `episode.session_id` if the field is non-null; OR
   - `"Episode {episode.id.slice(0, 8)}"` if `session_id` is null.
   `"Episode"` as a standalone title is explicitly disallowed.

3. **Subtitle.** The butler name (as a plain string, not a badge) MUST be passed as
   the `description` prop on `<Page>` so it appears below the H1. The butler badge
   rendered in the body card is supplemental, not a replacement.

4. **Body layout.** The existing card sections (Content, Status row, Details, Metadata,
   Timestamps) become the `primary` body slot.

#### Scenario: Episode detail page title shows session ID

- **WHEN** an episode has `session_id = "sess-abc123def456"`
- **THEN** the `<h1>` MUST read "sess-abc123def456"
- **AND** it MUST NOT read "Episode"

#### Scenario: Episode detail page title falls back to ID prefix

- **WHEN** an episode has `session_id = null` and `id = "ep-12345678-abcd-..."`
- **THEN** the `<h1>` MUST read "Episode ep-12345" (`id.slice(0, 8)` prepended with "Episode ")

#### Scenario: Episode detail page uses shell loading state

- **WHEN** `GET /api/memory/episodes/:id` is in flight
- **THEN** the `<Page>` shell MUST show `DetailSkeleton`
- **AND** no inline skeleton blocks MUST be rendered by the page

---

### Requirement: Memory hooks

The memory domain MUST use the following TanStack Query hooks:

| Hook | Query Key | Auto-Refresh | Conditional |
|---|---|---|---|
| `useMemoryStats()` | `memory-stats` | 30s | No |
| `useEpisodes(params)` | `memory-episodes` | 30s | No |
| `useFacts(params)` | `memory-facts` | 30s | No |
| `useFact(id)` | `memory-fact` | None | `enabled: !!factId` |
| `useRules(params)` | `memory-rules` | 30s | No |
| `useRule(id)` | `memory-rule` | None | `enabled: !!ruleId` |
| `useEpisode(id)` | `memory-episode` | None | `enabled: !!episodeId` |
| `useMemoryActivity(limit)` | `memory-activity` | 15s | No |

---

### Requirement: Costs page with summary stats and chart

The dashboard SHALL render a Costs page at `/costs` displaying LLM usage costs with summary statistics, a time-series chart, and a per-butler breakdown.

The page MUST contain:
- A heading "Costs & Usage".
- A 4-column stats grid (responsive: 2 on `sm`, 4 on `lg`): Total Cost (USD formatted), Total Sessions (count), Input Tokens (abbreviated: K/M), Output Tokens (abbreviated). Loading state MUST show pulsing skeleton placeholders.
- A 3-column grid (responsive): cost chart spanning 2 columns, breakdown table in the remaining column.

Token abbreviation rules: >= 1M shows "X.YM", >= 1K shows "X.YK", otherwise raw number. Cost formatting: amounts < $0.01 display as "$0.00", otherwise "$X.XX".

#### Scenario: Cost summary with large numbers

- **WHEN** total input tokens are 2,500,000 and output tokens are 150,000
- **THEN** the Input Tokens stat MUST display "2.5M"
- **AND** the Output Tokens stat MUST display "150.0K"

---

### Requirement: Cost area chart with period selector

The cost chart MUST render an area chart using Recharts `AreaChart` with:
- A gradient fill from primary color (30% opacity at top, 0% at bottom).
- X-axis formatted as "MMM d" (short month + day number).
- Y-axis formatted as "$X.XX".
- A tooltip showing cost formatted as currency and date formatted as "MMM d".
- Period selector buttons: "7 days", "30 days", "90 days". The active period MUST use `secondary` variant; inactive periods use `ghost`.
- Height of 256px in a `ResponsiveContainer`.
- Empty state: "No cost data available" centered text when no data points exist.

#### Scenario: Period change re-fetches summary

- **WHEN** the user switches from "7 days" to "30 days"
- **THEN** the cost summary MUST be re-fetched with the new period
- **AND** the chart MUST update to show 30 days of data

---

### Requirement: Cost breakdown table by butler

The dashboard MUST display a "Cost by Butler" table showing each butler's cost contribution. The table MUST display columns: Butler (name, bold), Cost (right-aligned, tabular-nums), % of Total (right-aligned), and a visual proportion bar (progress bar within a muted track).

Rows MUST be sorted by cost descending. Percentage formatting: values < 0.1% display as "<0.1%", otherwise "X.Y%".

#### Scenario: Single dominant butler

- **WHEN** the health butler accounts for $8.50 of $10.00 total cost
- **THEN** the health butler row MUST show "$8.50", "85.0%", and an 85%-width progress bar
- **AND** it MUST be the first row in the table

---

### Requirement: Cost widget for dashboard overview

The dashboard MUST provide a `CostWidget` component for embedding on the overview page. The widget MUST display:
- Title "Cost Today" with a "View all" link to `/costs`.
- Total cost for the day formatted as currency.
- Top butler name and cost (e.g., "Top: health ($3.50)").
- A 7-bar sparkline placeholder showing a mock 7-day trend (pending replacement with Recharts).

#### Scenario: Widget with no data

- **WHEN** `totalCostUsd` is 0 and `topButler` is null
- **THEN** the widget MUST display "$0.00" and no top-butler line

---

### Requirement: Top sessions table

The dashboard MUST provide a `TopSessionsTable` component displaying the most expensive LLM sessions. The table MUST display columns: rank number (#), Butler (secondary badge), Model (muted text), Tokens (input/output formatted as abbreviated counts separated by "/"), Cost (right-aligned, bold, tabular-nums), Time (right-aligned, formatted as "MMM d, HH:mm").

#### Scenario: Session token display

- **WHEN** a session has 50,000 input tokens and 12,000 output tokens
- **THEN** the Tokens column MUST display "50.0K / 12.0K"

---

### Requirement: Cost hooks with 60-second refresh

The costs domain MUST use the following TanStack Query hooks:

| Hook | Query Key | Auto-Refresh |
|---|---|---|
| `useSpendSummary(period)` | `cost-summary` | 60s |
| `useDailySpend()` | `daily-costs` | 60s |
| `useTopSessions(limit)` | `top-sessions` | 60s |

---

### Requirement: Cross-butler global search

The dashboard MUST provide a debounced global search hook (`useSearch`) that queries across all butlers.

The hook MUST:
- Accept a query string and optional `limit` (default 20).
- Debounce input by 300ms before firing the API call.
- Only enable the query when the debounced input is >= 2 characters.
- Return results grouped by category (sessions, state, and dynamic butler-specific categories).

The search results MUST conform to the `SearchResults` interface: an object keyed by category name, where each value is an array of `SearchResult` objects containing `id`, `butler`, `type`, `title`, and `snippet`.

#### Scenario: Short query suppressed

- **WHEN** the user types a single character "a"
- **THEN** the search query MUST NOT fire (enabled = false)
- **WHEN** the user types "ab"
- **THEN** the search query MUST fire after a 300ms debounce

#### Scenario: Search spans multiple butlers

- **WHEN** the user searches "headache"
- **THEN** the results MAY include: health butler symptom records, relationship butler interaction notes mentioning headache, and memory facts containing "headache"
- **AND** each result MUST include the originating `butler` name

---

### Requirement: Consistent pagination pattern

All paginated domain pages MUST follow a consistent pagination pattern:
- Page size constant (typically 50 for list pages, 20 for memory browser).
- `offset`/`limit` query parameters passed to the API.
- Previous/Next buttons with disabled states (Previous disabled at page 0, Next disabled when `has_more` is false or calculated from total).
- A "Showing X-Y of Z" text indicator.
- Changing any filter parameter MUST reset the page to 0.

---

### Requirement: Consistent loading and empty states

All domain pages MUST implement:
- **Loading state**: Skeleton rows matching the table column count, or skeleton cards matching the card grid layout. Skeletons MUST use the `Skeleton` component with appropriate widths.
- **Empty state**: An `EmptyState` component with a title and description explaining where the data comes from (e.g., "No conditions found" / "Health conditions will appear here as they are tracked by the Health butler."). Empty states MUST only render when `isLoading` is false and the data array is empty.
- Loading and empty states MUST be mutually exclusive: skeletons during loading, empty state after loading completes with zero results.

---

### Requirement: Auto-refresh intervals by domain

Data freshness MUST follow domain-appropriate refresh intervals:

| Domain | Interval | Rationale |
|---|---|---|
| Health data — deterministic (measurements, medications, conditions, symptoms, meals, research, latest, trend, adherence, nutrition summary) | 30s | Moderate update frequency from butler sessions |
| Health Overview — LLM Voice briefing (`/api/health/briefing`) | None (5-min TTL cache + manual refresh) | LLM endpoint; auto-refresh would multiply spawn cost |
| Health Overview — insight feed (`/api/switchboard/insights?butler=health`) | None (manual refresh) | Reads candidates produced by the scheduled insight-scan job; no per-pageview cost |
| Contact data (contacts, groups, labels) | None (on-demand) | Data changes infrequently; triggered by explicit sync |
| Calendar workspace entries | 30s | Events may change from external calendar providers |
| Calendar workspace metadata | 60s | Source/lane definitions change rarely |
| Memory stats, episodes, facts, rules | 30s | Memory consolidation runs periodically |
| Memory recent activity (rail) | 15s | Fastest-updating view for real-time monitoring |
| Cost summary and daily costs | 60s | Cost data accrues session-by-session |

#### Scenario: LLM Overview endpoints are not on the 30s interval

- **WHEN** the Health Overview renders its Voice briefing and insight feed
- **THEN** neither MUST be polled on the 30s health-data interval
- **AND** the briefing MUST be served from its 5-minute TTL cache between manual refreshes

#### Scenario: Deterministic health data stays on 30s

- **WHEN** any deterministic health list/KPI/trend hook renders
- **THEN** it MUST refresh on the 30s interval

---

## Source References

- `frontend/src/index.css` — `--severity-low`, `--severity-medium`,
  `--severity-high` token definitions (lines 67–69); `--category-1` through
  `--category-8` definitions (lines 78–85). Both sets are also aliased into
  Tailwind via `--color-severity-*` and `--color-category-*` (lines 263–281).
- Epic bu-v1tt2 (Vertical C) — token system migration that introduced the named
  CSS tokens; this spec change closes the remaining spec-code drift.
- `about/heart-and-soul/design-language.md` — token exemption for `--chart-*`
  palette (chart axis/line tokens are a separate axis; not replaced here).
