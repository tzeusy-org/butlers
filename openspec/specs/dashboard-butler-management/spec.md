# Dashboard Butler Management

## Purpose
Defines the dashboard surfaces for managing butlers as first-class entities: a fleet-wide butler list page, a per-butler detail page with 10+ tabbed views, and switchboard-specific operational surfaces. Together these views give the operator full visibility into butler identity, health, configuration, scheduling, state, memory, MCP tooling, session history, and (for the switchboard) registry, routing, triage, and backfill management. The dashboard is both an observability surface and a write-capable control plane -- operators can create schedules, mutate state, trigger sessions, invoke MCP tools, manage triage rules, and control backfill jobs without leaving the browser.

## Requirements

### Requirement: Butler List Page

The `/butlers` page SHALL render as a status board: a page-level header strip
showing fleet health at a glance, a unified 4-column grid of butler cells
sorted by recent activity, and a footer KPI band summarising fleet state.

The page SHALL use `<Page archetype="status-board">` as its outer shell. The
archetype shell owns the header strip, the cell grid slot, the footer band
slot, and the page-level loading and error states. Pages MUST NOT reinvent
those surfaces.

Implementation source constraints:

- **No new `ButlerSummary` fields.** Every cell field MUST be composed from
  existing data surfaces. `ButlerSummary` is defined in
  `src/butlers/api/models/__init__.py:101-120` and exposes `name`, `status`,
  `port`, `type`, `description`, and `sessions_24h`.
  The list router constructs summaries in
  `src/butlers/api/routers/butlers.py:124-131`.
  Note: `active_session_count` is NOT a `ButlerSummary` field; it comes from
  `useButlerHeartbeats` (the `ButlerHeartbeat.active_session_count` field,
  `frontend/src/api/types.ts:3626`).
- Cell data MUST be composed exclusively from these five existing data surfaces:
  1. `useButlers` -- butler list (`ButlerSummary` fields)
  2. `useRegistry` -- eligibility state (`RegistryEntry.eligibility_state`)
     via `frontend/src/hooks/use-general.ts:24-30`,
     `frontend/src/api/client.ts:1137-1140`,
     `frontend/src/api/types.ts:1055-1063`
  3. `useButlerHeartbeats` -- last-seen / heartbeat age / active session count
     via `frontend/src/hooks/use-system.ts:71-78`
  4. `useSpendSummary('today').by_butler` -- per-butler spend today via
     `frontend/src/hooks/use-spend.ts:31-47`
  5. Sessions for the last 24h -- fetched via `useQuery(getSessions({ since: <ISO> }))`
     (no new endpoint; the existing sessions endpoint filtered by a rolling ISO
     timestamp, bucketed client-side for the activity stripe)
  - Per-butler load% denominator (`max_concurrent`) comes from the existing
    `GET /api/butlers/{name}/runtime-config` endpoint.
- The cell identity mark MUST use the existing `ButlerMark` component from
  `frontend/src/components/ui/ButlerMark.tsx`.
- The list MUST render API-provided butler and staffer rows only. No butler
  names or types may be hardcoded in the grid render path.

**Doctrine citations** (`about/heart-and-soul/design-language.md`):

- Non-negotiable 2: "The `Page` is a primitive." The status-board page uses
  `<Page archetype="status-board">` and does not reinvent its chrome.
- Non-negotiable 1: "One token system or none." No raw `oklch(...)` values,
  hex literals, or ad-hoc inline styles in cell JSX. All colors use design
  tokens.
- Non-negotiable 4: "Time is a typed primitive." The clock display in the
  header strip and all `last` timestamps in cells MUST render via `<Time>`.
- Non-negotiable 6: "No em-dashes in prose." Cell copy, chip labels, KPI
  labels, and empty-state text MUST NOT contain em-dashes.

#### Scenario: Header strip

- **WHEN** the butler list page loads
- **THEN** a header strip SHALL be displayed containing:
  - An eyebrow label (e.g., "Fleet status")
  - An `h1` reading "The staff, at a glance" styled `text-2xl font-bold tracking-tight`
  - A healthy/total pill (count of healthy butlers over total registered count,
    where healthy = total minus offline and quarantined counts,
    derived from `StatusBoardAggregates`)
  - A clock and date display rendered via `<Time mode="clock-24h-mono">` that
    updates every minute (aligned to minute boundaries via a 60-second interval)

#### Scenario: Unified cell grid sorted by activity

- **WHEN** butler and staffer list rows are loaded from the API
- **THEN** all butlers and staffers SHALL be rendered in a single 4-column grid of
  butler cells without any grouping by type
- **AND** cells SHALL be sorted by `sessions_24h` descending; ties are broken by
  name ascending
- **AND** no butler or staffer SHALL be hidden from the grid; unavailable registry
  rows SHALL render a dim `--` activity verb without removing the cell

Note: the previous Butler List Page requirement asserted "the page preserves
the existing butlers and staffers grouping." That constraint is removed by
this change. The butler/staffer distinction is preserved in the footer KPI
band composition addendum and visually in each cell's `ButlerMark` component.

#### Scenario: Butler cell composition

- **WHEN** a butler cell is rendered
- **THEN** the cell SHALL display:
  - `ButlerMark` component representing the butler's identity
  - The butler's name, capitalized
  - A role tagline sourced from `ButlerSummary.description`
  - An activity chip showing the derived activity verb (see Activity Verb
    Derivation scenario)
  - A KPI quartet: sessions in the last 24h (`sessions_24h`), spend today
    (from `useSpendSummary('today').by_butler`), load% (derived client-side),
    and last active (last heartbeat timestamp from `useButlerHeartbeats`,
    rendered via `<Time>`)
  - A 24h activity stripe pinned to the bottom of the cell, derived from
    the sessions query (`getSessions({ since: <ISO> })`) bucketed client-side
  - A hover affordance (open arrow or equivalent) linking to the butler detail
    page

#### Scenario: Activity verb derivation

- **WHEN** the activity chip is rendered for a butler cell
- **THEN** the activity verb and chip color SHALL be derived client-side from
  existing signals using this priority order:
  1. If `status = down`: verb is `offline`, chip color is red
  2. If `eligibility_state = quarantined`: verb is `quarantined`, chip color is red
  3. If `active_session_count > 0`: verb is `running`, chip is green
  4. Otherwise: verb is `idle`, chip is dim
- **AND** the backend `_probe_butler` emits only `ok` and `down`; no `degraded`
  or `waiting` status values are produced by the current implementation.
- **AND** the mockup verbs `patrol`, `consolidating`, and `ingesting` MUST NOT
  be used. These verbs imply butler-specific semantic knowledge not carried by
  `ButlerSummary` and are explicitly rejected.

#### Scenario: Load percentage

- **WHEN** the KPI quartet's load field is rendered
- **THEN** load% SHALL be derived client-side as
  `active_session_count / max_concurrent * 100`
- **AND** `max_concurrent` comes from the per-butler `runtime-config`
  (`GET /api/butlers/{name}/runtime-config`), which is the existing runtime
  config endpoint
- **AND** when `max_concurrent` is unknown or zero, the load field SHALL render as
  `--` rather than a percentage

#### Scenario: Eligibility state rail

- **WHEN** a butler cell is rendered with registry data available
- **THEN** a left-edge state rail on the cell SHALL be colored by eligibility:
  - `active` eligibility: emerald rail
  - `stale` eligibility: amber rail, chip is clickable to restore
  - `quarantined` eligibility: red rail, chip is clickable to restore
  - No matching registry entry or unavailable registry response: dim rail
- **AND** clicking a `quarantined` or `stale` chip SHALL trigger the existing
  `setEligibility(name, "active")` mutation
  (`frontend/src/hooks/use-general.ts:36-53`)
- **AND** the cell SHALL NOT be hidden for any eligibility state, including
  unavailable

#### Scenario: Footer KPI band

- **WHEN** the butler list page renders
- **THEN** a footer KPI band SHALL be displayed below the cell grid containing:
  - Active butler count (with emerald status-tone dot, shown only when count > 0)
  - Offline butler count (with red status-tone dot, shown only when count > 0)
  - Quarantined butler count (with red status-tone dot, shown only when count > 0)
  - Fleet sessions in the last 24h
  - Fleet spend today (from `useSpendSummary('today')`)
  - Fleet average load% (mean of all per-butler load% values where
    `max_concurrent` is known)
  - A composition addendum showing "Nb butlers, Ns staffers" where Nb and Ns
    are the counts of butlers and staffers respectively in the API response

#### Scenario: Loading state

- **WHEN** any butler list data request is in flight on initial load
- **THEN** the page-level skeleton SHALL render:
  - A header strip skeleton line
  - A 2x4 grid of cell skeletons (8 placeholder cells)
  - A footer band skeleton
- **AND** this skeleton SHALL be owned by the status-board archetype shell, not by
  the page component directly

#### Scenario: Error resilience with stale data

- **WHEN** a refresh request fails but prior butler data exists in cache
- **THEN** the stale butler cells SHALL remain visible in the grid
- **AND** an error banner SHALL be displayed explaining that the shown data is from
  the last successful fetch

#### Scenario: Empty state

- **WHEN** the API returns zero butler list rows
- **THEN** an empty-state message SHALL be displayed: "No butlers found" with guidance
  to check daemon status

#### Scenario: Auto-refresh polling

- **WHEN** the butler list page is mounted
- **THEN** the following polling cadences SHALL be maintained:
  - Butler list (`useButlers`): every 30 seconds
  - Registry and heartbeats (`useRegistry`, `useButlerHeartbeats`): every 30
    seconds
  - Cost summary (`useSpendSummary`): every 60 seconds
  - Header strip clock: updates every minute via `<Time mode="clock-24h-mono">`,
    which aligns to the next minute boundary then fires a 60-second interval

### Requirement: Butler Detail Page Structure
The `/butlers/:name` page SHALL be a tabbed detail view where each butler is treated as a first-class navigable entity.

#### Scenario: URL-driven tab routing
- **WHEN** a user navigates to `/butlers/:name?tab=<value>`
- **THEN** the active tab is set to the `tab` query parameter value
- **AND** when `tab` is absent or invalid, the default tab is `overview`
- **AND** tab changes update the URL via `replaceState` (no history entry)

#### Scenario: Base tabs in operator mode
- **WHEN** any butler detail page loads in operator mode
- **THEN** the following tabs SHALL be visible: Overview, Sessions, Config, Skills, Schedules, Trigger, MCP, State, CRM, Memory

#### Scenario: Conditionally shown tabs -- switchboard
- **WHEN** the butler name is `switchboard`
- **THEN** two additional tabs are shown after the base tabs: "Routing Log" and "Registry"

#### Scenario: Conditionally shown tabs -- health
- **WHEN** the butler name is `health`
- **THEN** one additional tab is shown, labeled "Measurements"

#### Scenario: Conditionally shown tabs -- general
- **WHEN** the butler name is `general`
- **THEN** one additional tab is shown: "Collections"

#### Scenario: Lazy-loaded tabs for performance
- **WHEN** a non-default tab is selected for the first time
- **THEN** its component is loaded on demand via React `lazy()` with a centered "Loading {tab}..." fallback
- **AND** the following tabs are lazy-loaded: Skills, Schedules, Trigger, MCP, State, Memory, Routing Log, Registry

#### Scenario: Tab URL semantics and deep-linking
- **WHEN** the active tab is controlled by the `?tab=` query parameter
- **THEN** `overview` is the default tab and removes the query parameter from the URL
- **AND** accepted deep-link values include all base tab keys for the active mode plus conditional tab keys for the specific butler
- **AND** tab changes update the URL via `replaceState` without creating browser history entries

### Requirement: Butler detail page outer chrome uses status-board archetype

The Butler detail page at `/butlers/:name` SHALL use `<Page archetype="status-board">`
as its outer chrome. The tab block is the sole page body content; no footer KPI band,
no breadcrumbs prop, and no ButlerHeartbeatTile are rendered at the page layer.

**Shell contract:**

1. **Archetype.** The page MUST use `<Page archetype="status-board">` as its outer
   shell. No `breadcrumbs` prop is passed; the butler detail page does not expose a
   breadcrumb trail via the Page shell.

2. **Title.** The `title` prop on `<Page>` MUST be the butler's name titleized
   (e.g., `"relationship"` → `"Relationship"`).

3. **Description.** The `description` prop on `<Page>` MUST be sourced from
   `ButlerSummary.description` when available and `undefined` otherwise.

4. **Header slot.** The `header` prop on `<Page>` MUST be
   `<ButlerDetailHeader butler={name} actions={<ButlerDetailActions butlerName={name} onModeChange={setMode} />} />`.
   `ButlerDetailHeader` renders the butler identity block (name H1, description,
   activity status, port, uptime) using `ButlerMark` for the hue mark.
   `ButlerDetailActions` renders the page-level operational controls: Force Run
   button, Logs link, Config link, Prompt (`<ChatPanel />`), and Pause/Resume button.

5. **No footer slot.** No footer KPI band (`ButlerDetailFooter` or equivalent) is
   rendered. Identity and operational data live in the header slot and Overview tab
   card respectively.

6. **Loading state.** The `loading` prop on `<Page>` MUST reflect the top-level
   butler record fetch status. Per-tab `TabFallback` fallbacks handle lazy-tab loading
   independently. The Page archetype's own loading state is distinct from tab-level
   lazy loading.

7. **Error state.** The `error` prop on `<Page>` MUST be set when the butler record
   fetch fails. An `onRetry` callback MUST be wired to invalidate the butler query.
   Individual tab errors remain tab-scoped.

8. **Body.** The `<Tabs>` block (TabsList + all TabsContent entries) is rendered as
   the direct child of `<Page>`. No additional wrapper is needed at the page layer.

#### Scenario: Page shell uses status-board archetype

- **WHEN** the butler detail page renders for any butler name
- **THEN** `<Page archetype="status-board">` MUST be the outer shell
- **AND** no `breadcrumbs` prop MUST be passed to `<Page>`
- **AND** no `ButlerHeartbeatTile` MUST be rendered at the page layer
- **AND** no footer KPI band MUST be rendered at the page layer

#### Scenario: Butler name as page title

- **WHEN** the butler detail page renders for butler `"relationship"`
- **THEN** the `title` prop on `<Page>` MUST be `"Relationship"` (titleized)
- **AND** the `description` prop MUST be sourced from `ButlerSummary.description`
  when available

#### Scenario: Header slot composition

- **WHEN** the butler detail page renders for a resolved butler
- **THEN** `<ButlerDetailHeader>` MUST be passed as the `header` prop on `<Page>`
- **AND** `<ButlerDetailActions>` MUST be passed as the `actions` prop on
  `<ButlerDetailHeader>` (NOT directly as the `actions` prop on `<Page>`)
- **AND** `ButlerDetailActions` MUST render: Force Run button, Logs link, Config
  link, Prompt button (`<ChatPanel />`), and Pause/Resume button

#### Scenario: Tabs body is the page body

- **WHEN** the butler detail page renders the tab group
- **THEN** the complete `<Tabs>` block (TabsList + all TabsContent entries) MUST be
  rendered as the direct child inside `<Page>`
- **AND** the tab structure, content, and behavior MUST be unchanged from the current
  implementation

#### Scenario: Top-level loading delegates to Page shell

- **WHEN** the butler record fetch is in flight
- **THEN** the `loading` prop on `<Page>` MUST be `true`
- **AND** the Page archetype MUST handle the loading state via its own built-in
  mechanism; no `DetailSkeleton` is explicitly passed by the page component

#### Scenario: Unknown butler shows shell error

- **WHEN** a user navigates to `/butlers/nonexistent` and the butler record fetch
  returns 404 or an error
- **THEN** the `error` prop on `<Page>` MUST be set
- **AND** `onRetry` MUST be wired to invalidate the butler query and trigger a refetch

---

### Requirement: Butler detail page tab body vocabulary

The Butler detail page tab body SHALL use a mode-gated tab vocabulary controlled by
an operator/resident toggle persisted in `localStorage`. The `<Tabs>` block is the
sole page body content inside `<Page archetype="status-board">`.

**Slot mapping:**

- **Body (Tabs block):** The `<Tabs>` block, containing `TabsList` (all visible tab
  triggers for the active mode) and `TabsContent` for each tab. This is the entire
  interactive surface for the butler workspace.
- **No hero slot:** The butler identity (name, status, description, port, uptime) is
  rendered in `ButlerDetailHeader` (the Page `header` slot) and inside the Overview
  tab card. No separate page-level hero tier is needed.
- **No drawer slot:** Credential and advanced configuration content lives inside
  individual tabs (Config tab, State tab). No top-level drawer is rendered.
- **Tabs are NOT a candidate for page-level archetype expansion:** The multi-tab
  workspace is the correct answer for this record type. Future tab consolidation is
  a separate audit concern.

**Mode vocabulary:**

- **Operator mode** (10 base tabs): Overview, Sessions, Config, Skills, Schedules,
  Trigger, MCP, State, CRM, Memory. Extension tabs Models and Manage are also shown
  in operator mode.
- **Resident mode** (7 base tabs): Overview, Activity, Logs, Approvals, Spend,
  Config, Memory. Default for first-time visitors.
- A mode-toggle control (`DetailModeSwitch`) appears at the right end of the tab bar.
  The selected mode is persisted in `localStorage` under `butlers.detail.mode`.
- Deep-linking via `?tab=` MUST auto-promote the mode to the one that contains the
  requested tab if the tab is exclusive to the other mode.

**Butler-specific conditional tabs** (appended regardless of mode):

| Butler | Additional tabs |
|---|---|
| `health` | Health |
| `switchboard` | Routing Log, Registry |
| `education` | Reviews |
| `chronicler` | Timelines |
| `finance` | Finances |
| `general` | Collections |
| `home` | Devices |
| `lifestyle` | Taste |
| `messenger` | Conversations |
| `qa` | Investigations |
| `relationship` | Contacts |
| `travel` | Trips |

#### Scenario: Operator mode base tabs

- **WHEN** the butler detail page is in operator mode
- **THEN** the following ten base tab triggers MUST be visible: Overview, Sessions,
  Config, Skills, Schedules, Trigger, MCP, State, CRM, Memory
- **AND** Models and Manage extension tabs MUST also be visible
- **AND** these are rendered inside the `<TabsList>` inside `<Page>`

#### Scenario: Resident mode base tabs

- **WHEN** the butler detail page is in resident mode
- **THEN** the following seven base tab triggers MUST be visible: Overview, Activity,
  Logs, Approvals, Spend, Config, Memory
- **AND** operator-only tabs (Sessions, Skills, Schedules, Trigger, MCP, State, CRM,
  Models, Manage) MUST NOT be visible

#### Scenario: Mode toggle and persistence

- **WHEN** a user clicks the mode toggle at the end of the tab bar
- **THEN** the tab vocabulary MUST switch between operator and resident modes
- **AND** the selected mode MUST be persisted in `localStorage` under
  `butlers.detail.mode` so it survives page reloads

---

### Requirement: Tab Structures Reference (Non-Butler Pages)

The following tab structures exist on pages outside the butler detail view and SHALL be documented here as a consolidated reference.

#### Scenario: Memory browser tabs
- **WHEN** the `/memory` page or the butler detail Memory tab is active
- **THEN** a tabbed browser shows three tabs: Facts, Rules, Episodes
- **AND** when opened inside a butler detail page, all queries are scope-filtered to that butler

#### Scenario: Contact detail tabs
- **WHEN** `/contacts/:contactId` is visited
- **THEN** a tabbed view shows five tabs: Notes, Interactions, Gifts, Loans, Activity
- **AND** each tab loads its data lazily on first selection

#### Scenario: Approvals navigation integration
- **WHEN** the approvals section is accessed from the sidebar
- **THEN** two routes are available: `/approvals` (pending action queue with filters, metrics dashboard, and decision workflows) and `/approvals/rules` (standing rules list with detail, create, and revoke flows)
- **AND** the main approvals page provides: metrics dashboard with pending count and approval/rejection/auto-approval stats, filterable action queue by tool/status/butler, action detail dialog with approve/reject/rule creation, and stale action expiry management
- **AND** the rules page provides: filterable rules list by tool/active status/butler, rule detail dialog with constraint inspection, rule revocation capability, and use count and limit tracking

### Requirement: Overview Tab

The Butler detail Overview tab SHALL be the operational overview for the
selected butler and SHALL render a responsive panel grid (`ButlerPanelGrid`,
`frontend/src/components/butler-detail/atoms.tsx:53`) with up to four KPI columns
on wide viewports. The grid SHALL contain, in order: four single-column KPI
panels (status, sessions, spend, awaiting), a span-2 24-hour activity stripe
panel, a span-2 recent-events panel, a span-2 awaiting-your-action panel, and a
span-2 config panel (`frontend/src/components/butler-detail/ButlerOverviewTab.tsx`).
While the top-level butler record is loading, the tab SHALL render a matching
panel-grid skeleton (`OverviewSkeleton`) to prevent layout shift; per-panel data
sources resolve their own loading/error states independently rather than gating
the whole tab behind a single combined flag.

> Reconciled 2026-06-13 (bu-1p7gr) to the shipped status-board/KPI-panel layout.
> The earlier seven-unit card stack (identity/`ButlerMark` → process facts →
> heartbeat row → Module Health → cost → recent sessions → `EligibilityTimeline`)
> from the now-archived `redesign-detail-tab-overview-card-stack` change was
> superseded in code by the status-board KPI-panel + 24h ActivityStripe redesign
> and never shipped. This requirement now describes the actual
> `ButlerOverviewTab.tsx` implementation. Identity (`ButlerMark`, name,
> description, status) now lives in the page-level `ButlerDetailHeader`, not the
> Overview tab; module health and eligibility live on their own tabs.

#### Scenario: Status KPI panel

- **WHEN** the Overview tab loads for a butler
- **THEN** the "status" panel SHALL show a status dot and label derived from the
  butler status (`ok`/`healthy` → green "online"; `error`/`down` → red; otherwise dim),
  optionally suffixed with the activity
  verb from the status-board row
- **AND** the panel SHALL show a "last run" relative timestamp from the
  status-board row's `lastRunISO`, rendering "--" when unavailable
- **AND** the data SHALL come from the `useButler` hook
  (`frontend/src/hooks/use-butlers.ts:29`) and the per-butler `StatusBoardRow`
  produced by `useButlerStatusBoard`
  (`frontend/src/hooks/use-butler-status-board.ts:38`, `:149`)

#### Scenario: Sessions KPI panel

- **WHEN** the "sessions" panel renders
- **THEN** it SHALL show the 24-hour session count as a `KpiCell`, sourced from
  the status-board row's `sessions24h` and falling back to the butler record's
  `sessions_24h` (`frontend/src/hooks/use-butler-status-board.ts:52`)

#### Scenario: Spend KPI panel

- **WHEN** today's spend summary is available
- **THEN** the "spend" panel SHALL show the butler's USD cost for today as a
  `KpiCell`, with a per-session cost sub-line, and costs below $0.01 SHALL
  display as "$0.00"
- **AND** while the spend query is loading the panel SHALL render a skeleton in
  place of the value
- **AND** the cost SHALL be read from `useSpendSummary("today")`'s
  `by_butler[butlerName]` (`frontend/src/hooks/use-spend.ts:41`)

#### Scenario: Awaiting KPI panel

- **WHEN** the "awaiting" panel renders
- **THEN** it SHALL show the count of pending approval actions for this butler as
  a `KpiCell`, toned amber when greater than zero, with sub-text "pending
  review" or "nothing pending"
- **AND** the count SHALL be sourced from
  `useApprovalActions({ status: "pending", butler, limit: 5 })`
  (`frontend/src/hooks/use-approvals.ts:47`)

#### Scenario: 24-hour activity stripe panel

- **WHEN** the span-2 "activity" panel renders
- **THEN** it SHALL render a 24-bucket `ActivityStripe` bar visualization with an
  hour axis (00, 03, 06, 09, 12, 15, 18, 21, now)
- **AND** the bucket values SHALL be the status-board row's `hourlyStripe`,
  defaulting to 24 zero buckets when unavailable
  (`frontend/src/hooks/use-butler-status-board.ts:60`)

#### Scenario: Recent events panel

- **WHEN** the span-2 "recent" panel renders
- **THEN** it SHALL show up to five newest activity-feed events for the butler,
  each row showing a relative timestamp, the event summary, and an event-kind
  label (session/approval/memory/other)
- **AND** the data SHALL come from `useButlerActivityFeed(butlerName, 5)` over
  `GET /api/butlers/{name}/activity-feed`
  (`frontend/src/hooks/use-butler-analytics.ts:120`)
- **AND** loading SHALL render skeleton rows, errors SHALL render "Could not load
  recent events.", and an empty feed SHALL render "no recent events"

#### Scenario: Awaiting-your-action panel

- **WHEN** the span-2 "awaiting your action" panel renders
- **THEN** it SHALL list the pending approval actions (agent summary or tool
  name, relative request time) each with a "review" link to `/approvals`
- **AND** loading SHALL render skeleton rows, errors SHALL render "Could not load
  approvals.", and an empty list SHALL render "no items pending review"
- **AND** the data SHALL come from the same `useApprovalActions` pending query as
  the awaiting KPI panel (`frontend/src/hooks/use-approvals.ts:47`)

#### Scenario: Config panel

- **WHEN** the span-2 "config" panel renders
- **THEN** it SHALL show key/value rows for `port`, `registered` (hours derived
  from `registered_duration_seconds`), `modules` count, `schedules` count, and
  `skills` count, with the panel sub-title set to `config_path` when available
- **AND** the process facts (`port`, `registered_duration_seconds`,
  `config_path`) SHALL follow the process-facts contract and SHALL NOT render,
  type, or request a `pid` field, consistent with the Config Tab "process" panel
  below and the `add-butler-process-facts` contract
- **AND** missing process-facts source data SHALL render as explicit unavailable
  values ("--") rather than hiding the row

### Requirement: Sessions Tab
The sessions tab SHALL show paginated session history for the butler with drill-down capability, including model resolution metadata.

#### Scenario: Paginated session table
- **WHEN** the sessions tab is active
- **THEN** sessions are loaded with offset-based pagination (page size 20) and displayed in a session table
- **AND** the butler column is hidden since the context is already butler-scoped
- **AND** each session row shows the model used and complexity tier as a badge

#### Scenario: Session detail drawer
- **WHEN** the operator clicks a session row
- **THEN** a drawer opens showing full session details for the selected session
- **AND** the drawer includes model resolution metadata: model alias, runtime type, complexity tier, and resolution source (catalog or toml_fallback)

#### Scenario: Pagination controls
- **WHEN** the total session count exceeds one page
- **THEN** "Previous" and "Next" buttons are shown with the current page number and total pages
- **AND** "Previous" is disabled on the first page and "Next" is disabled when `has_more` is false

### Requirement: Config Tab

The Config tab SHALL provide full transparency into a butler's configuration
files, restyled from a card-per-section layout to a 2x2 panel-grid block
followed by a collapsed markdown accordion.

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
- **AND** the butler.toml accordion item SHALL preserve the "Formatted" / "Raw"
  toggle, where "Formatted" renders the TOML as a structured key-value tree and
  "Raw" renders the JSON representation with 2-space indentation

#### Scenario: Config error and null states

- **WHEN** a config file value is null (e.g., no MANIFESTO.md present)
- **THEN** the accordion item SHALL display "Not found" as its expanded content
- **AND** the item SHALL still be present and expandable
- **AND** when the config API request fails, an error message SHALL be shown with
  the failure reason
- **AND** when the response has no config data, a "No configuration data
  available" message SHALL be displayed

### Requirement: Skills Tab
The skills tab SHALL show all skills available to a butler with drill-down and trigger integration.

#### Scenario: Skill card grid
- **WHEN** skills are loaded
- **THEN** each skill is rendered as a card in a responsive grid (1/2/3 columns by breakpoint) showing the skill name, a "skill" badge, and the first non-heading, non-empty line of the SKILL.md content as a description (truncated to 120 characters)

#### Scenario: Skill detail dialog
- **WHEN** the operator clicks "View" on a skill card
- **THEN** a dialog opens showing the skill name as title and the full SKILL.md content in a scrollable monospace block

#### Scenario: Trigger integration
- **WHEN** the operator clicks "Trigger" on a skill card
- **THEN** the tab switches to the Trigger tab with the prompt pre-filled as "Use the {skill name} skill to "

### Requirement: Schedules Tab (CRUD)
The schedules tab SHALL provide full CRUD management of a butler's scheduled tasks, including complexity tier configuration.

#### Scenario: Schedule table columns
- **WHEN** schedules are loaded
- **THEN** a table displays: Name, Cron expression (monospace badge), Mode (prompt/job badge), Prompt/Job details (truncated to 80 chars), Complexity (tier badge), Enabled toggle (On/Off badge, clickable), Source, Next Run (relative time with absolute tooltip), Last Run (relative time with absolute tooltip), and Actions (Edit, Delete)

#### Scenario: Create schedule
- **WHEN** the operator clicks "Add Schedule"
- **THEN** a dialog opens with a form containing: Name (text input), Cron Expression (text input with standard 5-field hint), Mode selector (prompt or job), Complexity (dropdown: trivial, medium, high, extra_high; default medium), and mode-dependent fields
- **AND** in prompt mode: a Prompt textarea is shown
- **AND** in job mode: Job Name input and Job Args JSON textarea are shown
- **AND** the form validates that name and cron are non-empty, prompt is non-empty in prompt mode, and job name is non-empty with valid JSON args in job mode

#### Scenario: Edit schedule
- **WHEN** the operator clicks "Edit" on a schedule row
- **THEN** the same form dialog opens pre-filled with the schedule's existing values including complexity
- **AND** submission triggers an update mutation instead of create

#### Scenario: Delete schedule with confirmation
- **WHEN** the operator clicks "Delete" on a schedule row
- **THEN** a confirmation dialog appears with the schedule name and a warning that the action cannot be undone
- **AND** confirming the deletion triggers the delete mutation and shows a success toast

#### Scenario: Toggle schedule enabled state
- **WHEN** the operator clicks the enabled/disabled badge on a schedule row
- **THEN** the schedule's enabled state is toggled via mutation and a toast confirms the action

#### Scenario: Auto-refresh
- **WHEN** the schedules tab is mounted
- **THEN** schedule data is polled every 30 seconds

### Requirement: Trigger Tab (Manual Session Invocation)
The trigger tab SHALL allow operators to manually spawn a session for a butler with complexity-aware model selection.

#### Scenario: Prompt input and submission
- **WHEN** the trigger tab is active
- **THEN** a card with a textarea, a complexity selector (dropdown: Trivial, Medium, High, Extra High; default Medium), and "Trigger Session" button is shown
- **AND** the button is disabled when the textarea is empty or a trigger is in flight

#### Scenario: Resolved model preview
- **WHEN** the operator selects a complexity level
- **THEN** below the dropdown, a muted text line shows the resolved model (e.g. "Will use: claude-sonnet via claude")
- **AND** the preview updates reactively when complexity selection changes

#### Scenario: Skill pre-fill from query parameter
- **WHEN** the URL contains a `skill` query parameter
- **THEN** the prompt textarea is pre-filled with "Use the {skill} skill to "

#### Scenario: Result display
- **WHEN** a trigger completes
- **THEN** a result card shows a Success (emerald) or Failed (destructive) badge
- **AND** successful results show the output in a monospace block with a link to the session
- **AND** failed results show the error message

#### Scenario: Ephemeral trigger history
- **WHEN** triggers have been issued during the current page session
- **THEN** a "Trigger History" card lists all previous triggers with their status badge, prompt text (truncated), complexity tier badge, timestamp, and session link
- **AND** this history is not persisted and resets on page reload

### Requirement: MCP Debug Tab
The MCP tab SHALL provide a debugging interface for directly invoking MCP tools on a butler.

#### Scenario: Tool enumeration
- **WHEN** the MCP tab loads
- **THEN** it fetches the butler's available MCP tools and displays the count (e.g., "12 tools available")
- **AND** a "Refresh Tools" button allows manual re-fetch

#### Scenario: Tool selection and description
- **WHEN** tools are loaded
- **THEN** a dropdown select lists all tool names alphabetically
- **AND** selecting a tool displays its description below the dropdown

#### Scenario: Tool invocation with JSON arguments
- **WHEN** the operator selects a tool and optionally enters a JSON arguments object
- **THEN** clicking "Call Tool" sends the invocation to the butler's MCP server
- **AND** the arguments textarea validates JSON format before submission, rejecting non-object values, arrays, and invalid syntax

#### Scenario: Response display
- **WHEN** a tool call completes
- **THEN** a "Last Response" card shows: OK/Tool Error badge, the tool name, arguments (collapsible JSON viewer), parsed result (collapsible JSON viewer), and raw text (monospace block, when present)

#### Scenario: Error handling
- **WHEN** the tool list fetch or tool call fails
- **THEN** the error message is displayed inline without crashing the tab

### Requirement: State Tab (CRUD)
The state tab SHALL provide a browser and editor for the butler's key-value state store.

#### Scenario: State browser table
- **WHEN** state entries are loaded
- **THEN** a table displays: Key (monospace), Value (compact JSON preview, click to expand/collapse to full pretty-printed JSON), Updated timestamp, and Actions (Edit, Delete)

#### Scenario: Key prefix filter
- **WHEN** the operator types in the filter input
- **THEN** only entries whose key starts with the filter text (case-insensitive) are shown
- **AND** when no entries match, a message distinguishes between "no entries exist" and "no entries match the filter"

#### Scenario: Set new value
- **WHEN** the operator clicks "Set Value"
- **THEN** a dialog opens with Key (text input) and Value (JSON textarea) fields
- **AND** the value must be valid JSON; parse errors are shown inline
- **AND** submitting triggers a state set mutation with a success toast

#### Scenario: Edit existing value
- **WHEN** the operator clicks "Edit" on a state row
- **THEN** a dialog opens pre-filled with the entry's key (disabled) and pretty-printed JSON value
- **AND** saving triggers a state set mutation

#### Scenario: Delete with confirmation
- **WHEN** the operator clicks "Delete" on a state row
- **THEN** a confirmation dialog shows the key name and warns the action is irreversible
- **AND** confirming triggers a state delete mutation with a success toast

#### Scenario: Auto-refresh
- **WHEN** the state tab is mounted
- **THEN** state entries are polled every 30 seconds

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

### Requirement: Memory Tab

The Memory tab SHALL surface the per-butler memory subsystem state. It replaces the
prior resident-mode Memory tab layout (which rendered `MemoryTierCards` + `MemoryBrowser`
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

In operator mode the existing tabbed memory browser remains available below the
KPI quartet: scoped to the current butler, allowing navigation between episodes,
facts, and rules with pagination and search.

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

#### Scenario: Operator-mode memory browser

- **WHEN** the Memory tab is viewed in operator mode
- **THEN** a tabbed memory browser SHALL appear below the KPI quartet, scoped to
  the current butler, allowing navigation between episodes, facts, and rules with
  pagination and search

### Requirement: CRM Tab (Butler-Specific)
The CRM tab SHALL show relationship management features scoped to the relationship butler.

#### Scenario: Relationship butler context
- **WHEN** the CRM tab is viewed for the `relationship` butler
- **THEN** an "Upcoming Dates" card shows birthdays, anniversaries, and other important dates in the next 30 days
- **AND** each entry shows the date type badge, contact name (linked to contact detail), date, and a days-until badge (destructive styling when <= 3 days, "Today" / "Tomorrow" labels)
- **AND** a "Quick Links" card provides navigation to `/contacts` and `/groups`

#### Scenario: Non-relationship butler
- **WHEN** the CRM tab is viewed for any butler other than `relationship`
- **THEN** a centered message states "CRM features are only available for the relationship butler."

### Requirement: Bespoke resident tab per domain butler

Each domain butler SHALL support at most one bespoke resident-mode tab (zero or
one — not zero or more). A butler that does not have a domain-specific surface
MUST NOT invent a bespoke tab. Any bespoke tab MUST conform to the nine rules
below.

The following nine rules govern bespoke tabs:

**Rule 1 — Cardinality.** Each butler MAY have at most one bespoke tab. No
butler shall carry two or more bespoke tabs simultaneously in either mode.

**Rule 2 — Insertion point.** In the tab bar, the bespoke tab MUST appear
immediately after the Memory tab and before any operator-only tabs. In resident
mode this places it at position 8 (Overview, Activity, Logs, Approvals, Spend,
Config, Memory, <Bespoke>). In operator mode it appears at position 11
(Overview, Sessions, Config, Skills, Schedules, Trigger, MCP, State, CRM,
Memory, <Bespoke>).

**Rule 3 — Label.** The bespoke tab label is butler-specific and registered in
the canonical per-butler label table (see Requirement: Per-butler bespoke tab
label registry below). Labels MUST be sentence-case, single-word preferred, and
contain no punctuation. Multi-word labels are permitted only when no single-word
label is accurate (e.g., a hypothetical "Task list" would be acceptable;
"task-list" or "Task List" would not).

**Rule 4 — Discovery mechanism.** Bespoke tab presence is determined by a
hardcoded conditional on the butler name in
`frontend/src/pages/ButlerDetailPage.tsx` and
`frontend/src/pages/butler-detail-tabs.ts`. Discovery MUST NOT be driven by
`butler.toml` fields or runtime API responses. This matches the existing
conditional pattern (`showContactsTab = name === "relationship"`, etc.).

**Rule 5 — Visual contract.** Bespoke tab body content MUST conform to the
Panel grid and KPI quartet rules defined by the sibling resident-tab visual
contract change (bu-iuol4.1). Pages MUST NOT reinvent card layout, spacing
tokens, or KPI quartet shape. All bespoke tab bodies use the same Panel grid
shell as resident base-tab bodies.

**Rule 6 — Loading.** Bespoke tab body components MUST be lazy-loaded via React
`lazy()` and wrapped in `<Suspense fallback={<TabFallback label="..." />}>`.
The `<TabFallback>` component is the shared fallback defined in
`ButlerDetailPage.tsx`. Inline tab body components (non-lazy) are not permitted
for bespoke tabs.

**Rule 7 — Offline/paused fallback.** When the butler is paused or quarantined,
the bespoke tab MUST still render. It MUST display an appropriate empty state:
a centered, muted sentence-case message describing the unavailability (e.g.,
"No data available while this butler is paused."). The empty state MUST NOT use
em-dashes, celebration copy, or title-case headings per voice rules.

**Rule 8 — Mode independence.** Bespoke tabs are visible in both resident mode
and operator mode. They are appended after Memory in both mode tab bars. Deep
links to a bespoke tab key MUST NOT force a mode switch; the bespoke tab is
reachable from either mode.

**Rule 9 — Switchboard opt-out.** The switchboard butler explicitly MUST NOT
carry a resident bespoke tab. Its two existing tabs — Routing Log and Registry —
are operator-oriented surfaces that predate the resident vocabulary and serve
ingress triage, not resident self-service. Those two tabs are preserved unchanged
and are not reclassified as bespoke.

#### Scenario: Bespoke tab appears in resident mode tab list

- **WHEN** a domain butler (e.g., `relationship`) is viewed in resident mode
- **THEN** the tab bar SHALL show: Overview, Activity, Logs, Approvals, Spend,
  Config, Memory, <Bespoke label> — in that order
- **AND** the bespoke tab label (e.g., "Contacts") MUST be sentence-case and
  match the butler's registered bespoke label

#### Scenario: Bespoke tab appears in operator mode tab list

- **WHEN** a domain butler (e.g., `relationship`) is viewed in operator mode
- **THEN** the tab bar SHALL show: Overview, Sessions, Config, Skills, Schedules,
  Trigger, MCP, State, CRM, Memory, Contacts — in that order
- **AND** operator-only tabs (Models, if exposed) appear after the bespoke tab

#### Scenario: Bespoke tab is lazy-loaded

- **WHEN** the bespoke tab is selected for the first time
- **THEN** its body component MUST be loaded on demand via React `lazy()`
- **AND** a `<Suspense fallback={<TabFallback label="..." />}>` MUST wrap the
  component during loading
- **AND** the fallback MUST show the butler-specific label text

#### Scenario: Bespoke tab empty state when butler offline

- **WHEN** the butler status is `paused` or eligibility is `quarantined`
- **AND** the bespoke tab is selected
- **THEN** the bespoke tab body MUST still render
- **AND** it MUST display a centered, muted empty-state message in sentence case
  (e.g., "No data available while this butler is paused.")
- **AND** the message MUST NOT contain em-dashes, title-case headings, or
  celebratory copy

#### Scenario: Deep link to bespoke tab does not force mode switch

- **WHEN** a user navigates to `/butlers/relationship?tab=contacts`
- **AND** the stored mode is either `resident` or `operator`
- **THEN** the bespoke `contacts` tab MUST be selected in the current mode
  without switching to the other mode

#### Scenario: Switchboard has no resident bespoke tab

- **WHEN** the butler name is `switchboard`
- **THEN** the tab bar in resident mode MUST contain only the seven resident
  base tabs plus Routing Log and Registry — no additional bespoke tab
- **AND** the tab bar in operator mode MUST contain the ten operator base tabs
  plus Routing Log and Registry — no additional bespoke tab
- **AND** Routing Log and Registry MUST remain unchanged in label, position, and
  visibility

#### Scenario: Single bespoke tab per butler

- **WHEN** any domain butler is rendered
- **THEN** at most one tab beyond Memory SHALL be present that is classified as
  a bespoke tab for that butler
- **AND** no butler SHALL render two or more bespoke tabs simultaneously

### Requirement: Per-butler bespoke tab label registry

Each domain butler that carries a bespoke tab SHALL use the label registered in
the table below. The labels in this table are normative; any implementation that
uses a different label for a listed butler is non-conformant. Switchboard is
explicitly absent: it carries no resident bespoke tab (Rule 9).

| Butler       | Bespoke tab label | Justification                                                                   |
|-------------|-------------------|---------------------------------------------------------------------------------|
| chronicler  | Timelines         | Core identity: "retrospective time butler" that projects events and episodes.   |
| education   | Reviews           | Spaced-repetition review sessions are the primary user action; Anki integration is explicitly rejected by the manifesto ("We do not connect to Coursera, Anki, Canvas…"), so "Decks" is ruled out. |
| finance     | Finances          | Direct mapping to the butler's domain: financial clarity over inbox noise.      |
| general     | Collections       | The manifesto's organizing metaphor: "Collections let you group related things together." |
| health      | Measurements      | Health butler leads with measurement tracking; the existing "Health" label is generic and collides with the butler name ("Measurements" is the primary tracking surface). |
| home        | Devices           | Device orchestration and monitoring is the bespoke surface: "Monitor device health." |
| lifestyle   | Taste             | Manifesto central concept: "Taste is autobiography"; the butler is the keeper of your taste. |
| messenger   | Conversations     | Delivery health surface showing per-conversation send/receive outcomes; NOT a user-facing chat UI. |
| qa          | Investigations    | Primary operator surface: active and historical investigation dispatch records. |
| relationship| Contacts          | Contact management is the primary bespoke surface: "A living database of the people in your life." |
| travel      | Trips             | Trip-centric organization: "See your complete trip timeline" is the core value proposition. |

Labels are sentence-case. No em-dashes. No exclamation marks. No title-case.
Switchboard is absent from this table because it carries no resident bespoke tab.

#### Scenario: Each butler renders its registered bespoke tab label

- **GIVEN** the per-butler bespoke tab label registry above
- **WHEN** a domain butler from the registry is viewed in resident mode or
  operator mode
- **THEN** the bespoke tab trigger SHALL display exactly the label registered
  for that butler (e.g., `Timelines` for chronicler, `Investigations` for qa)
- **AND** the label MUST be sentence-case and contain no punctuation
- **AND** no butler in the table SHALL use a label that differs from the one
  registered here

#### Scenario: Switchboard does not render a bespoke tab from the registry

- **WHEN** the butler name is `switchboard`
- **THEN** the tab bar SHALL NOT contain any label from the per-butler registry
- **AND** the only tabs beyond the base set are the existing operator-oriented
  tabs: Routing Log and Registry

#### Scenario: New butlers (general, lifestyle, messenger, qa) include bespoke tabs

- **WHEN** any of `general`, `lifestyle`, `messenger`, or `qa` is viewed
- **THEN** the bespoke tab SHALL appear at position 8 in resident mode
  (immediately after Memory, before any operator-only tabs)
- **AND** the labels SHALL be exactly: `Collections` (general), `Taste`
  (lifestyle), `Conversations` (messenger), `Investigations` (qa)
- **AND** the health butler bespoke tab SHALL be relabeled from `Health` to
  `Measurements` to match the registry

### Requirement: Health Tab (Butler-Specific)
The health butler's bespoke tab SHALL be labeled "Measurements" and render a
panel-grid health data surface, appended only for the `health` butler.

#### Scenario: Health butler context
- **WHEN** the "Measurements" tab is viewed for the `health` butler
- **THEN** a panel grid renders health KPIs and trend panels (glucose, heart rate, HRV, weight, sleep) plus active medications and recent conditions
- **AND** a drilldown link to `/health/measurements` is preserved

#### Scenario: Non-health butler
- **WHEN** any butler other than `health` is viewed
- **THEN** no "Measurements" tab is appended (the tab is conditionally rendered only for `health`); no placeholder message is shown

### Requirement: Switchboard Registry Tab
The registry tab (switchboard-only) SHALL show the authoritative butler registry with liveness information.

#### Scenario: Registry table columns
- **WHEN** the registry tab loads on the switchboard butler
- **THEN** a table displays: Name, Endpoint URL (monospace), Modules (badge per module, parsed from comma-separated strings, JSON arrays, or nested string arrays), Description (truncated), and Last Seen (relative time via `formatDistanceToNow`)

#### Scenario: Module normalization
- **WHEN** the registry data contains modules in various formats (comma-separated string, JSON array string, nested arrays)
- **THEN** modules are normalized to a flat list of badge-rendered module names with a recursion depth limit of 10

#### Scenario: Empty registry
- **WHEN** no butlers are registered in the switchboard
- **THEN** a centered empty state message is shown

### Requirement: Switchboard Routing Log Tab
The routing log tab (switchboard-only) SHALL show inter-butler request routing activity.

#### Scenario: Routing log table columns
- **WHEN** the routing log tab loads
- **THEN** a table displays: Timestamp (formatted as "MMM d, HH:mm:ss"), Source butler, Target butler, Tool name (monospace), Status (OK/Failed badge), Duration in milliseconds, and Error message (truncated, destructive text)

#### Scenario: Source and target filters
- **WHEN** the operator enters text in the "Source butler" or "Target butler" filter inputs
- **THEN** the query is filtered server-side by those values
- **AND** a "Clear filters" button appears when any filter is active

#### Scenario: Pagination
- **WHEN** the routing log has more entries than one page (25 per page)
- **THEN** Previous/Next pagination controls are shown with page count

### Requirement: Switchboard Triage Filters

The filters surface (accessible from the ingestion page at `/ingestion?tab=filters`) SHALL manage unified ingestion rules, thread affinity settings, and Gmail label filters. It replaces the previous dual-model UI (triage rules table + ManageSourceFiltersPanel sheet) with a single rules table.

#### Scenario: Unified rules table with CRUD
- **WHEN** the user navigates to `/ingestion?tab=filters`
- **THEN** they see a single table of all ingestion rules with columns: Priority, Scope, Condition, Action, Enabled toggle, Actions (edit/delete)

#### Scenario: Scope display and filtering
- **WHEN** the rules table is rendered
- **THEN** each rule's scope is shown as a badge: "Global" for global rules, or the connector identity (e.g., "gmail:user:dev") for connector-scoped rules
- **AND** a scope filter dropdown above the table allows filtering by "All", "Global only", or specific connector scopes

#### Scenario: Rule editor drawer with scope selector
- **WHEN** the user creates or edits a rule
- **THEN** the rule editor drawer includes a scope selector (Global / Connector) and, when Connector is selected, a connector type and endpoint identity picker
- **AND** the action field is constrained based on scope: connector scope only allows "block"; global allows all actions

#### Scenario: Test rule dry-run
- **WHEN** the user clicks "Test" in the rule editor
- **THEN** a test envelope is sent to POST `/ingestion-rules/test` and the result is displayed inline (matched/no-match with reason)

#### Scenario: Thread affinity panel preserved
- **WHEN** the user scrolls below the rules table
- **THEN** the thread affinity panel (enable/disable toggle + TTL input) is displayed unchanged

#### Scenario: Import seed rules
- **WHEN** the user clicks "Import defaults"
- **THEN** a preview dialog shows the 9 default seed rules (now as global ingestion rules) and imports them on confirmation

#### Scenario: Connector detail page shows scoped rules
- **WHEN** the user navigates to a connector detail page (e.g., `/ingestion/connectors/gmail/gmail:user:dev`)
- **THEN** the page shows a rules section listing only rules with `scope = 'connector:gmail:gmail:user:dev'`, with an "+ Add Rule" button that pre-fills the scope

### Requirement: Switchboard Backfill Management
The backfill surface SHALL manage historical replay jobs across connectors.

#### Scenario: Backfill job list with live polling
- **WHEN** the backfill history tab loads
- **THEN** a paginated table shows all backfill jobs with: ID (truncated), Connector type, Endpoint identity, Status badge (with spinner for active/pending), Rows processed, Cost/Cap display, Created (relative time), and lifecycle action buttons

#### Scenario: Job status state machine
- **WHEN** a backfill job is displayed
- **THEN** action buttons are gated by the job's current status:
  - **Pause:** available when `pending` or `active` and connector is online
  - **Resume:** available when `paused` and connector is online
  - **Cancel:** available when `pending`, `active`, `paused`, `cost_capped`, or `error`
- **AND** all action buttons are disabled when any mutation is in flight

#### Scenario: Expandable job detail row
- **WHEN** the operator clicks a job row
- **THEN** an expanded detail section shows: date range, rate limit, rows skipped, target categories, start/completion timestamps, error details, and connector offline warnings

#### Scenario: Create backfill job dialog
- **WHEN** the operator clicks "New Backfill Job"
- **THEN** a dialog opens with: connector selector (only online connectors listed), date range (from/to date inputs), rate limit per hour (numeric, default 100), daily cost cap in dollars (numeric, default $5.00), and optional target categories (comma-separated)
- **AND** when no connectors are online, manual connector type and endpoint identity inputs are shown as fallback

#### Scenario: Active job progress polling
- **WHEN** a backfill job has status `pending` or `active`
- **THEN** its progress is polled every 5 seconds for live row count and cost updates
- **AND** inactive jobs are polled every 30 seconds

#### Scenario: Cost cap enforcement display
- **WHEN** a job reaches its daily cost cap
- **THEN** the status badge shows "cost capped" (destructive variant)
- **AND** the cost/cap display shows both the spent amount and the cap limit

### Requirement: Data Fetching Architecture
All butler management surfaces SHALL use TanStack Query for data fetching with consistent patterns.

#### Scenario: Query key hierarchy
- **WHEN** butler-scoped data is fetched
- **THEN** query keys follow the pattern `["butlers", butlerName, resource]` for cache isolation and targeted invalidation

#### Scenario: Mutation invalidation
- **WHEN** a write mutation succeeds (create, update, delete, toggle)
- **THEN** the relevant query key family is invalidated to trigger a re-fetch
- **AND** toast notifications confirm success or surface error messages

#### Scenario: Optimistic polling intervals
- **WHEN** list-type queries are mounted
- **THEN** they use a 30-second `refetchInterval` by default
- **AND** backfill job progress uses an accelerated 5-second interval for active jobs

#### Scenario: Conditional query enabling
- **WHEN** a query depends on a butler name parameter
- **THEN** the query is disabled (`enabled: false`) when the butler name is empty or undefined

### Requirement: Loading and Error State Consistency
All butler management tabs SHALL follow consistent loading and error patterns.

#### Scenario: Skeleton loading states
- **WHEN** any tab's data is loading
- **THEN** purpose-specific skeleton layouts are shown (card skeletons for overview, table row skeletons for lists, content block skeletons for config)
- **AND** skeleton shapes approximate the final content layout

#### Scenario: Error display pattern
- **WHEN** a tab's data fetch fails
- **THEN** the error is shown inline within a card using destructive text styling
- **AND** the error message includes the exception message when available, falling back to "Unknown error"

#### Scenario: Empty state messaging
- **WHEN** a data set is empty (no schedules, no skills, no state entries)
- **THEN** a centered, muted message describes the empty condition and, where applicable, guides the operator toward the creation action

### Requirement: Butler Detail Page — Dispatch Fold-In
The existing `/butlers/{name}` detail page SHALL fold in the `ButlersExpanded` design, with sections for fallback chain, system prompt, tools, memory access, activity, and kill switch.

#### Scenario: Page structure post-fold-in
- **WHEN** a user navigates to `/butlers/{name}`
- **THEN** the page renders the existing tab archetype plus, on the "Configuration" or equivalently-named tab, sections in this order:
  - **§1 Identity & routing** — fallback chain (primary + ordered fallbacks; `+ add fallback` link), schedule, `$/day ceiling`, approvals policy, timeout, concurrency.
  - **§2 System prompt** — serif prompt body, mono caption (`tokens · NNN · last edit · <actor>`), links `history · N versions →` and `diff vs vN-1 →`.
  - **§3 Tools & integrations** — table of `tool · description · scope · on` rows with toggles.
  - **§4 Memory access** — three tiles for short / mid / long term, each with read/write badges.
  - **§5 Activity** — 24h stripe-chart (sessions per hour).
  - **§6 Kill switch** — `kill switch · 30s grace →` link.

#### Scenario: Kill switch with grace
- **WHEN** a user clicks the kill switch link
- **THEN** a confirmation modal appears showing the grace seconds and the butler name
- **AND** on confirm, `POST /api/butlers/{name}/kill {grace_seconds: 30}` is called
- **AND** `audit.append("butler.kill", target=butler_name, note=f"grace={grace_seconds}s")` is invoked
- **AND** the butler initiates shutdown after the grace window.

### Requirement: System Prompt Versioning API
The dashboard SHALL expose CRUD over a butler's system prompt with version history.

#### Scenario: Read current prompt
- **WHEN** `GET /api/butlers/{name}/prompt` is called
- **THEN** the response is `ApiResponse[PromptVersion]` with `prompt: str`, `version: int`, `updated_at`, `updated_by`.

#### Scenario: Update prompt snapshots history
- **WHEN** `PUT /api/butlers/{name}/prompt {prompt: str}` is called
- **THEN** the current row is inserted into `public.system_prompt_history` (the snapshot), then the new prompt is stored as the current version with `version = old.version + 1`
- **AND** `audit.append("butler.prompt", target=butler_name, note=f"v{new_version}")` is invoked.

#### Scenario: Prompt history list
- **WHEN** `GET /api/butlers/{name}/prompt/history?limit=20` is called
- **THEN** the response is `PaginatedResponse[PromptVersion]` ordered `version DESC`, defaulting to the most recent 20 versions.

### Requirement: Tools & Scope API
The dashboard SHALL expose per-butler tool grants and scopes.

#### Scenario: Read tools
- **WHEN** `GET /api/butlers/{name}/tools` is called
- **THEN** the response is `ApiResponse[ButlerTool[]]` with `name`, `description`, `allowed: bool`, `scope: str | null`.

#### Scenario: Update a tool grant
- **WHEN** `PUT /api/butlers/{name}/tools/{tool} {allowed: bool, scope?: str}` is called
- **THEN** the grant is updated atomically
- **AND** `audit.append("butler.tool", target=f"{name}.{tool}", note=f"allowed={allowed}")` is invoked.

### Requirement: Memory Access Tiles API
The dashboard SHALL expose per-butler memory tier access.

#### Scenario: Read memory access
- **WHEN** `GET /api/butlers/{name}/memory-access` is called
- **THEN** the response is `ApiResponse[MemoryAccess]` with `read: ("short"|"mid"|"long")[]`, `write: ("short"|"mid"|"long")[]`, `namespace: str`, `embedding_model: str`, `drops_7d: int`.

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
  seven-unit card stack.
- `openspec/changes/detail-page-archetype/`: Butler detail page uses
  `<Page archetype="detail">`; tab body is the primary slot.
- `openspec/changes/add-butler-process-facts/`: Config process panel sources
  `container_name`, `port`, `registered_duration_seconds`, `config_path`;
  no `pid` field is permitted.
- PLAN.md §6 Phase 7 — dispatch fold-in scope.
- Visual reference: the `ButlersExpanded` redesign prototype (graduated; now
  shipped in `frontend/`) for the dispatch fold-in.
- Reuses `audit.append()` from dashboard-audit-log on every dispatch mutation.
