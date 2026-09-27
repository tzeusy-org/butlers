# Dashboard Butler Management

## Purpose
Defines the dashboard surfaces for managing butlers as first-class entities: a fleet-wide butler list page, a per-butler detail page with six base tabs plus per-butler domain tabs, and switchboard-specific operational surfaces. Together these views give the operator full visibility into butler identity, health, activity, spend, approvals, memory, configuration, scheduling, state, MCP tooling, session history, and (for the switchboard) registry and routing. The dashboard is both an observability surface and a write-capable control plane -- operators can run and pause butlers, create schedules, mutate state, and invoke MCP tools without leaving the browser.

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

- **WHEN** the butler list page is not showing an initial request failure with
  no cached board rows
- **THEN** a header strip SHALL be displayed containing:
  - An eyebrow label (e.g., "Fleet status")
  - An `h1` reading "The staff, at a glance" styled `text-2xl font-bold tracking-tight`
  - A healthy/total pill (count of healthy butlers over total registered count,
    where healthy = total minus `offline`, `quarantined`, `overdue`, and
    activity-derived `unknown` counts, derived from `StatusBoardAggregates`)
  - A clock and date display rendered via `<Time mode="clock-24h-mono">` that
    updates every minute (aligned to minute boundaries via a 60-second interval)
- **AND** the `unknown` count SHALL be derived from canonical `BoardRow.activity`
  values, not from registry eligibility availability

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

#### Scenario: Activity stripe is its own nested door (bu-27dxl.8.3)

- **WHEN** a butler cell renders
- **THEN** the 24h activity stripe region (label + bars/skeleton/error) is a
  nested `<button>`, keyboard-accessible and semantically valid HTML (never a
  nested anchor), with an accessible name naming the butler and the activity
  destination
- **AND** activating it routes to `/butlers/<name>?tab=activity`, independent
  of the root tile's own destination, and does not also trigger the root
  tile's navigation (the click does not propagate to the root)
- **AND** the root tile's own Enter/Space keyboard activation continues to
  route to `/butlers/<name>` (Overview), unaffected by the nested control
- **AND** this door remains present and reachable even while the stripe's
  underlying data is loading or has errored -- a sparse/incomplete stripe
  stays navigable, it just makes no claim of completeness in what it visually
  shows

#### Scenario: Activity verb derivation

- **WHEN** a butler's board row is assembled by `GET /api/butlers/board`
- **THEN** the activity verb (`BoardRow.activity`) and chip color
  (`BoardRow.cell_tone`) SHALL be derived SERVER-SIDE by the canonical
  `_derive_board_activity` function (`src/butlers/api/routers/butlers.py`) --
  this endpoint is the single source of the liveness verdict for every
  butler-status surface, replacing the former client-side per-cell derivation
  (bu-86c4c.17) -- using this first-match-wins priority order:
  1. `status = down` (the raw MCP probe result): verb `offline`, tone `red`
  2. `eligibility = quarantined`: verb `quarantined`, tone `red`
  3. `heartbeat_unavailable` is true, OR the registry `last_seen_at` is
     clock-skewed more than 5 minutes into the future (`clock_skewed`): verb
     `unknown`, tone `neutral` (an untrustworthy heartbeat is not a confidently
     healthy one, the bu-y1am9 clock-skew fold)
  4. `active_session_count > 0`: verb `running`, tone `green`
  5. `cadence_status = overdue` (silent longer than twice the butler's own cron
     cadence per `_CADENCE_OVERDUE_FACTOR`, or longer than 5 minutes when no
     cadence is known): verb `overdue`, tone `amber`
  6. otherwise: verb `idle`, tone `neutral`
- **AND** the complete verb set is `running` / `idle` / `overdue` / `offline` /
  `quarantined` / `unknown`, and the complete tone set is `green` / `amber` /
  `red` / `neutral` (there is no `dim` tone).
- **AND** `eligibility` may also be `unavailable` when the butler registry source
  errored (`_fetch_board_row` sets it when `registry_source_error` is true or no
  registry row exists); `unavailable` is NOT `quarantined`, so it does not
  trigger rule 2, and the row's verb is derived from the remaining heartbeat /
  session / cadence signals.
- **AND** the raw `_probe_butler` status (`BoardRow.status`) is only `ok` or
  `down`; the richer `overdue` / `unknown` verbs are produced by the derivation
  above, not by the probe, and no `degraded` or `waiting` status value is
  produced.
- **AND** the mockup verbs `patrol`, `consolidating`, and `ingesting` MUST NOT
  be used. These verbs imply butler-specific semantic knowledge not carried by
  the board row, and are explicitly rejected.

#### Scenario: Canonical cadence labels

- **WHEN** the board derives a human-facing cadence label from a butler's
  shortest enabled cron interval
- **THEN** exactly one hour, one day, and seven days SHALL be labeled `hourly`,
  `daily`, and `weekly` respectively
- **AND** any other positive interval, including two hours, SHALL be labeled
  `custom`
- **AND** a butler with no enabled schedule SHALL retain a null cadence label
- **AND** the raw `cadence_seconds` and cadence-overdue calculation SHALL remain
  authoritative and unchanged

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
- **AND** clicking a `quarantined` or `stale` chip SHALL schedule the existing
  `setEligibility(name, "active")` mutation (`frontend/src/hooks/use-general.ts:36-53`)
  a fixed undo window (5s) out behind an "Undo" toast action, rather than
  firing it instantly (restore-with-reason-and-undo, JARVIS audit move 6,
  bu-86c4c.15) -- clicking Undo before the window elapses cancels the
  mutation entirely; letting the window elapse fires it exactly as before
- **AND** the cell SHALL NOT be hidden for any eligibility state, including
  unavailable

#### Scenario: Footer KPI band

- **WHEN** the butler list page is not showing an initial request failure with
  no cached board rows
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
- **AND** the header strip and footer KPI band SHALL remain visible with the cached
  board context

#### Scenario: Initial request failure does not fabricate board context

- **WHEN** the initial butler list request fails and no cached board rows exist
- **THEN** the Page error surface and retry control SHALL render
- **AND** the header strip and footer KPI band SHALL NOT render around the error

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

### Requirement: Butler detail page outer chrome uses status-board archetype

The Butler detail page at `/butlers/:name` SHALL use `<Page archetype="status-board">`
as its outer chrome, with the tab block as the sole page body. No footer KPI
band, breadcrumb trail, or heartbeat tile renders at the page layer.

- **Title and description.** The page title is the butler's name titleized
  (e.g., `"relationship"` → `"Relationship"`); the description is the butler
  summary's description when available.
- **Header.** The page header is the butler detail header: the butler identity
  block (letter-mark, name, description, activity status, port, uptime, schedule
  facts) with the page actions inside it. The actions are, in order: a status
  pill, the command bar (see Butler Detail Command Bar), a Logs link to
  `?tab=activity&section=logs`, a Config link to `?tab=system&section=config`, a
  Chat button that opens the butler chat panel, and a Pause/Resume control.
- **Pause/Resume.** Pausing sets the butler's eligibility to `quarantined` and
  resuming sets it to `active`, each behind an undo window. While paused, the
  actions show when and why the butler was quarantined. The command palette
  offers the same Pause or Resume verb.
- **Loading and errors.** The page's loading state reflects the butler record
  fetch; tab-level lazy loading is separate. A failed butler fetch sets the page
  error with a retry that refetches the butler record. Tab errors stay
  tab-scoped.

#### Scenario: Page shell uses status-board archetype

- **WHEN** the butler detail page renders for any butler name
- **THEN** `<Page archetype="status-board">` SHALL be the outer shell
- **AND** no breadcrumb trail, heartbeat tile, or footer KPI band SHALL render
  at the page layer

#### Scenario: Butler name as page title

- **WHEN** the butler detail page renders for butler `"relationship"`
- **THEN** the page title SHALL be `"Relationship"` (titleized)
- **AND** the description SHALL come from the butler summary's description
  when available

#### Scenario: Header slot composition

- **WHEN** the butler detail page renders for a resolved butler
- **THEN** the page header SHALL be the butler detail header, with the page
  actions rendered inside the header rather than as a separate page actions slot
- **AND** the actions SHALL render, in order: status pill, command bar (prompt,
  complexity, Run), Logs link, Config link, Chat, and Pause/Resume
- **AND** the Logs link SHALL target `?tab=activity&section=logs` and the Config
  link SHALL target `?tab=system&section=config`

#### Scenario: Tabs body is the page body

- **WHEN** the butler detail page renders the tab group
- **THEN** the complete tab block (tab rail and every tab body) SHALL be the
  direct body content of the page, with no additional page-layer wrapper

#### Scenario: Top-level loading delegates to Page shell

- **WHEN** the butler record fetch is in flight
- **THEN** the page SHALL be in its loading state
- **AND** the Page shell SHALL render its own built-in loading treatment; the
  page component SHALL NOT pass a bespoke skeleton

#### Scenario: Unknown butler shows shell error

- **WHEN** a user navigates to `/butlers/nonexistent` and the butler record fetch
  returns 404 or an error
- **THEN** the page error SHALL be set
- **AND** its retry SHALL invalidate the butler query and trigger a refetch

### Requirement: Overview Tab

The Butler detail Overview tab SHALL be the operational overview for the
selected butler: a responsive panel grid with up to four KPI columns on wide
viewports. The grid contains, in order: four single-column KPI panels (status,
sessions, spend, awaiting), then full-width-pair panels for 24-hour activity,
recent events, awaiting-your-action, and config, followed by the delegations and
domain-events panels. While the butler record is loading, the tab renders a
matching panel-grid skeleton so the layout does not shift; each panel resolves
its own loading and error state independently. Butler identity lives in the page
header, not in this tab.

#### Scenario: Status KPI panel

- **WHEN** the Overview tab loads for a butler
- **THEN** the "status" panel SHALL show a status dot and label derived from the
  butler status (`ok`/`healthy` → green "online"; `error`/`down` → red; otherwise
  dim), optionally suffixed with the activity verb from the butler's status-board
  row
- **AND** the panel SHALL show a "last run" relative timestamp from the
  status-board row, rendering "--" when unavailable

#### Scenario: Sessions KPI panel

- **WHEN** the "sessions" panel renders
- **THEN** it SHALL show the 24-hour session count from the status-board row,
  falling back to the butler record's 24-hour session count

#### Scenario: Spend KPI panel

- **WHEN** today's spend summary is available
- **THEN** the "spend" panel SHALL show the butler's USD cost for today with a
  per-session cost sub-line, and costs below $0.01 SHALL display as "$0.00"
- **AND** while the spend query is loading the panel SHALL render a skeleton in
  place of the value

#### Scenario: Awaiting KPI panel

- **WHEN** the "awaiting" panel renders
- **THEN** it SHALL show the count of this butler's pending approval actions,
  toned amber when greater than zero, with sub-text "pending review" or "nothing
  pending"

#### Scenario: 24-hour activity stripe panel

- **WHEN** the "activity" panel renders
- **THEN** it SHALL render a 24-bucket activity stripe with a rolling
  relative-time axis (`-24h`, `-12h`, `now`) rather than clock-of-day labels
- **AND** the bucket values SHALL be the status-board row's hourly stripe,
  defaulting to 24 zero buckets when unavailable

#### Scenario: Recent events panel

- **WHEN** the "recent" panel renders
- **THEN** it SHALL show up to five newest activity-feed events for the butler
  from `GET /api/butlers/{name}/activity-feed`, each row showing a relative
  timestamp, the event summary, and an event-kind label
  (session/approval/memory/other)
- **AND** each `session_completed` row SHALL render the API-provided safe summary
  from the same structured-trigger-first projection as Timeline, without a
  client-side raw-prompt or envelope fallback
- **AND** loading SHALL render skeleton rows, errors SHALL render "Could not load
  recent events.", and an empty feed SHALL render "no recent events"

#### Scenario: Awaiting-your-action panel

- **WHEN** the "awaiting your action" panel renders
- **THEN** it SHALL list the pending approval actions (agent summary or tool
  name, relative request time) each with a "review" link to `/approvals`
- **AND** loading SHALL render skeleton rows, errors SHALL render "Could not load
  approvals.", and an empty list SHALL render "no items pending review"
- **AND** the list SHALL come from the same pending-approvals query as the
  awaiting KPI panel

#### Scenario: Config panel

- **WHEN** the "config" panel renders
- **THEN** it SHALL show key/value rows for `port`, `registered` (hours derived
  from `registered_duration_seconds`), `modules` count, `schedules` count, and
  `skills` count, with the panel sub-title set to `config_path` when available
- **AND** the process facts (`port`, `registered_duration_seconds`,
  `config_path`) SHALL NOT render, type, or request a `pid` field, consistent
  with the Config Tab "process" panel
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

The System tab's Config sub-section SHALL provide full transparency into a
butler's configuration: a two-by-two panel grid followed by a collapsed
accordion of the butler's configuration files.

- **Panel grid.** "process" shows container name, port, registered duration,
  and config path (read-only, the same facts as the Overview config panel, never
  a `pid`). "schedule" lists active schedules by name with a relative next-run
  time (empty: "No schedules."). "scopes and oauth" lists each module's OAuth
  status as authorized, unauthorized, or not required (empty: "No modules with
  OAuth."). "integrations" lists enabled modules as badges (empty: "No modules
  enabled.").
- **Accordion.** butler.toml, CLAUDE.md, AGENTS.md, and MANIFESTO.md each render
  as a collapsed item that expands to the file content in a monospace block,
  showing "Not found" when the file is absent. The butler.toml item keeps a
  Formatted/Raw toggle.

#### Scenario: Config 2x2 panel grid

- **WHEN** the Config sub-section loads
- **THEN** four panels SHALL render in two rows of two: process, schedule,
  scopes and oauth, integrations
- **AND** the panels SHALL form a single ruled grid per the shared panel
  vocabulary in dashboard-design-language

#### Scenario: Schedule panel relative timestamps

- **WHEN** the schedule panel renders a schedule's next-run time
- **THEN** the time SHALL be rendered through the shared time primitive in
  relative mode
- **AND** no raw locale formatting or manual date arithmetic SHALL appear

#### Scenario: Config markdown accordion collapsed by default

- **WHEN** the Config sub-section renders
- **THEN** the butler.toml, CLAUDE.md, AGENTS.md, and MANIFESTO.md items SHALL
  be collapsed by default
- **AND** expanding an item SHALL reveal the full file content in a monospace
  block
- **AND** the butler.toml item SHALL keep the "Formatted" / "Raw" toggle, where
  "Formatted" renders the TOML as a structured key-value tree and "Raw" renders
  the JSON representation with 2-space indentation

#### Scenario: Config error and null states

- **WHEN** a config file value is null (e.g., no MANIFESTO.md present)
- **THEN** the accordion item SHALL display "Not found" as its expanded content
- **AND** the item SHALL still be present and expandable
- **AND** when the config API request fails, an error message SHALL be shown with
  the failure reason
- **AND** when the response has no config data, a "No configuration data
  available" message SHALL be displayed

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

### Requirement: Activity tab

The Activity tab's Analytics sub-section SHALL be the per-butler session
analytics surface: a KPI quartet, a range-switchable activity chart, and a
session-kind breakdown. A range toggle (`24h`, `7d`, `30d`, default `24h`) sets
the window for every panel.

Data comes from the butler-scoped session analytics endpoints
`GET /api/butlers/{name}/analytics/hourly-activity`, `.../daily-activity`,
`.../latency-stats`, and `.../session-kinds`, plus the session aggregate for the
failed-session count.

#### Scenario: Activity tab KPI quartet

- **WHEN** the Activity tab loads
- **THEN** four KPI cells SHALL render: Sessions (the sum of the session-kind
  counts in the window), p50 ms and p95 ms (session latency percentiles for the
  window), and Errors (sessions that did not succeed in the window)
- **AND** the Errors value SHALL use the high-severity tone when greater than
  zero
- **AND** when the metrics fail to load, the quartet SHALL show "Could not load
  activity metrics."

#### Scenario: Activity stripe for 24h range

- **WHEN** the range is `24h`
- **THEN** the activity panel SHALL render a 24-column hourly activity stripe
  from the hourly-activity endpoint
- **AND** a failed request SHALL show "Could not load hourly activity."

#### Scenario: Day bars for 7d or 30d range

- **WHEN** the range is `7d` or `30d`
- **THEN** the activity panel SHALL render 7 or 30 daily bars from the
  daily-activity endpoint
- **AND** a failed request SHALL show "Could not load daily activity."

#### Scenario: Kind breakdown panel

- **WHEN** the session-kinds endpoint returns results
- **THEN** the "By kind" panel SHALL list each session kind with its count in
  tabular numerals
- **AND** a failed request SHALL show "Could not load session kind breakdown."

#### Scenario: Activity tab empty state

- **WHEN** no sessions exist for the butler in the selected window
- **THEN** the kind breakdown SHALL show "No sessions in this window."
- **AND** KPI cells whose value is unavailable SHALL render a placeholder rather
  than a fabricated `0`

### Requirement: Logs tab

The Activity tab's Logs sub-section SHALL be a structured viewer over the
butler's daemon log, read from `GET /api/butlers/{name}/logs` and polled every
five seconds while visible. It renders one full-width scroll panel with level
filter chips and log lines in fixed timestamp, level, and message columns.

#### Scenario: Log level filter chips

- **WHEN** the Logs sub-section is active
- **THEN** filter chips SHALL render for All, DEBUG, INFO, WARN, and ERROR, with
  All selected by default
- **AND** exactly one chip SHALL be active at a time
- **AND** selecting a level chip SHALL refetch the log list at that level

#### Scenario: Log line column widths

- **WHEN** log lines render
- **THEN** each line SHALL show a fixed-width millisecond-precision timestamp, a
  fixed-width level, and a message filling the remaining width, all in the
  monospace family
- **AND** timestamps and levels SHALL stay column-aligned across lines

#### Scenario: Log level color tokens

- **WHEN** a log line has level WARN
- **THEN** the level text SHALL use the amber token
- **AND** no oklch literal or hex color SHALL be used

- **WHEN** a log line has level ERROR
- **THEN** the level text SHALL use the destructive token

#### Scenario: Logs tab auto-scroll

- **WHEN** auto-scroll is on (the default)
- **THEN** the list SHALL scroll to the newest entry as polls deliver new lines
- **AND** scrolling up SHALL pause following until the operator re-enables
  auto-scroll

#### Scenario: Logs tab empty state

- **WHEN** the logs endpoint returns no entries for the selected level
- **THEN** the panel SHALL display "No logs yet." in muted text

### Requirement: Approvals tab

The Approvals tab SHALL list the pending approval actions for the current
butler in one full-width scroll panel, each row deep-linking into that action.

#### Scenario: Approvals list with pending items

- **WHEN** the Approvals tab loads for a butler with pending approval actions
- **THEN** each pending action SHALL render a severity dot, the tool name as
  title, a sub-line with the agent summary and relative age, and a "Review" link
  to `/approvals/{action_id}`
- **AND** severity SHALL derive from the action's expiry: high (destructive fill)
  when it expires within one hour or has expired, medium (amber fill) within 24
  hours, and low (muted fill) otherwise or with no expiry

#### Scenario: Approvals empty state

- **WHEN** no pending approvals exist for the butler
- **THEN** the panel SHALL display "No items pending review." in muted text

#### Scenario: Approvals age rendering

- **WHEN** a pending approval item is rendered
- **THEN** its age SHALL render through the shared time primitive in compact
  relative form (e.g., "3m ago")
- **AND** no raw locale formatting or manual date arithmetic SHALL be used

### Requirement: Spend tab

The Spend tab SHALL be the per-butler cost surface: a KPI quartet, a daily spend
trend with a range toggle (`24h`, `7d`, `30d`), and a 30-day model breakdown.
When the spend source is unavailable or some model usage is unpriced, the tab
SHALL say so in a degraded-source note rather than presenting partial totals as
complete.

#### Scenario: Spend KPI quartet

- **WHEN** the Spend tab loads
- **THEN** four KPI cells SHALL render: Spend today, Spend 30d, Cost / session ·
  30d, and Tokens today
- **AND** cost values SHALL be formatted as USD (e.g., "$0.04")
- **AND** zero or unavailable spend values SHALL render dimmed

#### Scenario: Spend trend bar chart

- **WHEN** the range is `24h`, `7d`, or `30d`
- **THEN** the trend panel SHALL render one bar per UTC day over the last 1, 7,
  or 30 days respectively
- **AND** a failed request SHALL show "Could not load spend trend."

#### Scenario: Model breakdown KV list

- **WHEN** 30-day model cost data is available
- **THEN** each model SHALL be listed with its cost, sorted by cost descending,
  in tabular numerals
- **AND** a failed request SHALL show "Could not load model breakdown."

#### Scenario: Spend tab empty state

- **WHEN** no spend data exists for the window
- **THEN** the trend panel SHALL show "No spend data for this period." and the
  model breakdown SHALL show "No model usage data available."

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

### Requirement: Butler detail header schedule facts are truthful

`ButlerDetailHeader` SHALL derive schedule facts only from enabled schedule
rows with a parseable finite `next_run_at` instant. The header SHALL use the
existing read-only schedule query and SHALL NOT write schedules, alter
scheduler calculations, or derive status-board activity. A scheduled instant
at or before the current wall clock is overdue; an instant strictly after it
is future-next. The header SHALL recompute that classification on schedule
polling and at least once per minute while mounted.

#### Scenario: Earliest future schedule is shown as next

- **WHEN** one or more enabled schedules have parseable `next_run_at` values
  strictly after the current wall clock
- **THEN** the header SHALL render the earliest such timestamp as its `next`
  fact using the shared `<Time>` primitive
- **AND** no later future schedule SHALL replace that fact

#### Scenario: Stale schedule is shown as an actionable overdue fact

- **WHEN** one or more enabled schedules have parseable `next_run_at` values
  at or before the current wall clock
- **THEN** the header SHALL render the oldest such timestamp as a visibly
  named `overdue` fact with the schedule name and a deterministic relative age
- **AND** the fact SHALL use the established amber foreground token and an
  accessible link name that does not rely on color alone
- **AND** the fact link SHALL target
  `/butlers/:name?tab=system&section=schedules`
- **AND** the stale timestamp SHALL NOT render as a literal `next` fact

#### Scenario: Overdue and future facts coexist

- **WHEN** enabled schedules include both overdue and future parseable
  `next_run_at` values
- **THEN** the header SHALL keep the most-overdue named fact visible
- **AND** the earliest independently truthful future-next fact SHALL remain
  visible

#### Scenario: Unusable timestamps do not fabricate certainty

- **WHEN** a schedule is disabled, has a null timestamp, has a malformed
  timestamp, or has an unparsable timestamp
- **THEN** that row SHALL contribute neither an overdue nor a future-next fact
- **AND** the header SHALL render no fabricated schedule age or future time for
  that row

#### Scenario: Equal schedule instants select a stable named fact

- **WHEN** multiple enabled parseable schedules tie for the selected overdue
  or future timestamp
- **THEN** the header SHALL select the fact deterministically by schedule name
  and then schedule id

### Requirement: Truthful Status-Board Summary and Error Composition

The `/butlers` status board SHALL present fleet health from the canonical
server-derived `BoardRow.activity` vocabulary. A healthy count SHALL exclude
every row whose activity is `offline`, `quarantined`, `overdue`, or `unknown`.
The `unknown` aggregate SHALL be derived from canonical row activity, not from
registry `eligibility = unavailable`, which remains a separate availability
diagnostic.

The Page shell SHALL omit status-board header and footer slots only when its
initial board request has failed and no cached rows exist. A normal empty
response and initial loading continue to use the shell’s existing behavior.

#### Scenario: Fleet health excludes every non-healthy activity

- **WHEN** board rows contain one or more `offline`, `quarantined`, `overdue`,
  or `unknown` canonical activity verdicts
- **THEN** the header’s healthy/total pill subtracts all four counts from the
  registered total
- **AND** registry availability alone SHALL NOT change the `unknown` count or
  make a row appear unhealthy without its canonical activity verdict

#### Scenario: Initial failure has no misleading board chrome

- **WHEN** the initial board request fails and no cached rows are available
- **THEN** the Page error region renders the error and retry control
- **AND** the status-board header and footer SHALL NOT render around that error

#### Scenario: Cached refresh failure keeps contextual chrome

- **WHEN** a board refresh fails after one or more cached rows were loaded
- **THEN** the cached rows, status-board header, and footer remain visible
- **AND** the page renders its stale-data warning instead of replacing the
  board with a full-page error

### Requirement: Canonical Status-Board Cadence Labels

The board’s human-facing cadence label SHALL describe only a canonical
interval: exactly one hour is `hourly`, exactly one day is `daily`, and
exactly seven days is `weekly`. A positive interval that is not one of those
canonical values, including two hours, SHALL be labeled `custom`. A butler with
no enabled schedule SHALL retain a null cadence label. The raw
`cadence_seconds` and cadence-overdue calculation remain authoritative and
unchanged.

#### Scenario: Canonical cadence interval has its named label

- **WHEN** a butler’s shortest enabled cron interval is exactly one hour, one
  day, or seven days
- **THEN** its board row exposes `hourly`, `daily`, or `weekly` respectively

#### Scenario: Noncanonical cadence avoids an inaccurate named label

- **WHEN** a butler’s shortest enabled cron interval is two hours or any other
  positive noncanonical duration
- **THEN** its board row exposes `cadence_label = custom`
- **AND** it SHALL NOT label that duration `hourly`, `daily`, or `weekly`

### Requirement: Butler Detail Tab Set

The `/butlers/:name` page SHALL render one tab vocabulary for every butler: six
base tabs (Overview, Activity, Approvals, Spend, Memory, System) plus the
butler's domain tabs from the Butler Domain Tabs registry. The tab rail shows
Overview, Activity, Approvals, Spend, and Memory first, then the butler's domain
tabs, and System last. No mode toggle exists; every tab is reachable for every
butler that carries it.

The Activity tab groups three sub-sections: Analytics (default, see Activity
tab), Sessions (see Sessions Tab), and Logs (see Logs tab). The System tab
groups seven sub-sections: Config (default, see Config Tab), Skills (see Skills
Section), Schedules (see Schedules Tab (CRUD)), MCP (see MCP Debug Tab), State
(see State Tab (CRUD)), Models, and Manage.

#### Scenario: URL-driven tab routing

- **WHEN** a user navigates to `/butlers/:name?tab=<value>`
- **THEN** the active tab SHALL be the `tab` value when it is a base tab or one
  of that butler's domain tabs
- **AND** when `tab` is absent or not valid for that butler, the active tab
  SHALL be Overview
- **AND** selecting Overview SHALL remove the `tab` parameter from the URL
- **AND** tab changes SHALL replace the current history entry rather than push
  a new one

#### Scenario: Base tabs for every butler

- **WHEN** any butler detail page loads
- **THEN** the tab rail SHALL show Overview, Activity, Approvals, Spend, Memory,
  and System
- **AND** no mode toggle or mode-gated tab SHALL be rendered

#### Scenario: Domain tabs precede System

- **WHEN** a butler with domain tabs is viewed (for example `switchboard`)
- **THEN** its domain tabs (Routing Log, Registry) SHALL appear after Memory and
  before System
- **AND** System SHALL remain the last tab

#### Scenario: Section sub-navigation is URL-driven

- **WHEN** the Activity or System tab is active
- **THEN** the active sub-section SHALL be carried in the `section` query
  parameter
- **AND** an absent or invalid `section` SHALL select the default sub-section
  (Analytics for Activity, Config for System)
- **AND** selecting the default sub-section SHALL remove `section` from the URL
- **AND** selecting Analytics SHALL also clear any `since` and `until` filters

#### Scenario: Deep link into a sub-section

- **WHEN** a user navigates to `/butlers/:name?tab=system&section=schedules`
- **THEN** the System tab SHALL open with the Schedules sub-section active

#### Scenario: Lazy-loaded tab bodies

- **WHEN** a tab or sub-section other than Overview, Activity analytics, or
  System config is selected for the first time
- **THEN** its body SHALL load on demand with a centered "Loading {label}..."
  fallback while it loads

#### Scenario: Keyboard tab navigation

- **WHEN** the butler detail page is focused
- **THEN** the digit keys 1 through 9 SHALL switch to the first nine tabs in
  rail order
- **AND** `[` and `]` SHALL switch to the previous and next tab, wrapping at the
  ends

#### Scenario: Reload from the command palette

- **WHEN** the butler detail page is open
- **THEN** the command palette SHALL offer a "Reload {butler}" command that
  refetches the butler record

### Requirement: Butler Domain Tabs

A butler's detail page SHALL append the domain tabs registered for that butler
in the table below, and no others. Domain tab presence is decided by the butler
name in the frontend tab registry, never by `butler.toml` fields or runtime API
responses. Labels are sentence-case with no punctuation.

| Butler | Domain tabs (tab key: label) |
|---|---|
| chronicler | `timelines`: Timelines |
| education | `reviews`: Reviews |
| finance | `finances`: Finances |
| general | `collections`: Collections; `entities`: Entities |
| health | `health`: Measurements |
| home | `devices`: Devices |
| lifestyle | `taste`: Taste |
| qa | `investigations`: Investigations |
| relationship | `contacts`: Contacts |
| switchboard | `routing-log`: Routing Log; `registry`: Registry |
| travel | `trips`: Trips |

Butlers absent from the table carry no domain tab. Domain tab bodies use the
shared panel vocabulary specified in dashboard-design-language.

#### Scenario: Each butler renders its registered domain tab labels

- **WHEN** a butler listed in the registry is viewed
- **THEN** the tab rail SHALL show exactly that butler's registered domain tab
  labels, in registry order, between Memory and System
- **AND** no butler SHALL render a domain tab registered to another butler

#### Scenario: Butler without a domain tab

- **WHEN** a butler absent from the registry is viewed
- **THEN** the tab rail SHALL show only the six base tabs

#### Scenario: Domain tab deep link

- **WHEN** a user navigates to `/butlers/relationship?tab=contacts`
- **THEN** the Contacts tab SHALL be active
- **AND** navigating to `/butlers/finance?tab=contacts` SHALL fall back to
  Overview

#### Scenario: Domain tab is lazy-loaded

- **WHEN** a domain tab is selected for the first time
- **THEN** its body SHALL load on demand with a "Loading {label}..." fallback
  naming that tab

### Requirement: Butler Detail Command Bar

The butler detail header SHALL provide a prompt-first command bar that starts a
session for the butler: a prompt input, a complexity selector listing the
backend complexity tiers (default workhorse), and a Run button.

#### Scenario: Empty prompt fires the scheduled tick

- **WHEN** the operator presses Run (or Enter) with an empty prompt
- **THEN** the butler SHALL be triggered with its default scheduled-tick prompt
  at the selected complexity
- **AND** a "Force run triggered" confirmation toast SHALL appear

#### Scenario: Custom prompt starts a session

- **WHEN** the operator enters a prompt and presses Run
- **THEN** the butler SHALL be triggered with that prompt at the selected
  complexity
- **AND** a "Prompt sent" confirmation toast SHALL appear

#### Scenario: Run navigates to the new session

- **WHEN** a trigger succeeds and returns a session id
- **THEN** the page SHALL navigate to `/sessions/{session_id}`

#### Scenario: Run failure and in-flight state

- **WHEN** a trigger is in flight
- **THEN** the prompt input, complexity selector, and Run button SHALL be
  disabled and the button SHALL read "Running…"
- **AND** when the trigger fails, a "Failed to run butler" error toast SHALL
  appear

### Requirement: Skills Section

The System tab's Skills sub-section SHALL show all skills available to a butler
with drill-down into each skill's SKILL.md.

#### Scenario: Skill card grid

- **WHEN** skills are loaded
- **THEN** each skill SHALL render as a card in a responsive grid showing the
  skill name, a "skill" badge, and the first non-heading, non-empty line of the
  SKILL.md content as a description, truncated to 120 characters

#### Scenario: Skill detail dialog

- **WHEN** the operator clicks "View" on a skill card
- **THEN** a dialog SHALL open showing the skill name as title and the full
  SKILL.md content in a scrollable monospace block

### Requirement: Butler Memory Tab Surface

The butler detail Memory tab SHALL surface the butler's memory state as a KPI
quartet over memory counts and a recent-writes feed, both scoped to the
current butler. The cross-butler memory browser lives on `/memory`, not in this
tab.

#### Scenario: Memory KPI quartet with "+N today" sub-lines

- **WHEN** the Memory tab loads
- **THEN** four KPI cells SHALL render: Episodes, Facts, Entities, Rules
- **AND** each cell's sub-line SHALL show "+N today", where N is the count added
  in the last 24 hours
- **AND** when the stats request fails, the quartet SHALL show "Could not load
  memory stats."

#### Scenario: Recent-writes feed

- **WHEN** the recent-writes panel renders
- **THEN** it SHALL list the butler's ten most recent episodes, newest first,
  each with a relative timestamp, the butler name, and a one-line content
  preview
- **AND** the panel SHALL be a fixed-height scroll region so no entry is cut off
  without scroll access
- **AND** when the request fails, it SHALL show "Could not load recent writes."

#### Scenario: Memory tab empty state

- **WHEN** no episodes exist for the butler
- **THEN** the recent-writes panel SHALL show "No memory writes recorded yet."
- **AND** the KPI cells SHALL render zero counts with "+0 today" sub-lines

### Requirement: Non-Butler Page Tab Structures

Tabbed structures outside the butler detail view SHALL behave as specified here.

#### Scenario: Memory browser tabs

- **WHEN** the `/memory` page's browser renders
- **THEN** it SHALL offer three registers: Facts, Rules, Episodes

#### Scenario: Contact detail redirect

- **WHEN** `/contacts/:contactId` is visited
- **THEN** the route SHALL replace-navigate to `/entities/index?has=contact`
- **AND** it SHALL NOT render the retired contact-detail page or its former tabs

## Source References

- `about/heart-and-soul/design-language.md`: token, `<Page>`, `<Time>`, voice,
  and type-system doctrine the detail page follows.
- `frontend/src/pages/ButlerDetailPage.tsx` and
  `frontend/src/pages/butler-detail-tabs.ts`: detail page shell, tab rail, and
  domain tab registry.
- `frontend/src/components/butler-detail/`: header, command bar, Activity and
  System sections, and every tab body.
- `src/butlers/api/routers/butlers.py`, `butler_management.py`,
  `butler_logs.py`, and `sessions.py` (under `src/butlers/api/routers/`):
  butler record, prompt and tool management, log, and session analytics
  endpoints.
- Reuses `audit.append()` from dashboard-audit-log on every dispatch mutation.
