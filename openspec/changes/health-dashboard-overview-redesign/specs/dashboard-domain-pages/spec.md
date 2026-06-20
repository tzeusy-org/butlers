# Dashboard Domain Pages — Delta for Health Overview Redesign

## ADDED Requirements

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

## MODIFIED Requirements

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
