# RFC 0007: Dashboard and API Surface

**Status:** Accepted
**Date:** 2026-03-24

## Summary

The Butlers dashboard is a single-pane-of-glass interface built with a FastAPI backend (port 41200) and Vite-powered frontend (port 41173 in development). Butler-specific API routes are auto-discovered from `roster/<butler>/api/router.py` files via importlib. The REST API follows a consistent envelope pattern (`ApiResponse<T>`, `PaginatedResponse<T>`) with domain-specific exceptions. The frontend provides 30+ routes organized around system core views, butler detail tabs, and domain-specific surfaces (relationship, health, memory, approvals, connectors, QA). Recovery and QA surfaces distinguish admission-control decisions from launched investigations, expose workflow phase/deadline summaries, and surface structured evidence without conflating it with session lists. Data access uses TanStack Query with domain-specific polling intervals. A global command palette provides keyboard-driven navigation.

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
  meta: Record<string, unknown>;
}

// Paginated list
interface PaginatedResponse<T> {
  data: T[];
  meta: {
    total: number;
    offset: number;
    limit: number;
    has_more: boolean;
  };
}

// Error
interface ErrorResponse {
  error: {
    code: string;
    message: string;
    butler: string | null;
    details: Record<string, unknown> | null;
  };
}
```

`ApiResponse<T>` always includes an extensible `meta` object, empty when the
endpoint has no metadata. `PaginatedResponse<T>` carries pagination metadata
inside `meta`; list endpoints MUST NOT expose `total`, `offset`, `limit`, or
`has_more` as top-level response fields. Error responses carry `code`,
`message`, `butler`, and `details` inside the top-level `error` object; they
MUST NOT expose `code` or `message` as top-level fields.

Explicit exceptions (frontend contract):

- Timeline uses `TimelineResponse` (unwrapped).
- Relationship domain endpoints use unwrapped typed payloads.
- Trigger endpoint returns `TriggerResponse` (unwrapped).

Admission-control decisions that did not launch a runtime session (for example cooldown or circuit-breaker rejects) MUST be exposed as their own records via `GET /api/healing/dispatch-events`. The dashboard MUST NOT present them as failed investigation executions. `GET /api/healing/attempts` returns only rows where a workflow runtime session was actually launched; dispatch-event records are never mixed into that list.

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
| `GET /api/spend/summary` | `ApiResponse<SpendSummary>` | Spend aggregates (filter: period) |
| `GET /api/spend/daily` | `ApiResponse<DailySpend[]>` | Per-day spend |
| `GET /api/search` | `ApiResponse<SearchResults>` | Cross-domain search (groups: sessions, state, contacts) |
| `GET /api/qa/summary` | `ApiResponse<QaSummary>` | QA staffer status, patrol rollup, circuit breaker |
| `GET /api/qa/cases` | `PaginatedResponse<QaCaseSummary>` | QA case rail summaries for the dossier renderer (filters: sev, since, offset, limit) |
| `GET /api/qa/cases/{id}` | `ApiResponse<QaCaseDossier>` | Full QA case dossier with notes, PR summary, and recent journal |
| `GET /api/qa/cases/{id}/journal` | `PaginatedResponse<QaJournalEvent>` | Chronological QA case journal stream (filters: cursor, limit) |
| `GET /api/qa/investigations` | `PaginatedResponse<QaInvestigation>` | QA-originated investigations with phase/deadline/evidence summary |
| `GET /api/qa/meta-review` | `PaginatedResponse<QaMetaReviewFinding>` | QA-self-recursive findings routed to operator lane; never auto-investigated |
| `GET /api/healing/dispatch-events` | `PaginatedResponse<DispatchDecision>` | Admission-control decisions that did not launch a workflow (distinct from failed executions) |

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

**QA:** Patrol summaries, finding history, investigation workflows with phase/deadline/evidence summary, circuit breaker controls, repository settings, and a meta-review lane for QA-self-recursive failures (findings where `source_butler == "qa"` and the originating session's `trigger_source` identifies a QA-owned investigation — these are surfaced at `GET /api/qa/meta-review` and never auto-investigated).

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
| `/qa` | QA overview (status, patrols, known issues, investigations, circuit breaker) |
| `/qa/patrols/:patrolId` | QA patrol detail |
| `/qa/investigations/:attemptId` | QA investigation detail |
| `/settings` | Local UI preferences |
| `/system` | System ownership page (see Amendment 1) |

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

---

## Amendment 1: /system Namespace

**Date:** 2026-05-03
**Status:** Accepted
**Implementing change:** `openspec/changes/system-page-capability/` (bu-ngfzz.*)

Vertical E shipped the `/system` dashboard route and the `/api/system/*` API namespace. This amendment registers both in RFC 0007.

### Frontend Route

Add to the Frontend Route Map under the Telemetry section:

| Route | Surface |
|-------|---------|
| `/system` | System ownership page (instance version, uptime, database size, backup recency, data-egress catalog, per-butler heartbeats) |

This route is registered in `frontend/src/router.tsx` alongside the Telemetry routes (`/traces`, `/timeline`) and appears in `frontend/src/components/layout/nav-config.ts` under the Telemetry nav section with no butler-presence filter (it is always visible). The page uses the `<Page archetype="overview">` shell.

### API Surface

Five ownership-fact endpoints are registered under `/api/system/`. Each endpoint uses the standard `ApiResponse<T>` envelope and is independently queryable so the frontend can load domains with different stale-time and retry policies.

| Endpoint | Response model | Description |
|----------|----------------|-------------|
| `GET /api/system/instance` | `ApiResponse<InstanceFacts>` | Software version (`version`), process uptime (`uptime_seconds`), and daemon start time (`started_at`). Version is read from `importlib.metadata`; on `PackageNotFoundError` falls back to `butlers.__version__`, then to `"unknown"` if that import also fails. |
| `GET /api/system/database` | `ApiResponse<DatabaseFacts>` | Total database size in bytes (`total_size_bytes`), per-butler-schema size breakdown (`schemas: SchemaSize[]`), and the ten largest tables (`largest_tables: TableSize[]`). `growth_rate_bytes_per_day` is always `null` in v1 (deferred to v2). Derived from PostgreSQL catalog queries (`pg_database_size`, `pg_total_relation_size`, `information_schema.tables`). Returns HTTP 503 if the catalog query fails. |
| `GET /api/system/backups` | `ApiResponse<BackupFacts>` | Backup recency (`last_backup_at`, `last_backup_size_bytes`), source reachability (`backup_source_reachable: bool`), and recent backup history (`backup_history: BackupEvent[]`). Degrades gracefully: always returns HTTP 200 with `backup_source_reachable: false` and null fields when no backup strategy is configured. |
| `GET /api/system/egress` | `ApiResponse<EgressCatalog>` | External-actor egress catalog: which external endpoints have received data from this instance, with `last_seen_at` and `total_calls` per actor (`actors: EgressActor[]`). `catalog_covers_from` communicates the oldest audit record used to build the catalog. **Owner-only**: returns HTTP 403 when the owner contact cannot be asserted. |
| `GET /api/system/butlers/heartbeat` | `ApiResponse<HeartbeatFacts>` | Per-butler liveness snapshot from the switchboard registry (`butlers: ButlerHeartbeat[]`). Each entry carries `last_heartbeat_at`, `heartbeat_age_seconds`, `last_session_at`, and `active_session_count`. Reads from the registry; does not issue live MCP calls. Degrades gracefully per butler when a schema is unreachable (`error: "schema_unreachable"`). |

Full Pydantic response models (`InstanceFacts`, `DatabaseFacts`, `SchemaSize`, `TableSize`, `BackupFacts`, `BackupEvent`, `EgressCatalog`, `EgressActor`, `HeartbeatFacts`, `ButlerHeartbeat`) are defined in `src/butlers/api/routers/system.py`. The router is registered in `src/butlers/api/app.py` (explicit include; the system router lives in the core `src/butlers/api/routers/` package rather than in a butler-specific `roster/*/api/` directory and is therefore not subject to butler auto-discovery).

For the complete data-model and privacy-contract specification, see `openspec/changes/system-page-capability/design.md`.

### Egress Catalog Data Source

The egress catalog is a read-only aggregation of `switchboard.dashboard_audit_log` — the same table that powers the audit-log dashboard view. No new write path or table is introduced.

**Actor enumeration.** The server-side actor registry maps `operation` strings to stable `actor_id` values and human-readable `display_name` values:

| `operation` (audit log) | `actor_id` | `display_name` |
|-------------------------|-----------|----------------|
| `llm_api_call` | `anthropic.claude` | Anthropic Claude API |
| `telegram_send` | `telegram.api` | Telegram Bot API |
| `google_calendar_write` | `google.calendar` | Google Calendar API |
| `gmail_send` | `google.gmail` | Gmail API |

Operations not in the registry are bucketed under `other / Other / Unrecognized`.

**What counts as an "external actor."** An external actor is any third-party service that receives a payload from this instance: LLM providers, messaging APIs, Google APIs, and any future outbound connector. Each outbound call site MUST emit a `dashboard_audit_log` row with the appropriate `operation` string so the catalog captures it. See `AGENTS.md` (§ "Egress audit operation naming convention") for the per-operation `request_summary` JSONB contract and the `write_audit_entry` / `emit_dashboard_audit` call sites.

**Privacy contract (v1).** The egress catalog is owner-only. The owner is identified by joining `public.contacts → public.entities` and asserting `'owner' = ANY(e.roles)` (`public.contacts.roles` was dropped in migration `core_016`; roles live exclusively on `public.entities.roles`). Non-owner callers receive HTTP 403. The endpoint never returns a partial view of egress data to non-owner contacts; multi-viewer access (if added in a future spec) MUST gate on an explicit owner-only capability flag rather than treating any authenticated dashboard session as sufficient.

The `catalog_covers_from` field on `EgressCatalog` makes the audit window explicit. The UI SHOULD display a footnote: "This catalog reflects data captured by the audit log. Coverage may be incomplete for some external services."

### OTel Egress Span

Every successful read of `GET /api/system/egress` MUST emit an OTel span named **`system.egress.read`** with the attribute **`actor_count`** set to the number of distinct actors returned in the response. This span is required for privacy auditing and was implemented in bu-4tluf (PR #1378).

Conformance with RFC 0005 (observability and telemetry):

- The span is created via the OpenTelemetry Python SDK tracer (`opentelemetry.trace.get_tracer`).
- `actor_count` MUST be a low-cardinality integer attribute (count of distinct `actor_id` values, not raw audit rows).
- The span MUST NOT carry high-cardinality identifiers (`request_id`, session IDs, actor names) as span attributes — these belong in logs or structured evidence, not metric labels (RFC 0005 § "Cardinality Discipline").
- The span wraps the egress query and actor-aggregation block; it is not emitted for 403 (owner-gate failure) or 503 (DB error) responses, where the endpoint returns before reaching the aggregation step.
