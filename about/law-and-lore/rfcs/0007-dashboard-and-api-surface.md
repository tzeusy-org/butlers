# RFC 0007: Dashboard and API Surface

**Status:** Accepted
**Date:** 2026-03-24

## Summary

The Butlers dashboard is a single-pane-of-glass interface built with a FastAPI backend (port 41200) and Vite-powered frontend (port 41173 in development). Butler-specific API routes are auto-discovered from `roster/<butler>/api/router.py` files via importlib. The REST API follows a consistent envelope pattern (`ApiResponse<T>`, `PaginatedResponse<T>`) with domain-specific exceptions. The frontend provides 30+ routes organized around system core views, butler detail tabs, and domain-specific surfaces (relationship, health, memory, approvals, connectors). Data access uses TanStack Query with domain-specific polling intervals. A global command palette provides keyboard-driven navigation.

## Motivation

A personal AI agent framework with multiple specialist butlers, connectors, scheduled tasks, and approval workflows requires a unified operational surface. Without it, operators must SSH into servers and query databases directly to understand system state. The dashboard consolidates butler health, session history, routing logs, contact management, memory inspection, and approval workflows into a single browser-based interface. Auto-discovery of butler-specific routes allows new butlers to add dashboard capabilities without modifying core API code.

## Design

### Architecture

**Backend:** FastAPI application served by uvicorn on port 41200. Provides REST endpoints under `/api`. Static frontend assets are served in production mode.

**Frontend:** Vite + React application. Development server on port 41173 proxies API requests to the backend. Production build is served as static files by the FastAPI backend.

**Data access:** Frontend talks to backend exclusively via REST (`/api` prefix). All requests are JSON-typed. Non-2xx responses throw `ApiError` with `code`, `message`, and `status` fields.

### Auto-Discovered Butler Routes

Butler-specific API routes are auto-discovered by `src/butlers/api/router_discovery.py`:

1. Scan `roster/*/api/router.py` for Python files.
2. Load each module via `importlib.util.spec_from_file_location`.
3. Validate that the module exports a `router` variable of type `APIRouter`.
4. Register discovered routers with the FastAPI application, prefixed with `/api/<butler_name>`.

Conventions:

- Each `router.py` MUST export a module-level `router` variable (APIRouter instance).
- No `__init__.py` needed in the `api/` directory.
- DB dependencies are auto-wired via `wire_db_dependencies()`.
- Pydantic models SHOULD be co-located in `models.py` alongside `router.py`.
- Butlers without `api/` directories are silently skipped.
- Invalid router exports (missing `router` variable, wrong type) are logged as warnings and skipped.

### Response Envelope

Standard envelope patterns:

```typescript
// Single resource or aggregate
interface ApiResponse<T> {
  data: T;
}

// Paginated list
interface PaginatedResponse<T> {
  data: T[];
  total: number;
  offset: number;
  limit: number;
}

// Error
interface ErrorResponse {
  code: string;
  message: string;
}
```

Explicit exceptions (frontend contract):

- Timeline uses `TimelineResponse` (unwrapped).
- Relationship domain endpoints use unwrapped typed payloads.
- Trigger endpoint returns `TriggerResponse` (unwrapped).

### Core System Endpoints

| Endpoint | Response | Description |
|----------|----------|-------------|
| `GET /api/health` | `{"status": "ok"}` | Health check |
| `GET /api/butlers` | `ApiResponse<ButlerSummary[]>` | All registered butlers |
| `GET /api/butlers/{name}` | `ApiResponse<ButlerDetail>` | Butler detail |
| `GET /api/butlers/{name}/config` | `ApiResponse<ButlerConfigResponse>` | Butler TOML config |
| `GET /api/butlers/{name}/skills` | `ApiResponse<SkillInfo[]>` | Available skills |
| `POST /api/butlers/{name}/trigger` | `TriggerResponse` | Spawn LLM session |
| `GET /api/butlers/{name}/mcp/tools` | `ApiResponse<MCPToolInfo[]>` | List MCP tools |
| `POST /api/butlers/{name}/mcp/call` | `ApiResponse<MCPToolCallResponse>` | Call MCP tool |

### Session Endpoints

| Endpoint | Response | Filters |
|----------|----------|---------|
| `GET /api/sessions` | `PaginatedResponse<SessionSummary>` | offset, limit, butler, trigger_source, status, since, until |
| `GET /api/sessions/{id}` | `ApiResponse<SessionDetail>` | -- |
| `GET /api/butlers/{name}/sessions` | `PaginatedResponse<SessionSummary>` | offset, limit, trigger_source, status, since, until |

### Operational Endpoints

| Endpoint | Response | Description |
|----------|----------|-------------|
| `GET /api/timeline` | `TimelineResponse` | Cross-butler event stream (filters: limit, butler, event_type, before cursor) |
| `GET /api/notifications` | `PaginatedResponse<NotificationSummary>` | Notification feed |
| `GET /api/notifications/stats` | `ApiResponse<NotificationStats>` | Delivery statistics |
| `GET /api/issues` | `ApiResponse<Issue[]>` | Grouped error issues |
| `GET /api/audit-log` | `PaginatedResponse<AuditEntry>` | Operation history |
| `GET /api/costs/summary` | `ApiResponse<CostSummary>` | Cost aggregates (filter: period) |
| `GET /api/costs/daily` | `ApiResponse<DailyCost[]>` | Per-day costs |
| `GET /api/search` | `ApiResponse<SearchResults>` | Cross-domain search (groups: sessions, state, contacts) |

### Butler Control Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/butlers/{name}/schedules` | GET, POST | List/create scheduled tasks |
| `/api/butlers/{name}/schedules/{id}` | PUT, DELETE | Update/delete scheduled task |
| `/api/butlers/{name}/schedules/{id}/toggle` | PATCH | Toggle enabled state |
| `/api/butlers/{name}/state` | GET | List state store entries |
| `/api/butlers/{name}/state/{key}` | PUT, DELETE | Set/delete state entry |

### Domain Endpoints

**Relationship:** Contacts, groups, labels, upcoming dates. Contact subresources: notes, interactions, gifts, loans, activity feed.

**Health:** Measurements, medications (with doses), conditions, symptoms, meals, research.

**Memory:** Stats, episodes, facts, rules, activity timeline.

**Approvals:** Pending/decided actions, approve/reject/expire operations, standing rules CRUD, rule suggestions, metrics.

**Connectors:** Connector list, detail, stats (with timeseries), cross-connector summary, fanout distribution matrix.

**General/Switchboard:** Collections, entities, routing log, butler registry.

**Calendar:** Workspace read (user/butler views), metadata, sync, user-event and butler-event mutations.

**OAuth:** Google OAuth start/callback, credential status surface.

### Frontend Route Map

| Route | Surface |
|-------|---------|
| `/` | Overview dashboard (topology, health, failed notifications, active issues) |
| `/butlers` | Butler status cards |
| `/butlers/:name` | Butler detail with tabbed interface |
| `/butlers/calendar` | Calendar workspace (dual-view) |
| `/sessions` | Cross-butler session list with filters |
| `/sessions/:id` | Session detail |
| `/traces` | Distributed trace index |
| `/traces/:traceId` | Trace detail with span waterfall |
| `/timeline` | Unified event stream |
| `/notifications` | Notification center |
| `/issues` | Active issues |
| `/audit-log` | Operation history |
| `/approvals` | Approval queue with decision workflows |
| `/approvals/rules` | Standing approval rules |
| `/contacts` | Contact list |
| `/contacts/:contactId` | Contact detail with tabs |
| `/groups` | Relationship groups |
| `/health/*` | Health domain (measurements, medications, conditions, symptoms, meals, research) |
| `/collections` | General collections |
| `/entities` | Entity browser |
| `/entities/:entityId` | Entity detail |
| `/connectors` | Connector overview with volume chart and fanout matrix |
| `/connectors/:type/:identity` | Connector detail with timeseries |
| `/costs` | Cost and usage analysis |
| `/memory` | Memory system (tier cards, browser, activity timeline) |
| `/settings` | Local UI preferences |

### Butler Detail Tabs

Always rendered:

- Overview, Sessions, Config, Skills, Schedules, Trigger, MCP, State, CRM, Memory

Conditionally rendered:

- `Health` -- only when butler name is `"health"`
- `Collections`, `Entities` -- only when butler name is `"general"`
- `Routing Log`, `Registry` -- only when butler name is `"switchboard"`

Active tab is controlled by `?tab=` query parameter. `overview` is the default.

### Data Access and Refresh

Frontend uses TanStack Query with default `staleTime: 30s` and `retry: 1`.

Domain-specific refetch intervals:

| Interval | Domains |
|----------|---------|
| 15s | Memory activity |
| 30s | Butlers, sessions, traces, timeline, audit log, issues, connectors list, general entities/collections, health datasets, memory stats/facts/rules, butler schedules/state |
| 60s | Cost summary, daily costs, connector stats/fanout |
| None (manual) | Notifications, contacts/groups, butler config/skills, session/trace detail |

User-controlled live refresh on sessions and timeline pages supports interval selection (5s, 10s, 30s, 60s) and pause/resume.

### Command Palette

Global command palette activated by `/` or `Ctrl/Cmd+K`. Provides keyboard-driven navigation to any route in the application. Search results are navigation-ready with `id`, `butler`, `type`, `title`, `snippet`, and `url` fields.

### Global Shell

All routes render inside a common shell with:

- Responsive sidebar navigation (desktop collapsible, mobile drawer)
- Header with breadcrumb trail and theme toggle
- Keyboard shortcut help dialog (`?` floating button)
- Error boundary around route content
- Toast notifications for mutation feedback

## Integration

- **RFC 0001:** The dashboard backend reads session data from butler schemas to provide the sessions and timeline views.
- **RFC 0002:** MCP debug tab allows listing and calling tools on any butler's MCP server.
- **RFC 0003:** Switchboard-specific tabs expose routing log and butler registry. Connector views show ingestion statistics.
- **RFC 0004:** Contact management views operate on `public` schema identity tables.
- **RFC 0005:** Traces page provides distributed trace index and span waterfall visualization.
- **RFC 0006:** Dashboard uses a privileged database connection that can read all butler schemas for cross-butler views.

## Alternatives Considered

**GraphQL instead of REST.** Rejected for simplicity. The dashboard's data access patterns are well-served by typed REST endpoints with consistent envelope patterns. GraphQL would add schema management complexity without commensurate benefit for the current query patterns.

**Centralized butler route registration.** Rejected in favor of auto-discovery. Requiring new butlers to modify a central registration file creates merge conflicts and coupling. Auto-discovery from `roster/<butler>/api/router.py` allows butlers to add dashboard capabilities independently.

**WebSocket for real-time updates.** Not implemented for most surfaces. Polling at 15-30s intervals is sufficient for the current update frequency. The SSE endpoint exists for targeted real-time use cases but is not the primary data access pattern. WebSocket could be added for the approvals queue if sub-second notification latency becomes important.
