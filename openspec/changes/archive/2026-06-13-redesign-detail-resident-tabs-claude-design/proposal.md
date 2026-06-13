## Why

The resident-mode tab vocabulary for `/butlers/:name` is now settled by
`redesign-detail-page-tab-vocabulary` (bu-41p8z Gate B2): the default view
exposes Overview, Activity, Logs, Approvals, Spend, Config, and Memory. Those
tabs exist as stubs in the current frontend. Before any implementation bead
(Layer D) can build them, the visual and interaction contract must be specced:
what panels appear, what atoms compose them, how data maps to layout, and what
empty/error states are required. This change authors that contract.

The panel-grid frame that the `/butlers` status-board redesign (`bu-hb7dh`)
landed is the right composition primitive for dense observability views. The
resident tabs are the per-butler instance of the same language: 4-col CSS grid,
consistent cell borders, KPI quartet rows, scroll panels for feeds and logs.
Speccing this now creates a hard gate before Layer D implementation children
begin, preventing each tab author from independently inventing layout atoms.

## What Changes

- Add a `<Panel>` atom contract to `dashboard-butler-management`: monospace
  eyebrow title, optional sub label, span 1-4 across the 4-col grid, optional
  scroll body, optional fixed height.
- Add a KPI quartet pattern: 4 single-span panels each showing a label, a value
  (28px tabular-nums for primary KPIs, 22px otherwise), optional sub-line, and
  optional tone (amber/red/green tokens; no oklch literals).
- Add a `RangeToggle` component vocabulary: three options (24h/7d/30d), mono
  labels, one per page, hidden for tabs that do not use a range.
- Replace Activity, Logs, Approvals, Spend, Memory, and Config tab stubs with
  full panel-grid requirement sections including scenarios for layout, data
  surfaces, scroll behavior, empty state, and error state.
- Restyle the Config tab from its current MarkdownSections card layout to a 2x2
  panel grid (process / schedule / scopes-oauth / integrations) with MarkdownSections
  collapsed into an accordion.
- Assert that no new `ButlerSummary` fields are required by any resident tab
  beyond what the backend epic siblings explicitly add.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-butler-management`: Activity, Logs, Approvals, Spend, Memory, and
  Config tab requirements replace stub placeholders with full panel-grid
  contracts including atom definitions, KPI quartet pattern, RangeToggle
  vocabulary, scroll behavior, empty-state copy, and source-data citations.

## Impact

- **Frontend implementation targets (Layer D beads):**
  - `bu-iuol4.16` ButlerActivityTab
  - `bu-iuol4.17` ButlerLogsTab
  - `bu-iuol4.18` ButlerApprovalsTab
  - `bu-iuol4.19` ButlerSpendTab
  - `bu-iuol4.20` ButlerMemoryTab
  - Config tab restyle (separate bead)
- **Shared primitive beads (Layer C):**
  - `bu-iuol4.13` KpiCell + Panel atoms
  - `bu-iuol4.14` RangeToggle
  - `bu-iuol4.15` DayBars7d30d
- **No new backend endpoints introduced by this spec.** Backend analytics
  endpoints are owned by Layer B beads (bu-iuol4.4 through bu-iuol4.12).
- **No new `ButlerSummary` fields.** Resident tabs source from existing or
  Layer-B-specified analytics endpoints, not from list-summary expansion.
- **Out of scope:** Overview tab (owned by `redesign-detail-tab-overview-card-stack`);
  per-butler bespoke tabs (owned by sibling bead bu-iuol4.2); operator-mode tabs
  (Sessions, Skills, Schedules, Trigger, MCP, State, CRM).
