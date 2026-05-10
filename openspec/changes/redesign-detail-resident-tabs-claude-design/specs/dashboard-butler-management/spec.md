## ADDED Requirements

### Requirement: Panel-grid frame

All resident-mode tab bodies SHALL use a 4-column CSS grid as the composition
frame. This mirrors the `/butlers` status-board cell convention introduced by
the `bu-hb7dh` status-board redesign.

Frame rules:
- The outermost `<div>` of each tab body receives `border-top border-left` using
  the `--border` token.
- Each `<Panel>` child receives `border-right border-bottom` using the `--border`
  token. Panels must not add their own top or left border.
- The grid uses `grid-cols-4` (4 equal columns). Panels span 1, 2, 3, or 4
  columns via a `span` prop.
- Panel height is determined by content unless an explicit `height` prop is
  provided (e.g., for fixed-height scroll bodies).
- No background fill on the frame or on panels. Surface color is the page
  background token.

**Doctrine citations:**
- `about/heart-and-soul/design-language.md` Non-Negotiable 1: no raw oklch or
  hex in JSX; all borders use the `--border` semantic token.
- `about/heart-and-soul/design-language.md` Non-Negotiable 2: the `<Page>`
  primitive owns chrome; the panel grid is the tab body, not a competing shell.

#### Scenario: Frame border topology

- **WHEN** a resident-mode tab renders its panel grid
- **THEN** the frame element SHALL have `border-top` and `border-left`
- **AND** each Panel child SHALL have `border-right` and `border-bottom`
- **AND** the resulting visual effect SHALL be a continuous ruled grid with no
  doubled borders at interior edges

---

### Requirement: Panel atom

The `<Panel>` component SHALL be the shared container atom for all resident tab bodies.
It encapsulates grid span, border application, and optional scroll behavior.

Panel contract (`<Panel title sub span scroll height>`):

| Prop | Type | Required | Description |
|---|---|---|---|
| `title` | `string` | Yes | Monospace eyebrow label rendered above the body. Sentence case, no em-dash. |
| `sub` | `string` | No | Secondary label rendered beneath the title in muted 11px text. |
| `span` | `1 \| 2 \| 3 \| 4` | No, default `1` | Number of grid columns the panel spans. |
| `scroll` | `boolean` | No, default `false` | When true, the panel body is a `overflow-y: auto` region. |
| `height` | `string` | No | CSS value for the panel body height when `scroll` is true (e.g., `"320px"`). |

The `title` is styled as JetBrains Mono (the numerals/eyebrow family per the
three-family type stack). It is the section's name, not a heading; it does not
use a heading tag. It renders at 10px, uppercase, letter-spacing: 0.06em,
`--muted-foreground` color.

**Doctrine citations:**
- `about/heart-and-soul/design-language.md` Type system: JetBrains Mono for
  eyebrow titles (numerals family); Source Serif 4 reserved for Voice surfaces;
  Inter Tight for labels and body text.
- `about/heart-and-soul/design-language.md` Non-Negotiable 1: no inline style
  for color or spacing; all values via token classes.
- `about/heart-and-soul/design-language.md` Voice: sentence case, no em-dash
  in any `title` or `sub` value.

#### Scenario: Panel renders title eyebrow

- **WHEN** a Panel is rendered with `title="session activity"`
- **THEN** the eyebrow text "session activity" SHALL be rendered in JetBrains
  Mono, uppercase, at `--muted-foreground`
- **AND** the eyebrow SHALL appear above the panel body, separated by a thin
  rule or spacing consistent with the design token scale

#### Scenario: Panel scroll body

- **WHEN** a Panel is rendered with `scroll={true}` and `height="320px"`
- **THEN** the panel body region SHALL be scrollable along the y-axis
- **AND** the panel height SHALL be constrained to 320px
- **AND** content that overflows the fixed height SHALL be accessible by scrolling

#### Scenario: Panel span

- **WHEN** a Panel is rendered with `span={4}`
- **THEN** the Panel SHALL span all 4 grid columns
- **AND** the Panel SHALL receive `border-right border-bottom` regardless of span

---

### Requirement: KPI quartet pattern

The KPI quartet SHALL be a row of exactly 4 single-span Panels that appears at the top
of Activity, Spend, and Memory tabs. It provides at-a-glance health for the
tab's primary domain.

Each KPI cell shows:
1. A label in muted 11px Inter Tight (the metric name).
2. A value in JetBrains Mono tabular-nums. Primary KPI values use 28px; secondary
   values use 22px. The size is declared per-tab in the requirement below.
3. An optional sub-line in 11px muted text (e.g., delta vs. prior period, unit).
4. An optional tone applied as `--severity-high` (red), `--severity-medium`
   (amber), or `--severity-low` (green) to the value text. No oklch literals.

Tone is applied only when the metric signals a degraded or notable state (e.g.,
error count > 0 renders the value in `--severity-high`). Normal/neutral values
render in `--foreground` without tone override.

**Doctrine citations:**
- `about/heart-and-soul/design-language.md` Non-Negotiable 1: tone colors MUST
  use named tokens (`--severity-high`, `--severity-medium`); raw oklch is banned.
- `about/heart-and-soul/design-language.md` Type system: tabular-nums is
  non-negotiable for every numeric value in the dashboard.
- `about/heart-and-soul/design-language.md` Butler hue scope: butler hue is
  letter-mark only; KPI cells do not receive butler-hue backgrounds.

#### Scenario: KPI quartet renders four panels

- **WHEN** a tab renders its KPI quartet
- **THEN** exactly 4 single-span Panels SHALL be rendered side by side in the
  first grid row
- **AND** each Panel SHALL show label, value, and optional sub-line
- **AND** all values SHALL use tabular-nums

#### Scenario: KPI tone on elevated error count

- **WHEN** a KPI cell's metric indicates a degraded state (e.g., error count > 0)
- **THEN** the value text SHALL be colored using the appropriate severity token
- **AND** the token SHALL NOT be an oklch literal or hex value

#### Scenario: KPI sub-line delta

- **WHEN** a KPI cell carries a comparison sub-line (e.g., "+3 today")
- **THEN** the sub-line SHALL be rendered at 11px muted text below the value
- **AND** positive deltas SHALL use `--severity-low`; negative deltas
  SHALL use `--severity-medium` or `--severity-high` per the tab's definition

---

### Requirement: RangeToggle vocabulary

Tabs that aggregate data over a user-selectable time range SHALL expose a
`RangeToggle` control with exactly three options: `24h`, `7d`, `30d`. The
vocabulary MUST be consistent across all tabs that use a range.

Rules:
- Labels are monospace (JetBrains Mono), lowercase, no units spelled out.
- Exactly one RangeToggle per page. If a tab uses a range, there is one toggle
  for the whole tab body; panels that don't use the range ignore it.
- Tabs that do not use a time range (Logs, Approvals, Config) SHALL NOT render a
  RangeToggle.
- The selected range controls the activity chart variant and the KPI quartet
  comparison period.
- Default range for all resident tabs is `24h`.

**Doctrine citations:**
- `about/heart-and-soul/design-language.md` Type system: JetBrains Mono for
  mono labels.
- `redesign-detail-page-tab-vocabulary`: resident tabs only; operator tabs
  do not receive RangeToggle unless their own spec adds it.

#### Scenario: RangeToggle default state

- **WHEN** a tab that uses ranges (Activity, Spend, Memory) is first mounted
- **THEN** the RangeToggle SHALL default to `24h`
- **AND** the selected option SHALL be visually distinguished from unselected options

#### Scenario: RangeToggle absent for non-range tabs

- **WHEN** the Logs tab or Approvals tab is the active tab
- **THEN** no RangeToggle SHALL be rendered anywhere on the page

---

### Requirement: Activity tab

The Activity tab SHALL be the per-butler analytics surface. It replaces the current
"Activity (coming soon)" stub and MUST render a panel-grid body with a KPI quartet,
activity chart, and kind breakdown panel.

Layout (panel-grid frame, 4 columns):

- **Row 1:** KPI quartet (4 single-span panels):
  - Sessions: count over the selected range. Primary 28px value. Sub-line:
    change vs. prior period (e.g., "+2 vs. yesterday"). Tone: neutral.
  - p50 latency: median session duration in seconds. 28px. Sub-line: "median".
    Tone: amber if p50 > threshold (threshold TBD by implementation).
  - p95 latency: 95th-percentile session duration. 28px. Sub-line: "95th pct".
    Tone: amber if p95 > threshold.
  - Errors: count of sessions with `exit_code != 0` or error flag over range.
    28px. Tone: `--severity-high` when > 0, else neutral.
- **Row 2:** Full-width panel (span=4), title "session activity":
  - When range=`24h`: renders `<ActivityStripe>` (24 hourly columns).
  - When range=`7d` or `30d`: renders `<DayBars7d30d>` (7 or 30 daily bars).
  - Panel height fixed at 120px.
- **Row 3:** Kind breakdown panel (span=4), title "session kinds":
  - Lists each `(trigger_source, count)` pair returned by the kinds analytics
    endpoint. One row per kind. Counts in tabular-nums 14px. Empty state: "No
    session data for this range."

Source data (Layer B beads, not added by this spec):
- Hourly sessions: `GET /api/butlers/{name}/analytics/hourly` (bu-iuol4.4)
- Daily sessions: `GET /api/butlers/{name}/analytics/daily` (bu-iuol4.5)
- Latency: `GET /api/butlers/{name}/analytics/latency` (bu-iuol4.6)
- Kinds: `GET /api/butlers/{name}/analytics/kinds` (bu-iuol4.7)

**Doctrine citations:**
- `about/heart-and-soul/design-language.md` Non-Negotiable 4: all timestamps
  via `<Time>`; no `toLocaleString()`.
- `redesign-detail-page-tab-vocabulary`: Activity is a resident-mode tab;
  operator Sessions tab is unchanged.
- `redesign-butler-detail-no-hero`: no Tier 2 hero; Activity tab is in the
  primary slot inside `<Page archetype="detail">`.

#### Scenario: Activity tab KPI quartet

- **WHEN** the Activity tab loads with range=`24h`
- **THEN** 4 KPI cells SHALL be rendered: sessions count, p50 latency, p95
  latency, and error count
- **AND** all values SHALL use 28px tabular-nums in JetBrains Mono
- **AND** the error count cell SHALL render in `--severity-high` when > 0

#### Scenario: Activity stripe for 24h range

- **WHEN** the Activity tab range is `24h`
- **THEN** the activity panel SHALL render `<ActivityStripe>` with 24 hourly
  columns derived from the hourly analytics endpoint
- **AND** the panel height SHALL be 120px fixed

#### Scenario: Day bars for 7d or 30d range

- **WHEN** the Activity tab range is `7d` or `30d`
- **THEN** the activity panel SHALL render `<DayBars7d30d>` with the
  corresponding number of daily bars from the daily analytics endpoint
- **AND** the panel height SHALL be 120px fixed

#### Scenario: Kind breakdown panel

- **WHEN** the kinds analytics endpoint returns results
- **THEN** the kind breakdown panel SHALL list each trigger source and its count
- **AND** counts SHALL be tabular-nums

#### Scenario: Activity tab empty state

- **WHEN** all analytics endpoints return zero data for the selected butler
- **THEN** each panel SHALL show an inline empty state: "No session data for
  this range."
- **AND** the KPI cells SHALL render `--` for the value rather than `0` or
  a loading state

---

### Requirement: Logs tab

The Logs tab SHALL be the structured log viewer for a butler's daemon output. It
replaces the current "Logs (coming soon)" stub and MUST render a full-width scroll
panel with level filter chips and fixed-column mono log lines.

Layout (panel-grid frame, 4 columns):

- **Row 1:** Full-width panel (span=4), title "raw log", sub "poll · 5s":
  - Filter chips row above the log list: ALL / INFO / DEBUG / WARN / ERROR.
    Only one chip active at a time. ALL is the default.
  - Log list below the chips. Each line is a monospace 11px row with three
    fixed-width columns:
    - Timestamp: 78px fixed, JetBrains Mono, rendered via `<Time>` at
      millisecond-precision (e.g., "08:30:01.234"). This requires a new
      `precision="ms"` or `format` prop on `<Time>` (tracked as part of
      bu-iuol4.17 implementation scope).
    - Level: 56px fixed, JetBrains Mono. Color: INFO = `--muted-foreground`,
      DEBUG = `--muted-foreground`, WARN = `--severity-medium`, ERROR =
      `--severity-high`.
    - Message: flex remaining width, JetBrains Mono, no wrap.
  - The panel body is a scroll region. Default height: 480px.
  - Auto-scroll opt-in via a toggle in the panel header. When enabled, the list
    scrolls to the newest entry on each poll cycle. When disabled, scroll
    position is preserved.

Data source: `GET /api/butlers/{name}/logs?level=<level>&limit=<n>` (bu-iuol4.10).
Poll interval: 5 seconds while the tab is visible.

**Doctrine citations:**
- `about/heart-and-soul/design-language.md` Non-Negotiable 4: all timestamps via
  `<Time>`; millisecond-precision display requires a `<Time>` extension
  (new `precision="ms"` value) to be landed in bu-iuol4.17.
- `about/heart-and-soul/design-language.md` Type system: JetBrains Mono for
  timestamps, IDs, and level indicators.
- `redesign-detail-page-tab-vocabulary`: Logs is a resident-mode tab with no
  RangeToggle.

#### Scenario: Log level filter chips

- **WHEN** the Logs tab is active
- **THEN** filter chips SHALL be rendered for ALL, INFO, DEBUG, WARN, ERROR
- **AND** exactly one chip SHALL be active at a time
- **AND** selecting a chip SHALL refetch or client-filter the log list to the
  selected level

#### Scenario: Log line column widths

- **WHEN** log lines are rendered
- **THEN** the timestamp column SHALL be 78px fixed
- **AND** the level column SHALL be 56px fixed
- **AND** the message column SHALL take the remaining flex width
- **AND** all three columns SHALL use JetBrains Mono at 11px

#### Scenario: Log level color tokens

- **WHEN** a log line has level WARN
- **THEN** the level text SHALL be colored `--severity-medium`
- **AND** no oklch literal or hex color SHALL be used

- **WHEN** a log line has level ERROR
- **THEN** the level text SHALL be colored `--severity-high`

#### Scenario: Logs tab auto-scroll

- **WHEN** the auto-scroll toggle is enabled
- **THEN** the log list SHALL scroll to the bottom after each poll delivers new entries
- **AND** manual scrolling upward SHALL NOT be prevented while auto-scroll is on

#### Scenario: Logs tab empty state

- **WHEN** the logs endpoint returns zero entries for the selected level
- **THEN** the scroll panel SHALL display "No log entries." in muted text
- **AND** no em-dash SHALL appear in the empty state text

---

### Requirement: Approvals tab

The Approvals tab SHALL list pending approval actions scoped to the current butler.
It replaces the current "Approvals (coming soon)" stub and MUST render a full-width
scroll panel with severity-dot rows and the settled empty-state copy.

Layout (panel-grid frame, 4 columns):

- **Row 1:** Full-width panel (span=4), title "pending approvals":
  - Scroll body listing pending `ApprovalAction` items filtered to this butler.
  - Each row in the list:
    - An 8px severity dot: `high` severity = `--destructive` fill; `medium`
      severity = `--severity-medium` fill; `low` severity =
      `--muted-foreground` fill.
    - Title: 14px Inter Tight, `--foreground`.
    - Sub-line: 10px JetBrains Mono, `--muted-foreground`. Shows the detail
      snippet and age (e.g., "approve tool call · 3m ago").
    - Action link: "Review" text link navigating to the approval detail.
  - Empty state (no pending items): "No items pending review." Muted text,
    sentence case, no em-dash, no exclamation mark.
  - The panel body is a scroll region with default height 480px.

Data source: existing `/api/approvals/actions` endpoint via `useApprovals`,
filtered client-side by butler name. No new backend changes.

**Doctrine citations:**
- `about/heart-and-soul/design-language.md` Non-Negotiable 1: severity dot fill
  colors MUST use named tokens, not oklch literals.
- `about/heart-and-soul/design-language.md` Voice: "No items pending review."
  is sentence case, no em-dash, no exclamation.
- `redesign-detail-page-tab-vocabulary`: Approvals is a resident-mode tab with
  no RangeToggle.

#### Scenario: Approvals list with pending items

- **WHEN** the Approvals tab loads for a butler with pending approval actions
- **THEN** each pending item SHALL be rendered with a severity dot, title,
  sub-line, and action link
- **AND** the severity dot for a high-severity item SHALL use `--destructive` fill
- **AND** the severity dot for a medium-severity item SHALL use `--severity-medium` fill
- **AND** the severity dot for a low-severity item SHALL use `--muted-foreground` fill

#### Scenario: Approvals empty state

- **WHEN** no pending approvals exist for the butler
- **THEN** the panel SHALL display "No items pending review." in muted text
- **AND** the text SHALL be sentence case with no em-dash, no exclamation mark

#### Scenario: Approvals age rendering

- **WHEN** a pending approval item is rendered
- **THEN** the age displayed in the sub-line SHALL use `<Time>` for relative
  formatting (e.g., "3m ago")
- **AND** no raw `toLocaleString()` or `Date.now()` difference SHALL be used

---

### Requirement: Spend tab

The Spend tab SHALL be the per-butler cost analytics surface. It replaces the current
"Spend (coming soon)" stub and MUST render a KPI quartet, spend trend chart, and model
breakdown panel.

Layout (panel-grid frame, 4 columns):

- **Row 1:** KPI quartet (4 single-span panels):
  - Today: butler's USD cost today. Primary 28px. Sub-line: "today". Tone: amber
    if today spend exceeds yesterday's total.
  - 30-day: butler's USD cost over the last 30 days. 22px. Sub-line: "30 days".
  - Per-session: average cost per session over the selected range. 22px.
    Sub-line: "per session".
  - Tokens: input/output token ratio displayed as two values. 22px each.
    Sub-line: "in / out". Tone: neutral.
- **Row 2:** Full-width panel (span=4), title "spend trend":
  - Bar chart showing daily spend over the selected range (7 bars for 7d, 30
    bars for 30d, 24 hourly bars for 24h).
  - Panel height fixed at 120px.
- **Row 3:** Full-width panel (span=4), title "by model":
  - KV list: each row shows `model name` (left, `--muted-foreground`) and `cost`
    (right, tabular-nums, `--foreground`). Rows sorted by cost descending.
  - Empty state: "No model cost data."

Source data: `useCostSummary` and butler-scoped cost analytics endpoints (Layer B,
bu-iuol4.8/bu-iuol4.9). No new `ButlerSummary` fields.

**Doctrine citations:**
- `about/heart-and-soul/design-language.md` Non-Negotiable 1: amber tone on
  today cell uses `--severity-medium`, not oklch.
- `about/heart-and-soul/design-language.md` Type system: tabular-nums on all
  cost and token values.
- `redesign-detail-page-tab-vocabulary`: Spend is a resident-mode tab.

#### Scenario: Spend KPI quartet

- **WHEN** the Spend tab loads
- **THEN** 4 KPI cells SHALL be rendered: today, 30-day, per-session, tokens
- **AND** all cost values SHALL be formatted as USD (e.g., "$0.04")
- **AND** the today cell SHALL apply `--severity-medium` tone when today's spend
  exceeds the prior day's total

#### Scenario: Spend trend bar chart

- **WHEN** the Spend tab range is `24h`
- **THEN** the spend trend panel SHALL render 24 hourly bars
- **WHEN** the range is `7d`
- **THEN** 7 daily bars SHALL be rendered
- **WHEN** the range is `30d`
- **THEN** 30 daily bars SHALL be rendered

#### Scenario: Model breakdown KV list

- **WHEN** model cost data is available
- **THEN** each model SHALL be listed with its cost in a KV pair
- **AND** rows SHALL be sorted by cost descending
- **AND** costs SHALL use tabular-nums

#### Scenario: Spend tab empty state

- **WHEN** no spend data is available for the selected range
- **THEN** each panel SHALL show an appropriate empty state in muted text
- **AND** the KPI cells SHALL render "$0.00" or "--" as appropriate

---

### Requirement: Memory tab

The Memory tab SHALL surface the per-butler memory subsystem state. It replaces the
current resident-mode Memory tab layout (which renders `MemoryTierCards` + `MemoryBrowser`
without per-butler scope enforcement). The new layout MUST make counts and recent writes
primary via a KPI quartet and a recent-writes feed panel.

Layout (panel-grid frame, 4 columns):

- **Row 1:** KPI quartet (4 single-span panels):
  - Episodes: total episode count. Primary 28px. Sub-line: "+N today" (count of
    episodes added in the last 24h). Tone: neutral.
  - Facts: total fact count. 28px. Sub-line: "+N today". Tone: neutral.
  - Entities: total entity count. 28px. Sub-line: "+N today". Tone: neutral.
  - Rules: total rule count. 28px. Sub-line: "+N today". Tone: neutral.
- **Row 2:** Full-width panel (span=4), title "recent writes", scroll=true,
  height="320px":
  - Feed listing the most recent memory write events across episodes, facts, and
    rules. Each row: `<Time>` relative timestamp (left, 80px, `--muted-foreground`)
    + kind badge (Episode/Fact/Rule, 60px fixed) + content preview (flex, truncated
    to one line). Rows sorted by timestamp descending (newest first).
  - Empty state: "No recent memory writes." Muted text, no em-dash.

Source data: butler-scoped memory analytics endpoint (Layer B, bu-iuol4.12).
No new `ButlerSummary` fields.

**Doctrine citations:**
- `about/heart-and-soul/design-language.md` Non-Negotiable 4: all timestamps
  rendered via `<Time>`.
- `about/heart-and-soul/design-language.md` Non-Negotiable 1: kind badges use
  named tokens, not hex.
- `redesign-detail-page-tab-vocabulary`: Memory appears in both resident mode
  (this spec) and operator mode (existing Memory tab). The KPI quartet row is
  additive; the existing memory browser below remains in operator mode.

#### Scenario: Memory KPI quartet with "+N today" sub-lines

- **WHEN** the Memory tab loads
- **THEN** 4 KPI cells SHALL be rendered: episodes, facts, entities, rules
- **AND** each cell's sub-line SHALL show "+N today" where N is the count of
  writes in the last 24h
- **AND** all counts SHALL use tabular-nums

#### Scenario: Recent-writes feed scroll

- **WHEN** the recent-writes panel contains more entries than its 320px height
  can display
- **THEN** the panel body SHALL be scrollable
- **AND** no content SHALL be cut off without scroll access

#### Scenario: Memory tab empty state

- **WHEN** no memory data exists for the butler
- **THEN** the KPI cells SHALL render `0` with "+0 today" sub-lines
- **AND** the recent-writes panel SHALL display "No recent memory writes."
- **AND** the text SHALL not contain an em-dash

---

## MODIFIED Requirements

### Requirement: Config Tab

The Config tab SHALL be restyled from a card-per-section layout to a 2x2 panel-grid
block followed by a collapsed markdown accordion. This MUST replace the existing
card-per-section scenarios in the Config tab requirement.

Layout (panel-grid frame, 4 columns):

- **Row 1, panels 1-2 (span=2 each):**
  - Panel 1, title "process": shows key process facts (container name, port,
    registered duration, config path) sourced from the Overview process facts
    card (identical data, read-only copy).
  - Panel 2, title "schedule": shows all active schedules as a compact list
    (name + next-run relative time via `<Time>`). Empty state: "No schedules."
- **Row 2, panels 3-4 (span=2 each):**
  - Panel 3, title "scopes and oauth": shows each module's OAuth authorization
    status. Each module: name + status chip (authorized/unauthorized/not
    required). Empty state: "No modules with OAuth."
  - Panel 4, title "integrations": shows enabled modules as a badge list.
    Empty state: "No modules enabled."
- **Accordion block below the panel grid:** Each of the markdown config files
  (butler.toml, CLAUDE.md, AGENTS.md, MANIFESTO.md) is rendered as a collapsed
  accordion item. The accordion is collapsed by default. Expanding an item
  reveals the file content in a monospace `<pre>` block. "Not found" is shown
  when the value is null.

The existing "Formatted" / "Raw" toggle for butler.toml content is preserved
inside the accordion item for butler.toml.

**Doctrine citations:**
- `about/heart-and-soul/design-language.md` Non-Negotiable 4: `<Time>` for all
  next-run timestamps.
- `about/heart-and-soul/design-language.md` Non-Negotiable 6: no em-dashes in
  panel titles, accordion labels, or empty state text.
- `add-butler-process-facts`: process panel sources `container_name`, `port`,
  `registered_duration_seconds`, `config_path` from the already-specified
  process facts surface. No `pid` field.

#### Scenario: Config 2x2 panel grid

- **WHEN** the Config tab loads
- **THEN** 4 panels SHALL be rendered in 2 rows: process (span=2), schedule
  (span=2), scopes-oauth (span=2), integrations (span=2)
- **AND** the panels SHALL use the panel-grid frame with `border-top border-left`
  on the frame and `border-right border-bottom` on each panel

#### Scenario: Schedule panel relative timestamps

- **WHEN** the schedule panel renders a schedule's next-run time
- **THEN** the time SHALL be rendered using `<Time>` in relative mode
- **AND** no raw `toLocaleString()` or manual date arithmetic SHALL appear

#### Scenario: Config markdown accordion collapsed by default

- **WHEN** the Config tab renders
- **THEN** the butler.toml, CLAUDE.md, AGENTS.md, and MANIFESTO.md items SHALL
  be collapsed by default
- **AND** expanding an item SHALL reveal the full file content in a monospace
  `<pre>` block

#### Scenario: Config error and null states

- **WHEN** a config file value is null (e.g., no MANIFESTO.md present)
- **THEN** the accordion item SHALL display "Not found" as its expanded content
- **AND** the item SHALL still be present and expandable

---

## Source References

- `about/heart-and-soul/design-language.md` Non-Negotiable 1 (one token system),
  Non-Negotiable 2 (Page is a primitive), Non-Negotiable 4 (Time is a typed
  primitive), Non-Negotiable 6 (no em-dashes), Voice and Copy rules, Type system
  (three-family stack: Inter Tight / Source Serif 4 / JetBrains Mono), Butler
  hue scope (letter-mark only).
- `openspec/changes/redesign-detail-page-tab-vocabulary/` Gate B2 (bu-41p8z):
  resident-mode tab vocabulary settled as Overview/Activity/Logs/Approvals/Spend/
  Config/Memory.
- `openspec/changes/redesign-butler-detail-no-hero/` Gate A A2 (bu-rx6c2): no
  Tier 2 hero; primary slot is `<Tabs>`; identity stays in Overview tab.
- `openspec/changes/redesign-detail-tab-overview-card-stack/`: Overview tab
  seven-unit card stack is out of scope for this change; referenced to confirm
  boundary.
- `openspec/changes/detail-page-archetype/`: Butler detail page uses
  `<Page archetype="detail">`; tab body is the primary slot.
- `openspec/changes/add-butler-process-facts/`: Config process panel sources
  `container_name`, `port`, `registered_duration_seconds`, `config_path`;
  no `pid` field is permitted.
