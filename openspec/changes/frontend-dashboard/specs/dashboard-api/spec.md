# Dashboard API

The Dashboard API is a FastAPI application that bridges the React frontend to the butler ecosystem. It provides REST endpoints for butler discovery, live operations (via MCP clients), and data browsing (via direct asyncpg reads). The API runs on port 40200 and serves as the single entry point for all dashboard interactions.

All butler daemons are discovered dynamically from the roster configuration directory and each has a dedicated PostgreSQL database and FastMCP SSE server. The dashboard API maintains connections to all discovered butlers.

---

## ADDED Requirements

### Requirement: Butler API Router Auto-Discovery

The dashboard API SHALL automatically discover and register butler-specific API routes from the roster configuration directory. Each butler MAY provide a `roster/{butler_name}/api/router.py` file exporting a module-level `router` variable (a FastAPI `APIRouter` instance).

The discovery process SHALL scan all subdirectories of the configured `butlers_dir` for `api/router.py` files and dynamically load them via `src/butlers/api/router_discovery.py`. Butlers without an `api/` directory are silently skipped. Butlers with an `api/router.py` file that fails to load or does not export a valid `router` variable are logged as warnings and skipped, but do not prevent discovery of other butlers.

Each discovered router SHALL be registered with the FastAPI app under its default prefix (typically `/api/butler/{butler_name}` or another convention defined by the butler). Router modules MAY optionally co-locate Pydantic request/response models in `roster/{butler_name}/api/models.py`.

#### Scenario: Auto-discovery finds all valid routers
- **WHEN** `discover_butler_routers()` is called and three butlers ("switchboard", "general", "relationship") have valid `api/router.py` files
- **THEN** three router modules SHALL be discovered and loaded
- **AND** each router's `router` variable SHALL be registered with the FastAPI app

#### Scenario: Butlers without api/ are silently skipped
- **WHEN** `discover_butler_routers()` is called and one butler ("health") has no `api/` subdirectory
- **THEN** "health" SHALL be silently skipped
- **AND** an info log entry SHALL record that "health" has no router

#### Scenario: Invalid router exports are logged and skipped
- **WHEN** `discover_butler_routers()` is called and one butler's `api/router.py` exports a non-router object or does not export a `router` variable at all
- **THEN** that butler SHALL be skipped with a warning logged
- **AND** discovery of other butlers SHALL continue normally

#### Scenario: Router module loading errors are logged and skipped
- **WHEN** `discover_butler_routers()` is called and one butler's `api/router.py` contains a syntax error or import failure
- **THEN** that butler SHALL be skipped with a warning logged (including the exception details)
- **AND** discovery of other butlers SHALL continue normally

#### Scenario: Router modules can colocate models
- **WHEN** a butler's `roster/{butler_name}/api/` directory contains both `router.py` and `models.py`
- **THEN** the router module MAY import and use models from `models.py` for request/response schema validation
- **AND** no `__init__.py` file is required in the `api/` directory

#### Scenario: DB dependencies are auto-wired
- **WHEN** a butler's router handlers declare FastAPI dependencies like `Depends(get_db_manager)` or `Depends(get_butler_pool("name"))`
- **THEN** those dependencies SHALL be automatically wired by the app factory via `wire_db_dependencies()` 
- **AND** the handlers SHALL receive the correct manager or pool instance at request time

---

### Requirement: FastAPI App Factory

The dashboard API SHALL provide a `create_app()` factory function that constructs and returns a fully configured FastAPI application. The factory MUST register all API routers under the `/api` prefix, attach CORS middleware, mount static file serving (when applicable), and wire up startup/shutdown lifecycle handlers for the database manager and MCP client manager.

The factory SHALL accept an optional `butlers_dir` parameter (defaulting to the `BUTLERS_DIR` environment variable) specifying the path to the butler config directories. The factory SHALL accept an optional `static_dir` parameter for production static file serving.

The app MUST include an OpenAPI title of `"Butlers Dashboard API"` and a version string.

#### Scenario: App factory creates a runnable application
- **WHEN** `create_app()` is called with a valid `butlers_dir` pointing to a directory containing butler config subdirectories
- **THEN** the returned FastAPI app SHALL have all API routers registered under `/api`
- **AND** the app SHALL have CORS middleware attached
- **AND** the app SHALL have startup and shutdown lifecycle event handlers registered

#### Scenario: App factory wires lifecycle handlers
- **WHEN** the FastAPI app starts (lifespan context entered)
- **THEN** the startup handler SHALL call `DatabaseManager.startup()` and `MCPClientManager.startup()`
- **AND** when the app shuts down (lifespan context exited), the shutdown handler SHALL call `DatabaseManager.shutdown()` and `MCPClientManager.shutdown()`

#### Scenario: App factory with no butlers_dir falls back to environment variable
- **WHEN** `create_app()` is called without a `butlers_dir` argument
- **THEN** the factory SHALL read the `BUTLERS_DIR` environment variable to locate butler config directories
- **AND** if neither the argument nor the environment variable is set, the factory SHALL raise a configuration error at startup

---

### Requirement: Multi-DB Connection Manager

The dashboard API SHALL provide a `DatabaseManager` class that maintains one asyncpg connection pool per butler database. The class MUST support startup (creating pools), shutdown (closing pools), single-butler pool access, and fan-out queries across multiple butler databases.

On startup, `DatabaseManager.startup()` SHALL receive the list of discovered `ButlerConfig` objects and create one asyncpg connection pool per butler, using each butler's `db_name` to construct the connection DSN. The DSN base (host, port, credentials) SHALL be read from the `DATABASE_URL` environment variable.

The `pool(butler_name)` method SHALL return the asyncpg pool for a specific butler, raising `KeyError` if the butler name is unknown.

The `fan_out(query, params?, butler_names?)` method SHALL execute the same SQL query concurrently across multiple butler databases using `asyncio.gather` and return a `dict[str, list[asyncpg.Record]]` mapping butler names to their result rows. If `butler_names` is not specified, the query SHALL run against all butler databases. If a query fails against one butler's database, the fan-out MUST NOT fail entirely -- it SHALL return an empty list for that butler and log the error.

On shutdown, `DatabaseManager.shutdown()` SHALL close all connection pools.

#### Scenario: Pools created for all discovered butlers
- **WHEN** `DatabaseManager.startup()` is called with configs for butlers "switchboard", "general", "relationship", "health", and "heartbeat"
- **THEN** five asyncpg connection pools SHALL be created, one per butler database (`butler_switchboard`, `butler_general`, `butler_relationship`, `butler_health`, `butler_heartbeat`)

#### Scenario: Single butler pool access
- **WHEN** `db_manager.pool("health")` is called after startup
- **THEN** the asyncpg pool connected to the `butler_health` database SHALL be returned

#### Scenario: Unknown butler name raises KeyError
- **WHEN** `db_manager.pool("nonexistent")` is called
- **THEN** a `KeyError` SHALL be raised with a message indicating the butler name is not recognized

#### Scenario: Fan-out query across all butlers
- **WHEN** `db_manager.fan_out("SELECT id, prompt, success FROM sessions ORDER BY created_at DESC LIMIT 10")` is called without specifying butler names
- **THEN** the query SHALL execute concurrently across all discovered butler databases
- **AND** the result SHALL be a dict with one key per butler mapping to their respective result rows

#### Scenario: Fan-out query with subset of butlers
- **WHEN** `db_manager.fan_out(query, butler_names=["health", "relationship"])` is called
- **THEN** the query SHALL execute only against the `butler_health` and `butler_relationship` databases
- **AND** the result dict SHALL contain only the keys "health" and "relationship"

#### Scenario: Fan-out tolerates individual database failures
- **WHEN** a fan-out query is executed and the `butler_health` database is unreachable
- **THEN** the result for "health" SHALL be an empty list
- **AND** the error SHALL be logged
- **AND** results for all other reachable butler databases SHALL be returned normally

#### Scenario: Shutdown closes all pools
- **WHEN** `db_manager.shutdown()` is called
- **THEN** all asyncpg connection pools SHALL be closed
- **AND** subsequent calls to `pool()` or `fan_out()` SHALL raise an error indicating the manager is shut down

---

### Requirement: MCP Client Manager

The dashboard API SHALL provide an `MCPClientManager` class that maintains one FastMCP client per butler for live operations (status checks, triggering CC, ticking, schedule/state writes). Connections SHALL be lazy -- established on first use, not at startup.

The `MCPClientManager` SHALL be initialized with the list of discovered `ButlerConfig` objects and SHALL derive each butler's MCP SSE endpoint from `http://localhost:{config.port}/sse`.

The `client(butler_name)` method SHALL return a connected FastMCP client for the named butler. If the client has not yet been connected, it SHALL establish the connection on this first call. If the butler daemon is unreachable (connection refused, timeout), the method SHALL NOT raise an exception -- it SHALL return `None` and log a warning.

The `call_tool(butler_name, tool_name, args?)` method SHALL call an MCP tool on the specified butler and return the result. If the butler is unreachable, it SHALL raise a `ButlerUnreachableError` with the butler name.

On shutdown, `MCPClientManager.shutdown()` SHALL close all open client connections.

#### Scenario: Lazy connection on first use
- **WHEN** `mcp_manager.client("health")` is called for the first time
- **THEN** a FastMCP client connection SHALL be established to `http://localhost:40103/sse`
- **AND** the connected client SHALL be cached for subsequent calls

#### Scenario: Cached client returned on subsequent calls
- **WHEN** `mcp_manager.client("health")` is called after a successful first connection
- **THEN** the previously established client SHALL be returned without creating a new connection

#### Scenario: Unreachable butler returns None
- **WHEN** `mcp_manager.client("health")` is called but the health butler daemon is not running
- **THEN** the method SHALL return `None`
- **AND** a warning SHALL be logged indicating that butler "health" is unreachable at `http://localhost:40103/sse`

#### Scenario: call_tool succeeds for reachable butler
- **WHEN** `mcp_manager.call_tool("switchboard", "status")` is called and the Switchboard daemon is running
- **THEN** the MCP `status` tool SHALL be invoked on the Switchboard
- **AND** the tool result SHALL be returned

#### Scenario: call_tool raises ButlerUnreachableError for down butler
- **WHEN** `mcp_manager.call_tool("health", "status")` is called but the health butler daemon is not running
- **THEN** a `ButlerUnreachableError` SHALL be raised with `butler_name="health"`

#### Scenario: Shutdown closes all open connections
- **WHEN** clients have been established for "switchboard" and "health", and `mcp_manager.shutdown()` is called
- **THEN** both MCP client connections SHALL be closed

---

### Requirement: Butler Discovery

The dashboard API SHALL discover butlers by scanning subdirectories of the configured `butlers_dir` and loading `butler.toml` from each subdirectory using the existing `load_config()` function from `src/butlers/config.py`. Discovery MUST NOT depend on any butler daemon being running.

The `discover_butlers(butlers_dir: Path)` function SHALL return a list of `ButlerConfig` objects, one per valid butler directory found. If a subdirectory does not contain a `butler.toml` or contains an invalid one, it SHALL be skipped with a warning logged -- it MUST NOT prevent discovery of other butlers.

The discovery result SHALL include all butlers regardless of whether their daemons are currently running. Daemon availability is determined separately via the MCP client manager.

#### Scenario: All discovered butlers are discovered from config directories
- **WHEN** `discover_butlers()` is called with a `butlers_dir` containing subdirectories for all configured butlers, each with a valid `butler.toml`
- **THEN** the function SHALL return `ButlerConfig` objects for all butlers
- **AND** each config SHALL have the correct port, description, and db_name from its `butler.toml`

#### Scenario: Invalid butler.toml is skipped
- **WHEN** `discover_butlers()` is called and one subdirectory contains a malformed `butler.toml`
- **THEN** that subdirectory SHALL be skipped with a warning logged
- **AND** all other valid butler configs SHALL still be returned

#### Scenario: Directory without butler.toml is skipped
- **WHEN** `discover_butlers()` is called and `butlers_dir` contains a subdirectory "notes/" with no `butler.toml`
- **THEN** that subdirectory SHALL be silently skipped
- **AND** only subdirectories with `butler.toml` SHALL be included in the result

#### Scenario: Discovery works when all daemons are down
- **WHEN** `discover_butlers()` is called and no butler daemons are running
- **THEN** the function SHALL still return all valid `ButlerConfig` objects
- **AND** the configs SHALL contain the correct names, ports, db_names, and descriptions

#### Scenario: Empty butlers directory returns empty list
- **WHEN** `discover_butlers()` is called with an empty directory
- **THEN** the function SHALL return an empty list without error

---

### Requirement: Dependency Injection

The dashboard API SHALL use FastAPI's dependency injection system to provide `DatabaseManager` and `MCPClientManager` instances to route handlers. Dependencies SHALL be defined in `src/butlers/api/deps.py`.

A `get_db_manager()` dependency SHALL return the `DatabaseManager` instance attached to the app state. A `get_mcp_manager()` dependency SHALL return the `MCPClientManager` instance attached to the app state. A `get_butler_pool(butler_name: str)` dependency SHALL return the asyncpg pool for a specific butler, raising an HTTP 404 if the butler name is unknown. A `get_butler_configs()` dependency SHALL return the list of discovered `ButlerConfig` objects.

The dashboard API SHALL provide a `wire_db_dependencies()` function that wires dependency injection for butler router modules. This function registers FastAPI dependencies that can be injected into butler-specific route handlers to provide access to the `DatabaseManager` and individual butler connection pools. The function SHALL be called during app factory initialization to ensure all discovered router modules can access database infrastructure.

#### Scenario: Route handler receives DatabaseManager via dependency
- **WHEN** a route handler declares a parameter `db: DatabaseManager = Depends(get_db_manager)`
- **THEN** the handler SHALL receive the singleton `DatabaseManager` instance that was initialized at app startup

#### Scenario: Route handler receives MCPClientManager via dependency
- **WHEN** a route handler declares a parameter `mcp: MCPClientManager = Depends(get_mcp_manager)`
- **THEN** the handler SHALL receive the singleton `MCPClientManager` instance

#### Scenario: Butler pool dependency returns pool for valid butler
- **WHEN** a route handler calls `get_butler_pool("relationship")`
- **THEN** the asyncpg pool for the `butler_relationship` database SHALL be returned

#### Scenario: Butler pool dependency returns 404 for unknown butler
- **WHEN** a route handler calls `get_butler_pool("nonexistent")`
- **THEN** an HTTP 404 response SHALL be raised with a message indicating the butler is not found

#### Scenario: Butler router handlers can use wired dependencies
- **WHEN** a butler's `roster/{butler_name}/api/router.py` contains a route handler that declares `Depends(get_db_manager)` or `Depends(get_butler_pool("butler_name"))`
- **THEN** the handler SHALL receive the wired dependencies at request time
- **AND** the handler MAY execute database queries using the provided pool or manager

---

### Requirement: Health Endpoint

The dashboard API SHALL expose a `GET /api/health` endpoint that returns the operational status of the dashboard API and its connections to butler infrastructure.

The response MUST be JSON with the following structure:
- `status`: `"ok"` if the API is running (always `"ok"` if the endpoint responds)
- `butlers`: a dict mapping each butler name to its connection status: `"up"` if the MCP client can reach the daemon, `"down"` if not
- `databases`: a dict mapping each butler name to its database pool status: `"connected"` if the pool is active, `"error"` if the pool failed

The endpoint MUST always return HTTP 200 -- even if all butlers are down and all databases are unreachable, the API itself is healthy if it can respond.

#### Scenario: All systems healthy
- **WHEN** `GET /api/health` is called and all discovered butler daemons are running and all databases are reachable
- **THEN** the response SHALL be HTTP 200 with `status: "ok"`
- **AND** all butler entries in `butlers` SHALL be `"up"`
- **AND** all butler entries in `databases` SHALL be `"connected"`

#### Scenario: Some butlers down
- **WHEN** `GET /api/health` is called and some butler daemons are not running but their databases are reachable
- **THEN** the response SHALL be HTTP 200 with `status: "ok"`
- **AND** the down butler names in `butlers` SHALL be `"down"`
- **AND** the reachable butler names in `butlers` SHALL be `"up"`
- **AND** all butler entries in `databases` SHALL be `"connected"`

#### Scenario: Health endpoint always returns 200
- **WHEN** `GET /api/health` is called and all butlers are down and all databases are unreachable
- **THEN** the response SHALL still be HTTP 200 with `status: "ok"`
- **AND** all entries in `butlers` SHALL be `"down"`
- **AND** all entries in `databases` SHALL be `"error"`

---

### Requirement: CORS Configuration

The dashboard API SHALL attach CORS middleware to allow requests from the frontend development server. In development, the Vite dev server runs on `http://localhost:40173`.

The CORS middleware MUST allow the origin `http://localhost:40173`. It MUST allow all standard HTTP methods (GET, POST, PUT, DELETE, OPTIONS). It MUST allow the `Content-Type` and `Authorization` headers. It MUST support credentials.

The allowed origins SHALL be configurable via the `CORS_ORIGINS` environment variable, which accepts a comma-separated list of origins. If `CORS_ORIGINS` is not set, the default SHALL be `["http://localhost:40173"]`.

#### Scenario: Frontend dev server origin allowed
- **WHEN** the frontend at `http://localhost:40173` sends a preflight OPTIONS request to the dashboard API
- **THEN** the response SHALL include `Access-Control-Allow-Origin: http://localhost:40173`
- **AND** the response SHALL include the allowed methods and headers

#### Scenario: Cross-origin GET request succeeds
- **WHEN** the frontend at `http://localhost:40173` sends a `GET /api/health` request
- **THEN** the response SHALL include the `Access-Control-Allow-Origin` header
- **AND** the response body SHALL be the health check JSON

#### Scenario: Custom CORS origins via environment variable
- **WHEN** the `CORS_ORIGINS` environment variable is set to `"http://localhost:40173,http://localhost:3000"`
- **THEN** both `http://localhost:40173` and `http://localhost:3000` SHALL be allowed origins

#### Scenario: Unknown origin is rejected
- **WHEN** a request arrives from `http://evil.example.com` and that origin is not in the allowed list
- **THEN** the response SHALL NOT include the `Access-Control-Allow-Origin` header for that origin

---

### Requirement: Static File Serving

In production mode, the dashboard API SHALL mount the frontend build output directory as a static file fallback. The frontend is built by Vite into a `frontend/dist/` directory containing `index.html` and associated JS/CSS assets.

The static file mount SHALL be configured via the `static_dir` parameter to `create_app()` or the `DASHBOARD_STATIC_DIR` environment variable. When set, the app SHALL mount a `StaticFiles` handler at `/` with `html=True` so that `index.html` is served for any path not matched by an API route (supporting client-side routing).

API routes under `/api` MUST take precedence over the static file mount. A request to `/api/health` SHALL always reach the API handler, never the static files.

When `static_dir` is not set, the static file mount SHALL NOT be registered (development mode, where Vite serves its own files).

#### Scenario: Production serves index.html at root
- **WHEN** `static_dir` is set to `frontend/dist/` and a browser requests `GET /`
- **THEN** the response SHALL serve the contents of `frontend/dist/index.html`

#### Scenario: Production serves static assets
- **WHEN** `static_dir` is set and a browser requests `GET /assets/main.abc123.js`
- **THEN** the response SHALL serve the file `frontend/dist/assets/main.abc123.js` with the correct content type

#### Scenario: Client-side routing fallback
- **WHEN** `static_dir` is set and a browser requests `GET /butlers/health/sessions`
- **AND** no API route matches that path
- **THEN** the response SHALL serve `frontend/dist/index.html` so the React router can handle the path

#### Scenario: API routes take precedence over static files
- **WHEN** `static_dir` is set and a request is made to `GET /api/health`
- **THEN** the API health endpoint handler SHALL respond, not the static file mount

#### Scenario: No static mount in development mode
- **WHEN** `create_app()` is called without `static_dir` and the `DASHBOARD_STATIC_DIR` environment variable is not set
- **THEN** no `StaticFiles` mount SHALL be registered
- **AND** requests to `/` SHALL return a 404 (the Vite dev server handles frontend requests separately)

---

### Requirement: API Error Handling

The dashboard API SHALL use a consistent JSON error response format across all endpoints. All error responses MUST conform to the following structure:

```json
{
  "error": {
    "code": "<ERROR_CODE>",
    "message": "<human-readable message>",
    "butler": "<butler_name or null>"
  }
}
```

The `code` field SHALL be a machine-readable uppercase string (e.g., `"BUTLER_UNREACHABLE"`, `"BUTLER_NOT_FOUND"`, `"VALIDATION_ERROR"`, `"INTERNAL_ERROR"`). The `message` field SHALL be a human-readable description. The `butler` field SHALL be present when the error is specific to a butler, otherwise `null`.

The API SHALL register a global exception handler for `ButlerUnreachableError` that returns HTTP 502 with code `"BUTLER_UNREACHABLE"`. The API SHALL register a global exception handler for `KeyError` on butler names that returns HTTP 404 with code `"BUTLER_NOT_FOUND"`. Unhandled exceptions SHALL return HTTP 500 with code `"INTERNAL_ERROR"` and MUST NOT leak stack traces to the client.

#### Scenario: Butler unreachable returns 502
- **WHEN** a route handler attempts to call an MCP tool on butler "health" and the daemon is not running
- **THEN** the response SHALL be HTTP 502
- **AND** the body SHALL be `{"error": {"code": "BUTLER_UNREACHABLE", "message": "Butler 'health' is not reachable", "butler": "health"}}`

#### Scenario: Unknown butler returns 404
- **WHEN** a request is made to an endpoint referencing butler name "nonexistent"
- **THEN** the response SHALL be HTTP 404
- **AND** the body SHALL be `{"error": {"code": "BUTLER_NOT_FOUND", "message": "Butler 'nonexistent' not found", "butler": "nonexistent"}}`

#### Scenario: Validation error returns 422
- **WHEN** a request is made with invalid parameters (e.g., missing required field)
- **THEN** the response SHALL be HTTP 422
- **AND** the body SHALL include `"code": "VALIDATION_ERROR"` with a descriptive message

#### Scenario: Unhandled exception returns 500 without stack trace
- **WHEN** an unhandled exception occurs in a route handler
- **THEN** the response SHALL be HTTP 500
- **AND** the body SHALL be `{"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred", "butler": null}}`
- **AND** the response MUST NOT include a Python stack trace or internal exception details
- **AND** the full exception details SHALL be logged server-side

#### Scenario: Error responses include correct Content-Type
- **WHEN** any error response is returned
- **THEN** the `Content-Type` header SHALL be `application/json`
