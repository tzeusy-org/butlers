# Dashboard Access

> **Purpose:** Explain how to start, access, and use the Butlers web dashboard.
> **Audience:** Developers and operators who want to monitor and manage butlers through the web UI.
> **Prerequisites:** [Dev Environment](dev-environment.md)

## Overview

The Butlers dashboard is a web application for real-time monitoring and management of all butler instances. It consists of two components: a FastAPI backend (the Dashboard API) and a Vite-powered React frontend. Docker Compose runs both; you can also run them as separate processes.

## Starting the Dashboard

### Option 1: Docker Compose

`./scripts/compose.sh` starts the dashboard API (`dashboard-api`) and the Vite frontend
(`frontend-dev`) with the rest of the stack; see [Dev Environment](dev-environment.md). The host
ports differ between dev and prod mode and are listed in the
[deployment port map](../../about/lay-and-land/deployment.md#port-assignments).

### Option 2: Separate Processes

Start the backend API in one terminal:

```bash
uv run butlers dashboard --port 41200
```

The `butlers dashboard` command launches a Uvicorn server hosting the FastAPI application. It
requires a running PostgreSQL instance.

Start the frontend dev server in another terminal:

```bash
cd frontend && npm install && npm run dev
```

Vite prints the URL it serves on; `frontend/vite.config.ts` sets no port, so it uses Vite's default
unless you pass `--port`. It proxies `/api` to `VITE_PROXY_TARGET`, defaulting to
`http://localhost:41200`.

Every `/api` route except `GET /api/health` requires owner authentication; see
[Dashboard Owner Authentication](../identity_and_secrets/dashboard-owner-auth.md).

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

### Identity Management

The dashboard is the primary interface for the identity model:

- **Owner setup** --- the owner entity's page shows a setup banner until your name, email, and
  Telegram handle are configured, so butlers can recognize you across channels
- **Secured credentials** --- app passwords, Telegram API keys, and other sensitive credentials
  that modules need to act on your behalf are stored in PostgreSQL and masked in the UI (list
  responses exclude raw values; a "Reveal" button shows one on demand)
- **Entity directory** --- browse and manage known people and unidentified senders

See [Owner Identity](../identity_and_secrets/owner-identity.md).

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
- Entity and identity endpoints
- Credential and secrets management
- OAuth flow endpoints for runtime authentication
- Butler-specific routes auto-discovered from the roster

## Verification

To confirm the dashboard is running and wired correctly:

```bash
# 1. Backend API health check
curl -s http://localhost:41200/api/health
# Expected: {"status": "ok"} or similar

# 2. Private routes are protected
curl -s -o /dev/null -w "%{http_code}" http://localhost:41200/api/butlers
# Expected: 401 (sign in through the browser to see butler records)

# 3. Frontend is reachable: open the URL Vite printed, or the Compose frontend host port
```

If the health check passes, the private route refuses anonymous access, and the signed-in
dashboard lists your butlers, the dashboard matches what this page describes. Session records appear in `GET /api/butlers/<name>/sessions` after triggering a butler. If auto-discovery of butler-specific API routes is not loading, check that `router.py` exports a module-level `router` variable.

## Related Pages

- [Dev Environment](dev-environment.md) --- full dev stack setup
- [Identity Model](../concepts/identity-model.md) --- how entities and identity work
- [First Butler Launch](first-butler-launch.md) --- triggering butlers and viewing session logs
