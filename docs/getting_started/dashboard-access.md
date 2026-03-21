# Dashboard Access

> **Purpose:** Explain how to start, access, and use the Butlers web dashboard.
> **Audience:** Developers and operators who want to monitor and manage butlers through the web UI.
> **Prerequisites:** [Dev Environment](dev-environment.md)

## Overview

The Butlers dashboard is a web application for real-time monitoring and management of all butler instances. It consists of two components: a FastAPI backend (the Dashboard API) and a Vite-powered React frontend. In development, these run as separate processes; in production, they can run together via Docker Compose.

## Starting the Dashboard

### Option 1: Separate Processes (Development)

Start the backend API in one terminal:

```bash
uv run butlers dashboard --port 41200
```

The `butlers dashboard` command launches a Uvicorn server hosting the FastAPI application. It requires a running PostgreSQL instance since it queries butler databases for session data, state, contacts, and configuration.

Start the frontend dev server in another terminal:

```bash
cd frontend && npm install && npm run dev
```

The Vite dev server starts on port 41173 and proxies API requests to the backend on port 41200.

**Access the dashboard at:** `http://localhost:41173`

### Option 2: Docker Compose (dev profile)

Run the database, API, and frontend together:

```bash
docker compose --profile dev up
```

This starts:

| Service | Port | Description |
| --- | --- | --- |
| PostgreSQL | 5432 | Database server |
| Dashboard API | 41200 | FastAPI backend |
| Frontend | 41173 | Vite dev server |

### Option 3: Full tmux Development

If you use `dev.sh`, the dashboard is included automatically:

```bash
./scripts/dev.sh
```

This starts all services (database, butlers, connectors, dashboard) in tmux panes. The dashboard API and frontend each get their own pane.

## Dashboard Capabilities

### Butler Monitoring

The main view shows all discovered butlers with their current status. For each butler you can see:

- **Running state** --- whether the daemon is up and responsive
- **Port and description** --- from `butler.toml`
- **Enabled modules** --- which modules loaded successfully
- **Recent sessions** --- with trigger source, duration, token usage, and output preview

### Session Browsing

Each butler's detail page lists its sessions. Session records include:

- Trigger source (schedule, trigger tool, route)
- Start and end timestamps
- Token usage (input and output)
- Tool calls made during the session
- Full session output text
- Model used for the invocation

### Contact and Identity Management

The dashboard is the primary interface for managing the identity model:

- **Owner contact setup** --- add your email address, Telegram handle, and Telegram chat ID so butlers can recognize you across channels
- **Secured credentials** --- add app passwords, Telegram API keys, and other sensitive credentials that modules need to act on your behalf. These are stored in PostgreSQL and masked in the dashboard UI (API-level masking excludes raw values from list responses, with a "Reveal" button for individual viewing)
- **Contact directory** --- browse and manage all known contacts

A one-time setup banner appears on the contacts page when identity fields are missing.

### LLM Runtime Authentication

The Settings page manages OAuth device-code authentication for LLM runtime CLIs:

- Click "Login" next to a provider (Claude, Codex, Gemini)
- Follow the device-code URL and authorize
- Tokens are persisted to the shared credential store
- Health probes verify token validity periodically
- Live auth status is displayed for each provider

This replaces the need for API key environment variables. Tokens survive restarts and do not require persistent volumes in containerized deployments.

### Module Management

Through the dashboard, you can view module states (active, failed, cascade-failed) and toggle modules on and off at runtime. Failed modules show the phase where they failed (credentials, config, migration, startup, tools) and the error message.

## Dashboard API Routes

Butler-specific API routes live in `roster/{butler}/api/router.py` and are auto-discovered by the router discovery system. Each `router.py` exports a module-level `router` variable (a FastAPI `APIRouter` instance). Database dependencies are auto-wired. No `__init__.py` is needed in the `api/` directory.

The main dashboard API application is defined in `src/butlers/api/app.py` and includes:

- Butler status and discovery endpoints
- Session listing and detail endpoints
- Contact and identity CRUD endpoints
- Credential and secrets management
- OAuth flow endpoints for runtime authentication
- Butler-specific routes auto-discovered from the roster

## Port Summary

| Service | Port | Notes |
| --- | --- | --- |
| Dashboard API | 41200 | Configurable via `--port` flag |
| Frontend (dev) | 41173 | Vite default; configurable in `vite.config.ts` |

## Related Pages

- [Dev Environment](dev-environment.md) --- full dev stack setup
- [Identity Model](../concepts/identity-model.md) --- how contacts and identity work
- [First Butler Launch](first-butler-launch.md) --- triggering butlers and viewing session logs
