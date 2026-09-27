# Dev Environment

> **Purpose:** Walk through setting up and running the full Butlers development stack.
> **Audience:** Developers ready to run Butlers locally for the first time.
> **Prerequisites:** [Prerequisites](prerequisites.md)

## Overview

There are two ways to run the Butlers development environment: Docker Compose through
`scripts/compose.sh` (the default), or starting each service by hand. Both use an external
PostgreSQL server and end up with the same services: butler daemons, connectors, and the dashboard.

## Quick Start (Docker Compose)

```bash
# 1. Install Python dependencies (tests, CLI, local tooling)
uv sync --dev

# 2. Start the dev stack
./scripts/compose.sh
```

The script loads `.env.dev` (database connection), builds the image, and starts every butler
daemon in `butlers-up`, the connectors, the dashboard API, and the Vite frontend. Dev mode
hot-reloads `src/` and publishes on dev host ports so it can run beside a prod stack
(`./scripts/compose.sh --prod`); the ports for both modes are in the
[deployment port map](../../about/lay-and-land/deployment.md#port-assignments). The script header
documents the flags for the OAuth gate, Tailscale, audio, and observability options.

### Chronicles map basemap

The Chronicles location map uses CARTO raster basemaps. CARTO requires a
basemap API key for these tiles; the key must be present when the Vite frontend
starts so it can append the `key` query parameter to tile requests.

Inject the dev project through Bitwarden Secrets Manager when starting the
Compose stack:

```bash
set -a
source /secrets/.env
set +a
bws run --project-id "${BWS_TZEHOUSE_ID_DEV}" -- ./scripts/compose.sh
```

The secret is named `CARTO_BASEMAP_API_KEY`. Compose passes it to the frontend
as `VITE_CARTO_BASEMAP_API_KEY`; do not put the value in a tracked `.env` file.
Because the browser receives the key, restrict it to the dev dashboard domain
in CARTO rather than treating it as a backend-only secret.

## Manual Approach

If you prefer more control or are not using tmux, start services step by step.

### Step 1: Install Dependencies

```bash
uv sync --dev
```

This installs all Python dependencies, including dev/test extras, into a virtual environment managed by uv.

### Step 2: Provision PostgreSQL

Butlers uses an **external** PostgreSQL instance (configured via `POSTGRES_HOST`
in `.env.dev` / `.env.prod`).  There is no `postgres` service in
`docker-compose.yml`.  Set up the database connection before proceeding:

1. Ensure your PostgreSQL server is running and that `POSTGRES_HOST`,
   `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` are set in your
   environment file (copy `.env.example` for a template).

2. Run the provisioning script as a superuser to install extensions and grant
   butler role membership:

   ```bash
   # Replace <POSTGRES_HOST> and <superuser> with your actual values.
   # The script grants roles to POSTGRES_USER (defaults to 'butlers').
   psql -h <POSTGRES_HOST> -U <superuser> -d butlers -f scripts/init-db.sql

   # To target a different runtime user:
   PGOPTIONS="-c butlers.connecting_user=$POSTGRES_USER" \
       psql -h <POSTGRES_HOST> -U <superuser> -d butlers -f scripts/init-db.sql
   ```

The database is used by all butlers (one database, per-butler schemas plus the
public schema).

**Role membership is required for `SET ROLE` enforcement.** On PostgreSQL 16+,
the membership also needs the role-membership `SET` option; a plain membership
row can still fail with `permission denied to set role`. The Alembic
migrations create per-butler database roles (`butler_{schema}_rw` and
`connector_writer`), but the connecting user (`POSTGRES_USER`, typically
`butlers`) must be granted membership in those roles before runtime `SET ROLE`
calls can succeed.  `scripts/init-db.sql` handles this step.  If you skip it,
runtime role switches will fail with a "permission denied to set role" error.

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

Vite prints the URL it serves on (its default port unless you pass `--port`); `/api` requests are
proxied to `VITE_PROXY_TARGET`, defaulting to `http://localhost:41200`.

### Step 5: Authenticate LLM Runtimes

Open the dashboard in your browser and navigate to the Settings page. For each LLM runtime provider your butlers use (Claude, Codex, Gemini), click "Login" and follow the OAuth device-code flow. Once authenticated, tokens are persisted and butlers can spawn LLM sessions.

## Service Ports

Every port -- butler MCP servers, connectors, dashboard, and the per-mode host ports -- is listed
once in the [deployment port map](../../about/lay-and-land/deployment.md#port-assignments). OTLP
traces go to an external collector and are not exposed locally.

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
make test-unit     # Explicitly marked unit tests (not a comprehensive fast lane)
make test-integration  # Integration tests (requires Docker)
```

For quick feedback during development, prefer targeted test runs:

```bash
uv run pytest tests/test_foo.py -q --tb=short
```

For agent work, run `make test-plan` before widening a scope. It prints a
dirty-worktree-aware suggestion but does not execute pytest. The exact test
ladder and CI-shaped final commands live in `AGENTS.md`.

## Verification

After completing setup, confirm the dev environment is healthy:

```bash
# 1. Python, uv, and Node versions meet minimums
python3 --version    # Must be 3.12+
uv --version
node --version       # Must be 22+

# 2. Dependencies installed
uv run python -c "import butlers; print('import ok')"

# 3. PostgreSQL reachable and migrations applied
uv run butlers db migrate  # Should print "Running migrations..." with no errors

# 4. Butler discovery works
butlers list  # Should list available butlers from roster/

# 5. Dashboard API starts without errors
uv run butlers dashboard --port 41200 &
sleep 2 && curl -s http://localhost:41200/api/health | python3 -m json.tool
kill %1  # Stop the background API
```

If the dashboard responds with `{"status": "ok"}`, the database and API are functioning. If `butlers list` shows butlers with correct ports and statuses, the dev environment matches what this page describes.

## Implementation Notes

- Debug compose services with `docker logs` and build `psql` commands from `.env.dev`
  (`POSTGRES_DB` may be unset; scripts default to `butlers`). Live run logs are inside the
  containers under `/app/logs/...`; the worktree's `logs/` can lag or belong to another run.
- On the tailnet, `/butlers-dev/` serves the Vite frontend and live JSON APIs are under
  `/butlers-dev-api/api/...`. Probing `/butlers-dev/api/...` returns the frontend's HTML fallback.
- `butlers-dev-dashboard-api-hotreload-1` does not reload Python despite its name: restart it after
  backend changes land on `main`. The Vite container does hot-reload.
- Prototyping beside butlers-dev: run a worktree Vite with `--base /butlers-<name>/` and
  `VITE_API_URL=/butlers-dev-api/api` (the default `/api` escapes tailscale path mounts), expose it
  with `tailscale serve --bg --set-path /butlers-<name> ...`, and verify through the tailnet URL.
  For backend changes, run a second `butlers dashboard` from the worktree with the container's
  `POSTGRES_*` env and mount it at `/butlers-<name>-api`. Kill helpers by listening port, never
  with a `pkill -f` pattern that matches your own shell.

## Related Pages

- [First Butler Launch](first-butler-launch.md) --- triggering a butler and viewing its session log
- [Dashboard Access](dashboard-access.md) --- more detail on the dashboard
- [Butler Lifecycle](../concepts/butler-lifecycle.md) --- what happens inside a butler daemon
