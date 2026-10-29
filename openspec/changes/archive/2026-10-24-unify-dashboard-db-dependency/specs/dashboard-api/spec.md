## MODIFIED Requirements

### Requirement: FastAPI Application Factory
The `create_app()` function in `src/butlers/api/app.py` SHALL build the FastAPI application with CORS middleware, lifespan handler, error handlers, static file serving, and router registration. The lifespan handler initializes `MCPClientManager`, `PricingConfig`, and `DatabaseManager` singletons on startup and tears them down on shutdown.

ID: REQ-dashboard-api-070
Source: bu-q11586.3 accepted S1 registry/dependency packet
Scope: v1-mandatory

#### Scenario: Application startup
- **WHEN** the FastAPI lifespan starts
- **THEN** `init_dependencies()` discovers all butlers from the roster directory and registers them in the `MCPClientManager` singleton
- **AND** `init_pricing()` loads `pricing.toml` into the `PricingConfig` singleton
- **AND** `init_db_manager()` creates asyncpg pools for each discovered butler plus the shared credential pool
- **AND** core and dynamic router modules consume one public `get_db`/`Db` dependency backed by that singleton, with the existing optional/uninitialized-read adapter preserved

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

### Requirement: Butler-Specific Route Auto-Discovery
`src/butlers/api/router_discovery.py` SHALL scan `roster/{butler}/api/router.py` files and dynamically load them via `importlib`. Butler-specific routers extend the API surface without modifying core router registration code. Direct butlers.api._roster.<butler>.router imports SHALL resolve on demand to the same actual router module used by explicit discovery, preserving the existing {butler_name}_api_router execution/cache identity. Its models namespace SHALL expose the router's actual local model objects. Installing the namespace resolver SHALL NOT mount or execute every router. Explicit discovery/create_app SHALL still mount the complete healthy router set and preserve dependency overrides, sorted traversal and optional invalid-router warnings/skips. Failed newly-created loads SHALL NOT be cached as successful routers, and explicit custom roster roots SHALL not reuse a different file solely by matching a butler name.

ID: REQ-dashboard-api-066
Source: bu-ly3lv5.4 released by bu-7lh5ew; performance-discipline; actual owning source and existing contract
Scope: v1-mandatory

#### Scenario: Discovery of butler routers
- **WHEN** `discover_butler_routers()` is called
- **THEN** it iterates sorted subdirectories under `roster/`, looking for `api/router.py` files
- **AND** each file is loaded via `importlib.util.spec_from_file_location` with module name `{butler_name}_api_router`

#### Scenario: Router validation
- **WHEN** a `router.py` module is loaded
- **THEN** it must export a module-level `router` variable that is an `APIRouter` instance
- **AND** modules without `router` or with non-APIRouter exports are logged as warnings and skipped

#### Scenario: DB dependency wiring
- **WHEN** a dynamic module declares its database dependency
- **THEN** it consumes the public `get_db`/`Db` singleton dependency shared with core routers
- **AND** callers may override that public dependency via FastAPI's `dependency_overrides` mechanism

#### Scenario: Namespace and discovery share the actual router
- **WHEN** a butler router or models namespace is requested and explicit discovery later runs
- **THEN** the requested router body has executed once and both paths share its actual module/model identities
- **AND** the healthy router is mounted with its existing database dependency wiring

#### Scenario: Invalid optional router remains visibly optional
- **WHEN** explicit bulk discovery encounters an invalid optional router
- **THEN** it warns and skips as already governed
- **AND** direct import of a failing requested router does not return a partial successful module
