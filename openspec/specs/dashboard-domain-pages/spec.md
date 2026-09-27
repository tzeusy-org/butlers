# Dashboard Domain Pages

## Purpose

The Butlers dashboard exposes domain-specific pages that surface data managed by individual butlers through read-only (and occasionally mutating) views. These pages turn raw butler data into actionable surfaces: health measurements become trend charts, contacts become an identity-aware CRM, calendar entries merge into a unified workspace, and memory tiers become inspectable knowledge graphs.

This spec codifies the requirements for the Health, Relationship, General/Entities, Calendar, and Memory domain pages, plus the cross-butler global search that ties them together. Spend is owned by `dashboard-spend-dashboard` and the Overview cost band by `dashboard-overview`.

## Requirements

### Requirement: Health read surfaces distinguish a failing source from calm absence

Health read surfaces MUST NOT render a calm empty state (e.g. "No doses logged yet", "No doses
recorded yet", "No nutrition data for this window", an empty trend, or an empty readings chart) when
the underlying query has failed. A failed read MUST instead render an inline degraded note that
names the source (the `SourceDegradedNote` vocabulary), so a broken source is never mistaken for
"nothing logged" on a health surface. This applies to the observed measurement vocabulary and
selection (`GET /api/health/measurements/types`), Overview latest values
(`GET /api/health/measurements/latest`), the measurement trend list and readings chart
(`GET /api/health/measurements/trend`, `GET /api/health/measurements`), the medication adherence
statement and dose history (`GET /api/health/medications/{id}/adherence`,
`GET /api/health/medications/{id}/doses`), and the meals daily-totals mini-KPI
(`GET /api/health/nutrition/summary`).

#### Scenario: A failing adherence source is named, not rendered as calm absence

- **WHEN** the medication adherence read for a row errors
- **THEN** the row MUST render an inline degraded note naming the failed adherence source
- **AND** it MUST NOT render "No doses logged yet" over the failure

#### Scenario: A failing dose-history source is named, not rendered as calm absence

- **WHEN** the dose-history read for an expanded medication errors
- **THEN** the detail panel MUST render an inline degraded note naming the failed source
- **AND** it MUST NOT render "No doses recorded yet" over the failure

#### Scenario: A failing nutrition-summary source is named, not rendered as calm absence

- **WHEN** the meals daily-totals read errors
- **THEN** the mini-KPI MUST render an inline degraded note naming the failed nutrition source
- **AND** it MUST NOT render "No nutrition data for this window" over the failure

#### Scenario: A failing measurement vocabulary or read is named, not rendered as an empty surface

- **WHEN** the measurement vocabulary, latest, trend, or readings read errors
- **THEN** the corresponding surface MUST render an inline degraded note naming the failed source
- **AND** it MUST NOT present an empty tracker, trend, chart, or blank KPI value as calm absence
  without that degraded note

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

Deterministic health reads MUST auto-refresh on a 30-second cadence: observed measurement
vocabulary, measurement, medication, dose, adherence, condition, symptom, meal, research, and
nutrition-summary lists, plus KPI/latest and trend reads. Slower-changing sleep, measurement-source,
and expected-signal reads MAY refresh on a 60-second cadence. Dose and adherence reads MUST fetch
only once a medication is selected.

**Auto-refresh carve-out:** the LLM Voice briefing (`GET /api/health/briefing`) and the insight
feed (`GET /api/switchboard/insights?butler=health`) MUST be excluded from auto-refresh. They rely
on the briefing's per-owner 5-minute TTL cache and a manual refresh from the BriefingStatus pill.
This is a permanent cost guard: an auto-refreshing LLM endpoint would multiply spawn cost.

#### Scenario: Deterministic hooks auto-refresh every 30s

- **WHEN** a deterministic health list, KPI, or trend read is mounted
- **THEN** it MUST refresh every 30 seconds without owner action

#### Scenario: LLM briefing and insight feed are excluded from auto-refresh

- **WHEN** the Health Overview Voice briefing or insight feed is mounted
- **THEN** it MUST NOT poll on any interval
- **AND** a fresh briefing MUST be obtained only on manual refresh or after the 5-minute TTL elapses

### Requirement: Entity browser for general butler data

The dashboard SHALL render an Entity Browser that displays structured JSONB entities from the
General butler in a searchable, filterable table with an expandable JSON viewer.

The table MUST display Collection, Tags, Data, and Created columns. The Data cell MUST show a
truncated one-line JSON preview; activating a row MUST toggle it between that preview and the full
recursive JSON viewer. Filters MUST include text search, a collection selector with an "All
collections" option, a tag selector with an "All tags" option, and a "Clear filters" action.

#### Scenario: Entity row expansion shows full JSON

- **WHEN** the user clicks an entity row with data `{"blood_type": "A+", "height_cm": 175, "notes": "..."}`
- **THEN** the Data cell MUST expand to show the full recursive JSON viewer with type-distinguished
  values
- **AND** clicking the same row again MUST collapse back to the truncated preview

### Requirement: Recursive JSON viewer component

The dashboard SHALL provide a reusable JSON viewer that renders any JSON value as a collapsible tree
and supports copy-to-clipboard.

The viewer MUST:
- Render objects and arrays recursively with collapsible nodes and depth indentation.
- Visually distinguish keys, strings, numbers, booleans, and null, using colors from
  `dashboard-design-language`.
- Offer a root-level "Copy JSON" action that copies the pretty-printed JSON (2-space indent).
- Support starting nested nodes collapsed.

#### Scenario: Copy to clipboard

- **WHEN** the user clicks "Copy JSON" on a viewer displaying `{"name": "Alice"}`
- **THEN** the clipboard MUST contain `{\n  "name": "Alice"\n}`
- **AND** the action MUST briefly confirm the copy before returning to its resting label

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

#### Scenario: Calendar workspace mutation refreshes both query keys

- **GIVEN** a calendar workspace mutation succeeds
- **WHEN** its success handler runs
- **THEN** it MUST invalidate both `calendar-workspace` and `calendar-workspace-meta`

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
A `derived_from` glyph (`↳`, mono, muted) MUST appear at row end only when the
fact has a `source_episode_id` with `source_episode_status = 'available'`. If
the source identifier is `expired` or `unresolved`, the row MUST instead show
the matching visible `Source expired` or `Source unresolved` state without a
live-episode navigation affordance. Fading rows MUST dim their entire foreground
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

The standing-orders register MUST expose single-select maturity pills for `all`, `candidate`,
`established`, `proven`, and `anti_pattern`. The selection MUST be bound to the `maturity` URL
query key. Selecting a maturity MUST reset the `offset` query key to zero, preserve the other
memory query keys, and pass the selected non-`all` maturity to the rules read.

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

#### Scenario: Maturity filter is URL-backed

- **WHEN** the owner selects the `anti_pattern` maturity pill
- **THEN** the URL MUST carry `maturity=anti_pattern` and reset `offset` to its omitted zero default
- **AND** the rules read MUST request only anti-pattern rules
- **AND** browser back navigation MUST restore the previous maturity selection

#### Scenario: Episode consolidation state is a glyph

- **WHEN** an episode is pending, consolidated, or dead-lettered
- **THEN** its row MUST render `◦`, `•`, or `✕` respectively at row end
- **AND** it MUST NOT render a word badge such as "Consolidated" or a colored
  chip

#### Scenario: Derived-from provenance distinguishes source availability

- **WHEN** a fact has a non-null `source_episode_id` and
  `source_episode_status = 'available'`
- **THEN** the ledger row MUST render a muted mono `↳` glyph at row end
- **AND** clicking the row MUST open `/memory/facts/{id}` (the detail page
  carries the episode link)
- **WHEN** a fact has a non-null `source_episode_id` with status `expired` or
  `unresolved`
- **THEN** the ledger row MUST render the matching visible source state
- **AND** it MUST NOT offer a link to `/memory/episodes/{source_episode_id}`

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
| one or more anti-pattern rules | red | "N anti-pattern rule(s)" | `/memory?register=rules&maturity=anti_pattern` |
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
(`register` / `q` / `kind` / `validity` / `maturity` / `status` / `offset`). The fact detail
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
  `/memory/episodes/{source_episode_id}` only when
  `source_episode_status = 'available'`; otherwise visible `Source expired` or
  `Source unresolved` text with no episode link), Supersedes (a link to
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

#### Scenario: Fact with an available source episode link

- **WHEN** a fact has `source_episode_id` set and
  `source_episode_status = 'available'`
- **THEN** the provenance section MUST render a clickable link to
  `/memory/episodes/{source_episode_id}`

#### Scenario: Fact with an unavailable source episode

- **WHEN** a fact has `source_episode_id` set and its status is `expired` or
  `unresolved`
- **THEN** the provenance section MUST render the matching visible source state
- **AND** it MUST NOT render a link to `/memory/episodes/{source_episode_id}`

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
  `/memory/episodes/{id}` only when `source_episode_status = 'available'`; a
  visible matching `Source expired` or `Source unresolved` state otherwise)
  when present; the section and its eyebrow MUST be omitted when no provenance
  exists.
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

#### Scenario: Rule source availability has no dangling door

- **WHEN** a rule has `source_episode_id` set and its status is `expired` or
  `unresolved`
- **THEN** its provenance MUST render the matching visible source state
- **AND** it MUST NOT render a link to `/memory/episodes/{source_episode_id}`

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

Memory reads MUST refresh without owner action: stats, episode, fact, and rule lists every 30
seconds, and the recent-activity and recent-writes rails every 15 seconds. Slower-changing
aggregate reads MAY refresh less often. Single-record reads (fact, rule, episode, entity) MUST NOT
fetch until their identifier is known and MUST NOT poll.

#### Scenario: Fact detail hook waits for an ID

- **GIVEN** no fact ID is available
- **WHEN** `useFact` renders
- **THEN** its query MUST remain disabled

### Requirement: Cross-butler global search

The dashboard MUST provide a debounced global search that queries across all butlers, following
the debounce and minimum-length rules in `dashboard-api` Requirement: Dashboard Query Defaults and
Refresh. Results MUST be grouped by category (sessions, state, and dynamic butler-specific
categories); each result carries `id`, `butler`, `type`, `title`, and `snippet`.

#### Scenario: Short query suppressed

- **WHEN** the user types a single character "a"
- **THEN** the search query MUST NOT fire (enabled = false)
- **WHEN** the user types "ab"
- **THEN** the search query MUST fire after a 300ms debounce

#### Scenario: Search spans multiple butlers

- **WHEN** the user searches "headache"
- **THEN** the results MAY include: health butler symptom records, relationship butler interaction notes mentioning headache, and memory facts containing "headache"
- **AND** each result MUST include the originating `butler` name

### Requirement: Consistent loading and empty states

All domain pages MUST use the shared shell patterns in `dashboard-shell` (Requirement: Skeleton
Loading Components; Requirement: Empty State Pattern; Requirement: Error Boundary). Empty-state copy
MUST explain where the data comes from (e.g., "Health conditions will appear here as they are
tracked by the Health butler."). Loading and empty states MUST be mutually exclusive: skeletons
while loading, and the empty state only after loading completes with zero results.

#### Scenario: Loading does not show the empty state

- **GIVEN** a domain page is loading an empty result set
- **WHEN** it renders its loading state
- **THEN** it MUST render skeletons and MUST NOT render `EmptyState`

### Requirement: Memory Overture renders graph-health coverage honestly

`MemoryOverture` SHALL consume `GET /api/memory/stats` metadata
`graph_health` as a read-only coverage observation. It SHALL distinguish
complete coverage from incomplete or unknown coverage without inferring a
healthy graph from missing metrics, an empty pool list, or a partial result.
Existing ordinary pool, catalog-drift, and retention notes SHALL remain
independent.

The Overture SHALL render a calm, explicitly coverage-only completion line for
`coverage='complete'`. It SHALL render a named incomplete or unknown coverage
note for every non-complete state, naming unknown sources when available. It
SHALL not add cleanup, repair, re-enable, drain, delete, owner-authorization,
or graph mutation controls; retrying the same read after an unavailable source
is permitted.

ID: REQ-dashboard-domain-pages-048
Source: [Observed] PR #3734; `openspec/changes/archive/2026-08-14-memory-graph-health-read-api/CANONICALIZATION.md`
Scope: v1-mandatory

#### Scenario: Complete coverage is visible without a health claim

- **WHEN** `GET /api/memory/stats` returns `meta.graph_health.coverage='complete'`
  with completed pool observations
- **THEN** the Overture SHALL state that graph-health coverage is complete
- **AND** it SHALL not label the graph healthy solely because coverage is
  complete

#### Scenario: Incomplete coverage names unavailable pools

- **WHEN** `GET /api/memory/stats` returns
  `meta.graph_health.coverage='incomplete'` with one or more pool observations
  where `coverage='unknown'`
- **THEN** the Overture SHALL state that graph-health coverage is incomplete
- **AND** it SHALL name each unknown `source_butler`
- **AND** it SHALL not render a complete-coverage or healthy graph claim

#### Scenario: Unknown coverage does not become an empty all-clear

- **WHEN** `GET /api/memory/stats` returns
  `meta.graph_health.coverage='unknown'`
- **THEN** the Overture SHALL state that graph-health coverage is unknown
- **AND** it SHALL not substitute a zero metric, a complete ratio, or a healthy
  graph statement

#### Scenario: Coverage presentation has no repair affordance

- **WHEN** any graph-health coverage state is rendered
- **THEN** the Overture SHALL not offer a cleanup, graph-repair, retention
  mutation, or authorization control
- **AND** a retry control, when present for an unavailable source, SHALL only
  repeat the existing stats read

---

### Requirement: Relationship overdue surfaces expose unmeasurable cadence honestly

Relationship dashboard surfaces MUST distinguish a complete set of measurable contacts from a
result in which stale-contact evaluation is unavailable. Unmeasurable contacts MUST NOT appear as
overdue, count toward overdue totals, or populate attention rails, and their suppression MUST NOT
be rendered as a complete cadence all-clear.

An entity PulseStrip cadence tile MUST bind its label, rolling query window, count, pagination
completeness, and error state as one evidence unit. Its cadence reader MUST echo the exact window
used for the observation and explicitly distinguish complete evidence from a bounded or paginated
subset. The count MUST include only active, stable interaction event rows with a literal
`interaction_` predicate prefix and MUST exclude the `interaction_note` annotation. While the
page remains open, the client MUST requery the selected window at least every 30 seconds and on
focus. The tile MUST compare the echoed start/end bounds to the requested duration and reject a
response whose `window_ended_at` is more than 90 seconds old or implausibly future-dated. It MUST
render "Quiet" only for a successful, complete, fresh matching-window observation with zero
interactions. Incomplete, mismatched-window, stale, or unavailable evidence MUST render a typed
attention state and MUST NOT render "Quiet" or an exact interaction count.

ID: REQ-dashboard-domain-pages-049
Source: relationship-stale-contact-producer-mapping design §6; heart-and-soul/vision.md;
`keep-relationship-cadence-evidence-honest` design
Scope: v1-mandatory

#### Scenario: Relationship Contacts tab does not turn suppression into calm

- **WHEN** the Relationship Contacts tab's overdue source includes an unmeasurable contact
- **THEN** that contact MUST NOT appear in the overdue list or overdue KPI
- **AND** the tab MUST identify cadence instrumentation or provenance as unavailable
- **AND** it MUST NOT render "Cadence all clear" or equivalent complete healthy copy

#### Scenario: Plex attention rail contains only measurable overdue contacts

- **WHEN** the Plex evaluates its "Worth attention" rail with one or more unmeasurable contacts
- **THEN** those contacts MUST NOT appear as overdue attention items
- **AND** the rail MUST expose incomplete cadence availability rather than a complete all-clear

#### Scenario: Healthy elapsed source keeps the existing overdue presentation

- **WHEN** a contact has exactly one healthy mapped producer and its existing effective cadence has
  elapsed
- **THEN** the existing overdue KPI, list, and attention-rail behavior MAY render that contact
- **AND** this source mapping MUST NOT change cadence, priority, ordering, or outreach copy

#### Scenario: Complete cadence evidence may render Quiet

- **WHEN** the cadence reader completes the requested rolling window with zero interactions and no
  additional page
- **THEN** the PulseStrip cadence label MUST name that same window
- **AND** the tile MAY render "Quiet"

#### Scenario: Annotations and predicate lookalikes do not count as interactions

- **WHEN** the same cadence window contains one active stable interaction event, an
  `interaction_note` annotation, and a predicate whose name only matches an unescaped
  `interaction_%` SQL pattern
- **THEN** the cadence count MUST be one
- **AND** removing that event MUST leave a complete zero despite the annotation and lookalike

#### Scenario: Paginated cadence evidence is attention, not calm

- **WHEN** the bounded cadence read reports incomplete evidence or an additional page
- **THEN** the PulseStrip MUST render a typed incomplete-attention state
- **AND** it MUST NOT render "Quiet" or present the observed subset as an exact count

#### Scenario: Cadence read failure is attention, not calm

- **WHEN** the cadence read fails
- **THEN** the PulseStrip MUST render a typed unavailable-attention state
- **AND** it MUST NOT render "Quiet"

#### Scenario: Window refresh recomputes label and value together

- **WHEN** the active cadence window changes
- **THEN** the cadence query MUST be re-keyed for that window
- **AND** the label and value MUST be derived only from evidence echoing the same window
- **AND** stale or mismatched-window evidence MUST NOT render "Quiet" or an exact count

#### Scenario: Cached calm ages into attention on an open page

- **WHEN** a complete zero-interaction response remains cached on an open page past the
  90-second freshness bound, or a response echoes the same duration with old start/end bounds
- **THEN** the tile MUST render a typed stale-attention state rather than "Quiet"
- **AND** an active page MUST requery the selected window at least every 30 seconds and on focus
- **AND** a failed refresh with cached complete-zero data MUST render unavailable attention

### Requirement: Health and General retain distinct Butler identity slots

The dashboard MUST resolve the Health butler to `--category-5` and the General butler to
`--category-4` through the canonical `ButlerMark` roster order. These identity tokens MUST remain
confined to Butler letter-marks and MUST NOT be globally replaced with one another.

#### Scenario: Health and General marks keep their permanent slots

- **WHEN** `ButlerMark` renders Health and General identity marks
- **THEN** Health MUST use `--category-5`
- **AND** General MUST use `--category-4`
- **AND** neither token may be applied as a global replacement for the other

### Requirement: Health measurements page at the canonical route

The dashboard SHALL render a Measurements page at `/health/measurements` (reachable from the `/health`
Overview) reframed from "data entry" to "trajectory": the page MUST lead with the trend rule-list
(mono-time / status-dot / value / `→`), not the input box. It displays health measurement data as
interactive line charts with a supporting raw-data view.

The page MUST contain:
- A chart type selector derived from `GET /api/health/measurements/types`. The response is the
  observed read-vocabulary authority: tabs MUST contain only observed entries with
  `chart_eligible = true`, using the response's labels, and MUST NOT fall back to a static type
  list. Clicking a tab SHALL filter the chart and rule-list to that type. If the requested initial
  type is not chart-eligible, the first returned chart-eligible type becomes active. The reading-log
  filter MUST include all observed types, not just chartable types, and preserve an unobserved raw
  `?type=` selection until the owner clears it.
- The observed vocabulary is read-only. It MUST NOT expand the manual measurement writer: its type
  choices and write allowlist remain exactly `weight`, `blood_pressure`, `heart_rate`,
  `blood_sugar`, and `temperature`.
- Date range filters (`since`/`until`) using date inputs, with a Clear button when any filter is
  active.
- The chart's `type`, `since`, and `until` URL keys are authoritative for chart initialization. A
  supplied type is valid only when it is an observed `chart_eligible` entry; supplied bounds must
  be real `YYYY-MM-DD` dates and ordered when both are present. A missing type may use the first
  observed chart-eligible entry as the ordinary default. An unknown or ineligible supplied type,
  malformed bound, or reversed range MUST NOT trigger a chart, trend, or readings query. The chart
  MAY show its ordinary first eligible tab as a visual fallback, but it MUST preserve the raw URL
  selection for the reading-log filter. Selecting a chart tab or editing/clearing chart dates MUST
  update only those keys with history replacement and preserve unrelated query keys.
- A Recharts `LineChart` in a `ResponsiveContainer` with explicit value-shape semantics. A scalar
  type MUST plot only finite values from the normalized `value` key. `blood_pressure` is the sole
  named compound exception: it MUST render `systolic` and `diastolic` as two lines. Another
  chart-eligible compound type MAY expose its raw data, but it MUST NOT guess a numeric key or invent
  a line series; it MUST state that no unambiguous series is available instead. The line palette MUST
  use the direct chart-series CSS custom-property reference `var(--chart-1)` passed to the Recharts
  SVG `stroke` prop, not a hardcoded hex or computed-style-derived literal. Chromium resolves that
  CSS custom property in the SVG presentation attribute at paint time. Where two lines are shown
  (systolic/diastolic), the second line MUST pass the separate `var(--chart-2)` reference to its
  SVG stroke so the two lines remain visually separable.
- The trend rule-list as the primary surface, sourced from `GET /api/health/measurements/trend`
  (the bucketed mean/min/max aggregation). Only scalar types MAY request or render that scalar
  aggregation. `blood_pressure` and other compound types MUST state that trend aggregation is
  unavailable rather than coercing a compound value. The page MUST provide a "Show/Hide raw data"
  affordance for the full table (Date, Type, Value, Notes); compound table values MUST format as
  `key: value` pairs.

#### Scenario: Chart tabs use the observed eligible vocabulary

- **WHEN** the measurements page renders the type selector
- **THEN** it MUST render exactly the observed entries whose `chart_eligible` flag is true
- **AND** it MUST use each observed entry's returned label
- **AND** a type absent from the observed response or marked ineligible MUST NOT appear as a chart
  tab merely because it appears in a static client list

#### Scenario: Vocabulary loading, failure, and empty results stay honest

- **WHEN** the observed vocabulary is loading
- **THEN** the chart surface MUST render a loading skeleton, not guessed static tabs
- **WHEN** the observed vocabulary read fails
- **THEN** the chart surface MUST render a `SourceDegradedNote` naming the type source
- **WHEN** the observed vocabulary succeeds with zero chart-eligible types
- **THEN** the chart surface MUST render a single serif-italic no-chartable-types line, not a
  fabricated tab or chart

#### Scenario: Chart URL initialization accepts only valid observed state

- **WHEN** `/health/measurements` loads with an observed chart-eligible `type` and ordered
  date-only `since`/`until` query keys
- **THEN** the matching tab MUST be active and chart reads MUST receive those bounds
- **AND** changing the tab or bounds MUST retain unrelated query keys
- **WHEN** the URL type is unknown or not chart-eligible, or either supplied bound is malformed or
  the range is reversed
- **THEN** that value MUST NOT become a chart tab or issue a chart query
- **AND** the chart MUST retain an honest non-data fallback while the owner can select a valid tab
  or correct the bounds

#### Scenario: Observed types never expand the manual writer

- **WHEN** the observed vocabulary contains an imported or unknown measurement type
- **THEN** the tracker may display it as a read filter and the chart or KPI may consume it only under
  their eligibility rules
- **AND** the manual measurement writer MUST still offer and accept exactly `weight`,
  `blood_pressure`, `heart_rate`, `blood_sugar`, and `temperature`

#### Scenario: Page leads with the trend, not the form

- **WHEN** the measurements page loads
- **THEN** the trend rule-list (or chart) MUST be the leading surface
- **AND** the create/input affordance MUST NOT be the first element

#### Scenario: Blood pressure dual-line chart

- **WHEN** the user selects `blood_pressure` as the measurement type
- **AND** there are measurements with `value` containing `systolic` and `diastolic` keys
- **THEN** the chart MUST render two lines for systolic and diastolic
- **AND** the two lines MUST use distinguishable chart-series tokens (the diastolic line a
  reduced-opacity or lightened variant) so they are not the same indistinguishable color
- **AND** the chart tooltip MUST label them "Systolic" and "Diastolic"

#### Scenario: Scalar and compound data never create a guessed series

- **WHEN** the active type is scalar and a reading has a finite normalized `value`
- **THEN** the chart MUST plot that `value` and the trend rule-list MAY use scalar aggregation
- **WHEN** the active type is a chart-eligible compound type other than `blood_pressure`
- **THEN** the page MUST NOT choose a numeric key, plot a line, or request scalar trend aggregation
- **AND** it MUST state that no unambiguous chart series is available while retaining its raw-data
  view when readings exist

#### Scenario: Empty state for type with no data

- **WHEN** the user selects a measurement type with zero records in the selected date range
- **THEN** the page MUST display a single serif-italic empty line rather than decorated empty-state
  chrome

### Requirement: Health Overview landing page

The dashboard SHALL render a Health Overview page at `/health` as the health surface's shipped
landing page. The Overview is a
two-column editorial composition (`grid-template-columns: 1.4fr 1fr`): the left column is the
Voice briefing plus a KPI strip; the right column is a quiet attention index. On narrow viewports
the grid MUST collapse to a single column with the attention index below the briefing.

The Overview MUST follow the Dispatch language: Display headline (not bold), the health butler's
`--category-5` identity hue only on the `ButlerMark` letter-mark, surfaces-not-cards, and state
color (`--red`/`--amber`/`--green`) reserved for genuine health signal, never decoration.
The General butler's separate `--category-4` identity slot remains unchanged.

The Overview MUST contain, in the left column:

- A **DateEyebrow** and a **Display** headline that names the single most important thing about the
  owner's health right now in one sentence.
- A **Voice briefing** (serif elaboration) sourced from `GET /api/health/briefing`, carrying a
  **BriefingStatus pill** that reads `llm · cached` when the line was model-written and `templated`
  when deterministic, so the owner always knows whether a line was computed or model-written.
- A **KPI strip** of exactly four structural cells, each a mono eyebrow over the latest value.
  `GET /api/health/measurements/types` is the observed read-vocabulary authority for selecting
  those cells. Observed core types `weight`, `blood_pressure`, `heart_rate`, and `blood_sugar` MUST
  retain their established positions even when an individual response marks them ineligible. An
  absent core position MAY be filled only by an unused, non-core observed type marked
  `kpi_eligible`, ordered by `latest_at` descending and then `type` ascending. A dynamic candidate
  MUST NOT displace an observed core type. If no eligible candidate is available, the position MUST
  retain its original core label and render a single em-dash, never a fabricated or placeholder
  value. `GET /api/health/measurements/latest` MUST be requested only for the selected types; the
  vocabulary response itself MUST NOT be treated as a latest value.
  Each cell with a reading MUST thread the reading's `measured_at` age into a delta line (e.g.
  "7d"). The documented per-vital freshness SLAs (`weight` 3 days, `blood_pressure` 3 days,
  `heart_rate` 2 days, `blood_sugar` 2 days) apply to their respective core readings, whose age
  MUST render amber (`--amber-text`) once stale. A dynamic fallback MAY show its real reading age
  but MUST NOT be declared fresh under a guessed SLA. Each cell MUST expose its reading's data
  source (resolved from the latest entry's metadata) as a hover tooltip when known.
- A **data-freshness indicator** sourced from `GET /api/health/measurements/sources` (one of the
  wire-orphaned reads this redesign consumes), shown as a quiet mono chip (e.g. "synced 2h ago" per
  source). It MUST state real last-sample times only; when no source data exists the chip is omitted,
  never faked. The source name for each row MUST be resolved as
  `COALESCE(metadata->>'source', metadata->>'provider')` so facts carrying only the legacy `provider`
  key are still attributed rather than dropped.

The Overview MUST contain, in the right column, an **AttentionList** sourced from the Switchboard
insight reader (`GET /api/switchboard/insights?butler=health&status=pending`). Each attention item MUST link to
the concerning signal (missed doses, severe symptom, drifting measurement) so it is reachable in one
click. When no insight candidate is pending, the attention index MUST collapse to a single
serif-italic line, with no empty-state decoration.

A measurement-gap or correlation-drift item MAY become a measurements door only when its metadata
contains a typed `measurement_door` object with a non-empty `type` and real, ordered date-only
`since` and `until` bounds. The dashboard MUST construct the destination itself as the fixed
same-origin `/health/measurements` path plus encoded `type`, `since`, and `until` query keys. It
MUST NOT navigate to an arbitrary `metadata.href` value. Missing, malformed, reversed, or
otherwise ineligible door metadata MUST fall back to the fixed measurements path without query
keys.

#### Scenario: Overview lands the owner on the most important thing

- **WHEN** the owner navigates to `/health`
- **THEN** the page MUST render the two-column editorial layout with the Voice briefing headline,
  the four-cell KPI strip, and the attention index
- **AND** the briefing headline MUST state the single most important current health fact in one
  sentence

#### Scenario: KPI strip keeps four structural positions from the observed vocabulary

- **WHEN** the Overview renders the KPI strip
- **THEN** it MUST render exactly four cells
- **AND** each observed core type (`weight`, `blood_pressure`, `heart_rate`, `blood_sugar`) MUST
  retain its established position
- **AND** an absent core position MAY use only an unused non-core type the server marks
  `kpi_eligible`, selected by newest `latest_at` and then ascending `type`
- **AND** a position without an eligible selected type MUST retain its core label and render an
  em-dash, never a fabricated value

#### Scenario: KPI vocabulary or latest read failure is named

- **WHEN** the measurement vocabulary or latest-read query fails
- **THEN** the four structural cells MUST remain visible with an inline `SourceDegradedNote` naming
  the failed source
- **AND** the Overview MUST NOT fabricate a cell selection, a latest value, or a calm no-data state

#### Scenario: KPI cell surfaces reading age and staleness

- **WHEN** a KPI cell has a reading whose `measured_at` age exceeds that vital's freshness SLA
- **THEN** the cell MUST show the reading's age (e.g. "7d") rendered amber
- **WHEN** the reading is within the vital's SLA
- **THEN** the age MUST render in the quiet muted tone, not amber
- **AND** the cell MUST expose the reading's data source as a hover tooltip when the source is known

#### Scenario: Freshness source name falls back to the legacy provider key

- **WHEN** the data-freshness indicator aggregates measurement sources and a fact carries only the
  legacy `metadata->>'provider'` key (no canonical `source`)
- **THEN** that fact MUST still be attributed to its provider via
  `COALESCE(metadata->>'source', metadata->>'provider')`, never dropped from the source list

#### Scenario: Expected measurement gaps distinguish instrument failure

- **WHEN** the Health expected-signals endpoint returns `unmeasurable`
- **THEN** the measurements tab SHALL render instrument unavailability and state that owner-behavior nudges are paused
- **WHEN** the endpoint returns `available=false` with `signals=null`
- **THEN** the tab SHALL render signal-health degradation, never an empty all-clear

#### Scenario: Voice line carries the honesty pill

- **WHEN** the Voice briefing renders a model-written elaboration served from cache
- **THEN** the BriefingStatus pill MUST read `llm · cached`
- **WHEN** the briefing falls back to the deterministic templated paragraph
- **THEN** the pill MUST read `templated`

#### Scenario: Empty attention index is one quiet line

- **WHEN** `GET /api/switchboard/insights?butler=health&status=pending` returns zero candidates
- **THEN** the attention index MUST collapse to a single serif-italic line
- **AND** it MUST NOT render placeholder cards, confetti, or celebratory styling

#### Scenario: Typed measurement insights open a bounded same-origin chart door

- **WHEN** a pending `measurement-gap` or `correlation-drift` insight supplies a typed
  `measurement_door` with a type and ordered `YYYY-MM-DD` bounds
- **THEN** its attention row MUST link to `/health/measurements` with encoded `type`, `since`, and
  `until` query keys
- **AND** the dashboard MUST build that URL itself, never navigate to `metadata.href`

#### Scenario: Invalid measurement-door metadata cannot control navigation

- **WHEN** a measurement insight omits a typed door or its type, dates, or date ordering are invalid
- **THEN** its attention row MUST fall back to `/health/measurements` without typed query keys
- **AND** arbitrary metadata values MUST NOT redirect the owner away from that same-origin route

### Requirement: Expired Episode Provenance Has No Dangling Door

Memory detail and register surfaces SHALL render a source episode as a
navigation link only when the typed source state is `available`. An `expired`
source MUST remain visible as truthful content-free provenance but MUST be
non-clickable; an `unresolved` source MUST be visibly uncertain and
non-clickable. The surfaces MUST NOT replace either state with a false
no-provenance presentation.

#### Scenario: Fact detail renders a deleted source without navigation

- **WHEN** FactDetailPage receives a fact with an `expired` source episode
- **THEN** it MUST display a visible `Source expired` provenance state
- **AND** it MUST NOT render a link to `/memory/episodes/:episodeId`

#### Scenario: Rule and register surfaces preserve truthfulness

- **WHEN** RuleDetailPage or a memory register receives an `expired` or
  `unresolved` source episode state
- **THEN** it MUST display the matching source state without a live-episode
  navigation affordance
- **AND** it MUST retain the durable fact, rule, or link evidence in view

### Requirement: Medications page exposes honest supply quantity

The dashboard Medications page SHALL provide one optional supply-quantity field
in the existing create/edit form. The field SHALL accept only a positive whole
number when present, SHALL send the value through the typed Health medication
API on create or edit, and SHALL use the existing edit quantity update as the
refill/current-supply path. A missing server quantity SHALL render explicitly
as `Supply: unknown`; the page SHALL never render zero or fabricate a supply
from dosage, frequency, or adherence data.

#### Scenario: The owner records an initial supply while creating a medication

- **WHEN** the owner enters a positive whole-number supply quantity and submits
  the create form
- **THEN** the dashboard SHALL send that exact `quantity` in
  `POST /api/health/medications` and render the returned medication's count

#### Scenario: The owner records a refill from the edit form

- **WHEN** the owner enters a new positive whole-number supply quantity for an
  existing medication and saves the edit form
- **THEN** the dashboard SHALL send that exact `quantity` in
  `PUT /api/health/medications/{id}`
- **AND** it SHALL continue to rely on the server-returned quantity and
  `quantity_updated_at`, not client-side depletion math

#### Scenario: An omitted supply is visibly unknown

- **WHEN** the medication response has `quantity: null` or omits a legacy
  quantity value
- **THEN** the medication row SHALL render `Supply: unknown`
- **AND** it SHALL NOT render `Supply: 0`, a standard pack size, or an
  adherence-derived estimate

#### Scenario: A blank edit does not erase known supply

- **WHEN** an existing medication has a recorded quantity and the owner saves
  an edit without entering a replacement quantity
- **THEN** the dashboard SHALL preserve the existing quantity by omitting the
  quantity field from the update payload

#### Scenario: Re-entering the same count records a refill

- **WHEN** an existing medication has a recorded quantity and the owner
  explicitly edits or re-enters that same positive whole-number count
- **THEN** the dashboard SHALL send `quantity` in the update payload
- **AND** an unrelated edit that leaves the prefilled supply field untouched
  SHALL omit `quantity` and SHALL NOT advance the refill timestamp

#### Scenario: Invalid form values are refused before submission

- **WHEN** the owner enters zero, a negative number, a decimal, or malformed
  text in the supply field
- **THEN** the form SHALL show a typed positive-whole-number validation message
- **AND** it SHALL NOT call the create or update mutation

## Source References

- `frontend/src/index.css` — `--severity-low`, `--severity-medium`,
  `--severity-high` token definitions; `--categorical-1` through `--categorical-12`
  definitions. Both sets are also aliased into Tailwind via `--color-severity-*`
  and `--color-categorical-*`.
- `about/heart-and-soul/design-language.md` — token exemption for `--chart-*`
  palette (chart axis/line tokens are a separate axis; not replaced here).
