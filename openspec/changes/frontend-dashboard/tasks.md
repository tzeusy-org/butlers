## 1. Framework Changes — Schema & Core Tools

- [ ] 1.1 Alembic migration: add `input_tokens` (INT nullable), `output_tokens` (INT nullable), `model` (TEXT nullable), `trace_id` (TEXT nullable), `parent_session_id` (UUID nullable) columns to `sessions` table in core chain
- [ ] 1.2 Update LLM CLI spawner to extract token usage (`input_tokens`, `output_tokens`, `model`) from Claude Agent SDK response and pass to `sessions.log_session()`
- [ ] 1.3 Update spawner to write session record with `completed_at = NULL` before spawning (active session detection), update on completion
- [ ] 1.4 Alembic migration: add `notifications` table to Switchboard chain (id UUID PK, source_butler, channel, recipient, message, metadata JSONB, status, error, session_id, trace_id, created_at; indexes on source_butler+created_at, channel+created_at, status)
- [ ] 1.5 Implement Switchboard `deliver(channel, message, recipient?, metadata?)` MCP tool — dispatches to telegram/email module, logs to notifications table, returns delivery result
- [ ] 1.6 Implement core `notify(channel, message, recipient?)` MCP tool registered on every butler daemon — forwards to Switchboard's `deliver()` via MCP client
- [ ] 1.7 Add Switchboard MCP client connection to butler daemon startup (configured via `switchboard_url` in butler.toml or derived from Switchboard config)
- [ ] 1.8 Handle `notify()` error cases: Switchboard unreachable returns error result (not exception), invalid channel returns error
- [ ] 1.9 Tests for token capture in spawner (mock SDK response with usage data)
- [ ] 1.10 Tests for `notify()` and `deliver()` tools (mock Switchboard client, mock telegram/email modules)

## 2. Dashboard API — Scaffolding

- [ ] 2.1 Add FastAPI, uvicorn, httpx to pyproject.toml dependencies
- [ ] 2.2 Create `src/butlers/api/__init__.py`
- [ ] 2.3 Create `src/butlers/api/app.py` — FastAPI app factory with CORS middleware, router registration, lifespan (startup/shutdown for DB pools and MCP clients)
- [ ] 2.4 Create `src/butlers/api/db.py` — `DatabaseManager` class: one asyncpg pool per butler DB, `pool(butler_name)`, `fan_out(query, butler_names?)` for concurrent cross-butler queries, startup/shutdown lifecycle
- [ ] 2.5 Create `src/butlers/api/deps.py` — `MCPClientManager` (lazy FastMCP client per butler, graceful unreachable handling), `discover_butlers()` using existing config loader, FastAPI dependency functions
- [ ] 2.6 Create `src/butlers/api/models/__init__.py` — shared Pydantic response/request models base
- [ ] 2.7 Add `GET /api/health` endpoint returning per-butler daemon and database status
- [ ] 2.8 Add `butlers dashboard` CLI command to `cli.py` — starts uvicorn with `--host` (default 0.0.0.0) and `--port` (default 40200) options
- [ ] 2.9 Add API error handling: consistent `{"error": {"code", "message", "butler?"}}` format, 502 for unreachable butlers, 404 for unknown butlers
- [ ] 2.10 Create `pricing.toml` config for per-model token pricing, loaded at API startup
- [ ] 2.11 Tests for DatabaseManager (pool creation, fan_out, shutdown)
- [ ] 2.12 Tests for MCPClientManager (lazy connection, unreachable handling)
- [ ] 2.13 Tests for butler discovery from config directories

## 3. Dashboard API — Butler Discovery & Status

- [ ] 3.1 Create `src/butlers/api/routers/butlers.py`
- [ ] 3.2 Create `src/butlers/api/models/butler.py` — ButlerSummary, ButlerDetail, ModuleStatus Pydantic models
- [ ] 3.3 Implement `GET /api/butlers` — discover butlers from config dirs, call `status()` via MCP client on each, return aggregated list with live health
- [ ] 3.4 Implement `GET /api/butlers/:name` — single butler detail with live status, module health
- [ ] 3.5 Implement `GET /api/butlers/:name/config` — read butler.toml, CLAUDE.md, AGENTS.md from disk
- [ ] 3.6 Implement `GET /api/butlers/:name/skills` — list skills/ directory, read SKILL.md files
- [ ] 3.7 Implement `GET /api/butlers/:name/modules` — module list with health status
- [ ] 3.8 Implement `POST /api/butlers/:name/trigger` — trigger CC via MCP client, return session result
- [ ] 3.9 Implement `POST /api/butlers/:name/tick` — force scheduler tick via MCP client
- [ ] 3.10 Implement `GET /api/issues` — aggregate active issues (unreachable butlers, failing scheduled tasks, cost anomalies)
- [ ] 3.11 Tests for butler discovery and status endpoints

## 4. Dashboard API — Sessions

- [ ] 4.1 Create `src/butlers/api/routers/sessions.py`
- [ ] 4.2 Create `src/butlers/api/models/session.py` — SessionSummary, SessionDetail Pydantic models
- [ ] 4.3 Implement `GET /api/sessions` — cross-butler paginated sessions via fan-out query (limit, offset, butler, trigger_source, success, from, to)
- [ ] 4.4 Implement `GET /api/butlers/:name/sessions` — single-butler paginated sessions
- [ ] 4.5 Implement `GET /api/butlers/:name/sessions/:id` — session detail with full prompt, result, tool_calls, tokens, trace_id
- [ ] 4.6 Tests for session endpoints (pagination, filtering, cross-butler aggregation)

## 5. Dashboard API — Schedules & State

- [ ] 5.1 Create `src/butlers/api/routers/schedules.py`
- [ ] 5.2 Create `src/butlers/api/models/schedule.py` — Schedule Pydantic model
- [ ] 5.3 Implement `GET /api/butlers/:name/schedules` — list schedules via direct DB read
- [ ] 5.4 Implement schedule write endpoints via MCP tool proxying: POST create, PUT update, DELETE, PATCH toggle
- [ ] 5.5 Create `src/butlers/api/routers/state.py`
- [ ] 5.6 Create `src/butlers/api/models/state.py` — StateEntry Pydantic model
- [ ] 5.7 Implement `GET /api/butlers/:name/state` — list state entries with prefix filter via DB read
- [ ] 5.8 Implement `GET /api/butlers/:name/state/:key` — single state entry
- [ ] 5.9 Implement state write endpoints via MCP tool proxying: PUT set, DELETE
- [ ] 5.10 Tests for schedule and state endpoints (CRUD, MCP proxying, error handling)

## 6. Dashboard API — Costs

- [ ] 6.1 Create `src/butlers/api/routers/costs.py`
- [ ] 6.2 Implement cost estimation logic: load pricing.toml, compute cost = input_tokens × input_price + output_tokens × output_price
- [ ] 6.3 Implement `GET /api/costs/summary` — aggregate cost data (today, 7d, 30d; per-butler breakdown) via fan-out
- [ ] 6.4 Implement `GET /api/costs/daily` — daily cost time series (from, to params)
- [ ] 6.5 Implement `GET /api/costs/top-sessions` — most expensive sessions (limit param)
- [ ] 6.6 Implement `GET /api/costs/by-schedule` — per-scheduled-task average cost and projected monthly spend
- [ ] 6.7 Tests for cost endpoints and estimation logic

## 7. Dashboard API — Traces

- [ ] 7.1 Create `src/butlers/api/routers/traces.py`
- [ ] 7.2 Create `src/butlers/api/models/trace.py` — TraceSummary, TraceDetail, SpanNode Pydantic models
- [ ] 7.3 Implement `GET /api/traces` — list traces aggregated from sessions across butler DBs, grouped by trace_id
- [ ] 7.4 Implement `GET /api/traces/:trace_id` — trace detail: fan-out query sessions + routing_log, assemble tree via parent_session_id
- [ ] 7.5 Implement trace tree assembly algorithm: index by ID, link by parent_session_id, NULL parents are roots, sort children by started_at
- [ ] 7.6 Tests for trace endpoints and tree assembly

## 8. Dashboard API — Timeline & Search

- [ ] 8.1 Create `src/butlers/api/routers/timeline.py`
- [ ] 8.2 Implement `GET /api/timeline` — cross-butler event stream: fan-out query sessions, routing_log, notifications, merge and sort by timestamp, cursor-based pagination
- [ ] 8.3 Implement event type mapping: sessions → session/error events, routing_log → routing events, notifications → notification events
- [ ] 8.4 Create `src/butlers/api/routers/search.py`
- [ ] 8.5 Implement `GET /api/search?q=...` — fan-out ILIKE search across butler DBs (sessions, state, contacts, entities, research) + filesystem skills scan, return grouped results
- [ ] 8.6 Tests for timeline (pagination, filtering, event merging) and search endpoints

## 9. Dashboard API — Notifications

- [ ] 9.1 Create `src/butlers/api/routers/notifications.py`
- [ ] 9.2 Create `src/butlers/api/models/notification.py` — Notification, NotificationStats Pydantic models
- [ ] 9.3 Implement `GET /api/notifications` — paginated notification history from Switchboard DB (butler, channel, status, from, to filters)
- [ ] 9.4 Implement `GET /api/notifications/stats` — summary stats (total today, failure rate, by butler, by channel)
- [ ] 9.5 Implement `GET /api/butlers/:name/notifications` — butler-scoped notification list
- [ ] 9.6 Tests for notification endpoints

## 10. Dashboard API — Domain-Specific Routers

- [ ] 10.1 Create `src/butlers/api/routers/relationship.py` — contacts list/search, detail with joins, sub-resources (notes, interactions, gifts, loans, feed), groups, labels, upcoming-dates
- [ ] 10.2 Create `src/butlers/api/models/relationship.py` — Contact, Group, Label, Note, Interaction, Gift, Loan Pydantic models
- [ ] 10.3 Create `src/butlers/api/routers/health.py` — measurements, medications (with dose log), conditions, symptoms, meals, research
- [ ] 10.4 Create `src/butlers/api/models/health.py` — Measurement, Medication, Dose, Condition, Symptom, Meal, Research Pydantic models
- [ ] 10.5 Create `src/butlers/api/routers/general.py` — collections (with entity counts), entities (list/search, detail)
- [ ] 10.6 Create `src/butlers/api/models/general.py` — Collection, Entity Pydantic models
- [ ] 10.7 Create `src/butlers/api/routers/switchboard.py` — routing-log (paginated), registry snapshot
- [ ] 10.8 Tests for domain-specific routers (relationship, health, general, switchboard)

## 11. Frontend — Project Scaffolding

- [ ] 11.1 Scaffold `frontend/` with Vite + React 18 + TypeScript (`npm create vite@latest`)
- [ ] 11.2 Install and configure Tailwind CSS
- [ ] 11.3 Install and configure shadcn/ui (init, add Button, Card, Table, Badge, Tabs, Dialog, DropdownMenu, Input, Textarea, Select, Toggle, Tooltip, Skeleton, Toast, Sheet)
- [ ] 11.4 Install React Router v7, configure routes for all pages (/, /timeline, /sessions, /traces, /traces/:traceId, /costs, /notifications, /butlers/:name)
- [ ] 11.5 Install and configure TanStack Query (QueryClient with defaults, QueryClientProvider)
- [ ] 11.6 Create `src/api/client.ts` — base fetch wrapper with configurable VITE_API_URL, error handling, typed responses
- [ ] 11.7 Create `src/api/types.ts` — TypeScript types matching API Pydantic models
- [ ] 11.8 Install cmdk, React Flow, Recharts, date-fns
- [ ] 11.9 Create `src/styles/globals.css` — Tailwind imports + shadcn theme variables
- [ ] 11.10 Set up Vite proxy for API in development (`vite.config.ts`)
- [ ] 11.11 Verify: frontend dev server renders, proxies to API health endpoint

## 12. Frontend — App Shell & Layout

- [ ] 12.1 Create `src/components/layout/Shell.tsx` — three-region layout: sidebar, header, main content
- [ ] 12.2 Create `src/components/layout/Sidebar.tsx` — collapsible sidebar with nav items (Overview, Timeline, Butlers section with status dots, Sessions, Traces, Notifications, Costs), issues badge, today's spend footer
- [ ] 12.3 Create `src/components/layout/PageHeader.tsx` — page title, breadcrumbs, dark mode toggle
- [ ] 12.4 Implement dark mode toggle — Tailwind dark class, persisted to localStorage, system preference fallback
- [ ] 12.5 Implement responsive sidebar — collapse to off-screen drawer below 768px, icon-only mode on desktop
- [ ] 12.6 Create error boundary component wrapping routed content
- [ ] 12.7 Create toast notification system for API errors and write confirmations
- [ ] 12.8 Create `src/api/hooks/useButlers.ts` — TanStack Query hook for butler list (populates sidebar)

## 13. Frontend — Overview Page

- [ ] 13.1 Create `src/pages/Overview.tsx`
- [ ] 13.2 Create `src/components/topology/TopologyGraph.tsx` — React Flow graph: Switchboard center, butler nodes with status badges, Heartbeat with dashed edges, active session pulse, module health dots, click → navigate
- [ ] 13.3 Create `src/components/issues/IssuesPanel.tsx` — prominent alert section: unreachable butlers, failing tasks, cost anomalies, failed notifications. Dismissable per-issue (localStorage)
- [ ] 13.4 Implement aggregate stats bar — total butlers, healthy count, sessions today, estimated cost today
- [ ] 13.5 Create cost summary widget — today's spend, 7-day sparkline (Recharts), top spender
- [ ] 13.6 Create recent activity feed — last 10 cross-butler events (preview of timeline). Implement heartbeat tick collapsing: consecutive heartbeat tick events (type `schedule`, `task_name` matches heartbeat pattern) within the same 10-minute cycle are collapsed into "Heartbeat: N butlers ticked, M failures". Collapsing is frontend-only; API returns individual events.
- [ ] 13.7 Create `src/api/hooks/useIssues.ts` — TanStack Query hook for issues endpoint

## 13b. Frontend — Overview Page: Issues & Aggregation

- [ ] 13b.1 Create `src/butlers/api/routers/issues.py` — issues aggregation endpoint (`GET /api/issues`): unreachable butlers, failing scheduled tasks (N consecutive failures), module dependency failures (per D13), butler-level cost anomalies (daily spend >2x 7-day daily average per D14), failed notification deliveries
- [ ] 13b.2 Create `src/butlers/api/models/issue.py` — Issue Pydantic model with fields: `severity` (critical/warning), `type` (unreachable/failing_task/module_error/cost_anomaly/notification_failure), `butler`, `description`, `link`
- [ ] 13b.3 Implement butler-level cost anomaly detection in issues endpoint — compute today's aggregate spend per butler, compare to 7-day daily average, flag when ratio > 2x (per D14)

## 14. Frontend — Butler Detail Page

- [ ] 14.1 Create `src/pages/ButlerDetail.tsx` — tabbed detail view with tab navigation via query params
- [ ] 14.2 Create Overview tab — identity card (name, MANIFESTO.md first line, port, uptime), module health badges using `status()` MCP response per D13 (green=connected, yellow=degraded, red=error, gray=unknown), active session indicator (elapsed time when `completed_at IS NULL`, otherwise "Idle"), error summary (failed sessions count in last 24h with link to filtered sessions tab), cost card, recent notifications (last 5)
- [ ] 14.3 Create Config tab — structured butler.toml view with raw toggle, CLAUDE.md rendered markdown, AGENTS.md, module credential status
- [ ] 14.4 Create Skills tab — skill cards (name, description), click → full SKILL.md, "trigger with skill" button
- [ ] 14.5 Create Trigger tab — prompt textarea, submit button, result display with token usage and cost

## 15. Frontend — Sessions

- [ ] 15.1 Create `src/api/hooks/useSessions.ts` — TanStack Query hooks for session list and detail
- [ ] 15.2 Create `src/components/sessions/SessionTable.tsx` — paginated table: timestamp, butler badge, trigger source, prompt (truncated), duration, tokens, cost, success/fail badge
- [ ] 15.3 Create `src/components/sessions/SessionDetail.tsx` — drawer/panel: full prompt, tool calls timeline, result, error, token breakdown, cost, trace link
- [ ] 15.4 Create `src/pages/Sessions.tsx` — cross-butler sessions page with filters (butler, date range, trigger source, success/fail)
- [ ] 15.5 Add Sessions tab to butler detail page — scoped session table
- [ ] 15.6 Implement cost column derivation (tokens × pricing config) and anomaly badges (>3x average)

## 16. Frontend — Schedules & State

- [ ] 16.1 Create `src/api/hooks/useSchedules.ts` — TanStack Query hooks with mutation support
- [ ] 16.2 Create `src/components/schedules/ScheduleTable.tsx` — table: name, cron + human-readable, next run, source badge, enabled toggle, last result, "Run now" button
- [ ] 16.3 Create `src/components/schedules/ScheduleForm.tsx` — create/edit form: name, cron input with live preview, prompt textarea, validation
- [ ] 16.4 Add Schedules tab to butler detail — table + CRUD (create, edit modal, toggle, delete with confirmation)
- [ ] 16.5 Create `src/api/hooks/useState.ts` — TanStack Query hooks with mutation support
- [ ] 16.6 Create `src/components/state/StateBrowser.tsx` — key-value table, expandable JSON rows, prefix search, syntax highlighting
- [ ] 16.7 Add State tab to butler detail — browser + set key modal (JSON editor with validation) + delete with confirmation

## 17. Frontend — Costs Page

- [ ] 17.1 Create `src/api/hooks/useCosts.ts` — TanStack Query hooks for cost endpoints
- [ ] 17.2 Create `src/components/costs/CostChart.tsx` — Recharts area chart: daily/weekly/monthly spend stacked by butler, period selector
- [ ] 17.3 Create `src/components/costs/CostSummary.tsx` — butler breakdown table: name, sessions, tokens, cost, % of total, trend arrow
- [ ] 17.4 Create `src/components/costs/TopSessions.tsx` — most expensive sessions table
- [ ] 17.5 Create `src/pages/Costs.tsx` — full cost page combining chart, breakdown, top sessions, per-schedule analysis

## 18. Frontend — Traces

- [ ] 18.1 Create `src/api/hooks/useTraces.ts` — TanStack Query hooks for trace list and detail
- [ ] 18.2 Create `src/components/traces/TraceList.tsx` — table: truncated trace ID, start time, duration, entry point, span count
- [ ] 18.3 Create `src/components/traces/TraceTimeline.tsx` — waterfall/timeline view: span bars colored by butler, proportional positioning, click → attributes panel
- [ ] 18.4 Create `src/pages/Traces.tsx` — trace list with date range and butler filters
- [ ] 18.5 Create `src/pages/TraceDetail.tsx` — trace detail with waterfall view and span attributes panel
- [ ] 18.6 Add trace_id link in session detail drawer → navigate to /traces/:traceId

## 19. Frontend — Timeline & Search

- [ ] 19.1 Create `src/api/hooks/useTimeline.ts` — TanStack Query hook with cursor-based pagination
- [ ] 19.2 Create `src/components/timeline/UnifiedTimeline.tsx` — vertical event stream: timestamp, butler badge, event type icon, one-line summary, expandable detail
- [ ] 19.3 Create `src/pages/Timeline.tsx` — timeline page with butler multi-select, event type multi-select, date range filters, auto-refresh toggle, infinite scroll
- [ ] 19.4 Create `src/api/hooks/useSearch.ts` — TanStack Query hook with debounce
- [ ] 19.5 Create `src/components/layout/CommandPalette.tsx` — cmdk overlay: Cmd+K / / shortcut, debounced search, results grouped by category, keyboard navigation, recent searches in localStorage

## 20. Frontend — Notifications

- [ ] 20.1 Create `src/api/hooks/useNotifications.ts` — TanStack Query hooks for notification list and stats
- [ ] 20.2 Create `src/components/notifications/NotificationFeed.tsx` — reverse-chronological feed: timestamp, butler badge, channel icon, message, status badge (sent/failed/pending), click → expanded view
- [ ] 20.3 Create `src/components/notifications/NotificationStats.tsx` — stats bar: total today, failure rate, most active butler, most used channel
- [ ] 20.4 Create `src/pages/Notifications.tsx` — notification page with filters (butler, channel, status, date range) and stats bar

## 21. Frontend — Relationship Butler Views

- [ ] 21.1 Create `src/api/hooks/useContacts.ts` — TanStack Query hooks for contacts, detail, sub-resources
- [ ] 21.2 Create `src/components/relationship/ContactTable.tsx` — searchable, label-filterable, sortable contact table
- [ ] 21.3 Create `src/components/relationship/ContactDetail.tsx` — header card, contact info, important dates with countdown, quick facts, relationships, tabbed content (Notes | Interactions | Gifts | Loans), activity feed
- [ ] 21.4 Create `src/pages/relationship/ContactsPage.tsx` and `ContactDetailPage.tsx`
- [ ] 21.5 Create `src/pages/relationship/GroupsPage.tsx` — groups list with member count, group detail with member list
- [ ] 21.6 Add Relationship sub-tabs to butler detail page (Contacts, Groups, Routing Log for Switchboard)

## 22. Frontend — Health Butler Views

- [ ] 22.1 Create `src/api/hooks/useMeasurements.ts` — TanStack Query hooks for health endpoints
- [ ] 22.2 Create `src/components/health/MeasurementChart.tsx` — Recharts line charts by type (weight, BP dual-line, HR), date range picker, type selector tabs, raw data table toggle
- [ ] 22.3 Create `src/components/health/MedicationTracker.tsx` — medication cards, dose log table, adherence percentage
- [ ] 22.4 Create Health sub-pages: MeasurementsPage, MedicationsPage, ConditionsPage, SymptomsPage, MealsPage, ResearchPage
- [ ] 22.5 Add Health sub-tabs to butler detail page

## 23. Frontend — General Butler & Switchboard Views

- [ ] 23.1 Create `src/components/general/EntityBrowser.tsx` — entity table with collection/tag filters, search, click → JSON tree viewer
- [ ] 23.2 Create `src/components/general/JsonViewer.tsx` — syntax highlighted, collapsible tree, copy-to-clipboard
- [ ] 23.3 Create General sub-pages: CollectionsPage, EntitiesPage
- [ ] 23.4 Create Switchboard sub-tabs: routing log table (timestamp, source, routed-to, prompt summary), registry table (name, endpoint, modules, last seen)
- [ ] 23.5 Add General and Switchboard sub-tabs to butler detail page

## 24. Docker & Deployment

- [ ] 24.1 Add `dashboard-api` service to docker-compose.yml — port 40200, depends on postgres healthy, mounts butlers/ config read-only
- [ ] 24.2 Add `frontend` dev service to docker-compose.yml (behind compose profile, not started by default) — node:22-slim, port 40173
- [ ] 24.3 Configure FastAPI static file serving for production — mount frontend/dist/ at / with html=True fallback, API routes take precedence
- [ ] 24.4 Add frontend build step documentation (vite build → dist/ → served by dashboard-api)

## 25. Polish & Integration

- [ ] 25.1 Loading states — skeleton loaders for all data tables and charts
- [ ] 25.2 Empty states — meaningful messages when no data for each page/tab
- [ ] 25.3 Breadcrumb navigation on all pages
- [ ] 25.4 Keyboard shortcuts: / or Cmd+K → search, g o → overview, g t → timeline
- [ ] 25.5 SSE endpoint for live butler status and active session updates (foundation for real-time)
- [ ] 25.6 Auto-refresh for timeline and session lists (polling with TanStack Query refetchInterval)

## 27. Audit Log

- [ ] 27.1 Alembic migration for `dashboard_audit_log` table in Switchboard DB (id UUID PK, timestamp TIMESTAMPTZ, butler TEXT, operation TEXT, user_context JSONB, request_summary JSONB, result TEXT, error_message TEXT; indexes on butler+timestamp, operation+timestamp, timestamp)
- [ ] 27.2 Create `src/butlers/api/routers/audit.py` — `GET /api/audit-log` endpoint with pagination, filtering by butler/operation/date range
- [ ] 27.3 Create `src/butlers/api/models/audit.py` — AuditEntry Pydantic model (id, timestamp, butler, operation, user_context, request_summary, result, error_message)
- [ ] 27.4 Add audit logging decorator to all write endpoints (trigger, tick, schedule CRUD, state CRUD) — extracts IP/User-Agent, logs operation result, non-blocking on failure
- [ ] 27.5 Create `src/components/audit/AuditLogTable.tsx` — paginated audit log table with columns: timestamp, butler badge, operation badge, IP, request summary, result badge, error message
- [ ] 27.6 Create `src/api/hooks/useAuditLog.ts` — TanStack Query hook for audit log endpoint with filters
- [ ] 27.7 Tests for audit log endpoints (CRUD logging, filtering, pagination, error handling)

## 28. Memory System Views (Contingent)

> Blocked on memory system implementation. Implement after `memories` table and MCP tools exist.

- [ ] 26.1 Create `src/butlers/api/routers/memory.py` — stats, entries (browse/search), single entry, activity endpoints
- [ ] 26.2 Create `src/butlers/api/models/memory.py` — MemoryStats, MemoryEntry Pydantic models
- [ ] 26.3 Create `src/components/memory/MemoryTierCards.tsx` — Eden/Mid-Term/Long-Term cards with capacity bars
- [ ] 26.4 Create `src/components/memory/MemoryBrowser.tsx` — searchable/filterable table of entries
- [ ] 26.5 Create `src/components/memory/MemoryActivity.tsx` — promotion/eviction timeline
- [ ] 26.6 Add Memory tab to butler detail page with health indicators (eviction rate, saturation, promotion rate)
