# Frontend Feature Inventory (Implemented)

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

- Active issues list with severity/butler context.
- Dismiss support with local persistence.
- Polling-backed feed of current operational alerts.

## Audit Log (`/audit-log`)

- Filterable entries by butler, operation, and date range.
- Expandable row detail showing request payload, user context, and error body.
- Pagination.

## Contacts (`/contacts`)

- Search + label-filterable contacts table.
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

## Connectors (`/connectors`)

- Connector overview cards:
  - One card per registered connector showing type icon, endpoint identity, liveness badge (online/stale/offline), self-reported health state (healthy/degraded/error), uptime percentage (today), last heartbeat age, and today's ingestion count.
- Volume time series chart:
  - Line or bar chart of ingestion volume per connector over selected period.
  - Period selector: 24h / 7d / 30d.
  - Toggle per-connector visibility.
- Fanout distribution table:
  - Matrix of connector x butler showing message counts for the selected period.
  - Columns: target butlers. Rows: connectors. Cells: message count.
- Error log panel:
  - Recent connector errors (heartbeats with state != healthy).
  - Columns: timestamp, connector type + identity, state (degraded/error), error message.
- Cross-connector summary stats:
  - Total connectors, online count, stale count, offline count, total messages ingested, total messages failed, overall error rate.

## Connector Detail (`/connectors/:connectorType/:endpointIdentity`)

- Connector identity card:
  - Type, endpoint identity, instance ID, version, registered_via, first_seen_at.
- Current status card:
  - Liveness badge, health state, error message (if any), uptime, last heartbeat age.
- Counters card:
  - Lifetime monotonic counters: messages ingested, messages failed, source API calls, checkpoint saves, dedupe accepted.
- Checkpoint card:
  - Current cursor value and last updated timestamp.
- Volume + health time series:
  - Same chart as overview page but scoped to this connector.
  - Period selector: 24h / 7d / 30d.
- Fanout breakdown:
  - Per-butler message distribution for this connector over selected period.

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

## Current Gaps / Partial States

- Many domain pages are read-focused with no create/edit/delete flows.
- Approvals workflows are implemented with dedicated frontend surfaces at `/approvals` (action queue + decision UI + metrics) and `/approvals/rules` (standing rule management).
