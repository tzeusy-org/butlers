# Dev Environment

> **Purpose:** Walk through setting up and running the full Butlers development stack.
> **Audience:** Developers ready to run Butlers locally for the first time.
> **Prerequisites:** [Prerequisites](prerequisites.md)

## Overview

There are two ways to run the Butlers development environment: the automated tmux approach using `dev.sh`, and the manual approach where you start each service individually. Both end up with the same set of running services: PostgreSQL, butler daemons, connectors, and the dashboard.

## Quick Start (tmux)

The fastest path from clone to running system:

```bash
# 1. Install Python dependencies
uv sync --dev

# 2. Start everything in tmux
./scripts/dev.sh
```

This launches a tmux session with panes for:

- PostgreSQL (via Docker Compose)
- All butler daemons (Switchboard, General, Relationship, Health, Messenger, etc.)
- Connector processes (Telegram bot, Telegram user-client, Gmail)
- Dashboard API and frontend

The `dev.sh` script sources secrets from `/secrets/.dev.env` and per-connector files under `secrets/connectors/`. Create these before your first run (see [Prerequisites](prerequisites.md) for the expected file layout). If a connector's secret file is missing, that connector pane will fail to start without affecting other services.

## Manual Approach

If you prefer more control or are not using tmux, start services step by step.

### Step 1: Install Dependencies

```bash
uv sync --dev
```

This installs all Python dependencies, including dev/test extras, into a virtual environment managed by uv.

### Step 2: Start PostgreSQL

```bash
docker compose up -d postgres
```

This starts a PostgreSQL container on port 5432. Default credentials are `postgres`/`postgres`. The database is used by all butlers (one database, per-butler schemas plus a shared schema).

### Step 3: Start Butler Daemons

Start all butlers:

```bash
butlers up
```

Or start specific butlers by name:

```bash
butlers up --only switchboard --only general
```

Names can also be comma-separated:

```bash
butlers up --only switchboard,general,health
```

The `butlers up` command discovers butler configurations from the `roster/` directory, checks for port conflicts, and starts each daemon. Butlers authenticate their LLM runtimes via the dashboard Settings page --- you will need to complete that step before butlers can spawn LLM sessions.

### Step 4: Start the Dashboard

In one terminal, start the backend API:

```bash
uv run butlers dashboard --port 41200
```

In another terminal, start the frontend:

```bash
cd frontend && npm install && npm run dev
```

The dashboard will be available at `http://localhost:41173`.

### Step 5: Authenticate LLM Runtimes

Open the dashboard in your browser and navigate to the Settings page. For each LLM runtime provider your butlers use (Claude, Codex, Gemini), click "Login" and follow the OAuth device-code flow. Once authenticated, tokens are persisted and butlers can spawn LLM sessions.

## Docker Compose (dev profile)

An alternative to starting services individually is the Docker Compose `dev` profile, which runs PostgreSQL, the Dashboard API, and the Vite frontend together:

```bash
docker compose --profile dev up
```

This starts:

- PostgreSQL on port 5432
- Dashboard API on port 41200
- Vite dev server on port 41173

You would still need to start butler daemons separately via `butlers up`.

## Service Ports

| Service | Port | Description |
| --- | --- | --- |
| Switchboard | 41100 | Message router --- routes MCP requests to domain butlers |
| General | 41101 | Catch-all assistant with collections and entities |
| Relationship | 41102 | Contacts, interactions, gifts, activity feed |
| Health | 41103 | Measurements, medications, conditions, symptoms |
| Messenger | 41104 | Delivery relay --- Telegram and email channel outputs |
| Dashboard API | 41200 | Web UI backend for monitoring and managing butlers |
| Frontend | 41173 | Vite dev server (development only) |
| PostgreSQL | 5432 | Shared database server (one DB, per-butler schemas) |

Butler MCP servers occupy the 41100--41199 port range. OTLP HTTP traces (port 4318) are sent to an external Alloy instance and are not exposed locally.

## Listing Discovered Butlers

To see what butlers are available and their current status:

```bash
butlers list
```

This prints a table showing each butler's name, port, running status (checked via port probe), enabled modules, and description.

## Running Tests

Once the dev environment is running, you can run the test suite:

```bash
make check         # Lint + test
make test          # Tests only
make test-unit     # Fast unit tests (no Docker needed)
make test-integration  # Integration tests (requires Docker)
```

For quick feedback during development, prefer targeted test runs:

```bash
uv run pytest tests/test_foo.py -q --tb=short
```

## Related Pages

- [First Butler Launch](first-butler-launch.md) --- triggering a butler and viewing its session log
- [Dashboard Access](dashboard-access.md) --- more detail on the dashboard
- [Butler Lifecycle](../concepts/butler-lifecycle.md) --- what happens inside a butler daemon
