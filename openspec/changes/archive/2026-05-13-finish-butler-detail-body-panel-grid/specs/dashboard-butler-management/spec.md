## MODIFIED Requirements

### Requirement: Compact body frame

All resident-mode tab bodies SHALL use a 4-column CSS grid as the composition
frame. This extends the frame contract from `redesign-detail-resident-tabs-claude-design`
with explicit responsive and mobile behavior rules.

Frame rules:

- The outermost `<div>` of each tab body receives `border-top border-left` using
  the `--border` token.
- Each `<Panel>` child receives `border-right border-bottom` using the `--border`
  token. Panels MUST NOT add their own top or left border.
- The grid MUST use explicit responsive column classes, e.g.,
  `grid-cols-1 sm:grid-cols-2 md:grid-cols-4`. The implementation MUST NOT use
  `auto-fill` or `auto-fit` grid keywords, as both can produce implicit columns
  that create unintended layouts at narrow viewports.
- A `span=1` panel occupies one column at md+ and spans the full row at sm and
  below. A `span=2` panel spans 2 columns at md+, collapses to 1 column at sm
  and below. A `span=4` panel spans the full width at every breakpoint.
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

#### Scenario: Responsive column collapse at sm

- **WHEN** a tab body with `span=2` panels renders at viewport width < 640px (sm)
- **THEN** each `span=2` panel SHALL render as full-width (span=1 effectively)
- **AND** no implicit extra columns SHALL appear

#### Scenario: No auto-fill or auto-fit

- **WHEN** the frame grid is inspected
- **THEN** the CSS MUST NOT use `repeat(auto-fill, ...)` or `repeat(auto-fit, ...)`
  in any grid-template-columns declaration
- **AND** all column counts SHALL be explicit per-breakpoint class declarations

#### Scenario: Span-4 panel is always full-width

- **WHEN** a Panel is rendered with `span={4}` at any viewport width
- **THEN** the Panel SHALL span the full frame width regardless of breakpoint
- **AND** the Panel SHALL receive `border-right border-bottom`

---

### Requirement: Overview Tab

The Butler detail Overview tab SHALL be the identity surface for the selected
butler. This requirement MODIFIES the card-stack layout from
`redesign-detail-tab-overview-card-stack` and REPLACES the seven-card layout
with a compact Panel-grid layout covering the same data content.

**Layout (panel-grid frame, 4 columns):**

- **Row 1:**
  - identity panel (span=2, title "identity"): `<ButlerMark>`, name, status
    badge, description.
  - process panel (span=2, title "process"): `container_name`, `port`,
    `registered_duration_seconds` as a human-readable duration, `config_path`.
    No `pid` field.
- **Row 2:**
  - heartbeat and eligibility panel (span=2, title "heartbeat"):
    `last_heartbeat_at` via `<Time relative>`, `heartbeat_age_seconds`, and the
    eligibility badge/restore control (active/stale/quarantined, same interaction
    semantics as the former eligibility row). Quarantine reason shown as muted
    text when present.
  - modules panel (span=2, title "modules"): module-health badge list.
    "No modules registered" when empty.
- **Row 3:**
  - cost panel (span=1, title "cost today"): today's USD cost, percent share of
    global total, global total.
  - recent sessions panel (span=3, title "recent sessions"): up to 5 most recent
    sessions. Each row: `<Time relative>` timestamp, trigger source, duration,
    status badge.
- **Row 4:**
  - activity feed panel (span=4, title "activity", scroll=true, height="320px"):
    merged event stream from `GET /api/butlers/{name}/activity-feed`. Each row:
    `<Time relative>` timestamp (left, 80px), event-type badge
    (session / approval / memory, 80px), summary text (flex, truncated to one line).
    Sorted newest first. Empty state: "No recent activity."

**Data source constraints:**

- Identity and process: `useButler(name)` and `GET /api/butlers/{name}`.
- Process facts: `container_name`, `port`, `registered_duration_seconds`,
  `config_path`. Source: `add-butler-process-facts`. No `pid` field.
- Heartbeat: `useButlerHeartbeats()` (`GET /api/system/butlers/heartbeat`).
- Eligibility: `useRegistry()` and `setEligibility()` mutation.
- Modules: `GET /api/butlers/{name}/modules` via `_get_module_health_via_mcp`.
- Cost: `useCostSummary("today").by_butler[name]`.
- Recent sessions: `useButlerSessions(name, { limit: 5 })`.
- Activity feed: `useButlerActivityFeed(name)` wrapping
  `GET /api/butlers/{name}/activity-feed`. This function DOES NOT currently
  exist in `frontend/src/api/client.ts` and is a required backend/client
  contract (see API contracts requirement).

**No Tier 2 hero.** The identity panel is inside the Overview tab body. No
identity content, hero block, or action strip SHALL appear between the Page
header and the `<Tabs>` block. Gate A A2 from `redesign-butler-detail-no-hero`
is preserved.

**Doctrine citations:**
- `about/heart-and-soul/design-language.md` Non-Negotiable 4: all timestamps
  via `<Time>`.
- `about/heart-and-soul/design-language.md` Non-Negotiable 1: all panel and
  badge colors via CSS variable tokens.
- `redesign-butler-detail-no-hero`: no Tier 2 hero.
- `add-butler-process-facts`: process facts contract (no `pid`).

#### Scenario: Overview panel grid renders

- **WHEN** the Overview tab loads for a resolved butler
- **THEN** the panel-grid frame SHALL render with exactly 7 panels across 4 rows
- **AND** each panel SHALL use the `<Panel>` atom (not a `<Card>` primitive)
- **AND** the identity panel SHALL display `<ButlerMark>`, the butler name,
  status badge, and description

#### Scenario: Process panel has no pid

- **WHEN** the process panel renders
- **THEN** it SHALL display `container_name`, `port`, `registered_duration_seconds`
  (human-readable), and `config_path`
- **AND** no `pid` field SHALL appear anywhere in the process panel DOM

#### Scenario: Heartbeat and eligibility in one panel

- **WHEN** the heartbeat panel renders for a butler with an active registry entry
- **THEN** `last_heartbeat_at` SHALL be rendered via `<Time relative>`
- **AND** an eligibility badge SHALL appear showing the current eligibility state
- **AND** clicking a quarantined or stale badge SHALL trigger the
  `setEligibility(name, "active")` mutation

#### Scenario: Activity feed panel populated

- **WHEN** the activity-feed endpoint returns events
- **THEN** the activity feed panel SHALL render each event with a relative
  timestamp via `<Time>`, an event-type badge, and a summary text
- **AND** events SHALL be sorted newest first

#### Scenario: Activity feed empty state

- **WHEN** the activity-feed endpoint returns zero events
- **THEN** the activity feed panel SHALL display "No recent activity."
- **AND** no em-dash SHALL appear in the empty state text

#### Scenario: Overview unified loading state

- **WHEN** any data source for the Overview tab is still loading on first mount
- **THEN** the tab SHALL show a Panel-grid skeleton matching the 4-row layout
- **AND** no partial or partially-loaded content SHALL flash before all initial
  data resolves

#### Scenario: Overview error state

- **WHEN** a data source request for the Overview tab fails
- **THEN** the affected panel SHALL show an inline error message in destructive
  text styling
- **AND** panels whose data loaded successfully SHALL continue to render their
  content

#### Scenario: No Recent Notifications card

- **WHEN** the Overview tab renders
- **THEN** no panel titled "Recent Notifications" or "Notifications" SHALL appear
- **AND** notification-type events SHALL be surfaced exclusively via the activity
  feed panel

---

### Requirement: Config Tab

The Config tab SHALL render a compact 2x2 Panel-grid block followed by a
collapsed markdown accordion. This MODIFIES the requirement from
`redesign-detail-resident-tabs-claude-design` to add explicit scenarios for the
accordion surface and confirm all implementation-level constraints.

**Layout (panel-grid frame, 4 columns):**

- **Row 1:**
  - process panel (span=2, title "process"): `container_name`, `port`,
    `registered_duration_seconds` as human-readable, `config_path`. No `pid`.
  - schedule panel (span=2, title "schedule"): active schedules as a compact
    list (name + next-run via `<Time relative>`). Empty state: "No schedules."
- **Row 2:**
  - scopes-oauth panel (span=2, title "scopes and oauth"): each module's OAuth
    authorization status (module name + status chip: authorized / unauthorized /
    not required). Empty state: "No modules with OAuth."
  - integrations panel (span=2, title "integrations"): enabled modules as a
    badge list. Empty state: "No modules enabled."
- **Accordion block (below panel grid):** Four items, collapsed by default:
  butler.toml, CLAUDE.md, AGENTS.md, MANIFESTO.md. Each item expands to
  reveal the file content in a monospace `<pre>` block. "Not found" when null.
  The butler.toml item preserves the Formatted/Raw toggle inside its expanded
  content. The `RuntimeConfigCard` MUST NOT appear in the Config tab layout.

**Doctrine citations:**
- `about/heart-and-soul/design-language.md` Non-Negotiable 4: `<Time>` for all
  next-run timestamps.
- `about/heart-and-soul/design-language.md` Non-Negotiable 6: no em-dashes in
  panel titles, accordion labels, or empty state text.
- `add-butler-process-facts`: process panel sources from the same contract as
  the Overview process panel; no `pid` field.

#### Scenario: Config 2x2 panel grid

- **WHEN** the Config tab loads
- **THEN** 4 panels SHALL be rendered in 2 rows: process (span=2), schedule
  (span=2), scopes-oauth (span=2), integrations (span=2)
- **AND** the panels SHALL use the panel-grid frame

#### Scenario: No RuntimeConfigCard in Config tab

- **WHEN** the Config tab renders
- **THEN** `<RuntimeConfigCard>` SHALL NOT appear in the Config tab DOM

#### Scenario: Schedule panel relative timestamps

- **WHEN** the schedule panel renders a schedule's next-run time
- **THEN** the time SHALL be rendered using `<Time>` in relative mode
- **AND** no raw `toLocaleString()` or manual date arithmetic SHALL appear

#### Scenario: Config accordion collapsed by default

- **WHEN** the Config tab renders
- **THEN** the butler.toml, CLAUDE.md, AGENTS.md, and MANIFESTO.md accordion
  items SHALL all be collapsed by default
- **AND** expanding an item SHALL reveal the full file content in a monospace
  `<pre>` block

#### Scenario: Config accordion null content

- **WHEN** a config file value is null (e.g., no MANIFESTO.md present)
- **THEN** the accordion item SHALL display "Not found" as its expanded content
- **AND** the item SHALL still be present and expandable

#### Scenario: Config error state

- **WHEN** the config API request fails
- **THEN** an inline error message SHALL be shown in destructive text styling
  with the failure reason
- **AND** the error state SHALL render inside a Panel, not a bare Card

#### Scenario: Config process panel has no pid

- **WHEN** the process panel in the Config tab renders
- **THEN** it SHALL NOT include a `pid` field
- **AND** the data source SHALL be the same process-facts endpoint as the
  Overview process panel

---

### Requirement: Memory Tab

The Memory tab SHALL surface per-butler memory subsystem state. This MODIFIES
the existing Memory tab requirement and the resident-mode requirement from
`redesign-detail-resident-tabs-claude-design` to enforce Panel-grid atoms and
per-butler scoping for KPI counts.

**Layout (panel-grid frame, 4 columns):**

- **Row 1:** KPI quartet (4 single-span panels using `<Panel>` atoms):
  - episodes panel (title "episodes"): per-butler total episode count. Primary
    28px. Sub-line: "+N today" derived from `episodes_24h`. Tone: neutral.
  - facts panel (title "facts"): per-butler total fact count. 28px. Sub-line:
    "+N today". Tone: neutral.
  - entities panel (title "entities"): per-butler total entity count. 28px.
    Sub-line: "+N today". Tone: neutral.
  - rules panel (title "rules"): per-butler total rule count. 28px. Sub-line:
    "+N today". Tone: neutral.
- **Row 2:** Full-width panel (span=4, title "recent writes", scroll=true,
  height="320px"):
  - Feed listing the most recent episodes for this butler via
    `useMemoryRecentWrites(name, 10)`. Each row: `<Time>` relative timestamp
    (left, 80px), butler name label (90px mono), content preview (flex,
    truncated to one line). Sorted newest first.
  - Empty state: "No memory writes recorded yet."

**Data source constraints:**

- KPI counts and 24h deltas: `useButlerMemoryStats(name)` wrapping
  `GET /api/butlers/{name}/memory/stats`. This is a new endpoint (see API
  contracts requirement). The global `useMemoryStats()` endpoint MUST NOT be
  used as the KPI data source.
- Recent writes: `useMemoryRecentWrites(name, 10)` wrapping
  `GET /api/memory/episodes?butler={name}&limit=10`.

**Doctrine citations:**
- `about/heart-and-soul/design-language.md` Non-Negotiable 4: timestamps via
  `<Time>`.
- `about/heart-and-soul/design-language.md` Non-Negotiable 1: KPI tone colors
  via named tokens; no hex.
- `redesign-detail-resident-tabs-claude-design` Panel atom: `<Panel>` is
  required for KPI cells; `<Card>` primitives MUST NOT wrap KPI content.

#### Scenario: Memory KPI quartet uses Panel atoms

- **WHEN** the Memory tab renders its KPI row
- **THEN** exactly 4 `<Panel>` atoms SHALL be rendered for episodes, facts,
  entities, and rules
- **AND** no `<Card>` primitive SHALL wrap any KPI cell
- **AND** all count values SHALL use tabular-nums

#### Scenario: KPI counts are per-butler

- **WHEN** the Memory tab renders KPI counts
- **THEN** the counts SHALL come from `GET /api/butlers/{name}/memory/stats`
  scoped to the current butler
- **AND** global `GET /api/memory/stats` SHALL NOT be called from this tab

#### Scenario: KPI "+N today" sub-lines populated

- **WHEN** the per-butler memory stats response includes `episodes_24h > 0`
- **THEN** the episodes panel sub-line SHALL render "+N today" where N equals
  `episodes_24h`
- **AND** when `episodes_24h` is 0, the sub-line SHALL render "+0 today"

#### Scenario: Memory tab loading state

- **WHEN** either the per-butler stats request or the recent-writes request is
  in flight on first mount
- **THEN** the tab SHALL render Panel-skeleton placeholders for the KPI row and
  the recent-writes panel
- **AND** no partial count values SHALL flash during loading

#### Scenario: Memory tab empty state

- **WHEN** `GET /api/butlers/{name}/memory/stats` returns all-zero counts
- **THEN** each KPI panel SHALL render 0 with "+0 today" sub-lines
- **AND** the recent-writes panel SHALL display "No memory writes recorded yet."
- **AND** the empty state text SHALL not contain an em-dash

---

### Requirement: Switchboard Routing Log Tab

The Switchboard Routing Log tab SHALL use the panel-grid frame vocabulary.
This MODIFIES the existing requirement by removing the `<Card>` wrapper and
replacing it with a `<Panel>` atom.

Layout (panel-grid frame, 4 columns):

- **Row 1:** Full-width panel (span=4, title "routing log", scroll=true,
  height="480px"):
  - The `<RoutingLogTable>` component renders as the panel body, unchanged.
  - The panel provides the scroll region; `RoutingLogTable` itself does not
    scroll.

The existing table column behavior (Timestamp, Source, Target, Tool, Status,
Duration, Error), filter inputs, and pagination controls are unchanged.

**Doctrine citations:**
- `about/heart-and-soul/design-language.md` Non-Negotiable 1: panel border
  via `--border` token.
- `redesign-detail-resident-tabs-claude-design` Panel atom: `<Panel>` is
  the normative container; `<Card>` MUST NOT wrap the table.

#### Scenario: Routing log uses Panel atom

- **WHEN** the Routing Log tab renders on the switchboard butler page
- **THEN** the table SHALL be wrapped in a `<Panel>` atom with `span={4}`
- **AND** no `<Card>` primitive SHALL wrap `<RoutingLogTable>`

#### Scenario: Routing log scroll region

- **WHEN** the routing log has more entries than the 480px panel height shows
- **THEN** the panel body SHALL be scrollable
- **AND** the pagination controls SHALL remain visible outside the scroll region

#### Scenario: Routing log empty state

- **WHEN** the routing log endpoint returns zero entries
- **THEN** the panel body SHALL display "No routing activity." in muted text
- **AND** no em-dash SHALL appear in the empty state text

---

### Requirement: Switchboard Registry Tab

The Switchboard Registry tab SHALL use the panel-grid frame vocabulary.
This MODIFIES the existing requirement by removing the `<Card>` wrapper and
replacing it with a `<Panel>` atom.

Layout (panel-grid frame, 4 columns):

- **Row 1:** Full-width panel (span=4, title "butler registry"):
  - The `<RegistryTable>` component renders as the panel body, unchanged.

The existing table column behavior (Name, Endpoint URL, Modules, Description,
Last Seen) and module normalization logic are unchanged.

**Doctrine citations:**
- `about/heart-and-soul/design-language.md` Non-Negotiable 1: panel border
  via `--border` token.
- `redesign-detail-resident-tabs-claude-design` Panel atom: `<Panel>` is
  the normative container; `<Card>` MUST NOT wrap the table.

#### Scenario: Registry uses Panel atom

- **WHEN** the Registry tab renders on the switchboard butler page
- **THEN** the table SHALL be wrapped in a `<Panel>` atom with `span={4}`
- **AND** no `<Card>` primitive SHALL wrap `<RegistryTable>`

#### Scenario: Registry empty state

- **WHEN** no butlers are registered in the switchboard
- **THEN** the panel body SHALL display an empty state message
- **AND** the message SHALL not contain an em-dash

---

## ADDED Requirements

### Requirement: API and client contracts for tab data

The following backend endpoint and frontend client function SHALL be implemented
as required contracts for the Overview and Memory tab beads. These contracts
MUST be in place before or alongside the frontend implementation beads they gate.

#### Contract A: Per-butler memory stats endpoint

`GET /api/butlers/{name}/memory/stats` SHALL return a `ButlerMemoryStats`
response containing:
- `total_episodes` (integer)
- `episodes_24h` (integer, count of episodes created in the last 24 hours)
- `total_facts` (integer)
- `facts_24h` (integer)
- `total_entities` (integer)
- `entities_24h` (integer)
- `total_rules` (integer)
- `rules_24h` (integer)

The endpoint SHALL query the butler's own schema tables (`{schema}.episodes`,
`{schema}.facts`, `{schema}.rules`) and the public entities table filtered by
`butler_name`. It SHALL degrade gracefully when a butler does not have the
memory module enabled (return all zeros, not a 404 or 500).

#### Contract B: Activity-feed frontend client function

`getButlerActivityFeed(name: string, limit?: number)` SHALL be added to
`frontend/src/api/client.ts` as a wrapper for
`GET /api/butlers/{name}/activity-feed`. Return type: `ApiResponse<ActivityFeed>`.
A `useButlerActivityFeed(name, limit?)` hook SHALL be added to
`frontend/src/hooks/use-butlers.ts` wrapping the function with TanStack Query,
polling every 30 seconds.

#### Scenario: Per-butler memory stats returns zeros for butler without memory module

- **WHEN** `GET /api/butlers/{name}/memory/stats` is called for a butler that
  has no memory module enabled
- **THEN** the response SHALL be HTTP 200 with all count fields set to 0
- **AND** no 404 or 500 SHALL be returned for an absent memory schema

#### Scenario: Activity-feed client function is callable from Overview tab

- **WHEN** the Overview tab mounts for butler `"relationship"`
- **THEN** `useButlerActivityFeed("relationship")` SHALL issue a request to
  `GET /api/butlers/relationship/activity-feed`
- **AND** the returned events SHALL be rendered in the activity feed panel

---

### Requirement: Rejected visual exemplar elements

Implementations SHALL NOT adopt the following patterns from the visual exemplar
at `pr/overview/specific-butler-page-redesign/`. Each pattern is explicitly
rejected and MUST NOT appear in any production component authored for this spec:

1. **`pid` field.** The exemplar renders `pid` in the process facts row and as
   a KV entry. No tab in the butler detail page SHALL display `pid`. The
   `add-butler-process-facts` contract explicitly excludes `pid`; only
   `container_name`, `port`, `registered_duration_seconds`, and `config_path`
   are permitted.

2. **Body hero section.** The exemplar renders a large hero block below the
   page header and above the tab rail. No such element is permitted. Gate A A2
   from `redesign-butler-detail-no-hero` prohibits any Tier 2 hero. The
   identity panel lives inside the Overview tab body.

3. **Fictional butler names.** The exemplar's data file references butler names
   `calendar` and `household`, which do not exist in the real roster from
   `useButlers()`. No butler name SHALL be hardcoded in any render path.
   All butler lists MUST be sourced from `useButlers()`.

4. **Hardcoded mock data.** The exemplar uses `window.BUTLERS_DATA` for all
   data. No hardcoded or mock data SHALL appear in production components.

5. **Raw color values.** The exemplar uses inline `style={{ color: C.amber }}`
   and `oklch(...)` literals. No hex, oklch, or rgb literal SHALL appear in
   any JSX; all colors MUST use CSS variable tokens.

#### Scenario: No pid in any Overview or Config panel

- **WHEN** the Overview tab or Config tab renders for any butler
- **THEN** no element with text content containing "pid" (case-insensitive) SHALL
  appear in the process facts area
- **AND** the process data SHALL be limited to container name, port, registered
  duration, and config path

#### Scenario: No body hero between header and tabs

- **WHEN** the butler detail page renders
- **THEN** no element with role "region" or class "hero" SHALL appear between
  the `<Page>` shell header and the `<Tabs>` block
- **AND** the first child of the `<Tabs>` block SHALL be the tab trigger list

#### Scenario: No fictional butler names

- **WHEN** the sibling nav strip or any butler list in the tab body renders
- **THEN** only butlers returned by `useButlers()` from the live API SHALL appear
- **AND** the names "calendar" and "household" SHALL NOT appear in any rendered
  butler list

---

## Source References

- `about/heart-and-soul/design-language.md` Non-Negotiable 1 (one token
  system), Non-Negotiable 2 (Page is a primitive), Non-Negotiable 4 (Time is a
  typed primitive), Non-Negotiable 6 (no em-dashes), Voice and Copy rules,
  Type system (three-family stack), Butler hue scope.
- `openspec/changes/redesign-detail-resident-tabs-claude-design/`: Panel atom,
  KPI quartet, RangeToggle, Activity/Logs/Approvals/Spend/Memory/Config
  requirements (this change extends and ratifies those decisions).
- `openspec/changes/redesign-detail-tab-overview-card-stack/`: Overview
  seven-card stack (MODIFIED by this change).
- `openspec/changes/redesign-butler-detail-no-hero/` Gate A A2 (bu-rx6c2):
  no Tier 2 hero; identity stays inside the Overview tab.
- `openspec/changes/add-butler-process-facts/`: process facts contract,
  explicitly prohibits `pid`.
- `openspec/changes/2026-05-13-extend-butler-detail-status-board-chrome/`
  (archive): chrome layer complete; this change gates the body layer.
- `src/butlers/api/routers/activity_feed.py`: backend activity-feed endpoint,
  merged, no frontend client function yet.
- `frontend/src/components/butler-detail/ButlerMemoryTab.tsx`: current
  implementation using `<Card>` wrappers and global `useMemoryStats()`.
- `frontend/src/components/butler-detail/ButlerRoutingLogTab.tsx`: current
  `<Card>` wrapper to be replaced.
- `frontend/src/components/butler-detail/ButlerRegistryTab.tsx`: current
  `<Card>` wrapper to be replaced.
