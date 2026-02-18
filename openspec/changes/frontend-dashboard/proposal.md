## Why

The butler framework provides no visibility into system state, health, costs, or session history beyond CLI access and direct database queries. As the system grows to multiple butlers with scheduled tasks, inter-butler routing, and outbound notifications, operators need a single pane of glass to understand what the system is doing, what it costs, and what's broken. The dashboard is not the primary interaction method (chat is), but it is the definitive administrative interface.

## What Changes

- Add a FastAPI-based Dashboard API (`src/butlers/api/`) that provides REST endpoints over butler infrastructure
- Add a React + TypeScript frontend (`frontend/`) with topology visualization, session/trace browsing, cost tracking, and butler-specific domain views
- Introduce a dual data-access pattern: MCP client calls for real-time operations (status, trigger, tick) and direct asyncpg reads for data browsing (sessions, state, contacts, measurements)
- Add a core `notify()` tool to the butler framework enabling any butler to send outbound messages (telegram, email) through the Switchboard
- Add `notifications` table to the Switchboard database for delivery tracking
- Add token tracking columns (`input_tokens`, `output_tokens`, `model`, `trace_id`, `parent_session_id`) to the sessions table for cost estimation and trace correlation
- Add a `butlers dashboard` CLI command to start the API server
- Add Docker Compose services for the dashboard API and frontend dev server

## Capabilities

### New Capabilities

- `dashboard-api`: FastAPI application providing REST endpoints over butler infrastructure. Includes butler discovery, multi-DB connection management (one asyncpg pool per butler DB), MCP client integration for live operations, CORS, and health checks. Serves as the bridge between the React frontend and the butler ecosystem.
- `dashboard-frontend`: React 18 + TypeScript SPA built with Vite, shadcn/ui, Tailwind CSS, TanStack Query. Provides the sidebar-navigated shell, dark mode, responsive layout, and all page/component structure. Includes React Flow topology graph, Recharts visualizations, cmdk command palette, and React Router navigation.
- `dashboard-sessions`: Cross-butler session browsing and detail views. Paginated session lists with filtering (butler, date range, trigger source, success/fail), session detail with full prompt, tool call timeline, result, error details, and token/cost breakdown.
- `dashboard-schedules`: Schedule management UI and API. CRUD operations for cron-driven tasks: create, edit, toggle enable/disable, delete, and "run now". All write operations go through MCP client to the butler daemon.
- `dashboard-state`: State store browser and editor. Key-value listing with prefix filtering, JSON pretty-printing of values, set/delete operations via MCP client.
- `dashboard-costs`: Token usage and cost tracking. Aggregated cost summaries (daily/weekly/monthly), per-butler breakdown, per-schedule cost analysis, top expensive sessions, anomaly detection. Cost derived at query time from token counts and configurable per-model pricing.
- `dashboard-traces`: Simplified distributed trace viewer. Trace list with filtering, waterfall/timeline visualization of span hierarchy reconstructed from session records and routing logs using `trace_id` and `parent_session_id`.
- `dashboard-timeline`: Unified cross-butler event stream. Aggregates sessions, routing decisions, heartbeat ticks, state changes, notifications, and errors into a single chronological feed with cursor-based pagination, butler/type filters, and auto-refresh.
- `dashboard-search`: Global search via Cmd+K command palette. Fan-out search across all butler databases covering sessions, state keys, contacts, entities, skills, and research notes. Results grouped by category with highlighted snippets.
- `dashboard-notifications`: Notification history and monitoring. Cross-butler view of all outbound messages (telegram, email) with delivery status, failure highlighting, per-butler filtering, and summary statistics.
- `dashboard-relationship`: Relationship butler domain views. Contact browsing/search, contact detail pages (info, dates, facts, relationships, notes, interactions, gifts, loans, activity feed), groups, labels, and upcoming dates.
- `dashboard-health`: Health butler domain views. Measurement charts (weight, BP, HR over time), medication tracking with adherence stats, condition cards, symptom log with severity, meal timeline, and research notes browser.
- `dashboard-general`: General butler domain views. Collection listing, entity browsing with collection/tag filtering and full-text search, JSON tree viewer for entity data.
- `dashboard-switchboard`: Switchboard-specific views. Routing log table (timestamp, source, routed-to, prompt summary) and butler registry snapshot.
- `core-notify`: Framework-level core tool enabling any butler's runtime instance to send outbound messages through the Switchboard. Adds `notify(channel, message, recipient?)` as a core MCP tool, Switchboard `deliver()` tool, and `notifications` table in the Switchboard database.
- `dashboard-overview`: Overview page with topology graph (React Flow), aggregate stats bar (total butlers, healthy count, sessions today, cost), issues aggregation panel (unreachable butlers, failing tasks, module errors, cost anomalies, failed notifications), cost summary widget (today's spend, 7-day sparkline, top spender), and recent activity feed with heartbeat tick collapsing.
- `dashboard-butler-detail`: Butler detail overview tab with identity card (name, MANIFESTO.md description, port, uptime), module health badges (per D13), active session indicator (elapsed time or idle), and error summary (failed sessions in last 24h).
- `dashboard-audit`: Audit log tracking all dashboard-initiated write operations (trigger, schedule CRUD, state CRUD). Stores timestamp, butler, operation type, user context (IP/user-agent), request summary, and result. Paginated API with filtering. Frontend audit log table.
- `dashboard-memory`: Memory system browser (contingent on memory plan finalization). Tier overview cards, promotion/eviction timeline, searchable memory entry browser. **Blocked on memory system implementation.**

### Modified Capabilities

- `session-log`: Add `input_tokens`, `output_tokens`, `model`, `trace_id`, and `parent_session_id` columns. LLM CLI spawner must capture token usage from SDK response and store alongside session records.
- `llm-cli-spawner`: Update to capture and persist token usage (input/output tokens, model) from Claude Code SDK response into the session record.
- `switchboard`: Add `deliver(channel, message, recipient?, metadata?)` tool for notification routing, and `notifications` table for delivery logging.
- `cli-and-deployment`: Add `butlers dashboard` CLI command and Docker Compose services for dashboard-api and frontend.
- `dashboard-health`: Write operations (e.g., logging new measurements) explicitly deferred for v1. Dashboard health views are read-only; health data entry is handled via chat interactions with the Health butler.

## Impact

- **New code**: `src/butlers/api/` (FastAPI routers, models, deps, DB manager), `frontend/` (entire React SPA)
- **Modified code**: `src/butlers/core/` (spawner — token capture, session schema), Switchboard butler (deliver tool, notifications schema)
- **New dependencies (Python)**: FastAPI, uvicorn, httpx
- **New dependencies (frontend)**: React, TypeScript, Vite, Tailwind CSS, shadcn/ui, TanStack Query, React Flow, Recharts, cmdk, date-fns, React Router
- **Database changes**: New columns on `sessions` table (all butler DBs), new `notifications` table (Switchboard DB)
- **Infrastructure**: Dashboard API service (port 8200), frontend dev server (port 5173), production static file serving from FastAPI
- **APIs**: ~50 REST endpoints across butler discovery, sessions, schedules, state, traces, timeline, search, costs, notifications, and domain-specific views
