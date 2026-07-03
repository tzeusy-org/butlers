# Frontend Feature Inventory (Implemented)

> **Purpose:** Catalog all implemented dashboard features per route, including cross-cutting capabilities and current gaps.
> **Audience:** Frontend developers, product managers, and operators assessing dashboard coverage.
> **Prerequisites:** [Information Architecture](information-architecture.md).

This inventory describes what is implemented today in `frontend/src/**`.

## Cross-Cutting Features

- Typed API client (`frontend/src/api/client.ts`) and typed models (`frontend/src/api/types.ts`).
- TanStack Query for server-state caching and background refetch.
- Common loading skeletons, explicit empty states, and error messaging across views.
- Pagination for list-heavy views (offset/limit or cursor where applicable).
- Relative and absolute timestamp formatting for operational readability.
- Command palette with grouped search results and recent-search persistence.
- Keyboard shortcuts:
  - `/` and `Ctrl/Cmd+K` for search palette.
  - `g` then `o|b|s|t|r|n|i|a|m|c|h` for route jumps.
- Theme toggle (light/dark/system with localStorage persistence).
- Toast feedback for write mutations (schedule/state operations).

## Route-Level Features

## Overview (`/`)

- Aggregate cards for total/healthy butlers.
- Live cards for `Sessions Today` and `Est. Cost Today`.
- Topology graph with clickable butler nodes (switchboard/heartbeat-aware layout).
- Failed Notifications panel with quick link to full notifications view.
- Active Issues panel with alert list and dismiss actions.

## Butlers (`/butlers`)

- Sorted butler cards with status badge and port metadata.
- Summary cards for total and healthy butlers.
- Explicit states:
  - loading skeleton
  - empty result
  - initial load error
  - refetch error with stale data retained

## Calendar Workspace (`/butlers/calendar`)

- Top-level `User` / `Butler` segmented toggle, persisted in URL query state (`view`) and restored from deep links/reloads.
- Range controls for `month`, `week`, `day`, and `list`, with `Prev`/`Today`/`Next` navigation.
- Query-state range model (`range`, `anchor`) drives calendar window selection and route-restorable state.
- Calendar shell consumes workspace read/meta APIs:
  - `GET /api/calendar/workspace` for normalized entries + source freshness + lanes.
  - `GET /api/calendar/workspace/meta` for connected sources and lane metadata.
- Dual-pane shell layout:
  - primary calendar canvas (month grid or event table by selected range)
  - side panel for source freshness and butler lane summaries

## Butler Detail (`/butlers/:name`)

### Overview Tab

- Butler identity/status/port card.
- Module health badges (when module data is available).
- Cost card for selected butler (today scope + share of global spend).
- Recent notifications feed scoped to the selected butler.

### Sessions Tab

- Butler-scoped session table.
- Pagination and row click to open session detail drawer.

### Config Tab

- Display of `butler.toml` data with formatted/raw toggle.
- Display of `CLAUDE.md`, `AGENTS.md`, and `MANIFESTO.md` content.

### Skills Tab

- Skill cards with inferred short description (first non-heading line).
- Full `SKILL.md` content in modal.
- "Trigger" action that pre-fills Trigger tab via `?tab=trigger&skill=...`.

### Schedules Tab

- Schedule table: cron/prompt/source/enabled/next-run/last-run.
- Schedule rows are mode-agnostic: runtime schedules and native schedules share the same UI surfaces; native runs may not have a matching session row.
- Mutations:
  - create schedule
  - edit schedule
  - toggle schedule enabled state
  - delete schedule (confirmed dialog)

### State Tab

- Key-value browser with prefix filter.
- Expand/collapse JSON payloads.
- Mutations:
  - set/create value
  - edit value
  - delete key (confirmed dialog)

### Trigger Tab

- Freeform prompt submission to trigger a butler session.
- Immediate result panel (success/failure + output/error + session link).
- In-memory trigger history for current page session.
- Skill-prefill support from Skills tab.

### MCP Tab

- Lists MCP tools exposed by the selected butler.
- Allows ad-hoc MCP tool invocation with optional JSON object arguments.
- Shows parsed result payload, raw text payload, and tool error status.

### CRM Tab

- For `relationship` butler:
  - upcoming dates widget (next 30 days)
  - quick links to contacts and groups
- For non-relationship butlers:
  - informational unavailable-state card

### Memory Tab

- Memory tier cards (episodes/facts/rules health).
- Memory browser tabs (facts/rules/episodes), scoped to current butler.

### Health Tab

- For `health` butler: quick-link cards to health sub-routes.
- For non-health butlers: informational unavailable-state card.

### General-Only Tabs

- `Collections`: paginated collection cards with entity counts.
- `Entities`: searchable/filterable entity browser.

### Switchboard-Only Tabs

- `Routing Log`: filterable source/target table with pagination.
- `Registry`: registered butlers, endpoints, module badges, last-seen time.

## Sessions (`/sessions`)

- Cross-butler session table with filters:
  - butler
  - trigger source
  - status
  - date range
- Auto-refresh toggle (interval + pause/resume).
- Drawer detail view for selected session.

## Session Detail (`/sessions/:id`)

- Metadata card, prompt, result, and error sections.
- Supports butler-scoped fetch via `?butler=<name>` and global fetch fallback.

## Traces (`/traces`)

- Paginated trace table (root butler, spans, status, duration, start time).

## Trace Detail (`/traces/:traceId`)

- Trace metadata card with root butler link.
- Interactive expandable span waterfall with nested children and token/model details.

## Timeline (`/timeline`)

- Unified timeline with butler and event-type filters.
- Auto-refresh toggle.
- Cursor-based "Load More".
- Heartbeat/tick collapsing into grouped entries for readability.

## Notifications (`/notifications`)

- Notification stats bar:
  - total
  - sent
  - failed
  - failure rate
  - by-channel badges
- Filterable notifications feed by:
  - butler
  - channel
  - status
  - date range
- Notification drill-through links to session and trace detail when IDs are present.
- Pagination.

## Issues (`/issues`)

- Grouped error/warning list across butlers (grouped by error message).
- Chronology metadata per group: occurrences + first seen + last seen.
- Newest-first ordering by latest occurrence time.
- Dismiss support with local persistence.
- Polling-backed feed from `/api/issues`.

## Audit Log (`/audit-log`)

- Filterable entries by butler, operation, and date range.
- Expandable row detail showing request payload, user context, and error body.
- Pagination.

## Contacts (`/contacts`)

- Search + label-filterable contacts table.
- Header action: `Sync From Google` triggers manual incremental Google contacts sync and refreshes the list on success.
- Pagination.
- Row click navigation to detail.

## Contact Detail (`/contacts/:contactId`)

- Contact profile header (identity, labels, contact channels, metadata).
- Sub-tabs:
  - Notes
  - Interactions
  - Gifts
  - Loans
  - Activity

## Groups (`/groups`)

- Paginated group table (description, member count, labels, created date).

## Health Routes

- Measurements (`/health/measurements`):
  - type chips
  - date filters
  - chart (single-line or BP dual-line)
  - optional raw table
- Medications (`/health/medications`):
  - active/all filters
  - medication cards
  - expandable dose log with adherence percentage
- Conditions (`/health/conditions`):
  - paginated status table
- Symptoms (`/health/symptoms`):
  - name/date filters
  - severity visualization
  - paginated table
- Meals (`/health/meals`):
  - meal-type/date filters
  - grouped-by-day tables
  - paginated list
- Research (`/health/research`):
  - search + tag filters
  - expandable note content rows
  - paginated table

## General Data Routes

- Collections (`/collections`):
  - collection cards with entity counts
  - click-through to filtered entities
  - pagination
- Entities (`/entities`):
  - search, collection filter, tag filter
  - URL-synced collection/tag query params
  - expandable JSON previews
  - pagination
- Entity Detail (`/entities/:entityId`):
  - metadata and full JSON payload viewer

## Ingestion (`/ingestion`)

Redesigned Dispatch-language surface (`openspec/specs/dashboard-ingestion-dispatch-console/spec.md`)
as first-class routes rather than a page-level tab switcher. Legacy `?tab=`
query-param URLs and the bare `/connectors` route redirect into the
equivalent sub-route.

- Timeline (`/ingestion`, default route):
  - Reverse-chronological event ledger with a time-first, every-row-expandable
    layout; range picker (1h/24h/7d), toolbar, hour strip with dispatch ticks
    (per-butler dispatch activity), saved views, and event drawer (raw
    payload, replay history, step ledger).
- Connectors (`/ingestion/connectors`):
  - Dense hairline-divided connector register (`ConnectorsRoster`) — no card
    chrome. Attention strip surfaces unhealthy connectors above the table.
    Summary-level liveness/health/today's counts only; no per-connector
    volume chart or fanout matrix (see "Orphaned capabilities" below).
  - Discovery: dormant/available-but-not-registered connector types listed
    separately from the live roster.
- Connector Detail (`/ingestion/connectors/:connectorType/:endpointIdentity`):
  - Identity, current status (liveness, health, error, uptime), lifetime
    counters, checkpoint cursor, 24h stats histogram, recent events and
    incidents, connector-scoped routing rules, and a settings editor
    (`BatchSettingsCard`).
- Filters (`/ingestion/filters`):
  - Five-gate pipeline diagram with a proportional funnel, gate sections with
    rule rows, priority senders block, channel-defaults inline editor,
    archived rules section, and a rule editor (create/edit + DSL dry-run
    test) with footer actions.

### Orphaned capabilities (accepted loss, bu-4utdw.2)

The legacy tabbed `/ingestion` surface (`IngestionPage`, flag-gated behind
`INGESTION_DISPATCH_CONSOLE`) was deleted once the redesign above shipped as
the default. The owner explicitly accepted dropping these legacy-only
capabilities rather than porting them:

- Backfill job manager tab (`BackfillHistoryTab`) — the component still
  exists at `frontend/src/components/switchboard/BackfillHistoryTab.tsx` but
  is no longer mounted anywhere; needs a rehome or an intentional retirement
  decision (follow-up discovered from bu-4utdw.2).
- Thread-affinity settings (global enable/TTL) and Gmail label include/exclude
  filters, previously exposed via `FiltersTab.tsx` (also still present but
  unmounted) — not yet ported to the new Filters Pipeline surface (same
  follow-up).
- Fanout distribution matrix (connector × butler message counts).
- Volume time-series chart and the 24h/7d/30d period selector on it.
- Tier-breakdown donut chart.
- The legacy connector-card inline delete (deregister) button.

## Costs (`/costs`)

- Period selector (7d/30d/90d).
- Summary cards:
  - total cost
  - session count
  - input tokens
  - output tokens
- Area chart of daily spend.
- Cost-by-butler breakdown table with percentage bars.

## Memory (`/memory`)

- Memory tier health cards.
- Browser tabs for facts/rules/episodes with search and pagination.
- Recent memory activity timeline.

## Settings (`/settings`)

- Appearance controls (`light`/`dark`/`system`) backed by persisted theme preference.
- Live-refresh defaults (enabled + interval) persisted and reused by Sessions/Timeline auto-refresh controls.
- Command palette maintenance action to clear locally stored recent searches.

## Implemented But Not Currently Wired to a Route

- `CostWidget` component.
- `TopSessionsTable` component and `useTopSessions` hook.
- `BackfillHistoryTab` and `FiltersTab` (switchboard) — see "Orphaned
  capabilities" under Ingestion above.

## Current Gaps / Partial States

- Many domain pages are read-focused with no create/edit/delete flows.
- Approvals workflows are implemented with dedicated frontend surfaces at `/approvals` (action queue + decision UI + metrics) and `/approvals/rules` (standing rule management).

## Related Pages

- [Information Architecture](information-architecture.md) -- Route map and tab structures
- [Data Access and Refresh](data-access-and-refresh.md) -- API domains and refresh intervals
- [Backend API Contract](backend-api-contract.md) -- Required backend endpoints
- [Purpose and Single-Pane Role](purpose-and-single-pane.md) -- Dashboard scope boundaries
