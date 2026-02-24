# Dashboard Data Layer and API

## Purpose
Defines the complete data access layer connecting the Butlers dashboard frontend to backend infrastructure. This covers the FastAPI application factory, REST endpoint inventory across all domains, cross-butler database fan-out, MCP client proxy, butler-specific route auto-discovery, TanStack Query refresh patterns, SSE real-time streaming, OAuth bootstrap flow, generic secrets management, response envelope standards, and the pricing/cost estimation model. Together these form the single-pane-of-glass contract between the React frontend and the Python backend.

## ADDED Requirements

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
- **THEN** the default CORS origin `http://localhost:40173` (Vite dev server) is used
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

#### Scenario: TypeScript mirror types
- **WHEN** the frontend imports from `frontend/src/api/types.ts`
- **THEN** `ApiResponse<T>`, `PaginatedResponse<T>`, `ErrorResponse`, `ErrorDetail`, and `PaginationMeta` are available as generic interfaces matching the backend Pydantic shapes

### Requirement: Error Handling Middleware
`src/butlers/api/middleware.py` registers exception handlers that convert domain exceptions into the standard error envelope.

#### Scenario: Butler unreachable
- **WHEN** a `ButlerUnreachableError` is raised during request handling
- **THEN** a 502 response with `code: "BUTLER_UNREACHABLE"` and `butler: "<name>"` is returned

#### Scenario: Butler not found
- **WHEN** a `KeyError` is raised (unknown butler lookup)
- **THEN** a 404 response with `code: "BUTLER_NOT_FOUND"` is returned

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

#### Traces
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/traces` | Cross-butler paginated trace list (fan-out) |
| GET | `/api/traces/{traceId}` | Trace detail with span tree |

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

#### Costs
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/costs/summary` | Aggregate cost summary (MCP fan-out) |
| GET | `/api/costs/daily` | Daily cost time series (MCP fan-out) |
| GET | `/api/costs/top-sessions` | Most expensive sessions (MCP fan-out) |
| GET | `/api/costs/by-schedule` | Per-schedule cost analysis (MCP fan-out) |

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
| POST | `/api/approvals/actions/{id}/approve` | Approve action (stub: 501) |
| POST | `/api/approvals/actions/{id}/reject` | Reject action (stub: 501) |
| POST | `/api/approvals/actions/expire-stale` | Expire stale actions |
| GET | `/api/approvals/rules` | Standing rule list |
| GET | `/api/approvals/rules/{id}` | Rule detail |
| POST | `/api/approvals/rules` | Create rule (stub: 501) |
| POST | `/api/approvals/rules/from-action` | Create rule from action (stub: 501) |
| POST | `/api/approvals/rules/{id}/revoke` | Revoke rule (stub: 501) |
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

#### OAuth
| Method | Path | Purpose |
|--------|------|---------|
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
| Traces list | 30s | `useTraces` |
| Timeline | 30s (default, overridable) | `useTimeline` |
| Health measurements | 30s | `useMeasurements`, `useMedications`, `useConditions`, `useSymptoms`, `useMeals`, `useResearch` |
| Memory stats/episodes/facts/rules | 30s | `useMemoryStats`, `useEpisodes`, `useFacts`, `useRules` |
| Switchboard routing/registry | 30s | `useRoutingLog`, `useRegistry` |
| Backfill jobs | 30s | `useBackfillJobs`, `useBackfillJob` |
| Connector detail | 30s | `useConnectorDetail` |
| Calendar workspace | 30s (default, overridable) | `useCalendarWorkspace` |
| Cost summary | 60s | `useCostSummary` |
| Daily costs | 60s | `useDailyCosts` |
| Top sessions | 60s | `useTopSessions` |
| Connectors list/summary | 60s | `useConnectorSummaries`, `useCrossConnectorSummary`, `useIngestionOverview`, `useConnectorStats` |
| Connector fanout | 120s | `useConnectorFanout` |
| Calendar workspace meta | 60s (default, overridable) | `useCalendarWorkspaceMeta` |
| Memory activity | 15s | `useMemoryActivity` |
| Backfill job progress (active) | 5s | `useBackfillJobProgress` (when status is pending/active) |
| Backfill job progress (idle) | 30s | `useBackfillJobProgress` (when status is completed/cancelled/paused) |
| No auto-interval | n/a | Notifications, contacts, groups, labels, butler config/skills, session/trace detail, approval queries, triage rules (use staleTime: 60s instead) |

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
- **WHEN** a hook receives a nullable identifier (e.g., `useButler(name)`, `useTraceDetail(traceId)`)
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
`src/butlers/api/routers/secrets.py` provides CRUD for the `butler_secrets` table. Secret values are write-only and never returned in API responses.

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

#### Scenario: Cost endpoints use MCP fan-out
- **WHEN** cost summary, daily costs, or top-sessions endpoints are called
- **THEN** each butler is queried via MCP tools (`sessions_summary`, `sessions_daily`, `top_sessions`) in parallel
- **AND** per-model token counts from each butler are converted to USD using the pricing config
- **AND** results are merged across butlers (e.g., daily costs are aggregated by date)

### Requirement: Audit Log
`src/butlers/api/routers/audit.py` queries the switchboard butler's `dashboard_audit_log` table and provides a `log_audit_entry()` helper for other routers to record write operations.

#### Scenario: Read audit log
- **WHEN** `GET /api/audit-log` is called
- **THEN** paginated audit entries are returned from the switchboard DB
- **AND** filters for `butler`, `operation`, `since`, `until` are supported

#### Scenario: Write audit entry
- **WHEN** `log_audit_entry(db, butler, operation, request_summary)` is called by any router after a write operation
- **THEN** an entry is inserted into `dashboard_audit_log` in the switchboard DB
- **AND** errors in audit logging are silently swallowed (never break the primary operation)

### Requirement: Calendar Workspace
`src/butlers/api/routers/calendar_workspace.py` provides a normalized calendar read surface, metadata endpoint, sync trigger, and mutation endpoints for both user-view and butler-view events.

#### Scenario: Workspace read
- **WHEN** `GET /api/calendar/workspace?view=user&start=...&end=...` is called
- **THEN** calendar entries are fan-out queried across butler DBs (joining `calendar_event_instances`, `calendar_events`, `calendar_sources`, and `calendar_sync_cursors`)
- **AND** entries are normalized into `UnifiedCalendarEntry` objects with computed `source_type`, `status`, and `sync_state`
- **AND** optional `timezone` parameter converts all timestamps to the requested display timezone

#### Scenario: Workspace mutations
- **WHEN** user-event or butler-event mutation endpoints are called
- **THEN** the request is proxied to the owning butler via MCP tool calls (`calendar_create_event`, `calendar_update_event`, etc.)
- **AND** projection freshness metadata is fetched after mutation and included in the response

### Requirement: Memory Endpoints (Cross-Butler Fan-Out)
`src/butlers/api/routers/memory.py` probes all butler pools for memory tables and gracefully skips pools that lack memory schema.

#### Scenario: Memory fan-out with graceful skip
- **WHEN** a memory endpoint queries across butler pools
- **THEN** pools without memory tables (episodes, facts, rules) are silently skipped
- **AND** results from pools with memory tables are merged and paginated

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

### Requirement: Triage Rules and Thread Affinity
Frontend hooks in `use-triage.ts` manage email triage rules and thread affinity settings via dedicated API endpoints.

#### Scenario: Triage rule management
- **WHEN** triage rule hooks are used (`useTriageRules`, `useCreateTriageRule`, `useUpdateTriageRule`, `useDeleteTriageRule`)
- **THEN** rules are fetched with `staleTime: 60s` (no refetchInterval by default)
- **AND** mutations invalidate the `["triage-rules"]` query key family

#### Scenario: Rule dry-run testing
- **WHEN** `useTestTriageRule` is called
- **THEN** the mutation sends a test request to the backend without invalidating any cache

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
