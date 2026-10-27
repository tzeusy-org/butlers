## ADDED Requirements

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

### Requirement: Contacts index compatibility route

The legacy `/contacts` route SHALL remain registered for bookmark compatibility and MUST
replace-navigate to `/entities/index?has=contact`. The entity index defined by
`dashboard-relationship` is the canonical list, search, filtering, and curation surface. The
compatibility route MUST NOT revive the retired standalone contacts page or its page-specific
table and Google-sync composition.

This route disposition does not itself remove the contact hook module or reusable contact
components imported by embedded consumers. It also does not make their backend-dead readers live:
`getContacts`, `getContact`, and `getContactInteractions` still target absent relationship routes,
as recorded by Requirement: Contact hooks with conditional fetching.

#### Scenario: Contacts bookmark opens the canonical filtered index

- **WHEN** the owner navigates to `/contacts`
- **THEN** the router MUST replace-navigate to `/entities/index?has=contact`
- **AND** the entity index MUST own the resulting contact-filtered workflow

### Requirement: Contact detail compatibility route

The legacy `/contacts/:contactId` route SHALL remain registered for bookmark compatibility and
MUST replace-navigate to `/entities/index?has=contact`. The retired `public.contacts` identity
cannot be resolved to a canonical entity at this route boundary, so the compatibility route MUST
NOT fabricate an entity-detail destination or render the retired tabbed contact page. Canonical
single-record navigation starts from the entity index and opens `/entities/:entityId`.

#### Scenario: Legacy contact detail bookmark falls back to the entity index

- **WHEN** the owner navigates to `/contacts/nonexistent-id` or any other legacy contact ID
- **THEN** the router MUST replace-navigate to `/entities/index?has=contact`
- **AND** it MUST NOT claim that the legacy ID resolved to an entity

### Requirement: Costs compatibility route

The legacy `/costs` route SHALL remain registered for bookmark compatibility and MUST
replace-navigate to `/spend`. The canonical Spend page, its layout, and its API behavior are owned
by `dashboard-spend-dashboard`; this spec MUST NOT duplicate the retired Costs page's Recharts
area chart, summary-grid, or per-butler table composition.

#### Scenario: Costs bookmark opens Spend

- **WHEN** the owner navigates to `/costs`
- **THEN** the router MUST replace-navigate to `/spend`
- **AND** the canonical Spend page MUST render there

### Requirement: Spend hooks with bus-aware refresh

Shared Spend consumers MUST use the following TanStack Query hooks:

| Hook | Query Key | Refresh behavior |
|---|---|---|
| `useSpendSummary(period)` | `cost-summary` | Fleet-event invalidation plus bus-aware polling |
| `useDailySpend()` | `daily-costs` | Fleet-event invalidation plus bus-aware polling |
| `useTopSessions(limit)` | `top-sessions` | Fleet-event invalidation plus bus-aware polling |
| `useCostsBySchedule(from, to)` | `costs-by-schedule` | Fleet-event invalidation plus bus-aware polling |

#### Scenario: Spend summary follows event-bus health

- **GIVEN** `useSpendSummary` has fetched a period
- **WHEN** a Spend event invalidates `cost-summary`
- **THEN** it MUST refresh that period's cost summary
- **AND** its polling interval MUST follow the shared bus-aware poll policy rather than a fixed
  60-second timer

#### Scenario: Schedule costs follow event-bus health

- **GIVEN** `useCostsBySchedule` has fetched a date range
- **WHEN** a Spend event invalidates `costs-by-schedule`
- **THEN** it MUST refresh that range's per-schedule costs
- **AND** its polling interval MUST follow the shared bus-aware poll policy

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

### Requirement: Spend widget for dashboard overview

The dashboard MUST provide a `CostWidget` component for embedding on the overview page. The widget MUST display:
- Title "Cost Today" with a "View all" link to `/spend`.
- Total cost for the day formatted as currency.
- Top butler name and cost (e.g., "Top: health ($3.50)").
- A sparkline showing the real trailing 7-day daily spend series.

#### Scenario: Widget with no data

- **WHEN** `totalCostUsd` is 0 and `topButler` is null
- **THEN** the widget MUST display "$0.00" and no top-butler line

## MODIFIED Requirements

### Requirement: Contact hooks with conditional fetching

The contact hook inventory MUST distinguish exported/imported hooks from backend-supported paths:

The `use-contacts.ts` module remains imported by embedded relationship, ingestion-filter, and
entity-detail consumers. Import presence MUST NOT be treated as proof of backend support. This
documentation reconciliation neither removes nor repairs the imported dead-path readers:

| Hook | Backend path | Current contract status |
|---|---|---|
| `useContacts(params)` | `GET /api/relationship/contacts` | Imported, but backend-dead; tracked by the client/OpenAPI contract allowlist |
| `useContact(id)` | `GET /api/relationship/contacts/:id` | Imported, but backend-dead; no single-contact route exists |
| `useContactInteractions(id)` | `GET /api/relationship/contacts/:id/interactions` | Imported, but backend-dead; tracked by the client/OpenAPI contract allowlist |
| `useOverdueContacts(days)` | `GET /api/relationship/contacts/overdue` | Backend-supported |
| `useGroups(params)` | `GET /api/relationship/groups` | Backend-supported |
| `useGroupMembers(id)` | `GET /api/relationship/groups/:id/members` | Backend-supported |
| `useLabels()` | `GET /api/relationship/labels` | Backend-supported |
| `useUpcomingDates(days)` | `GET /api/relationship/upcoming-dates` | Backend-supported |

#### Scenario: Contact detail hook waits for an ID

- **GIVEN** no contact ID is available
- **WHEN** `useContact` renders
- **THEN** its query MUST remain disabled
- **AND** that conditional-fetch behavior MUST NOT be described as backend route support

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

## REMOVED Requirements

### Requirement: Health measurements page with trend charting

**Reason**: The bare `/measurements` location is not the shipped Health route.

**Migration**: Use `/health/measurements`, as specified by the successor requirement in this change.

### Requirement: Contacts page with search, label filtering, and Google sync

**Reason**: The standalone contacts list was superseded by the canonical entity index and its `has=contact` filter.

**Migration**: Keep `/contacts` as a compatibility alias to `/entities/index?has=contact`; retain imported hook/component consumers without representing backend-dead contact readers as supported APIs.

### Requirement: Contact detail page with tabbed sub-resources

**Reason**: The standalone tabbed contact page was superseded by entity detail and the entity-scoped activity stream.

**Migration**: Keep `/contacts/:contactId` as a compatibility alias to `/entities/index?has=contact`; navigate to canonical `/entities/:entityId` details from the index.

### Requirement: Costs page with summary stats and chart

**Reason**: The standalone Costs page was merged into the canonical Spend surface.

**Migration**: Keep `/costs` as a compatibility alias to `/spend`; current Spend behavior is owned by `dashboard-spend-dashboard`.

### Requirement: Cost area chart with period selector

**Reason**: The retired Costs-page Recharts composition was superseded by the canonical Spend surface.

**Migration**: Use the Spend page chart and window behavior defined by `dashboard-spend-dashboard`.

### Requirement: Cost breakdown table by butler

**Reason**: The retired Costs-page table was superseded by the canonical Spend breakdown surface.

**Migration**: Use the Spend page breakdown defined by `dashboard-spend-dashboard`.

### Requirement: Cost hooks with 60-second refresh

**Reason**: Fixed 60-second polling was superseded by fleet-event invalidation with bus-aware reconciliation polling.

**Migration**: Use the shared Spend hooks and refresh behavior in the new Spend hooks requirement.

### Requirement: [TARGET-STATE] Health Overview landing page

**Reason**: The Health Overview is shipped at `/health`; the target-state marker and absence claim are obsolete.

**Migration**: Use the shipped Health Overview successor requirement.

### Requirement: Cost widget for dashboard overview

**Reason**: The live widget now links to canonical Spend and renders a real daily series rather than the legacy Costs alias and mock trend.

**Migration**: Use the Spend widget successor requirement; the `CostWidget` component remains live.
