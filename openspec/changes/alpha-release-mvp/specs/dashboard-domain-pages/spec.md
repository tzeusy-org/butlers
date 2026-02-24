# Dashboard Domain Pages

## Purpose

The Butlers dashboard exposes domain-specific pages that surface data managed by individual butlers through read-only (and occasionally mutating) views. These pages turn raw butler data into actionable surfaces: health measurements become trend charts, contacts become an identity-aware CRM, calendar entries merge into a unified workspace, memory tiers become inspectable knowledge graphs, and session costs become budget visibility.

This spec codifies the requirements for six domain page groups: Health, Relationship/Contacts, General/Entities, Calendar, Memory, and Costs -- plus the cross-butler global search that ties them together.

---

## ADDED Requirements

### Requirement: Health measurements page with trend charting

The dashboard SHALL render a Measurements page at `/measurements` that displays health measurement data as interactive line charts with a supporting raw-data table.

The page MUST contain:
- A type selector displaying badge tabs for measurement types: `weight`, `blood_pressure`, `heart_rate`, `glucose`, `temperature`, `sleep`, `oxygen`. Clicking a type SHALL filter the chart and table to that type.
- Date range filters (`since`/`until`) using date inputs, with a Clear button when any filter is active.
- A Recharts `LineChart` in a `ResponsiveContainer` (height 288px). For `blood_pressure`, the chart MUST render two lines (systolic in blue `#3b82f6`, diastolic in rose `#f43f5e`). For all other types, a single blue line plotting the primary numeric value.
- A "Show/Hide raw data" toggle button. When expanded, the raw data table MUST display columns: Date, Type, Value (formatted compound JSONB), Notes.
- Measurement values are compound JSONB objects (e.g., `{"systolic": 120, "diastolic": 80}`). The chart MUST extract numeric values from these objects using key-aware parsing. The table MUST format them as `key: value` pairs.

#### Scenario: Blood pressure dual-line chart

- **WHEN** the user selects `blood_pressure` as the measurement type
- **AND** there are measurements with `value` containing `systolic` and `diastolic` keys
- **THEN** the chart MUST render two lines: systolic (blue) and diastolic (rose)
- **AND** the chart tooltip MUST label them "Systolic" and "Diastolic"

#### Scenario: Date range filtering resets chart

- **WHEN** the user sets a `since` date of "2025-01-01" and an `until` date of "2025-06-01"
- **THEN** the API query MUST include both date parameters
- **AND** only measurements within the range MUST appear in the chart and table
- **AND** up to 500 measurements MUST be fetched for charting

#### Scenario: Empty state for unfamiliar type

- **WHEN** the user selects a measurement type with zero records in the selected date range
- **THEN** the page MUST display "No measurements found for this type and date range."

---

### Requirement: Medications page with adherence tracking

The dashboard SHALL render a Medications page at `/medications` displaying active and inactive medications as a card grid, each card expandable to show a dose history log with adherence calculation.

The page MUST contain:
- Active/All filter toggle buttons at the top. When "Active" is selected, the API query MUST include `active=true`.
- A responsive card grid (1 column on mobile, 2 on `sm`, 3 on `lg`). Each card MUST display: medication name, active/inactive badge, dosage description, frequency, and optional notes.
- Clicking a medication card SHALL expand it to show a `DoseLog` sub-component for that medication.
- The DoseLog MUST display: an adherence progress bar (green >= 80%, amber >= 50%, red < 50%), a textual summary ("X of Y doses taken"), and a table of the 20 most recent doses with columns: Date, Status (Taken/Skipped badge), Notes.

#### Scenario: Adherence calculation

- **WHEN** a medication has 18 taken doses and 2 skipped doses out of 20 total
- **THEN** the adherence percentage MUST display "90%"
- **AND** the progress bar MUST be green (>= 80%)

#### Scenario: Inactive medications hidden by default

- **WHEN** the page loads with the "Active" filter selected
- **THEN** only medications with `active = true` MUST be fetched
- **AND** switching to "All" MUST fetch all medications regardless of active status

---

### Requirement: Conditions page with status badges

The dashboard SHALL render a Conditions page at `/conditions` displaying health conditions in a paginated table.

The table MUST display columns: Name (bold), Status (badge with color coding), Diagnosed (date formatted as "MMM d, yyyy"), Notes (truncated to max-width), Updated (date formatted).

Status badge colors MUST follow: `active` = green, `resolved` = gray, `managed` = amber.

Pagination MUST use a page size of 50 with Previous/Next buttons and a "Showing X-Y of Z" indicator.

#### Scenario: Empty conditions table

- **WHEN** no conditions exist in the database
- **THEN** the page MUST display an empty state: "No conditions found" with description "Health conditions will appear here as they are tracked by the Health butler."

---

### Requirement: Symptoms page with severity visualization

The dashboard SHALL render a Symptoms page at `/symptoms` displaying symptom entries in a paginated, filterable table.

The page MUST contain:
- Filter controls: name text input, date range (From/To date inputs), Clear button when any filter is active. Changing any filter MUST reset pagination to page 0.
- A table with columns: Name (bold), Severity (progress bar + numeric "X/10"), Occurred (datetime formatted), Notes (truncated), Condition (badge linking to condition_id or em-dash).
- Severity visualization MUST use a 16px-wide progress bar: green for 1-3, amber for 4-6, red for 7-10.

#### Scenario: Severity color mapping

- **WHEN** a symptom has severity 2
- **THEN** the severity bar MUST be green (#22c55e) at 20% width
- **WHEN** a symptom has severity 8
- **THEN** the severity bar MUST be red (#ef4444) at 80% width

---

### Requirement: Meals page with day-grouped display

The dashboard SHALL render a Meals page at `/meals` displaying meals grouped by date with meal-type filtering.

The page MUST contain:
- Meal-type badge toggle filters: All, breakfast, lunch, dinner, snack. Selecting a type SHALL filter to that type; re-selecting SHALL deselect (show all).
- Date range filters (From/To) and a Clear button.
- Meals grouped by date. Each date group MUST display a heading formatted as "EEEE, MMMM d, yyyy" (e.g., "Monday, January 15, 2025") followed by a table with columns: Type (badge), Description (truncated), Nutrition (formatted JSONB as "key: value" pairs), Time (HH:mm), Notes.
- Nutrition JSONB formatting: if `null`, display em-dash; otherwise iterate entries as "key: value" comma-separated.

#### Scenario: Day grouping preserves chronological order

- **WHEN** meals exist on 3 different dates
- **THEN** date groups MUST appear in chronological order (most recent date determined by sort)
- **AND** each group's table MUST contain only meals from that date

---

### Requirement: Research page with expandable content

The dashboard SHALL render a Research page at `/research` displaying health research notes in a paginated table with text search, tag filtering, and expandable content rows.

The page MUST contain:
- Search input (placeholder "Search research...") and tag filter badges. Tags MUST be derived from the current result set. Selecting a tag SHALL filter; "All tags" resets.
- A table with columns: Title (bold, clickable row), Tags (badge list), Source (clickable link or em-dash), Updated (date).
- Clicking a row SHALL toggle an expanded content row below it that displays the note's full `content` in a prose block with `whitespace-pre-wrap`.
- Source URLs MUST open in a new tab (`target="_blank"`, `rel="noopener noreferrer"`).

#### Scenario: Expand and collapse research note

- **WHEN** the user clicks a research note row with title "Blood pressure study"
- **THEN** a new row MUST appear below it spanning all 4 columns displaying the full content
- **WHEN** the user clicks the same row again
- **THEN** the expanded row MUST collapse

---

### Requirement: Health data hooks with auto-refresh

All health domain pages MUST use TanStack Query hooks that auto-refresh data every 30 seconds (`refetchInterval: 30_000`). The following hooks MUST be provided:

| Hook | Query Key Prefix | API Function |
|---|---|---|
| `useMeasurements(params)` | `health-measurements` | `getMeasurements` |
| `useMedications(params)` | `health-medications` | `getMedications` |
| `useMedicationDoses(id, params)` | `health-medication-doses` | `getMedicationDoses` |
| `useConditions(params)` | `health-conditions` | `getConditions` |
| `useSymptoms(params)` | `health-symptoms` | `getSymptoms` |
| `useMeals(params)` | `health-meals` | `getMeals` |
| `useResearch(params)` | `health-research` | `getResearch` |

The `useMedicationDoses` hook MUST be conditionally enabled (`enabled: !!medicationId`) since it depends on a selected medication.

---

### Requirement: Contacts page with search, label filtering, and Google sync

The dashboard SHALL render a Contacts page at `/contacts` displaying contacts in a searchable, label-filterable, paginated table with a Google sync action.

The page MUST contain:
- A heading area with title "Contacts", description, and a "Sync From Google" button that triggers incremental Google Contacts sync. The button MUST be disabled while syncing and display "Syncing..." during the operation.
- On successful sync, a toast MUST display the sync summary (created, updated, skipped, errors counts). On failure, a toast MUST display the error message.
- A `ContactTable` component with: search input (placeholder "Search contacts..."), label filter badges (All + one per label, with deterministic hash-based coloring for labels without explicit colors), and a table with columns: Name (with optional nickname in parentheses), Email, Phone, Labels (colored badges), Last Interaction (relative time via `formatDistanceToNow`).
- Each contact row MUST be clickable, navigating to `/contacts/:id`.
- Pagination with page size 50.

#### Scenario: Label color determinism

- **WHEN** a label named "family" has no explicit `color` set
- **THEN** its badge color MUST be deterministically derived from a hash of "family" using the palette: `#3b82f6, #8b5cf6, #f59e0b, #14b8a6, #f43f5e, #6366f1, #06b6d4, #f97316`
- **AND** the same label MUST always render with the same color

#### Scenario: Google sync with mixed results

- **WHEN** the user clicks "Sync From Google" and the sync returns `{created: 5, updated: 12, skipped: 3, errors: 1}`
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

#### Scenario: Group with no labels

- **WHEN** a group has zero associated labels
- **THEN** the Labels column MUST display an em-dash

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
- A header with title "Calendar Workspace", timezone badge, entry count badge, "Sync now" button, and context-appropriate create buttons ("Create Event" in user view, "Create Butler Event" in butler view).
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

### Requirement: Memory page layout

The dashboard SHALL render a Memory page at `/memory` with a three-section layout:
1. **Tier cards row** (full width) -- three summary cards for Episodes, Facts, and Rules.
2. **Memory browser** (left column, flex-1) -- tabbed table browser for all memory tiers.
3. **Activity timeline** (right column, 350px fixed width) -- recent memory events.

The layout MUST use a responsive grid: single column on small screens, two-column (`[1fr_350px]`) on `lg` and above.

---

### Requirement: Memory tier cards with health indicators

The Memory page MUST display three tier overview cards, one for each memory tier:

1. **Episodes (Eden tier)** -- Title "Episodes" with description "Eden tier -- raw session memories". Stats: Total, Unconsolidated (with percentage), Consolidated (with percentage). Health indicator: ratio of consolidated episodes to total episodes.

2. **Facts (Mid-term tier)** -- Title "Facts" with description "Mid-term tier -- consolidated knowledge". Stats: Total, Active (with percentage), Fading (with percentage). Health indicator: ratio of active facts to total facts.

3. **Rules (Long-term tier)** -- Title "Rules" with description "Long-term tier -- behavioral patterns". Stats: Total, Candidate (with percentage), Established (with percentage), Proven (with percentage), Anti-pattern (with percentage). Health indicator: ratio of (established + proven) rules to total rules.

Each card MUST display a health badge: "Healthy" (green, ratio >= 0.8), "Warning" (amber outline, ratio >= 0.5), or "Needs attention" (destructive, ratio < 0.5).

#### Scenario: Healthy episodes tier

- **WHEN** there are 100 total episodes and 85 are consolidated
- **THEN** the health badge MUST display "Healthy" (green)
- **AND** the Unconsolidated stat MUST show "15 (15%)"

---

### Requirement: Memory browser with tabbed tier navigation

The Memory Browser MUST display a tabbed interface with three tabs: Facts, Rules, Episodes.

**Facts tab**: Search input, paginated table (page size 20) with columns: Subject, Predicate, Content (truncated to 80 chars), Confidence (progress bar with percentage), Permanence (color-coded badge: permanent=blue, stable=sky, standard=secondary, volatile=amber outline, ephemeral=red outline), Validity (badge: active=green, fading=amber outline, superseded=secondary, expired=destructive), Scope (outline badge).

**Rules tab**: Search input, paginated table with columns: Content (truncated), Maturity (badge: proven=green, established=sky, candidate=secondary, anti_pattern=destructive), Effectiveness (percentage), Applied (count), Scope (outline badge).

**Episodes tab**: Paginated table (no search) with columns: Content (truncated with Expand/Collapse link), Butler (outline badge), Importance (1 decimal), Consolidated (Yes=green badge / No=secondary badge), Created (locale datetime). Expanding an episode MUST show full content in a muted background row spanning all columns.

The Memory Browser MUST accept an optional `butlerScope` prop that filters all queries to a specific butler.

#### Scenario: Fact confidence bar rendering

- **WHEN** a fact has confidence 0.73
- **THEN** the progress bar MUST be 73% filled
- **AND** the label MUST display "73%"

---

### Requirement: Memory activity timeline

The Memory page MUST display a vertical timeline of recent memory events in a right-sidebar card.

Each timeline entry MUST display:
- A type badge: Episode (secondary), Fact (sky-blue), Rule (violet).
- An optional butler badge (outline) if the event is scoped to a specific butler.
- A summary text (truncated).
- A timestamp formatted via `toLocaleString`.

The timeline MUST:
- Render a vertical line on the left with dot indicators at each event.
- Default to fetching the 30 most recent events.
- Auto-refresh every 15 seconds.
- Display "No recent activity." when empty.

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
| `useCostSummary(period)` | `cost-summary` | 60s |
| `useDailyCosts()` | `daily-costs` | 60s |
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
| Health data (measurements, medications, conditions, symptoms, meals, research) | 30s | Moderate update frequency from butler sessions |
| Contact data (contacts, groups, labels) | None (on-demand) | Data changes infrequently; triggered by explicit sync |
| Calendar workspace entries | 30s | Events may change from external calendar providers |
| Calendar workspace metadata | 60s | Source/lane definitions change rarely |
| Memory stats, episodes, facts, rules | 30s | Memory consolidation runs periodically |
| Memory activity timeline | 15s | Fastest-updating view for real-time monitoring |
| Cost summary and daily costs | 60s | Cost data accrues session-by-session |
