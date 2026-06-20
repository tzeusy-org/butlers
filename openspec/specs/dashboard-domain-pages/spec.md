# Dashboard Domain Pages

## Purpose

The Butlers dashboard exposes domain-specific pages that surface data managed by individual butlers through read-only (and occasionally mutating) views. These pages turn raw butler data into actionable surfaces: health measurements become trend charts, contacts become an identity-aware CRM, calendar entries merge into a unified workspace, memory tiers become inspectable knowledge graphs, and session costs become budget visibility.

This spec codifies the requirements for six domain page groups: Health, Relationship/Contacts, General/Entities, Calendar, Memory, and Costs -- plus the cross-butler global search that ties them together.

---

## Requirements

### Requirement: Health measurements page with trend charting

The dashboard SHALL render a Measurements page at `/measurements` that displays health measurement data as interactive line charts with a supporting raw-data table.

The page MUST contain:
- A type selector displaying badge tabs for measurement types: `weight`, `blood_pressure`, `heart_rate`, `glucose`, `temperature`, `sleep`, `oxygen`. Clicking a type SHALL filter the chart and table to that type.
- Date range filters (`since`/`until`) using date inputs, with a Clear button when any filter is active.
- A Recharts `LineChart` in a `ResponsiveContainer` (height 288px). For `blood_pressure`, the chart MUST render two lines (systolic in `var(--category-1)` (blue), diastolic in `var(--category-5)` (rose)). For all other types, a single line in `var(--category-1)` (blue) plotting the primary numeric value.
- A "Show/Hide raw data" toggle button. When expanded, the raw data table MUST display columns: Date, Type, Value (formatted compound JSONB), Notes.
- Measurement values are compound JSONB objects (e.g., `{"systolic": 120, "diastolic": 80}`). The chart MUST extract numeric values from these objects using key-aware parsing. The table MUST format them as `key: value` pairs.

#### Scenario: Blood pressure dual-line chart

- **WHEN** the user selects `blood_pressure` as the measurement type
- **AND** there are measurements with `value` containing `systolic` and `diastolic` keys
- **THEN** the chart MUST render two lines: systolic colored with `var(--category-1)` (blue) and diastolic colored with `var(--category-5)` (rose)
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
- Severity visualization MUST use a 16px-wide progress bar: `var(--severity-low)` (green) for 1-3, `var(--severity-medium)` (amber) for 4-6, `var(--severity-high)` (red) for 7-10.

#### Scenario: Severity color mapping

- **WHEN** a symptom has severity 2
- **THEN** the severity bar MUST be colored `var(--severity-low)` at 20% width
- **WHEN** a symptom has severity 8
- **THEN** the severity bar MUST be colored `var(--severity-high)` at 80% width

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
| Health data (measurements, medications, conditions, symptoms, meals, research) | 30s | Moderate update frequency from butler sessions |
| Contact data (contacts, groups, labels) | None (on-demand) | Data changes infrequently; triggered by explicit sync |
| Calendar workspace entries | 30s | Events may change from external calendar providers |
| Calendar workspace metadata | 60s | Source/lane definitions change rarely |
| Memory stats, episodes, facts, rules | 30s | Memory consolidation runs periodically |
| Memory recent activity (rail) | 15s | Fastest-updating view for real-time monitoring |
| Cost summary and daily costs | 60s | Cost data accrues session-by-session |

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
