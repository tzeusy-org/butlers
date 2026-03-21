# Dashboard API

> **Purpose:** Document the FastAPI dashboard application, its router architecture, auto-discovery system, and real-time event streaming.
> **Audience:** Frontend developers, operators, anyone integrating with the dashboard backend.
> **Prerequisites:** [Architecture Overview](../architecture/index.md).

## Overview

The Butlers Dashboard API is a FastAPI application that provides a single-pane-of-glass REST API over the entire butler infrastructure. It serves 80+ endpoints across 18 domain groups, supports real-time SSE streaming for live updates, and auto-discovers butler-specific API routers from the roster directory. The application is created via the `create_app()` factory in `src/butlers/api/app.py`.

## Application Factory

`create_app()` accepts three optional parameters:

- **`cors_origins`** -- List of allowed CORS origins (defaults to `["http://localhost:41173"]` for the Vite dev server).
- **`static_dir`** -- Path to the built frontend `dist/` directory for production SPA serving. Falls back to `DASHBOARD_STATIC_DIR` environment variable.
- **`api_key`** -- When provided, enables `ApiKeyMiddleware`. When `None`, reads `DASHBOARD_API_KEY` from environment. Pass `""` to explicitly disable auth.

## Lifespan Management

The `lifespan` async context manager handles startup and shutdown:

**Startup:**
1. Initialize shared dependencies (`init_dependencies()`).
2. Load pricing configuration for cost estimation.
3. Initialize `DatabaseManager` with pools for all discovered butlers.
4. Wire DB dependencies for both static and dynamically-discovered routers.
5. Restore CLI auth tokens from the database to the filesystem.

**Shutdown:**
1. Close all database pools via `shutdown_db_manager()`.
2. Clean up shared dependencies.

## Middleware Stack

| Middleware | Purpose |
|-----------|---------|
| `CORSMiddleware` | Cross-origin requests from the frontend (all methods and headers allowed for configured origins) |
| `ApiKeyMiddleware` | Optional API key authentication on `/api/*` routes (health endpoints always public) |

Error handlers convert domain exceptions into standardized JSON responses (502 for unreachable butlers, 404 for unknown butlers, 400 for validation errors, 500 for unhandled exceptions).

## Core Routers

The app registers these static routers (all prefixed under `/api`):

| Router | Domain |
|--------|--------|
| `approvals` | Approval decisions and pending actions |
| `butlers` | Butler listing, status, detail |
| `notifications` / `butler_notifications` | Notification history and butler-scoped notifications |
| `issues` | Issue aggregation |
| `costs` | Cost tracking and period selectors |
| `sessions` / `butler_sessions` | Session lifecycle and butler-scoped sessions |
| `schedules` | Cron schedule CRUD |
| `modules` | Module status and configuration |
| `secrets` | Credential management |
| `state` | KV state store operations |
| `ingestion_events` | Switchboard ingestion event log |
| `timeline` | Unified timeline view |
| `calendar_workspace` | Calendar events and scheduling |
| `search` | Cross-butler search |
| `audit` | Audit trail |
| `memory` | Memory tier health and search |
| `oauth` | Google OAuth flow with CSRF protection |
| `cli_auth` | CLI runtime auth sessions and health probes |
| `sse` | Server-Sent Events for live updates |
| `catalog` / `butler_model` | Model catalog and per-butler model settings |
| `healing` | Self-healing operations |
| `provider_settings` | Provider configuration |

## Auto-Discovered Butler Routers

Butler-specific API routes live in `roster/{butler}/api/router.py` and are auto-discovered by `src/butlers/api/router_discovery.py`. The discovery process:

1. Scans `roster/` for butler subdirectories containing `api/router.py`.
2. Loads each module dynamically via `importlib.util.spec_from_file_location()`. Already-loaded modules in `sys.modules` are reused.
3. Validates that the module exports a `router` variable that is an `APIRouter` instance.
4. Returns a sorted list of `(butler_name, router_module)` tuples.

Dynamic routers are mounted **after** static core routers to prevent shadowing fixed API paths (e.g., `/api/oauth/*`). No `__init__.py` is needed in the `api/` directory. DB dependencies are auto-wired via `wire_db_dependencies()`.

### Adding a Butler Router

Create `roster/<butler-name>/api/router.py`:

```python
from fastapi import APIRouter

router = APIRouter(prefix="/api/<butler-name>", tags=["<butler-name>"])

@router.get("/custom-endpoint")
async def custom_endpoint():
    return {"status": "ok"}
```

Co-locate Pydantic models in `models.py` alongside `router.py`.

## SSE Streaming

The `/api/events` endpoint streams Server-Sent Events for live dashboard updates. Events include butler status changes, session lifecycle events, and ingestion activity. Multiple concurrent subscribers are supported via `asyncio.Queue` instances.

## OTel Instrumentation

When `OTEL_EXPORTER_OTLP_ENDPOINT` is configured, the app automatically instruments FastAPI with OpenTelemetry via `FastAPIInstrumentor`, sending traces and metrics to the configured OTLP endpoint.

## Health Endpoints

Two health endpoints at `/api/health` and `/health` return `{"status": "ok"}`. Always public regardless of API key configuration.

## Static File Serving

In production, when `static_dir` or `DASHBOARD_STATIC_DIR` is set, a `StaticFiles` handler is mounted at `/` with `html=True` for SPA fallback routing. This mount happens **after** all API routes, ensuring `/api/*` paths always take precedence.

## Related Pages

- [MCP Tools](mcp-tools.md) -- How tools are registered by modules
- [Environment Variables](../identity_and_secrets/environment-variables.md) -- Dashboard configuration variables
- [Docker Deployment](../operations/docker-deployment.md) -- Running the dashboard in containers
