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
    where healthy = total minus paused, awaiting, and quarantined counts,
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
  1. If `status = degraded`: verb is `paused`, rail color is red
  2. If `status = waiting` OR `eligibility_state = quarantined`: verb is
     `awaiting` or `quarantined` (prefer `quarantined` when eligibility is
     explicitly quarantined), rail color is amber (awaiting) or red (quarantined)
  3. If `active_session_count > 0`: verb is `running`, chip is green
  4. Otherwise: verb is `idle`, chip is dim
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
  - Paused butler count (with amber status-tone dot, shown only when count > 0)
  - Awaiting butler count (with red status-tone dot, shown only when count > 0)
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
The `/butlers/:name` page is a tabbed detail view where each butler is treated as a first-class navigable entity.

#### Scenario: URL-driven tab routing
- **WHEN** a user navigates to `/butlers/:name?tab=<value>`
- **THEN** the active tab is set to the `tab` query parameter value
- **AND** when `tab` is absent or invalid, the default tab is `overview`
- **AND** tab changes update the URL via `replaceState` (no history entry)

#### Scenario: Breadcrumb navigation
- **WHEN** the butler detail page renders
- **THEN** a breadcrumb trail is shown: Overview > Butlers > {butler name}

#### Scenario: Base tabs always present
- **WHEN** any butler detail page loads
- **THEN** the following tabs are always visible: Overview, Sessions, Config, Skills, Schedules, Trigger, MCP, State, CRM, Memory

#### Scenario: Conditionally shown tabs -- switchboard
- **WHEN** the butler name is `switchboard`
- **THEN** two additional tabs are shown after the base tabs: "Routing Log" and "Registry"

#### Scenario: Conditionally shown tabs -- health
- **WHEN** the butler name is `health`
- **THEN** one additional tab is shown: "Health"

#### Scenario: Conditionally shown tabs -- general
- **WHEN** the butler name is `general`
- **THEN** two additional tabs are shown: "Collections" and "Entities"

#### Scenario: Lazy-loaded tabs for performance
- **WHEN** a non-default tab is selected for the first time
- **THEN** its component is loaded on demand via React `lazy()` with a centered "Loading {tab}..." fallback
- **AND** the following tabs are lazy-loaded: Skills, Schedules, Trigger, MCP, State, Memory, Routing Log, Registry

#### Scenario: Tab URL semantics and deep-linking
- **WHEN** the active tab is controlled by the `?tab=` query parameter
- **THEN** `overview` is the default tab and removes the query parameter from the URL
- **AND** accepted deep-link values include all base tab keys (`overview`, `sessions`, `config`, `skills`, `schedules`, `trigger`, `mcp`, `state`, `crm`, `memory`) plus conditional tab keys (`health`, `collections`, `entities`, `routing-log`, `registry`)
- **AND** tab changes update the URL via `replaceState` without creating browser history entries

### Requirement: Butler detail page outer chrome conforms to the detail-page archetype

The Butler detail page at `/butlers/:name` SHALL adopt `<Page archetype="detail">`
for its outer chrome. The existing tab structure is the `primary` body slot; the inner
tab content is NOT changed by this requirement.

**Changes from the existing requirement (§Requirement: Butler Detail Page Structure):**

1. **Shell adoption.** The page MUST use `<Page archetype="detail">` as its outer
   shell. Breadcrumbs (currently "Overview > Butlers > {butler name}" via a standalone
   `<Breadcrumbs>` component) MUST be passed via the `breadcrumbs` prop on `<Page>`.
   The standalone `<Breadcrumbs>` component at the page layer MUST be removed.

2. **Title.** The `title` prop on `<Page>` MUST be the butler's name (`name` field
   from the butler record), titleized (e.g., `"relationship"` → `"Relationship"`).

3. **Actions.** The `<ChatPanel />` button, currently pinned right alongside the H1
   in the page header flex row, MUST be migrated to the `actions` prop on `<Page>`.
   The `<ChatPanel />` component itself is unchanged; only its placement moves to the
   shell's header action slot.

4. **Primary slot.** The `<Tabs>` block (containing the `BASE_TABS`, conditional
   tabs, and `TabsContent` sections) becomes the `primary` body slot rendered inside
   the shell's `children`. No content is removed; the tab structure is preserved
   exactly.

5. **Loading state.** The existing per-tab `TabFallback` loading behavior is
   preserved for lazy-loaded tabs. The shell's `loading` prop MUST reflect the top-level
   butler record fetch status; when the butler record is loading, the shell shows
   `DetailSkeleton` before any tab content renders.

6. **Error state.** When the butler record fetch fails (e.g., unknown butler name),
   the `error` prop on `<Page>` MUST be set. The shell renders the destructive error
   card. Individual tab errors remain tab-scoped.

#### Scenario: Breadcrumbs via Page shell prop

- **WHEN** the butler detail page renders for butler `"relationship"`
- **THEN** breadcrumbs MUST be rendered via the `breadcrumbs` prop on `<Page>`:
  `[{ label: "Overview", href: "/" }, { label: "Butlers", href: "/butlers" }, { label: "Relationship" }]`
- **AND** no standalone `<Breadcrumbs>` component MUST be rendered at the page layer

#### Scenario: Butler name as page title

- **WHEN** the butler detail page renders for butler `"relationship"`
- **THEN** the `<h1>` rendered by the `<Page>` shell MUST read "Relationship"
- **AND** it MUST NOT read "Butler" or "Butler Detail"

#### Scenario: ChatPanel in page header actions

- **WHEN** the butler detail page renders for a resolved butler
- **THEN** the `<ChatPanel />` component MUST appear in the page header row (via the
  `actions` prop), to the right of the title
- **AND** it MUST NOT appear only as a sibling div to the page title at the page layer

#### Scenario: Tabs body is the primary slot

- **WHEN** the butler detail page renders the tab group
- **THEN** the complete `<Tabs>` block (TabsList + all TabsContent entries) MUST be
  rendered as the top-level child inside the `<Page>` shell
- **AND** the tab structure, content, and behavior MUST be unchanged from the current
  implementation

#### Scenario: Top-level loading shows shell skeleton

- **WHEN** the butler record fetch is in flight (before the butler name is resolved)
- **THEN** the `<Page>` shell MUST show `DetailSkeleton`
- **AND** no tab content MUST be rendered during this state

#### Scenario: Unknown butler shows shell error

- **WHEN** a user navigates to `/butlers/nonexistent` and the butler record fetch
  returns 404 or an error
- **THEN** the `error` prop on `<Page>` MUST be set
- **AND** the shell MUST render the destructive error card with the butler name in
  the breadcrumbs for navigation context

---

### Requirement: Butler detail page tab body vocabulary

The Butler detail page tab body SHALL map to the four-tier archetype vocabulary as follows:

- **Primary slot:** The `<Tabs>` block, containing `TabsList` (all visible tab
  triggers) and `TabsContent` for each tab. This is the entire interactive surface
  for the butler workspace.
- **No hero slot:** The butler identity (name, status, description, port) is rendered
  inside the Overview tab's identity card, not in a page-level hero tier. The overview
  tab IS the identity surface; no separate hero tier is needed at the page layer.
- **No drawer slot:** Credential and advanced configuration content lives inside
  individual tabs (Config tab, State tab). No top-level practical drawer is needed.
- **Tabs are NOT a candidate for page-level archetype expansion:** The eleven-plus
  tabs are the correct answer for this workspace-grade record. Future tab
  consolidation (if needed) is a separate audit concern, not a `<Page>` slot concern.

#### Scenario: Base tabs present on all butler pages

- **WHEN** any butler detail page loads
- **THEN** the following ten tab triggers MUST be visible: Overview, Sessions, Config,
  Skills, Schedules, Trigger, MCP, State, CRM, Memory
- **AND** these are rendered inside the primary slot's `<TabsList>`, unchanged from
  the current implementation

---

### Requirement: Tab Structures Reference (Non-Butler Pages)

The following tab structures exist on pages outside the butler detail view. They are documented here as a consolidated reference.

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

The Butler detail Overview tab SHALL be the identity surface for the selected
butler and SHALL render a dense card stack composed of exactly seven ordered
units: identity card, process facts card, heartbeat row, module health card,
cost card, recent sessions card, and eligibility row. The stack SHALL use
existing butler detail, system heartbeat, module health, cost, sessions, and
switchboard eligibility data surfaces, plus the process-facts fields specified
by `add-butler-process-facts`. To prevent layout shifts, the tab SHALL
maintain a unified loading state by combining loading flags from all data
sources with a logical OR.

#### Scenario: Identity card

- **WHEN** the Overview tab loads for a butler
- **THEN** the first card SHALL display the `ButlerMark` identity component,
  butler name, status badge, and description when present
- **AND** the card SHALL source its detail data from the existing `useButler`
  hook (`frontend/src/hooks/use-butlers.ts:26-33`) and the butler detail
  endpoint response
- **AND** the card SHALL remain inside the Overview tab rather than above the
  tabs as a Tier 2 Hero, per `redesign-butler-detail-no-hero`
  (`openspec/changes/redesign-butler-detail-no-hero/specs/dashboard-butler-management/spec.md:10-18`)

#### Scenario: Process facts card

- **WHEN** the Overview tab renders the process facts card
- **THEN** the card SHALL show `container_name`, `port`,
  `registered_duration_seconds` rendered as a human-readable liveness duration,
  and `config_path`
- **AND** the card SHALL follow the sibling process-facts contract and SHALL NOT
  render, type, or request a `pid` field
  (`openspec/changes/add-butler-process-facts/specs/dashboard-butler-management/spec.md:3-38`)
- **AND** missing source data SHALL render as explicit unavailable values rather
  than hiding the row

#### Scenario: Heartbeat row

- **WHEN** the Overview tab renders heartbeat data for the selected butler
- **THEN** the heartbeat row SHALL show `last_heartbeat_at` and
  `heartbeat_age_seconds` from the system heartbeat surface
- **AND** the backend source SHALL be `GET /api/system/butlers/heartbeat`, which
  reads switchboard `butler_registry.last_seen_at` and computes heartbeat age
  (`src/butlers/api/routers/system.py:639-699`)
- **AND** the frontend SHALL consume that data through `useButlerHeartbeats` or
  an equivalent hook over the same endpoint (`frontend/src/hooks/use-system.ts:71-78`)
- **AND** unavailable heartbeat data SHALL render as an explicit stale/unknown
  row state rather than removing the row

#### Scenario: Module health card

- **WHEN** the butler reports active modules
- **THEN** a Module Health card SHALL render one badge per module, colored by
  status: `connected`/`ok` as emerald, `degraded` as amber, `error` as
  destructive, and other statuses as secondary
- **AND** if no modules are registered, the card SHALL show "No modules
  registered"
- **AND** module health SHALL be sourced through the existing MCP status path:
  `_get_module_health_via_mcp` and the `/api/butlers/{name}/modules` endpoint
  (`src/butlers/api/routers/butlers.py:549-670`)

#### Scenario: Cost card

- **WHEN** cost summary data is available for today
- **THEN** a Cost Today card SHALL show the butler's USD cost, its percentage
  share of the global total, and the global total
- **AND** costs below $0.01 SHALL display as "$0.00"
- **AND** the card SHALL use the existing `useSpendSummary("today")` data path
  (`frontend/src/hooks/use-spend.ts:31-47`)

#### Scenario: Recent sessions card

- **WHEN** the Overview tab renders recent activity for the selected butler
- **THEN** a Recent sessions card SHALL show up to five newest sessions for that
  butler
- **AND** the card SHALL use `useButlerSessions(butlerName, { limit: 5 })` or an
  equivalent query over the same butler-scoped sessions endpoint
  (`frontend/src/hooks/use-sessions.ts:27-35`)
- **AND** if no recent sessions exist, the card SHALL show an explicit empty
  state rather than falling back to the old recent notifications feed

#### Scenario: Eligibility row

- **WHEN** the butler has a registry entry from the switchboard
- **THEN** the eligibility row SHALL show `Active` as an emerald badge,
  `Quarantined` as a destructive clickable badge, or `Stale` as an amber
  clickable badge
- **AND** clicking a `Quarantined` or `Stale` badge SHALL trigger a
  `setEligibility(name, "active")` mutation to restore the butler
- **AND** when a quarantine reason exists, it SHALL be shown as muted text next
  to the badge
- **AND** the row SHALL include the existing "24h History" timeline using the
  `EligibilityTimeline` component semantics: segments colored by state
  (`active` emerald-600, `stale` amber-500, `quarantined` red-600), data from
  `GET /switchboard/registry/{name}/eligibility-history?hours=24`, native
  `title` tooltips, window start/now labels, and 60-second refresh cadence
- **AND** the frontend SHALL continue to use the existing registry,
  eligibility-history, and set-eligibility hooks
  (`frontend/src/hooks/use-general.ts:24-53`)

### Requirement: Sessions Tab
The sessions tab shows paginated session history for the butler with drill-down capability, including model resolution metadata.

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
The config tab provides full transparency into a butler's configuration files.

#### Scenario: butler.toml display with format toggle
- **WHEN** the config tab loads successfully
- **THEN** the `butler.toml` contents are shown in a card with a "Formatted" / "Raw" toggle button
- **AND** "Formatted" mode renders the TOML as a structured key-value tree, while "Raw" mode renders the JSON representation with 2-space indentation

#### Scenario: Markdown file sections
- **WHEN** the config response includes `claude_md`, `agents_md`, or `manifesto_md`
- **THEN** each is rendered in its own card with the filename as the title (CLAUDE.md, AGENTS.md, MANIFESTO.md)
- **AND** content is displayed in a monospace `<pre>` block, or "Not found" if the value is null

#### Scenario: Error and empty states
- **WHEN** the config API request fails
- **THEN** an error message is shown with the failure reason
- **AND** when the response has no config data, a "No configuration data available" message is displayed

### Requirement: Skills Tab
The skills tab shows all skills available to a butler with drill-down and trigger integration.

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
The schedules tab provides full CRUD management of a butler's scheduled tasks, including complexity tier configuration.

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
The trigger tab allows operators to manually spawn a session for a butler with complexity-aware model selection.

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
The MCP tab provides a debugging interface for directly invoking MCP tools on a butler.

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
The state tab provides a browser and editor for the butler's key-value state store.

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

### Requirement: Memory Tab
The memory tab shows the three-tier memory system health and a browsable memory store.

#### Scenario: Memory tier summary cards
- **WHEN** the memory tab loads
- **THEN** three cards are displayed in a row, one per memory tier:
  - **Episodes (Eden):** total, unconsolidated, consolidated counts with a health badge (healthy >= 80% consolidated, warning >= 50%, needs attention < 50%)
  - **Facts (Mid-term):** total, active, fading counts with a health badge (healthy >= 80% active)
  - **Rules (Long-term):** total, candidate, established, proven, anti-pattern counts with a health badge (healthy >= 80% established+proven)

#### Scenario: Memory browser
- **WHEN** the tier cards load
- **THEN** a tabbed memory browser appears below, scoped to the current butler, allowing navigation between episodes, facts, and rules with pagination and search

### Requirement: CRM Tab (Butler-Specific)
The CRM tab shows relationship management features scoped to the relationship butler.

#### Scenario: Relationship butler context
- **WHEN** the CRM tab is viewed for the `relationship` butler
- **THEN** an "Upcoming Dates" card shows birthdays, anniversaries, and other important dates in the next 30 days
- **AND** each entry shows the date type badge, contact name (linked to contact detail), date, and a days-until badge (destructive styling when <= 3 days, "Today" / "Tomorrow" labels)
- **AND** a "Quick Links" card provides navigation to `/contacts` and `/groups`

#### Scenario: Non-relationship butler
- **WHEN** the CRM tab is viewed for any butler other than `relationship`
- **THEN** a centered message states "CRM features are only available for the relationship butler."

### Requirement: Health Tab (Butler-Specific)
The health tab shows navigation to health management sub-pages, available only for the health butler.

#### Scenario: Health butler context
- **WHEN** the Health tab is viewed for the `health` butler
- **THEN** a card grid shows links to six health sub-pages: Measurements, Medications, Conditions, Symptoms, Meals, and Research
- **AND** each card has a title, description, and a "View" link button

#### Scenario: Non-health butler
- **WHEN** the Health tab is viewed for any butler other than `health`
- **THEN** a centered message states "Health features are only available for the health butler."

### Requirement: Switchboard Registry Tab
The registry tab (switchboard-only) shows the authoritative butler registry with liveness information.

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
The routing log tab (switchboard-only) shows inter-butler request routing activity.

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

The filters surface (accessible from the ingestion page at `/ingestion?tab=filters`) manages unified ingestion rules, thread affinity settings, and Gmail label filters. It replaces the previous dual-model UI (triage rules table + ManageSourceFiltersPanel sheet) with a single rules table.

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
The backfill surface manages historical replay jobs across connectors.

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
All butler management surfaces use TanStack Query for data fetching with consistent patterns.

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
All butler management tabs follow consistent loading and error patterns.

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
