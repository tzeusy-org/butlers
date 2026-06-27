# Dashboard Data Layer and API

## Purpose
Defines the complete data access layer connecting the Butlers dashboard frontend to backend infrastructure. This covers the FastAPI application factory, REST endpoint inventory across all domains, cross-butler database fan-out, MCP client proxy, butler-specific route auto-discovery, TanStack Query refresh patterns, SSE real-time streaming, OAuth bootstrap flow, generic secrets management, response envelope standards, and the pricing/cost estimation model. Together these form the single-pane-of-glass contract between the React frontend and the Python backend.
## Requirements
### Requirement: FastAPI Application Factory
The `create_app()` function in `src/butlers/api/app.py` builds the FastAPI application with CORS middleware, lifespan handler, error handlers, static file serving, and router registration. The lifespan handler initializes `MCPClientManager`, `PricingConfig`, and `DatabaseManager` singletons on startup and tears them down on shutdown.

#### Scenario: Application startup
- **WHEN** the FastAPI lifespan starts
- **THEN** `init_dependencies()` discovers all butlers from the roster directory and registers them in the `MCPClientManager` singleton
- **AND** `init_pricing()` loads `pricing.toml` into the `PricingConfig` singleton
- **AND** `init_db_manager()` creates asyncpg pools for each discovered butler plus the shared credential pool
- **AND** `wire_db_dependencies()` overrides the `_get_db_manager` stub in every static and dynamic router module

#### Scenario: CORS configuration
- **WHEN** `create_app(cors_origins=None)` is called
- **THEN** the default CORS origin `http://localhost:41173` (Vite dev server) is used
- **AND** all methods and headers are allowed with credentials enabled

#### Scenario: Router registration order
- **WHEN** routers are registered
- **THEN** core static routers (approvals, butlers, notifications, sessions, schedules, etc.) are mounted first
- **AND** auto-discovered butler routers from `roster/*/api/router.py` are mounted after, so dynamic routes cannot shadow fixed API paths like `/api/oauth/*`

#### Scenario: Static file serving (production)
- **WHEN** `static_dir` or `DASHBOARD_STATIC_DIR` is set and the directory exists
- **THEN** a `StaticFiles(html=True)` handler is mounted at `/` for SPA fallback
- **AND** it is mounted after all API routes so `/api/*` always takes precedence

### Requirement: Standard Response Envelopes
All API responses use consistent wrapper types. Backend Pydantic models and frontend TypeScript interfaces are kept in sync.

#### Scenario: ApiResponse wrapper
- **WHEN** a single-resource or aggregate endpoint succeeds
- **THEN** the response body is `{ "data": T, "meta": {} }` where `meta` is an extensible object

#### Scenario: PaginatedResponse wrapper
- **WHEN** a list endpoint with offset/limit pagination succeeds
- **THEN** the response body is `{ "data": T[], "meta": { "total": number, "offset": number, "limit": number, "has_more": boolean } }`
- **AND** `has_more` is a computed field: `offset + limit < total`

#### Scenario: ErrorResponse envelope
- **WHEN** any request fails
- **THEN** the response body is `{ "error": { "code": string, "message": string, "butler": string | null, "details": object | null } }`
- **AND** the HTTP status code reflects the error type (400, 404, 500, 502, 503)

#### Scenario: Unwrapped response exceptions
- **WHEN** certain domain endpoints return data
- **THEN** the following endpoints use unwrapped typed payloads instead of the standard `ApiResponse<T>` wrapper:
  - Timeline: `GET /api/timeline` returns `TimelineResponse` (unwrapped)
  - Relationship domain: `GET /api/relationship/contacts/{contactId}` returns `ContactDetail` (unwrapped), and other relationship sub-resource endpoints return unwrapped arrays
  - Trigger: `POST /api/butlers/{name}/trigger` returns `TriggerResponse` (unwrapped)
- **AND** the frontend type layer accounts for these exceptions with dedicated response interfaces

#### Scenario: TypeScript mirror types
- **WHEN** the frontend imports from `frontend/src/api/types.ts`
- **THEN** `ApiResponse<T>`, `PaginatedResponse<T>`, `ErrorResponse`, `ErrorDetail`, and `PaginationMeta` are available as generic interfaces matching the backend Pydantic shapes

### Requirement: Error Handling Middleware
`src/butlers/api/middleware.py` registers exception handlers that convert domain exceptions into the standard error envelope.

#### Scenario: Butler unreachable
- **WHEN** a `ButlerUnreachableError` is raised during request handling
- **THEN** a 502 response with `code: "BUTLER_UNREACHABLE"` and `butler: "<name>"` is returned

#### Scenario: Butler not found
- **WHEN** a `ButlerNotFoundError` is raised (unknown butler lookup)
- **THEN** a 404 response with `code: "BUTLER_NOT_FOUND"` and `butler: "<name>"` is returned
- **NOTE** Raw `KeyError` (e.g. from dict-access bugs) is **not** caught by this handler — it propagates to the catch-all and produces a 500 INTERNAL_ERROR response

#### Scenario: Validation error
- **WHEN** a `ValueError` is raised
- **THEN** a 400 response with `code: "VALIDATION_ERROR"` is returned

#### Scenario: Catch-all unhandled exception
- **WHEN** an unhandled exception bypasses all specific handlers
- **THEN** the `CatchAllErrorMiddleware` returns a 500 response with `code: "INTERNAL_ERROR"` and `message: "Internal server error"`
- **AND** the original exception is logged with full traceback

### Requirement: API Client (Frontend)
`frontend/src/api/client.ts` provides a typed `apiFetch<T>()` wrapper over native `fetch` that prepends the base URL, sets JSON headers, and converts non-2xx responses to `ApiError`.

#### Scenario: Base URL resolution
- **WHEN** `apiFetch` is called
- **THEN** the request URL is `${VITE_API_URL ?? "/api"}${path}`

#### Scenario: Error parsing
- **WHEN** the server returns a non-2xx response
- **THEN** the response body is parsed as `ErrorResponse` and an `ApiError` is thrown with `code`, `message`, and `status` properties
- **AND** if the body is not valid JSON, generic defaults are used

#### Scenario: Per-endpoint typed functions
- **WHEN** the frontend calls an endpoint function (e.g., `getButlers()`, `getSessions()`)
- **THEN** the return type is fully generic-typed (e.g., `Promise<ApiResponse<ButlerSummary[]>>`, `Promise<PaginatedResponse<SessionSummary>>`)

### Requirement: DatabaseManager (Per-Butler Pool Management)
`src/butlers/api/db.py` maintains one asyncpg connection pool per butler and a dedicated shared credential pool. Supports both legacy multi-DB and one-DB/multi-schema topologies.

#### Scenario: Add butler pool
- **WHEN** `add_butler(name, db_name, db_schema)` is called
- **THEN** an asyncpg pool is created with `search_path` set to `"{schema}", public` when a schema is specified
- **AND** the pool is cached by butler name for subsequent lookups

#### Scenario: Shared credential pool
- **WHEN** `set_credential_shared_pool(db_name, db_schema)` is called
- **THEN** a separate pool is created for the shared credential database
- **AND** `credential_shared_pool()` returns it (or raises `KeyError` if not configured)

#### Scenario: Pool lookup
- **WHEN** `pool(butler_name)` is called
- **THEN** the cached asyncpg pool for that butler is returned
- **AND** `KeyError` is raised if the butler has not been added

#### Scenario: Schema-scoped search path
- **WHEN** a butler has `db_schema` configured (e.g., `"switchboard"`)
- **THEN** the pool's `search_path` server setting is `"{schema}", shared, public`
- **AND** all queries on that pool are scoped to the butler's schema by default

### Requirement: Cross-Butler Fan-Out
The `DatabaseManager.fan_out()` method executes a SQL query concurrently across multiple butler databases, enabling cross-butler aggregate endpoints.

#### Scenario: Fan-out across all butlers
- **WHEN** `fan_out(query, args)` is called without `butler_names`
- **THEN** the query is executed concurrently on every registered butler pool via `asyncio.gather`
- **AND** results are returned as `dict[str, list[Record]]` keyed by butler name

#### Scenario: Fan-out with butler filter
- **WHEN** `fan_out(query, args, butler_names=["atlas"])` is called
- **THEN** only the specified butler(s) are queried

#### Scenario: Partial failure resilience
- **WHEN** one butler's query fails during fan-out
- **THEN** that butler's entry is an empty list in the result
- **AND** the error is logged as a warning
- **AND** successful results from other butlers are still returned

#### Scenario: Sessions cross-butler merge
- **WHEN** `GET /api/sessions` is called
- **THEN** fan-out queries every butler DB for sessions
- **AND** results are merged, sorted by `started_at DESC`, and paginated with correct cross-butler total count

### Requirement: MCP Client Proxy
`src/butlers/api/deps.py` provides `MCPClientManager` for lazy FastMCP client connections to running butler MCP daemons. Write operations (state set/delete, schedule CRUD, triggers) are proxied through MCP to preserve the architectural constraint that only the butler mutates its own database.

#### Scenario: Lazy client connection
- **WHEN** `get_client(butler_name)` is called for the first time
- **THEN** a FastMCP SSE client is created, connected to `http://localhost:{port}/sse`, and cached
- **AND** subsequent calls return the cached client if still connected

#### Scenario: Client reconnection
- **WHEN** a cached client is found to be disconnected
- **THEN** the old client is closed and a new connection is established

#### Scenario: Butler unreachable
- **WHEN** a butler's MCP server is not running or connection fails
- **THEN** `ButlerUnreachableError` is raised with the butler name and cause

#### Scenario: Tool invocation proxy
- **WHEN** the dashboard calls `POST /api/butlers/{name}/mcp/call` with `{ "tool_name": "...", "arguments": {...} }`
- **THEN** the backend connects to the butler via MCP and calls `client.call_tool(tool_name, arguments)`
- **AND** the MCP result is parsed (JSON when possible, raw text as fallback) and returned in an `MCPToolCallResponse` envelope

#### Scenario: State write via MCP
- **WHEN** `PUT /api/butlers/{name}/state/{key}` is called
- **THEN** the backend calls the butler's MCP `state_set` tool with `{ "key": key, "value": value }`
- **AND** an audit log entry is recorded on success or failure

#### Scenario: Schedule CRUD via MCP
- **WHEN** schedule create/update/delete/toggle endpoints are called
- **THEN** the backend proxies to the butler's `schedule_create`, `schedule_update`, `schedule_delete`, or `schedule_toggle` MCP tools respectively
- **AND** each operation records an audit log entry

### Requirement: Butler-Specific Route Auto-Discovery
`src/butlers/api/router_discovery.py` scans `roster/{butler}/api/router.py` files and dynamically loads them via `importlib`. Butler-specific routers extend the API surface without modifying core router registration code.

#### Scenario: Discovery of butler routers
- **WHEN** `discover_butler_routers()` is called
- **THEN** it iterates sorted subdirectories under `roster/`, looking for `api/router.py` files
- **AND** each file is loaded via `importlib.util.spec_from_file_location` with module name `{butler_name}_api_router`

#### Scenario: Router validation
- **WHEN** a `router.py` module is loaded
- **THEN** it must export a module-level `router` variable that is an `APIRouter` instance
- **AND** modules without `router` or with non-APIRouter exports are logged as warnings and skipped

#### Scenario: DB dependency wiring
- **WHEN** `wire_db_dependencies()` is called with dynamic modules
- **THEN** each dynamic module with a `_get_db_manager` stub has it overridden with the `get_db_manager` singleton
- **AND** the override applies via FastAPI's `dependency_overrides` mechanism

### Requirement: Butler Discovery and Status Probing
`src/butlers/api/routers/butlers.py` provides butler list and detail endpoints that combine static config discovery with live MCP status probing.

#### Scenario: List butlers with live status
- **WHEN** `GET /api/butlers` is called
- **THEN** each discovered butler is probed in parallel via MCP `ping()` with a 5s timeout
- **AND** butlers that respond get `status: "ok"`; unreachable butlers get `status: "down"`

#### Scenario: Butler detail
- **WHEN** `GET /api/butlers/{name}` is called
- **THEN** the butler's config is loaded from `roster/{name}/butler.toml`, modules are enumerated, skills are discovered from `skills/` subdirectory, and live status is obtained via MCP

#### Scenario: Butler config endpoint
- **WHEN** `GET /api/butlers/{name}/config` is called
- **THEN** `butler.toml` is parsed as a dict and returned along with raw text of `CLAUDE.md`, `AGENTS.md`, and `MANIFESTO.md` (null if missing)

### Requirement: API Endpoint Inventory

The following is the complete endpoint inventory grouped by domain.

#### Core System
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Health check |
| GET | `/api/butlers` | List all butlers with live status |
| GET | `/api/butlers/{name}` | Butler detail |
| GET | `/api/butlers/{name}/config` | Butler configuration files |
| GET | `/api/butlers/{name}/skills` | Butler skills (name + SKILL.md content) |
| POST | `/api/butlers/{name}/trigger` | Trigger runtime session |
| POST | `/api/butlers/{name}/tick` | Force scheduler tick |
| GET | `/api/butlers/{name}/mcp/tools` | List MCP tools |
| POST | `/api/butlers/{name}/mcp/call` | Invoke MCP tool |
| GET | `/api/butlers/{name}/modules` | Module health status |
| GET | `/api/butlers/{name}/module-states` | Module runtime states |
| PUT | `/api/butlers/{name}/module-states/{module}/enabled` | Toggle module enabled state |

#### Sessions
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/sessions` | Cross-butler paginated session list (fan-out) |
| GET | `/api/butlers/{name}/sessions` | Butler-scoped paginated session list |
| GET | `/api/butlers/{name}/sessions/{id}` | Session detail |

#### Ingestion Events
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/ingestion/events` | Paginated ingestion event list (limit, offset, source_channel filter) |
| GET | `/api/ingestion/events/{requestId}` | Single ingestion event detail |
| GET | `/api/ingestion/events/{requestId}/sessions` | All sessions attributed to this request ID across all butlers |
| GET | `/api/ingestion/events/{requestId}/rollup` | Token/cost/butler topology rollup for this request ID |

#### Timeline
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/timeline` | Cross-butler unified event stream (cursor pagination) |

#### Notifications
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/notifications` | Cross-butler paginated notification list |
| GET | `/api/notifications/stats` | Aggregate notification statistics |
| GET | `/api/butlers/{name}/notifications` | Butler-scoped notification list |

#### State Store
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/butlers/{name}/state` | List all state entries |
| GET | `/api/butlers/{name}/state/{key}` | Get single state entry |
| PUT | `/api/butlers/{name}/state/{key}` | Set state value (MCP proxy) |
| DELETE | `/api/butlers/{name}/state/{key}` | Delete state entry (MCP proxy) |

#### Schedules
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/butlers/{name}/schedules` | List schedules |
| POST | `/api/butlers/{name}/schedules` | Create schedule (MCP proxy) |
| PUT | `/api/butlers/{name}/schedules/{id}` | Update schedule (MCP proxy) |
| DELETE | `/api/butlers/{name}/schedules/{id}` | Delete schedule (MCP proxy) |
| PATCH | `/api/butlers/{name}/schedules/{id}/toggle` | Toggle schedule enabled (MCP proxy) |

#### Schedule Execution Semantics
- **WHEN** the dashboard displays or interprets schedule data
- **THEN** `Schedule.source` describes the schedule origin (`toml` for TOML-defined, `db` for dashboard-created); it is NOT the execution mode
- **AND** runtime-mode schedules (those with a `prompt`) execute through `spawner.trigger(..., trigger_source="schedule:<task-name>")` and correlate with `sessions` rows
- **AND** native-mode schedules (those with `dispatch_mode = "job"` and `job_name`) execute deterministic Python jobs directly and may not create `sessions` rows
- **AND** the dashboard treats schedule status fields (`enabled`, `next_run_at`, `last_run_at`) as authoritative regardless of execution mode
- **AND** schedule failures for both execution modes surface through `GET /api/issues` as `scheduled_task_failure:<schedule-name>`

#### Spend
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/spend/summary` | Aggregate cost summary (MCP fan-out) |
| GET | `/api/spend/daily` | Daily cost time series (MCP fan-out) |
| GET | `/api/spend/top-sessions` | Most expensive sessions (MCP fan-out) |
| GET | `/api/spend/by-schedule` | Per-schedule cost analysis (MCP fan-out) |

#### Memory
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/memory/stats` | Aggregated memory tier counts (fan-out) |
| GET | `/api/memory/episodes` | Paginated episode list (fan-out) |
| GET | `/api/memory/facts` | Paginated fact list with text search (fan-out) |
| GET | `/api/memory/facts/{id}` | Single fact detail (fan-out) |
| GET | `/api/memory/rules` | Paginated rule list with text search (fan-out) |
| GET | `/api/memory/rules/{id}` | Single rule detail (fan-out) |
| GET | `/api/memory/activity` | Recent memory activity interleaved (fan-out) |

#### Approvals
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/approvals/actions` | Pending action queue |
| GET | `/api/approvals/actions/executed` | Executed actions audit |
| GET | `/api/approvals/actions/{id}` | Action detail |
| POST | `/api/approvals/actions/{id}/approve` | Approve action and dispatch for execution |
| POST | `/api/approvals/actions/{id}/reject` | Reject action with optional reason |
| POST | `/api/approvals/actions/expire-stale` | Expire stale actions |
| GET | `/api/approvals/rules` | Standing rule list |
| GET | `/api/approvals/rules/{id}` | Rule detail |
| POST | `/api/approvals/rules` | Create standing approval rule |
| POST | `/api/approvals/rules/from-action` | Create rule from a pending action |
| POST | `/api/approvals/rules/{id}/revoke` | Revoke (deactivate) rule |
| GET | `/api/approvals/rules/suggestions/{actionId}` | Constraint suggestions |
| GET | `/api/approvals/metrics` | Aggregate approval metrics |

#### Search
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/search` | Cross-butler ILIKE search (sessions + state) |

#### Audit Log
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/audit-log` | Paginated audit log from switchboard DB |

#### Issues
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/issues` | Aggregated issues (reachability + audit errors) |

#### Calendar Workspace
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/calendar/workspace` | Normalized calendar entries for time range |
| GET | `/api/calendar/workspace/meta` | Workspace metadata (sources, lanes, writables) |
| POST | `/api/calendar/workspace/sync` | Trigger provider/projection sync |
| POST | `/api/calendar/workspace/user-events` | Create/update/delete user-view events (MCP) |
| POST | `/api/calendar/workspace/butler-events` | Create/update/delete/toggle butler events (MCP) |
| GET | `/api/calendar/workspace/audit` | Calendar mutation audit trail (read-only) |
| POST | `/api/calendar/workspace/undo/{action_id}` | Reverse a previously-applied calendar mutation |

#### OAuth
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/oauth/{provider}/start` | Begin OAuth flow for any provider (generalised; `provider=google` unchanged) |
| GET | `/api/oauth/{provider}/callback` | Handle OAuth callback for any provider (generalised) |
| GET | `/api/oauth/google/start` | Begin Google OAuth flow (redirect or JSON) |
| GET | `/api/oauth/google/callback` | Handle Google OAuth callback |
| GET | `/api/oauth/status` | OAuth credential status probe |
| PUT | `/api/oauth/google/credentials` | Store Google app credentials |
| GET | `/api/oauth/google/credentials` | Masked credential status |
| DELETE | `/api/oauth/google/credentials` | Delete Google credentials |

#### Secrets
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/butlers/{name}/secrets` | List secrets (metadata only, values masked) |
| GET | `/api/butlers/{name}/secrets/{key}` | Single secret metadata |
| PUT | `/api/butlers/{name}/secrets/{key}` | Upsert secret (write-only value) |
| DELETE | `/api/butlers/{name}/secrets/{key}` | Delete secret |
| GET | `/api/secrets/inventory` | Passport inventory (cli/system/user; `?identity=`) |
| GET | `/api/secrets/user/{provider}` | User credential evidence (`?identity=`) |
| GET | `/api/secrets/system/{key}` | System secret evidence |
| GET | `/api/secrets/cli/{id}` | CLI runtime evidence |
| POST | `/api/secrets/user/{provider}/reauthorize` | Begin reauthorize OAuth dance (`?identity=`) |
| POST | `/api/secrets/user/{provider}/rotate` | Rotate user credential value (`?identity=`) |
| POST | `/api/secrets/user/{provider}/disconnect` | Disconnect user credential (`?identity=`) |
| POST | `/api/secrets/user/{provider}/probe` | Probe user credential (`?identity=`) |
| POST | `/api/secrets/system/{key}` | Set/rotate/override system secret |
| POST | `/api/secrets/system/{key}/probe` | Probe system secret |
| DELETE | `/api/secrets/system/{key}` | Remove system secret/override (`?target=`) |
| POST | `/api/secrets/cli/{id}/rotate` | Rotate CLI runtime (returns value once) |
| POST | `/api/secrets/cli/{id}/revoke` | Revoke CLI runtime |
| GET | `/api/secrets/audit/{scope}/{key}` | Per-credential audit history (`?limit=`) |
| GET | `/api/secrets/breaks-catalogue` | Provider feature/break catalogue (`?provider=`) |

#### SSE
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/events` | Server-Sent Events stream |

### Requirement: TanStack Query Patterns
The frontend uses TanStack Query (`@tanstack/react-query`) via `frontend/src/hooks/` for all data fetching, with domain-specific stale times, refetch intervals, and mutation invalidation patterns.

#### Scenario: Default query client configuration
- **WHEN** the TanStack QueryClient is initialized (`frontend/src/lib/query-client.ts`)
- **THEN** default `staleTime` is 30,000 ms (30s) and `retry` is 1

#### Scenario: Domain-specific refetch intervals
- **WHEN** hooks are used from the hooks directory
- **THEN** the following refetch intervals are applied:

| Domain | Interval | Hooks |
|--------|----------|-------|
| Butlers list | 30s | `useButlers` |
| Sessions (list) | 30s | `useSessions`, `useButlerSessions` |
| Schedules | 30s | `useSchedules` |
| State entries | 30s | `useButlerState` |
| Issues | 30s | `useIssues` |
| Audit log | 30s | `useAuditLog` |
| Ingestion events | 30s | `useIngestionEvents` |
| Timeline | 30s (default, overridable) | `useTimeline` |
| Health measurements | 30s | `useMeasurements`, `useMedications`, `useConditions`, `useSymptoms`, `useMeals`, `useResearch` |
| Memory stats/episodes/facts/rules | 30s | `useMemoryStats`, `useEpisodes`, `useFacts`, `useRules` |
| Switchboard routing/registry | 30s | `useRoutingLog`, `useRegistry` |
| Backfill jobs | 30s | `useBackfillJobs`, `useBackfillJob` |
| Connector detail | 30s | `useConnectorDetail` |
| Calendar workspace | 30s (default, overridable) | `useCalendarWorkspace` |
| Spend summary | 60s | `useSpendSummary` |
| Daily spend | 60s | `useDailySpend` |
| Top sessions | 60s | `useTopSessions` |
| Connectors list/summary | 60s | `useConnectorSummaries`, `useCrossConnectorSummary`, `useIngestionOverview`, `useConnectorStats` |
| Connector fanout | 120s | `useConnectorFanout` |
| Calendar workspace meta | 60s (default, overridable) | `useCalendarWorkspaceMeta` |
| Memory activity | 15s | `useMemoryActivity` |
| Backfill job progress (active) | 5s | `useBackfillJobProgress` (when status is pending/active) |
| Backfill job progress (idle) | 30s | `useBackfillJobProgress` (when status is completed/cancelled/paused) |
| Approval pending actions | 15s | `useApprovalActions` (when status filter is `pending`) |
| Approval rules / executed audit | 60s | `useApprovalRules`, `useExecutedActions` |
| No auto-interval | n/a | Notifications, contacts, groups, labels, butler config/skills, session detail, triage rules (use staleTime: 60s instead) |

#### Scenario: Mutation invalidation pattern
- **WHEN** a mutation hook succeeds (e.g., `useCreateSchedule`, `useSetState`, `useDeleteSecret`)
- **THEN** the related query cache is invalidated via `queryClient.invalidateQueries({ queryKey: [...] })`
- **AND** all mutations use `useMutation` with `onSuccess` callbacks for invalidation

#### Scenario: Approval query key factory
- **WHEN** approval hooks are used
- **THEN** a structured key factory (`approvalKeys`) provides hierarchical keys: `["approvals", "actions", params]`, `["approvals", "rules", params]`, etc.
- **AND** mutation success invalidates the parent `["approvals"]` prefix for broad cache busting

#### Scenario: Debounced search
- **WHEN** `useSearch(query)` is called
- **THEN** the query is debounced by 300ms and only fires when the query length is at least 2 characters

#### Scenario: Conditional query enablement
- **WHEN** a hook receives a nullable identifier (e.g., `useButler(name)`)
- **THEN** `enabled: !!identifier` prevents the query from executing until the identifier is available

#### Scenario: User-controlled auto-refresh
- **WHEN** the `useAutoRefresh` hook is used on Sessions or Timeline pages
- **THEN** users can select intervals (5s, 10s, 30s, 60s), pause/resume, and the setting persists in localStorage

### Requirement: SSE Real-Time Streaming
`src/butlers/api/routers/sse.py` provides a `GET /api/events` endpoint that streams Server-Sent Events to connected dashboard clients.

#### Scenario: Event stream connection
- **WHEN** a client connects to `GET /api/events`
- **THEN** a `StreamingResponse` with `media_type: "text/event-stream"` is returned
- **AND** an initial `event: connected` event is sent with `{"status": "ok"}`
- **AND** response headers include `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`

#### Scenario: Event broadcasting
- **WHEN** `broadcast(event_type, data)` is called from any part of the backend
- **THEN** all connected SSE subscribers receive the event via their asyncio.Queue
- **AND** subscribers whose queues are full are removed (dead subscriber cleanup)

#### Scenario: Event types
- **WHEN** the SSE stream is active
- **THEN** the following event types can be broadcast: `connected`, `butler_status`, `session_start`, `session_end`

#### Scenario: Keepalive
- **WHEN** no events are broadcast for 30 seconds
- **THEN** a `: keepalive` comment is sent to prevent connection timeout

#### Scenario: Client disconnection
- **WHEN** a client disconnects
- **THEN** the subscriber's queue is removed from the subscriber list and the generator exits cleanly

### Requirement: OAuth Bootstrap Flow
`src/butlers/api/routers/oauth.py` implements a two-leg Google OAuth 2.0 authorization-code flow with CSRF protection, DB-backed credential persistence, and structured status reporting.

#### Scenario: OAuth start
- **WHEN** `GET /api/oauth/google/start` is called
- **THEN** a CSRF state token is generated via `secrets.token_urlsafe(32)` and stored in an in-memory store with a 10-minute TTL
- **AND** app credentials (client_id, client_secret) are resolved from the shared credential DB
- **AND** the Google authorization URL is built with `access_type=offline`, `prompt=consent`, and full scope set (Gmail, Calendar, Contacts)
- **AND** if `redirect=true` (default), a 302 redirect is returned; if `redirect=false`, the URL is returned as JSON

#### Scenario: OAuth callback
- **WHEN** `GET /api/oauth/google/callback` receives `code` and `state` parameters
- **THEN** the CSRF state is validated and consumed (one-time use)
- **AND** the authorization code is exchanged for tokens via Google's token endpoint
- **AND** the refresh token is persisted to the shared credential DB via `store_google_credentials()`
- **AND** either a dashboard redirect (`302`) or a JSON success payload is returned

#### Scenario: OAuth status probe
- **WHEN** `GET /api/oauth/status` is called
- **THEN** the endpoint checks: (1) app credentials exist in DB, (2) refresh token exists, (3) token refresh probe to Google validates scope coverage
- **AND** returns `OAuthCredentialStatus` with `state` enum (`connected`, `not_configured`, `expired`, `missing_scope`, `redirect_uri_mismatch`, `unapproved_tester`, `unknown_error`) and actionable `remediation` text
- **AND** the endpoint always returns HTTP 200 (errors encoded in the payload for safe polling)

#### Scenario: Credential management
- **WHEN** `PUT /api/oauth/google/credentials` is called with `{ "client_id", "client_secret" }`
- **THEN** app credentials are stored in the shared credential DB, preserving any existing refresh token
- **WHEN** `GET /api/oauth/google/credentials` is called
- **THEN** masked presence indicators are returned (boolean flags, never raw secret values)
- **WHEN** `DELETE /api/oauth/google/credentials` is called
- **THEN** all stored Google credential keys are deleted

### Requirement: Generic Secrets Management
`src/butlers/api/routers/secrets.py` SHALL provide CRUD for the `butler_secrets` table. Secret values MUST be write-only and never returned in API responses.

#### Scenario: List secrets
- **WHEN** `GET /api/butlers/{name}/secrets` is called
- **THEN** metadata-only `SecretEntry` records are returned (key, category, description, is_sensitive, is_set, timestamps)
- **AND** raw secret values are never included in the response

#### Scenario: Shared secrets target
- **WHEN** `{name}` is `"shared"` (case-insensitive)
- **THEN** the dedicated shared credential pool is used instead of a butler-specific pool

#### Scenario: Upsert with partial update preservation
- **WHEN** `PUT /api/butlers/{name}/secrets/{key}` is called with some optional fields omitted
- **THEN** existing values for category, description, is_sensitive are preserved from the current record
- **AND** new secrets use defaults (category: "general", is_sensitive: true)

#### Scenario: Frontend secret resolution with shared fallback
- **WHEN** the frontend `useSecrets(butlerName)` hook fetches secrets for a non-shared butler
- **THEN** both the butler-specific secrets and shared secrets are fetched
- **AND** shared secrets that are not overridden by local secrets are included with `source: "shared"`

#### Scenario: Legacy CRUD unchanged by passport surface
- **WHEN** `GET /api/butlers/{name}/secrets` (or any `/api/butlers/{name}/secrets/*` CRUD endpoint) is called
- **THEN** the response shape and behaviour are unchanged for direct programmatic access to `butler_secrets`
- **AND** the endpoint continues to return metadata only (no raw values)
- **AND** the new `/api/secrets/*` namespace (the surface for the redesigned `/secrets` page) is additive; existing consumers of `/api/butlers/{name}/secrets/*` are not broken

### Requirement: Secrets Inventory and Per-Credential Read Endpoints
The dashboard API SHALL expose a `/api/secrets/*` namespace that backs the passport-book `/secrets` page. All endpoints conform to the `ApiResponse<T>` envelope contract (RFC 0007 §Response Envelope); list/aggregate endpoints embed nested arrays inside `data`, never as top-level fields.

#### Scenario: Inventory endpoint shape
- **WHEN** `GET /api/secrets/inventory?identity=<uuid>` is called
- **THEN** the response is `ApiResponse<{ cli: CliRuntime[], system: SystemSecret[], user: UserSecret[] }>` with `meta` containing severity counts and the `needs_hand_count` field
- **AND** the `?identity=` query parameter filters the `user` array to credentials associated with the specified entity (projection-lens semantics; see `butler-secrets`)
- **AND** when `?identity=` is omitted, the owner identity is used as the default
- **AND** every credential row includes `state`, `fingerprint` (sha256 first-8 hex, computed on-read, never persisted), and per-family identity (`provider` / `key` / `id`)
- **AND** the response does NOT include any raw secret values

#### Scenario: Per-credential read endpoints
- **WHEN** `GET /api/secrets/user/<provider>?identity=<uuid>` is called
- **THEN** the response is `ApiResponse<UserSecret>` with the full evidence payload: `state`, `fingerprint`, `issued`, `expires`, `last_verified`, `last_used`, `scopes_required`, `scopes_granted`, `feeds`, `failure_tail`, `breaks[]`, `test` (most recent `TestResult`), `audit[]` (last 10), and `webhook` (when `kind=webhook`)
- **AND** `GET /api/secrets/system/<key>` returns `ApiResponse<SystemSecret>` with `key`, `category`, `row_state` (one of `shared` / `local` / `missing`), `fingerprint`, `description`, `source`, `target`, `last_verified`, `used_by[]`, `breaks[]`, `test`, `audit[]`
- **AND** `GET /api/secrets/cli/<id>` returns `ApiResponse<CliRuntime>` with `id`, `label`, `fingerprint`, `state`, `issued`, `expires`, `last_used`, `scopes_required`, `scopes_granted`, `test`
- **AND** none of these endpoints return raw secret values; values are returned only by explicit mutation endpoints in the specific cases defined below

#### Scenario: Probe-log LRU integration
- **WHEN** any per-credential read endpoint computes the `test` field
- **THEN** the field is sourced from the most recent row in `public.secret_probe_log` matching `(credential_scope, credential_key)` ordered by `recorded_at DESC`
- **AND** the `at` field is server-formatted to a human-friendly relative timestamp (e.g. `"14:21 today"`, `"yesterday 09:08"`) before serialization
- **AND** when no probe has ever been recorded for the credential, `test` is `null`

### Requirement: Secrets Mutation Endpoints
The `/api/secrets/*` namespace SHALL expose mutation endpoints for every action the passport page can dispatch. Every mutation SHALL write to `public.audit_log` (see `core-credentials` Audit Action Enum requirement) with an appropriate action value.

#### Scenario: User credential mutations
- **WHEN** `POST /api/secrets/user/<provider>/reauthorize?identity=<uuid>` is called
- **THEN** the response is `ApiResponse<{ redirect_url: str }>` and the redirect URL begins the OAuth dance with `page_of_origin=secrets` carried in the state token
- **AND** `POST /api/secrets/user/<provider>/rotate?identity=<uuid>` with body `{ value }` returns `ApiResponse<UserSecret>` (updated) and writes an audit row with action `rotated`
- **AND** `POST /api/secrets/user/<provider>/disconnect?identity=<uuid>` returns `ApiResponse<{ status: "disconnected" }>` and writes an audit row with action `disconnected`
- **AND** `POST /api/secrets/user/<provider>/probe?identity=<uuid>` returns `ApiResponse<TestResult>`, writes one row to `public.secret_probe_log`, and writes one audit row with action `verified` (on ok) or `failed` (on fail)

#### Scenario: System credential mutations
- **WHEN** `POST /api/secrets/system/<key>` is called with body `{ value, target: "shared" | "<butler>" }`
- **THEN** the response is `ApiResponse<SystemSecret>` (updated)
- **AND** when `target = "shared"` the value is written to the switchboard's `butler_secrets` table; when `target = "<butler>"` an override row is created in that butler's `butler_secrets` table
- **AND** an audit row is written with action `set` (first-time create), `rotated` (existing key), or `overrode` (new override)
- **AND** `POST /api/secrets/system/<key>/probe` returns `ApiResponse<TestResult>` and writes to probe-log + audit as in the User probe
- **AND** `DELETE /api/secrets/system/<key>?target=<butler|shared>` removes the row and writes an audit row with action `disconnected` (or `revoked` for override removal)

#### Scenario: CLI runtime mutations
- **WHEN** `POST /api/secrets/cli/<id>/rotate` is called
- **THEN** the response is `ApiResponse<{ fingerprint: str, value: str }>` and the raw value is returned **once** in the response body (so the owner can copy it to their local config)
- **AND** an audit row is written with action `rotated`
- **AND** `POST /api/secrets/cli/<id>/revoke` returns `ApiResponse<{ status: "revoked" }>` and writes an audit row with action `disconnected`

#### Scenario: Mutation endpoints ignore `?identity=` for authorization
- **WHEN** any `/api/secrets/*` mutation is called with `?identity=<member-id>`
- **THEN** the endpoint validates that the credential exists for the given identity and mutates it
- **AND** the endpoint does NOT enforce that the caller has permission to act on the member's credential (v1 single-owner; projection-lens semantics)

### Requirement: Secrets Audit-History and Breaks-Catalogue Endpoints
The `/api/secrets/*` namespace SHALL expose two read-side endpoints supporting the StampRow audit display and the WhatBreaks affordance.

#### Scenario: Audit history endpoint
- **WHEN** `GET /api/secrets/audit/<scope>/<key>?limit=50` is called (where `scope ∈ {user, system, cli}`)
- **THEN** the response is `ApiResponse<AuditEvent[]>` with the most recent audit rows filtered to the credential
- **AND** each `AuditEvent` includes `ts` (server pre-formatted relative timestamp), `actor`, `action`, `note` (serif-italic; verbatim stored note, never LLM-generated)
- **AND** the default `limit` is 10; max is 50
- **AND** the response includes a `meta.deep_link` field pointing to `/audit-log?key=<canonical-key>` for the full reel

#### Scenario: Breaks-catalogue endpoint
- **WHEN** `GET /api/secrets/breaks-catalogue?provider=<p>` is called
- **THEN** the response is `ApiResponse<BreakEntry[]>` reading from `public.provider_feature_catalogue`
- **AND** each `BreakEntry` includes `butler`, `feature`, `severity` (one of `high` / `medium` / `low`), `required_scopes` (jsonb array)
- **AND** when `?provider=` is omitted, the endpoint returns the full catalogue keyed by provider in `meta.by_provider`

### Requirement: OAuth Per-Provider Generalisation
The existing `/api/oauth/*` namespace (currently Google-only per `src/butlers/api/routers/oauth.py:156-1893`) SHALL be generalised to accept a `<provider>` path segment. Provider scope-sets SHALL be resolved from each butler's `butler.toml` declaration. The `/api/oauth/google/*` endpoints SHALL continue to function unchanged (path generalisation is additive; existing routes resolve via `provider=google`).

#### Scenario: Generalised begin endpoint
- **WHEN** `GET /api/oauth/<provider>/start?redirect_uri=<uri>&account_hint=<hint>&force_consent=<bool>&page_of_origin=<page>` is called
- **THEN** the response is `ApiResponse<{ authorization_url: str }>`
- **AND** the `state` token carries the `page_of_origin` value so the callback can route the user back appropriately
- **AND** for `provider=google`, the response is identical to the pre-change behaviour of `/api/oauth/google/start`

#### Scenario: Generalised callback endpoint
- **WHEN** `GET /api/oauth/<provider>/callback?code=<code>&state=<state>` is invoked
- **THEN** the callback exchanges the code for tokens, persists them to the correct authoritative store (`butler_secrets` for system, `public.entity_info` for per-account user credentials per `about/heart-and-soul/security.md:107-127`), writes a `connected` audit row, and redirects the browser based on `state.page_of_origin`:
  - `secrets` → `/secrets?focus=u:<provider>&toast=connected`
  - `ingestion` → `/ingestion/connectors`
  - (default / missing) → `/secrets?focus=u:<provider>&toast=connected`

#### Scenario: Provider scope resolution from butler.toml
- **WHEN** the OAuth begin endpoint is called for a provider whose scopes are declared in one or more `butler.toml` files
- **THEN** the resolved scope-set is the union of all scopes declared by butlers that consume the provider
- **AND** the resolved scope-set is the value passed to the OAuth authorization URL

### Requirement: Response Envelope Conformance for Secrets and OAuth Namespaces
All endpoints under the new `/api/secrets/*` namespace and the generalised `/api/oauth/*` namespace SHALL conform to the `ApiResponse<T>` envelope contract defined in RFC 0007 §Response Envelope (and codified in the existing `dashboard-api §Standard Response Envelopes` requirement). Endpoints MUST NOT expose top-level data fields outside the `data` / `meta` / `error` envelope shape. The `/api/audit-log` endpoint extension (key-filter) uses the existing `PaginatedResponse<T>` envelope per the existing spec; that envelope is unchanged.

#### Scenario: Envelope conformance check
- **WHEN** any endpoint under `/api/secrets/*` or `/api/oauth/*` returns a 2xx response
- **THEN** the response body has the shape `{ data: <T>, meta: <object> }` (or the standard error envelope for non-2xx responses)
- **AND** no array or scalar is returned at the top level of the response body

### Requirement: Pricing and Cost Estimation
`src/butlers/api/pricing.py` loads per-model token pricing from `pricing.toml` and exposes cost estimation for session cost calculation.

#### Scenario: Pricing config loading
- **WHEN** `load_pricing()` is called at startup
- **THEN** the `pricing.toml` file is parsed from the repo root
- **AND** each `[models.<model_id>]` section is loaded into a `ModelPricing(input_price_per_token, output_price_per_token)` dataclass

#### Scenario: Cost estimation
- **WHEN** `estimate_cost(model_id, input_tokens, output_tokens)` is called
- **THEN** the cost is calculated as `(input_price * input_tokens) + (output_price * output_tokens)`
- **AND** `None` is returned for unknown model IDs

#### Scenario: Session cost estimation
- **WHEN** `estimate_session_cost(config, model_id, input_tokens, output_tokens)` is called
- **THEN** it returns the estimated USD cost, or `0.0` for unknown models
- **AND** a warning is logged once per unknown model ID

#### Scenario: Spend endpoints use MCP fan-out
- **WHEN** spend summary, daily spend, or top-sessions endpoints are called
- **THEN** each butler is queried via MCP tools (`sessions_summary`, `sessions_daily`, `top_sessions`) in parallel
- **AND** per-model token counts from each butler are converted to USD using the pricing config
- **AND** results are merged across butlers (e.g., daily spend is aggregated by date)

### Requirement: Audit Log
`src/butlers/api/routers/audit.py` SHALL query the switchboard butler's `dashboard_audit_log` table and provide a `log_audit_entry()` helper for other routers to record write operations.

#### Scenario: Read audit log
- **WHEN** `GET /api/audit-log` is called
- **THEN** paginated audit entries are returned from the switchboard DB
- **AND** filters for `butler`, `operation`, `since`, `until` are supported

#### Scenario: Filter by canonical credential key
- **WHEN** `GET /api/audit-log?key=u:google&limit=50` is called
- **THEN** the response is `PaginatedResponse<AuditLogEntry>` filtered to `public.audit_log` rows whose normalised `target` equals the canonical credential key `u:google`
- **AND** the canonical credential-key format matches the focus-key format used by the `/secrets` page: `u:<provider>`, `s:<KEY>`, `c:<id>`
- **AND** a normalisation function (defined in `core-credentials`) is applied to match against existing `target` values written by other writers (e.g. older audit rows that used non-canonical formats)
- **AND** the existing `?since=`, `?actor=`, `?action=`, and `?limit=` query parameters remain functional and combinable with `?key=`
- **AND** the response uses the existing `PaginatedResponse<T>` envelope (RFC 0007), not the `ApiResponse<T>` envelope

#### Scenario: Unknown credential key returns empty page
- **WHEN** `GET /api/audit-log?key=u:does-not-exist` is called
- **THEN** the response is an empty `PaginatedResponse` with `meta.total = 0` and `meta.has_more = false`

#### Scenario: Write audit entry
- **WHEN** `log_audit_entry(db, butler, operation, request_summary)` is called by any router after a write operation
- **THEN** an entry is inserted into `dashboard_audit_log` in the switchboard DB
- **AND** errors in audit logging are silently swallowed (never break the primary operation)

### Requirement: Calendar Workspace
`src/butlers/api/routers/calendar_workspace.py` provides a normalized calendar read surface, metadata endpoint, sync trigger, and mutation endpoints for both user-view and butler-view events. It SHALL additionally expose a read-only accounts surface (`GET /api/calendar/accounts`) and a per-calendar source enable/disable mutation (`POST /api/calendar/sources`); the sync trigger SHALL accept a `full` recovery flag and the metadata endpoint SHALL carry a per-source `error_kind` so the workspace can render the correct Recover/Reconnect CTA. No new table is introduced — source enable/disable reuses the existing `calendar_sources` projection rows, and the accounts surface reuses `public.google_accounts` plus the Google Calendar connector health.

#### Scenario: Workspace read
- **WHEN** `GET /api/calendar/workspace?view=user&start=...&end=...` is called
- **THEN** calendar entries are fan-out queried across butler DBs (joining `calendar_event_instances`, `calendar_events`, `calendar_sources`, and `calendar_sync_cursors`)
- **AND** entries are normalized into `UnifiedCalendarEntry` objects with computed `source_type`, `status`, and `sync_state`
- **AND** optional `timezone` parameter converts all timestamps to the requested display timezone

#### Scenario: Workspace mutations
- **WHEN** user-event or butler-event mutation endpoints are called
- **THEN** the request is proxied to the owning butler via MCP tool calls (`calendar_create_event`, `calendar_update_event`, etc.)
- **AND** projection freshness metadata is fetched after mutation and included in the response

#### Scenario: Meta carries per-source error_kind
- **WHEN** `GET /api/calendar/workspace/meta` is called
- **THEN** each `connected_sources` entry includes an `error_kind` field classifying a failed/stale source as one of `none`, `token_expired`, `auth`, `not_found`, or `transient`
- **AND** a client that ignores `error_kind` observes the pre-change meta shape otherwise unchanged

#### Scenario: Sync trigger forwards full recovery flag
- **WHEN** `POST /api/calendar/workspace/sync` is called with `full=true` (optionally scoped to a `source_key`/`source_id`)
- **THEN** the request is forwarded to `calendar_force_sync(full=true)` for the targeted source(s), running a full re-sync that ignores the stored cursor
- **AND** the response reports per-target whether a full recovery ran
- **AND** `full=false` (or omitting `full`) preserves the existing incremental sync behavior

#### Scenario: List connected calendar accounts with health
- **WHEN** `GET /api/calendar/accounts` is called
- **THEN** the connected `public.google_accounts` rows are returned, each joined with the Google Calendar connector's per-account health (status, `error_kind`, last ingest)
- **AND** when connector health is unavailable, accounts are still returned with a degraded/unknown health indicator rather than the endpoint failing
- **AND** the endpoint is read-only — it does not connect or disconnect accounts (account lifecycle stays in the Google accounts surface)

#### Scenario: Enable or disable a calendar source
- **WHEN** `POST /api/calendar/sources` is called to enable or disable a single calendar as a sync source
- **THEN** the enabled/disabled state is toggled on the existing `calendar_sources` row (no new table)
- **AND** a disabled source is skipped by the sync loop on subsequent syncs
- **AND** a disabled source is surfaced as off (not failed) in the workspace meta so its staleness is not read as an error

### Requirement: Memory Endpoints (Cross-Butler Fan-Out)
The memory endpoint surface (`src/butlers/api/routers/memory.py`) SHALL probe
all butler pools for memory tables, gracefully skip pools that lack a memory
schema, and SHALL be extended to back the house-ledger `/memory` redesign with
the following **additive, backward-compatible** read-side deltas and two new
fact lifecycle mutations. Every new field and parameter MUST have a verified
data source; no affordance on the redesigned page may ship without its wire here.

- `GET /api/memory/stats` SHALL additionally return:
  - `last_consolidation_at: str | null` — ISO timestamp of the most recent
    successful consolidation run (sourced from `public.consolidation_runs`).
  - `last_consolidation_facts_produced: int | null` — facts produced by that run.
  - `dead_letter_episodes: int` — count of dead-lettered episodes (default 0).
  These fields are additive; existing `/stats` consumers are not broken.
- `GET /api/memory/episodes` SHALL accept a `status` filter over the
  `consolidation_status` enum `{pending, consolidated, failed, dead_letter}`.
  The legacy `consolidated: bool` parameter SHALL remain accepted; when both are
  supplied, `status` takes precedence.
- `GET /api/memory/facts` SHALL accept a `source_episode_id: str | null` filter
  (facts whose source episode matches) and an `importance_min: float | null`
  filter (facts with importance ≥ the threshold). The response `meta.total`
  reflects the filtered count (the attention rail reads it for the
  "important facts fading" row).
- `GET /api/memory/facts/{id}` SHALL additionally return `superseded_by:
  str | null`, computed by the reverse query `WHERE supersedes_id = $1`
  (the forward `supersedes_id` field already exists).
- `POST /api/memory/facts/{id}/confirm` SHALL be added: body `{}` →
  `ApiResponse<Fact>`, delegating to the storage `confirm_memory()` operation
  (re-inking; updates `last_confirmed_at`). It is the backend for the fact
  detail Confirm commit pill.
- `POST /api/memory/facts/{id}/retract` SHALL be added: body `{}` →
  `ApiResponse<Fact>` with `validity = 'retracted'`, delegating to the storage
  `forget_memory()` operation. It is the backend for the Retract secondary pill.
- `GET /api/memory/inspect` (existing) SHALL back the page's single unified
  search; pagination is **one offset across the union of kinds** for v1 (the
  current handler paginates the union; this is acceptable and is stated here so
  the frontend reads one offset, not per-kind offsets).
- `GET /api/memory/reembed/pending` (existing, per-tier `counts` + `total`)
  SHALL back the embeddings housekeeping surface and the rail's stale-embeddings
  row; no change required.

All new and existing memory endpoints continue to use the cross-butler fan-out
pattern and the `ApiResponse<T>` / `PaginatedResponse<T>` envelopes (RFC 0007);
pools without memory tables are silently skipped.

#### Scenario: Memory fan-out with graceful skip
- **WHEN** a memory endpoint queries across butler pools
- **THEN** pools without memory tables (episodes, facts, rules) are silently skipped
- **AND** results from pools with memory tables are merged and paginated

#### Scenario: Stats carries consolidation fields
- **WHEN** `GET /api/memory/stats` is called
- **THEN** the response includes `last_consolidation_at`,
  `last_consolidation_facts_produced`, and `dead_letter_episodes`
- **AND** a client that ignores those fields observes the pre-change `/stats`
  shape unchanged

#### Scenario: Episodes status filter takes precedence over legacy bool
- **WHEN** `GET /api/memory/episodes?status=dead_letter` is called
- **THEN** only episodes with `consolidation_status = 'dead_letter'` are returned
- **AND** when both `status` and the legacy `consolidated` bool are supplied,
  `status` governs the filter

#### Scenario: Facts source-episode and importance filters
- **WHEN** `GET /api/memory/facts?source_episode_id=<id>` is called
- **THEN** only facts whose source episode equals `<id>` are returned (backing
  the episode detail page's derived-facts list)
- **WHEN** `GET /api/memory/facts?importance_min=8&validity=fading` is called
- **THEN** `meta.total` reflects the count of high-importance fading facts
  (backing the rail's "important facts fading" row)

#### Scenario: Fact detail carries superseded-by
- **WHEN** `GET /api/memory/facts/{id}` is called and another fact has
  `supersedes_id` equal to `{id}`
- **THEN** the response includes `superseded_by` set to that other fact's id
- **WHEN** no fact supersedes it
- **THEN** `superseded_by` is `null`

#### Scenario: Confirm re-inks a fact
- **WHEN** `POST /api/memory/facts/{id}/confirm` is called
- **THEN** the response is `ApiResponse<Fact>` with `last_confirmed_at` updated
- **AND** the operation delegates to the storage `confirm_memory()` path

#### Scenario: Retract sets validity to retracted
- **WHEN** `POST /api/memory/facts/{id}/retract` is called
- **THEN** the response is `ApiResponse<Fact>` with `validity = 'retracted'`
- **AND** the operation delegates to the storage `forget_memory()` path

#### Scenario: Inspect paginates the union with one offset
- **WHEN** `GET /api/memory/inspect?q=<term>&offset=<n>` is called
- **THEN** results across kinds are paginated as a single union with one offset
  (v1 semantics), not per-kind offsets

### Requirement: Consolidation Run Audit Table (additive-only)
The data plane SHALL gain one new cross-butler table,
`public.consolidation_runs`, written once per successful consolidation run.
This table is **additive-only**: it introduces no change to any existing memory
table (episodes, facts, rules), preserving the redesign's no-storage-migration
intent. It exists because `last_consolidation_facts_produced` (surfaced by
`/api/memory/stats`, the overture, and the rail) is otherwise underivable from
existing tables.

The table SHALL carry at least: `id`, `butler`, `consolidated_at`,
`episodes_processed`, `facts_produced`, `facts_updated`, `rules_created`,
`confirmations_made`, and `errors` — the counts the consolidation pipeline
already computes and returns on each run. The consolidation pipeline SHALL
insert one row on each successful run (write-on-completion). Cross-butler
aggregation for `/api/memory/stats` SHALL follow the established memory fan-out
pattern and MUST NOT breach per-butler schema isolation.

#### Scenario: One row per successful consolidation run
- **WHEN** a butler's consolidation run completes successfully
- **THEN** exactly one row is inserted into `public.consolidation_runs` with the
  run's counts
- **AND** no existing memory table schema is altered by this change

#### Scenario: Stats derives last-write-up from the audit table
- **WHEN** `GET /api/memory/stats` computes `last_consolidation_at` and
  `last_consolidation_facts_produced`
- **THEN** the values are read from the most recent `public.consolidation_runs`
  row (by `consolidated_at`), aggregated across butler pools

### Requirement: Issues Aggregation
`src/butlers/api/routers/issues.py` aggregates live reachability problems and grouped audit-log error history into a single issues feed.

#### Scenario: Issue aggregation
- **WHEN** `GET /api/issues` is called
- **THEN** all butlers are probed for reachability in parallel (critical severity)
- **AND** audit-log errors are grouped by normalized error message with occurrence counts and first/last-seen timestamps
- **AND** scheduled task failures are classified as critical severity
- **AND** results are sorted by recency (newest `last_seen_at` first)

### Requirement: Butler Eligibility Control
Butler-specific routers can expose domain-specific API endpoints that are auto-discovered and mounted.

#### Scenario: Switchboard eligibility
- **WHEN** the switchboard butler has `roster/switchboard/api/router.py`
- **THEN** its endpoints (e.g., routing log, registry, set eligibility) are auto-discovered and mounted
- **AND** the frontend `useSetEligibility` mutation calls the switchboard-specific endpoint

### Requirement: Ingestion Rules and Thread Affinity
Frontend hooks manage unified ingestion rules and thread affinity settings via dedicated API endpoints. The previous triage-specific hooks (`useTriageRules`, `useCreateTriageRule`, etc.) and source filter hooks (`useSourceFilters`, `useCreateSourceFilter`, etc.) are replaced by unified ingestion rules hooks.

#### Scenario: Ingestion rule management
- **WHEN** ingestion rule hooks are used (`useIngestionRules`, `useCreateIngestionRule`, `useUpdateIngestionRule`, `useDeleteIngestionRule`)
- **THEN** rules are fetched from `/api/switchboard/ingestion-rules` with `staleTime: 60s` (no refetchInterval by default)
- **AND** mutations invalidate the `["ingestion-rules"]` query key family
- **AND** optional scope filtering is supported via query params

#### Scenario: Rule dry-run testing
- **WHEN** `useTestIngestionRule` is called
- **THEN** the mutation sends a test envelope to `/api/switchboard/ingestion-rules/test` without invalidating any cache

#### Scenario: Thread affinity management
- **WHEN** `useThreadAffinitySettings`, `useUpsertThreadAffinityOverride`, `useDeleteThreadAffinityOverride` hooks are used
- **THEN** settings are fetched with `staleTime: 60s`
- **AND** override mutations invalidate both settings and overrides query keys

### Requirement: Backfill Job Management
Frontend hooks in `use-backfill.ts` manage historical data backfill jobs with lifecycle operations.

#### Scenario: Adaptive polling for active jobs
- **WHEN** `useBackfillJobProgress(jobId, currentStatus)` is called
- **THEN** polling interval is 5s when the job status is `pending` or `active`
- **AND** polling interval falls back to 30s when the job is completed, cancelled, or paused

#### Scenario: Lifecycle mutations
- **WHEN** pause/cancel/resume mutations succeed
- **THEN** three query key families are invalidated: `["backfill-jobs"]`, `["backfill-job", jobId]`, and `["backfill-job-progress", jobId]`

### Requirement: Ingestion Analytics
Frontend hooks in `use-ingestion.ts` provide multi-tab analytics for the ingestion monitoring page.

#### Scenario: Shared query key strategy
- **WHEN** the Overview and Connectors tabs share data
- **THEN** both tabs reuse warm cache via the shared `ingestionKeys` factory
- **AND** the key hierarchy is `["ingestion", "connectors-list"]`, `["ingestion", "connectors-summary", period]`, etc.

#### Scenario: Lazy-loaded per-tab data
- **WHEN** a tab is inactive
- **THEN** its `enabled` flag prevents unnecessary fetches until the tab is activated

### Requirement: Ingestion Timeline Tab Frontend Hooks
TanStack Query hooks for the Timeline tab on the Ingestion page, following the same cache-key and stale-time conventions as existing ingestion hooks.

#### Scenario: Ingestion events list hook
- **WHEN** the Timeline tab renders on the Ingestion page
- **THEN** `useIngestionEvents(filters)` fetches from `GET /api/ingestion/events` with a 30s stale time
- **AND** the cache key hierarchy is `["ingestion", "events", filters]`

#### Scenario: Request lineage hook
- **WHEN** a user selects a specific ingestion event on the Timeline tab
- **THEN** `useIngestionEventLineage(requestId)` fetches sessions and rollup data in parallel
- **AND** the sessions cache key is `["ingestion", "events", requestId, "sessions"]`
- **AND** the rollup cache key is `["ingestion", "events", requestId, "rollup"]`
- **AND** both use a 30s stale time (no auto-refresh interval; use staleTime only, same as session detail)

### Requirement: Calendar Overlay Projection

The dashboard API SHALL extend the calendar workspace read endpoint so `GET /api/calendar/workspace?view=overlays` projects cached overlay contributions from `calendar.v_overlay_contributions` into `UnifiedCalendarEntry` rows tagged with a new `source_type` value `"overlay_contribution"`. The projection MUST be a pure read of the precomputed view — no LLM session and no cross-schema fan-out at request time — and MUST be fail-open: a missing view, a missing contributing specialist `state` table, or a projection-query failure returns `entries: []` with `has_domain_context: false` rather than HTTP 500.

#### Scenario: Overlays view projects cached entries
- **WHEN** `GET /api/calendar/workspace?view=overlays` is called with a `start`/`end` range
- **THEN** each overlay contribution entry whose target date falls within `[start, end]` is returned as a `UnifiedCalendarEntry` with `source_type="overlay_contribution"`, `editable=false`, `start_at` set to the entry's target date, `title` set to the entry's `label`, and `metadata` carrying `kind`, `priority`, `source_butler` (from the view's hardcoded `butler` column), and the entry's `meta`
- **AND** the response includes `has_domain_context: true`
- **AND** no LLM session is invoked while serving the request

#### Scenario: Overlay entries never appear in user or butler views
- **WHEN** `GET /api/calendar/workspace?view=user` or `view=butler` is called
- **THEN** no entries with `source_type="overlay_contribution"` appear in the response
- **BECAUSE** overlays are a read-only domain-context layer, not user-owned or butler-owned calendar events

#### Scenario: Overlays view is fail-open and empty when none
- **WHEN** `calendar.v_overlay_contributions` is absent (pre-migration), a contributing specialist's `state` table is missing, or the projection query fails
- **THEN** the endpoint returns `entries: []` with `has_domain_context: false` rather than HTTP 500
- **AND** the failure is logged at WARNING level

#### Scenario: Malformed contribution skipped
- **WHEN** a row read from the view has a `value->>'butler'` that does not match the view's hardcoded `butler` source column, or is missing required envelope fields (`butler`, `date`, `has_entries`)
- **THEN** that contribution is skipped with a warning log and excluded from `entries`
- **AND** `has_domain_context` reflects only the valid contributions

### Requirement: Day-Briefing Card Read

The dashboard API SHALL expose a structured day-briefing ("tomorrow at a glance") card read assembled from the cached overlay view for a target date. The response MUST be structured (grouped overlay entries, not generated prose), MUST be served with NO per-open LLM call, and MUST carry an honest empty-state via a `has_domain_context` boolean so the frontend can distinguish "nothing for this day" from "context unavailable".

#### Scenario: Day-card assembled from the cached view
- **WHEN** the day-briefing card read is called for a target date for which at least one specialist has written a contribution (even with `has_entries=false`)
- **THEN** the response is a structured payload grouping the date's overlay entries by butler/kind with `has_domain_context: true`
- **AND** no LLM session is invoked while serving the request

#### Scenario: Day-card honest empty-state
- **WHEN** no specialist has written any contribution for the target date (jobs have not run, or the view is absent)
- **THEN** the response has `entries: []` and `has_domain_context: false`
- **AND** the frontend renders "No domain context for this day" rather than silently omitting the card section

#### Scenario: Day-card is degraded fail-open, not Prometheus-degraded
- **WHEN** the underlying overlay view query fails or the view is absent
- **THEN** the endpoint returns the honest empty-state (`entries: []`, `has_domain_context: false`) rather than HTTP 500
- **AND** the response does NOT use the `aggregates_available` Prometheus degraded-envelope (the day-card reads no Prometheus metrics)

### Requirement: Calendar Quick-Add Parse Endpoint

`src/butlers/api/routers/calendar_workspace.py` SHALL expose a parse-only endpoint `POST /api/calendar/workspace/parse-quick-add` that turns a natural-language string into a **draft** calendar event for confirmation. The endpoint SHALL perform no provider or projection write and SHALL NOT create a calendar event. Event creation continues to flow exclusively through the existing `POST /api/calendar/workspace/user-events` create path (the `calendar_create_event` MCP tool) with a `request_id`; the parse-quick-add response is advisory only.

#### Scenario: Natural-language string parsed into a draft event

- **WHEN** `POST /api/calendar/workspace/parse-quick-add` is called with a free-text `text` (e.g. `"lunch with Sarah Fri 1pm at Tartine"`) and an optional display `timezone`
- **THEN** the text is parsed by an LLM resolved via `resolve_model(pool, butler_name, Complexity.CHEAP)` (the simple/cheap complexity tier — one cheap parse per submit)
- **AND** the response has HTTP 200 with `parse_available=true` and a `draft` object containing the proposed `title`, `start_at`, `end_at`, and optional `location` and `description`
- **AND** no Google event is created and no projection row is written (the parse is read-only)

#### Scenario: Draft is confirmed via the existing create path

- **WHEN** the user accepts (and optionally edits) the returned `draft`
- **THEN** confirmation is submitted to the existing `POST /api/calendar/workspace/user-events` endpoint with `action="create"` and a `request_id`
- **AND** no separate confirm/write endpoint is introduced — the structured create path and its `request_id` idempotency are reused unchanged

#### Scenario: LLM unavailable returns a degraded parse with no fabricated event

- **WHEN** `resolve_model(pool, butler_name, Complexity.CHEAP)` returns `None` (no enabled model qualifies in any tier) or the LLM parse otherwise cannot be produced
- **THEN** the response has HTTP 200 with `parse_available=false` and a human-readable `reason`
- **AND** the response contains no `draft` object (the field is absent or null)
- **AND** the endpoint does not fabricate an event or fall back to a heuristic guess
- **BECAUSE** silently materializing a guessed event on a single-owner calendar would risk writing an unintended event on confirm

#### Scenario: Empty or unparseable input is rejected without a write

- **WHEN** the endpoint is called with empty/blank `text`, or the LLM returns a response that cannot be interpreted as a single event draft
- **THEN** the response indicates the input could not be parsed (`parse_available=false` with a `reason`, or a 422 validation error for blank input) and contains no `draft`
- **AND** no provider or projection write occurs

### Requirement: Meeting-Prep Rail Endpoint

The dashboard API SHALL expose `GET /api/calendar/workspace/prep/{event_id}` returning the meeting-prep context (resolved attendees with relationship letter-marks, relationship notes, and last-met) for a selected calendar event. The endpoint MUST be sourced exclusively from the precomputed `calendar.v_prep_contributions` cached view: it MUST NOT issue a direct cross-schema query (e.g. `SELECT ... FROM relationship.*` / `health.*`) at request time and MUST NOT spawn an LLM session. It MUST merge contributions across contributing butlers by attendee `entity_id` (so a single attendee carries relationship context plus any future message context), skip envelopes whose payload `butler` disagrees with the view's hardcoded source column, and fail open to a structured empty payload (never HTTP 500) when no prep contribution exists.

#### Scenario: Prep rail returns precomputed context
- **WHEN** `GET /api/calendar/workspace/prep/{event_id}` is called for an event that has precomputed prep contributions
- **THEN** the response carries the event's attendees (each with `entity_id`, `name`, `dunbar_tier`, `notes`, `last_met`/`last_met_event`), `has_prep_context: true`, and `source_butlers` listing the contributing schemas
- **AND** no direct cross-butler read and no LLM session occur while serving the request

#### Scenario: Prep rail honest empty-state
- **WHEN** the prep rail read is called for an event with no precomputed prep contribution (co-attended-edge / contact-link coverage not yet populated)
- **THEN** the endpoint returns `has_prep_context: false` with an empty `attendees` list and empty `source_butlers`, not HTTP 500
- **BECAUSE** the prep rail renders "no prep context yet" for events lacking coverage rather than fabricating context or reading sibling schemas live

#### Scenario: Prep rail never reads sibling schemas on demand
- **WHEN** the prep rail read is served
- **THEN** it reads only `calendar.v_prep_contributions` (contribution-sourced cached data) and issues no on-demand `SELECT` against `relationship.*`, `health.*`, or any other sibling schema, and opens no MCP/LLM session
- **BECAUSE** RFC-0020 rejected the on-demand cross-schema read and the per-open LLM synthesis paths

#### Scenario: Prep rail fail-open on missing view
- **WHEN** `calendar.v_prep_contributions` is absent (pre-migration), a contributing specialist's `state` table is missing, or the projection query fails
- **THEN** the endpoint returns `has_prep_context: false` with an empty `attendees` list rather than HTTP 500
- **AND** the failure is logged at WARNING level

#### Scenario: Prep rail merges attendees across butlers
- **WHEN** more than one contributing butler has written a prep envelope for the event with the same attendee `entity_id`
- **THEN** the response merges them into a single attendee carrying the union of their notes and message context, and `source_butlers` lists every contributing schema

#### Scenario: Prep rail skips butler-mismatched envelope
- **WHEN** a row read from the view has a `value->>'butler'` that does not match the view's hardcoded `butler` source column
- **THEN** that contribution is skipped with a warning log and excluded from the response

### Requirement: Calendar ICS Export

The dashboard API SHALL expose `GET /api/calendar/export/ics`, a read-only
data-portability export that streams the calendar workspace entries for a date
range as a `text/calendar` (iCalendar / VCALENDAR) file generated with the
`icalendar` library. The export SHALL reuse the existing workspace
read/projection and accept the same `view`, `butlers`, `sources`, `status`, and
`source_type` filters as `GET /api/calendar/workspace`, so the exported set
matches what the workspace read returns for the same inputs. Each entry SHALL
become a VEVENT whose `SUMMARY` is the entry title verbatim — the `BUTLER:`
prefix on butler-authored events MUST be preserved. The endpoint MUST perform no
provider write, MUST NOT spawn an LLM session, and MUST NOT require a database
migration. ICS subscribe (`webcal`) and `.ics` import are out of scope.

#### Scenario: Range exported as valid VCALENDAR

- **WHEN** `GET /api/calendar/export/ics` is called with a valid `view`, `start`,
  and `end`
- **THEN** the response is `text/calendar` with a `Content-Disposition: attachment`
  header and a body that parses as a valid VCALENDAR containing one VEVENT per
  workspace entry in the range, each with `UID`, `DTSTART`, `DTEND`, and `SUMMARY`

#### Scenario: Butler title prefix preserved

- **WHEN** the exported range includes a butler-authored event whose title begins
  with the `BUTLER:` prefix
- **THEN** that event's VEVENT `SUMMARY` retains the `BUTLER:` prefix verbatim

#### Scenario: Empty range yields an empty calendar

- **WHEN** the requested range contains no entries
- **THEN** the endpoint returns HTTP 200 with a valid VCALENDAR that contains no
  VEVENT components, rather than an error

#### Scenario: Invalid range rejected

- **WHEN** `end` is not after `start`, or the range exceeds the 90-day maximum,
  or a `status`/`source_type` facet value is unknown
- **THEN** the endpoint returns HTTP 400 and writes nothing; a request missing
  the required `start`/`end` parameters returns HTTP 422

### Requirement: Calendar ICS Subscribe Feed

The dashboard API SHALL expose `GET /api/calendar/subscribe.ics`, a read-only
live ICS feed an external calendar application can subscribe to (for example via
`webcal://`). On each fetch the endpoint SHALL re-render the **current** calendar
workspace entries — over a rolling window relative to the request time (the
default window is `now − 30 days … now + 60 days`, within the 90-day workspace
range cap) — as a `text/calendar` (iCalendar / VCALENDAR) body generated with the
`icalendar` library, reusing the same projection, the `view` / `butlers` /
`sources` / `status` / `source_type` filters, and the `BUTLER:` title-prefix
preservation as `GET /api/calendar/export/ics`. The response SHALL use
`Content-Disposition: inline` so clients treat it as a subscription feed rather
than a one-shot download. The endpoint MUST perform no provider write, MUST NOT
spawn an LLM session, MUST NOT require a database migration, and MUST be served
behind the same network boundary as the other dashboard/calendar endpoints (no
new unauthenticated surface, no per-feed token).

#### Scenario: Feed re-renders current workspace entries

- **WHEN** `GET /api/calendar/subscribe.ics` is fetched
- **THEN** the response is HTTP 200 `text/calendar` with a
  `Content-Disposition: inline` header and a body that parses as a valid
  VCALENDAR containing one VEVENT per current workspace entry in the rolling
  window, each with `UID`, `DTSTART`, `DTEND`, and `SUMMARY`

#### Scenario: Butler title prefix preserved

- **WHEN** the feed window includes a butler-authored event whose title begins
  with the `BUTLER:` prefix
- **THEN** that event's VEVENT `SUMMARY` retains the `BUTLER:` prefix verbatim

#### Scenario: Unknown facet rejected

- **WHEN** a `status` or `source_type` facet value is unknown
- **THEN** the endpoint returns HTTP 400 and writes nothing

### Requirement: Calendar ICS Import With Dedup

The dashboard API SHALL expose `POST /api/calendar/import/ics`, which accepts an
uploaded `.ics` file plus a target `butler_name` (and optional `calendar_id`),
parses its VEVENT components, and creates the events in the user calendar through
the existing `calendar_create_event` MCP path. The import SHALL be **deduplicated
against existing workspace entries** using the read-model's existing
`(title, starts_epoch)` collapse key: an event whose collapse key matches an
existing workspace entry — including every event when the same `.ics` is imported
again — MUST be skipped rather than creating a duplicate. Duplicate VEVENTs within
the uploaded file itself MUST also be collapsed. The endpoint SHALL return the
`parsed`, `imported`, and `skipped_duplicates` counts, where
`imported + skipped_duplicates == parsed`. The endpoint MUST require no database
migration.

#### Scenario: New events imported

- **WHEN** a `.ics` containing events not present in the workspace is imported
- **THEN** each such event is created via `calendar_create_event` and the
  response reports `imported` equal to the number of new events with
  `skipped_duplicates` of 0

#### Scenario: Re-importing the same file is a no-op

- **WHEN** a `.ics` whose events already exist in the workspace (the
  `(title, starts_epoch)` collapse key matches existing entries) is imported
- **THEN** no `calendar_create_event` call is made for those events and the
  response reports `imported` of 0 with `skipped_duplicates` equal to the parsed
  event count

#### Scenario: Empty or invalid payload rejected

- **WHEN** the uploaded file is empty or is not parseable as iCalendar
- **THEN** the endpoint returns HTTP 400 and creates nothing

### Requirement: Prep Rail Surfaces Merged Message Context

The meeting-prep rail read `GET /api/calendar/workspace/prep/{event_id}` SHALL
surface a populated `message_context` for an attendee when an email/message-owning
butler has contributed one. The endpoint MUST union the relationship-sourced
envelope (attendee + notes + last-met) with the email-sourced envelope (message
context) by attendee `entity_id`, reading both exclusively from the precomputed
`calendar.v_prep_contributions` cached view. It MUST NOT issue a direct cross-schema
query and MUST NOT spawn an LLM session at request time, and MUST continue to fail
open to a structured empty payload when no contribution exists.

#### Scenario: Message context merges into the relationship attendee
- **WHEN** both the relationship butler and an email-owning butler (messenger/travel) have written a prep envelope for the same event with the same attendee `entity_id`
- **THEN** the response carries a single merged attendee whose `notes`/`last_met` come from the relationship envelope and whose `message_context` carries the email envelope's recent threads, and `source_butlers` lists both contributing schemas

#### Scenario: Message context surfaced without request-time cross-butler read
- **WHEN** the prep rail read serves an event with email message context
- **THEN** the `message_context` is read only from `calendar.v_prep_contributions` (the precomputed cached view), with no on-demand `SELECT` against any sibling schema and no LLM/Gmail session opened while serving the request

### Requirement: Calendar Duplicate-Cluster Review

The dashboard API SHALL expose `GET /api/calendar/workspace/duplicates`, a
read-only surface that exposes the cross-source duplicate clusters the workspace
read-model collapses. For a `view` + `start` + `end` range it SHALL re-run the
same two-pass dedup over the un-collapsed workspace rows and return every cluster
of more than one member the dedup would collapse: the kept survivor (lowest
keyset), the collapsed-away `duplicate_entries`, the `match_pass`
(`origin_ref` | `title`) that grouped them, the `member_count`, and a
`keep_separate` flag. Clusters with fewer members than the active
`noisy_threshold` SHALL be omitted. The endpoint MUST be fail-open: any read
failure SHALL yield HTTP 200 with `available=false` and an empty `clusters` list,
never an HTTP 500. It MUST perform no provider write and MUST NOT spawn an LLM
session.

#### Scenario: Collapsed cluster exposed

- **WHEN** the same event is synced into multiple butler schemas (identical
  `origin_ref` + start) and `GET /api/calendar/workspace/duplicates` is called
  for a range covering it
- **THEN** the response contains one cluster with `match_pass="origin_ref"`,
  `member_count` equal to the number of copies, a `kept_entry`, and the remaining
  copies as `duplicate_entries`, and `available=true`

#### Scenario: Below-threshold clusters omitted

- **WHEN** a cluster's member count is less than the active `noisy_threshold`
- **THEN** that cluster is not included in the returned `clusters` list

#### Scenario: Fail-open on read failure

- **WHEN** the underlying workspace read fails
- **THEN** the endpoint returns HTTP 200 with `available=false` and an empty
  `clusters` list rather than an error

#### Scenario: Invalid range rejected

- **WHEN** `end` is not after `start`, or the range exceeds the 90-day maximum
- **THEN** the endpoint returns HTTP 400; a request missing the required
  `start`/`end` parameters returns HTTP 422

### Requirement: Calendar Dedup Rules

The dashboard API SHALL expose `PATCH /api/calendar/workspace/dedup-rules` to
persist the workspace-global cross-source dedup rules: a `match_strategy` of
`exact` (origin-ref identity pass only), `balanced` (origin-ref + title/start
collapse; the default), or `aggressive` (as `balanced` but normalising titles by
stripping non-alphanumerics), and a `noisy_threshold` (minimum cluster size for
the review surface to report a cluster, at least 2). Omitted fields SHALL be left
unchanged. An unknown `match_strategy` SHALL be rejected without persisting. The
live workspace read SHALL honor the persisted rules so that changing the strategy
changes what the read collapses. The rules SHALL persist across requests.

#### Scenario: Strategy and threshold persisted

- **WHEN** `PATCH /api/calendar/workspace/dedup-rules` is called with a valid
  `match_strategy` and `noisy_threshold`
- **THEN** the endpoint returns HTTP 200 with the new rules and a subsequent read
  of the rules returns the persisted values

#### Scenario: Unknown strategy rejected

- **WHEN** `PATCH /api/calendar/workspace/dedup-rules` is called with a
  `match_strategy` outside `exact`/`balanced`/`aggressive`
- **THEN** the endpoint rejects the request (HTTP 400 or 422) and does not change
  the persisted rules

### Requirement: Calendar Keep-Separate Override

The dashboard API SHALL expose `POST /api/calendar/workspace/duplicates/keep-separate`
to pin or unpin a duplicate cluster (identified by its `cluster_key`) so the
dedup does not collapse it. When pinned (`keep_separate=true`) the workspace read
SHALL keep all members of that cluster as distinct entries, and the review
surface SHALL still report the cluster with its `keep_separate` flag set. When
unpinned (`keep_separate=false`) the override SHALL be removed and the cluster
SHALL collapse again under the active rules. Overrides SHALL persist across
requests.

#### Scenario: Pinned cluster is not collapsed

- **WHEN** a cluster is pinned via `POST /api/calendar/workspace/duplicates/keep-separate`
  with `keep_separate=true`
- **THEN** the workspace read keeps every member of that cluster as a distinct
  entry, and the duplicates surface still lists the cluster with
  `keep_separate=true`

#### Scenario: Unpin restores collapse

- **WHEN** a previously-pinned cluster is unpinned with `keep_separate=false`
- **THEN** the override is removed and the cluster collapses again under the
  active dedup rules

