# Docker Compose Dev Environment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace `scripts/dev.sh` (tmux-based orchestration) with a `docker-compose.dev.yml` overlay that launches the full Butlers dev stack with proper network segregation, layered startup DAG, and bind-mounted logs.

**Architecture:** A dev compose overlay extends the existing `docker-compose.yml` (postgres, minio, dashboard-api, frontend-dev). New services: migrations init container, oauth-gate sidecar, four connectors (telegram-bot, telegram-user, gmail, live-listener), and butlers-up backend. Network isolation via four named networks. A `--profile dev` toggle controls volume-mount hot-reload vs baked-image mode.

**Tech Stack:** Docker Compose v2 (profiles, `depends_on` conditions, `service_completed_successfully`), existing Dockerfile, Python 3.12, uv

---

## Background / Key Facts

### Connector entrypoints (all `uv run python -m ...`)
| Connector | Module | Health port | Env vars |
|---|---|---|---|
| Telegram bot | `butlers.connectors.telegram_bot` | default | `CONNECTOR_PROVIDER=telegram CONNECTOR_CHANNEL=telegram_bot` |
| Telegram user | `butlers.connectors.telegram_user_client` | default | `CONNECTOR_PROVIDER=telegram CONNECTOR_CHANNEL=telegram_user_client` |
| Gmail | `butlers.connectors.gmail` | default | `CONNECTOR_PROVIDER=gmail CONNECTOR_CHANNEL=email` |
| Live listener | `butlers.connectors.live_listener` | 40091 | `CONNECTOR_PROVIDER=live-listener CONNECTOR_CHANNEL=voice` + `LIVE_LISTENER_DEVICES` |

### Secrets model
All runtime secrets (API keys, tokens) are stored in PostgreSQL (`butler_secrets` table) and managed via the dashboard. Only **infrastructure bootstrap** env vars are needed at container level:
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- `OTEL_EXPORTER_OTLP_ENDPOINT` (optional)
- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI` (bootstrap-only)

### Startup DAG (preserved from dev.sh)
```
Layer 0:   postgres (healthcheck)
Layer 0.5: migrations (one-shot, depends on postgres healthy)
Layer 1a:  dashboard-api (depends on migrations)
           frontend-dev (depends on dashboard-api)
Layer 1b:  telegram-bot, telegram-user, live-listener (depend on migrations)
Layer 2:   oauth-gate (one-shot, depends on dashboard-api healthy)
Layer 3:   butlers-up, gmail connector (depend on oauth-gate completed)
```

### Network topology
```
networks:
  db:        postgres, migrations, dashboard-api, butlers-up, connectors
  backend:   dashboard-api, butlers-up, all connectors (inter-service)
  frontend:  frontend-dev, dashboard-api only
  telemetry: all services that emit OTEL (optional)
```

---

## Task 1: OAuth Gate Script

**Files:**
- Create: `scripts/oauth_gate.py`
- Test: manual — run against local postgres

This one-shot script polls the DB for a Google OAuth refresh token and exits 0 when found (or exits 1 on timeout). Used as a compose init service.

**Step 1: Write `scripts/oauth_gate.py`**

```python
#!/usr/bin/env python3
"""OAuth gate: poll DB for Google refresh token, exit 0 when found.

Used as a Docker Compose init service to gate Layer 3 startup.

Environment variables:
  POSTGRES_HOST     (default: postgres)
  POSTGRES_PORT     (default: 5432)
  POSTGRES_USER     (default: butlers)
  POSTGRES_PASSWORD (default: butlers)
  POSTGRES_DB       (default: butlers)
  OAUTH_GATE_TIMEOUT  (default: 0 = infinite)
  OAUTH_POLL_INTERVAL (default: 5)
  SKIP_OAUTH_CHECK    (default: false)
"""

import os
import sys
import time

SKIP = os.environ.get("SKIP_OAUTH_CHECK", "false").lower() in ("true", "1", "yes")
TIMEOUT = int(os.environ.get("OAUTH_GATE_TIMEOUT", "0"))
INTERVAL = int(os.environ.get("OAUTH_POLL_INTERVAL", "5"))


def _check_token() -> bool:
    """Return True if a Google OAuth refresh token exists in shared.entity_info."""
    import psycopg

    dsn = (
        f"host={os.environ.get('POSTGRES_HOST', 'postgres')} "
        f"port={os.environ.get('POSTGRES_PORT', '5432')} "
        f"user={os.environ.get('POSTGRES_USER', 'butlers')} "
        f"password={os.environ.get('POSTGRES_PASSWORD', 'butlers')} "
        f"dbname={os.environ.get('POSTGRES_DB', 'butlers')}"
    )
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM shared.entity_info ei
                JOIN shared.entities e ON e.id = ei.entity_id
                WHERE 'owner' = ANY(e.roles)
                  AND ei.type = 'google_oauth_refresh'
                  AND ei.value IS NOT NULL
                  AND length(ei.value) > 0
                """
            ).fetchone()
            return bool(row and row[0] > 0)
    except Exception as exc:
        print(f"oauth-gate: DB check failed: {exc}", file=sys.stderr)
        return False


def main() -> None:
    if SKIP:
        print("oauth-gate: SKIP_OAUTH_CHECK=true, exiting immediately")
        sys.exit(0)

    elapsed = 0
    while True:
        if _check_token():
            print("oauth-gate: Google OAuth refresh token found")
            sys.exit(0)

        if TIMEOUT > 0 and elapsed >= TIMEOUT:
            print(
                f"oauth-gate: timed out after {TIMEOUT}s — "
                "continuing without Google credentials",
                file=sys.stderr,
            )
            # Exit 0 so dependent services still start (matches dev.sh behavior)
            sys.exit(0)

        if elapsed == 0:
            print(
                f"oauth-gate: waiting for Google OAuth credentials "
                f"(poll every {INTERVAL}s, timeout: {TIMEOUT or 'infinite'})"
            )

        time.sleep(INTERVAL)
        elapsed += INTERVAL
        if elapsed % 30 == 0:
            print(f"oauth-gate: still waiting... ({elapsed}s)")


if __name__ == "__main__":
    main()
```

**Step 2: Verify it runs locally**

```bash
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=54320 uv run python scripts/oauth_gate.py &
# Should print "waiting for Google OAuth credentials..."
kill %1
```

**Step 3: Commit**

```bash
git add scripts/oauth_gate.py
git commit -m "feat: add oauth_gate.py for docker-compose init service"
```

---

## Task 2: Network Definitions and Base Compose Changes

**Files:**
- Modify: `docker-compose.yml`

Add named networks and assign existing services to them. Also add a healthcheck to `dashboard-api` so downstream services can use `service_healthy`.

**Step 1: Add networks block at bottom of `docker-compose.yml`**

After the `volumes:` block, add:

```yaml
networks:
  db:
    driver: bridge
  backend:
    driver: bridge
  frontend:
    driver: bridge
```

**Step 2: Assign networks to existing services**

Add `networks:` to each existing service:

- `postgres`: `[db]`
- `minio`, `minio-setup`: `[db]` (minio is object storage, co-located with DB net)
- `switchboard`: `[db, backend]`
- `general`, `relationship`, `health`: `[db, backend]`
- `dashboard-api`: `[db, backend, frontend]`
- `frontend-dev`: `[frontend]`

**Step 3: Add healthcheck to `dashboard-api`**

```yaml
  dashboard-api:
    # ... existing config ...
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:41200/health || exit 1"]
      interval: 5s
      timeout: 5s
      retries: 10
      start_period: 15s
```

**Step 4: Verify existing services still start**

```bash
docker compose up -d postgres
docker compose ps
docker compose down
```

**Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add network segregation and dashboard healthcheck to compose"
```

---

## Task 3: Migrations Init Service

**Files:**
- Modify: `docker-compose.yml`

Add a one-shot service that runs `butlers db migrate` before anything else starts.

**Step 1: Add `migrations` service after `minio-setup`**

```yaml
  # Run Alembic migrations before any butler service starts (init container)
  migrations:
    build:
      context: .
      dockerfile: Dockerfile
    command: ["db", "migrate"]
    environment:
      - POSTGRES_HOST=postgres
      - POSTGRES_PORT=5432
      - POSTGRES_USER=butlers
      - POSTGRES_PASSWORD=butlers
    networks: [db]
    depends_on:
      postgres:
        condition: service_healthy
```

**Step 2: Update `dashboard-api` and butler services to depend on migrations**

Add to `dashboard-api`, `switchboard`, `general`, `relationship`, `health`:

```yaml
    depends_on:
      migrations:
        condition: service_completed_successfully
      # keep existing minio-setup dependency for butlers that had it
```

**Step 3: Test the init container**

```bash
docker compose up migrations
# Should show migration output then exit 0
docker compose ps -a | grep migrations
# Should show "Exited (0)"
```

**Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add migrations init service to compose"
```

---

## Task 4: Docker Compose Dev Overlay — Connectors + OAuth Gate + Backend

**Files:**
- Create: `docker-compose.dev.yml`

This is the main new file. It adds all the dev-only services.

**Step 1: Write `docker-compose.dev.yml`**

```yaml
# Dev overlay — extends docker-compose.yml with connectors, oauth gate, and backend.
#
# Usage:
#   docker compose -f docker-compose.yml -f docker-compose.dev.yml up
#   docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile hotreload up
#
# Prerequisites:
#   - tailscale serve configured on host (see scripts/dev.sh header for setup)
#   - .env with bootstrap vars (see .env.example)
#
# Profiles:
#   (default)   — baked images, uv sync at container start
#   hotreload   — volume-mount src/ and frontend/ for live code changes

services:
  # ── Layer 0.5 override: mount roster for migrations ───────────────────
  migrations:
    volumes:
      - ./roster:/app/roster:ro

  # ── Layer 1a override: dashboard-api with OAuth redirect URI ──────────
  dashboard-api:
    environment:
      - POSTGRES_HOST=postgres
      - POSTGRES_PORT=5432
      - POSTGRES_USER=butlers
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-butlers}
      - GOOGLE_OAUTH_REDIRECT_URI=${GOOGLE_OAUTH_REDIRECT_URI:-http://localhost:41200/api/oauth/google/callback}
      - GOOGLE_OAUTH_CLIENT_ID=${GOOGLE_OAUTH_CLIENT_ID:-}
      - GOOGLE_OAUTH_CLIENT_SECRET=${GOOGLE_OAUTH_CLIENT_SECRET:-}
      - BUTLERS_DISABLE_FILE_LOGGING=1

  # ── Layer 1a: frontend (promote from profile to always-on in dev) ─────
  frontend-dev:
    profiles: []  # override: always start in dev overlay
    environment:
      - VITE_API_URL=${VITE_API_URL:-/butlers-api/api}

  # ── Layer 1b: Telegram bot connector ──────────────────────────────────
  connector-telegram-bot:
    build:
      context: .
      dockerfile: Dockerfile
    entrypoint: ["uv", "run", "python", "-m", "butlers.connectors.telegram_bot"]
    command: []
    environment:
      - CONNECTOR_PROVIDER=telegram
      - CONNECTOR_CHANNEL=telegram_bot
      - POSTGRES_HOST=postgres
      - POSTGRES_PORT=5432
      - POSTGRES_USER=butlers
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-butlers}
      - OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_EXPORTER_OTLP_ENDPOINT:-}
    networks: [db, backend]
    depends_on:
      migrations:
        condition: service_completed_successfully
    restart: unless-stopped

  # ── Layer 1b: Telegram user-client connector ──────────────────────────
  connector-telegram-user:
    build:
      context: .
      dockerfile: Dockerfile
    entrypoint: ["uv", "run", "python", "-m", "butlers.connectors.telegram_user_client"]
    command: []
    environment:
      - CONNECTOR_PROVIDER=telegram
      - CONNECTOR_CHANNEL=telegram_user_client
      - POSTGRES_HOST=postgres
      - POSTGRES_PORT=5432
      - POSTGRES_USER=butlers
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-butlers}
      - OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_EXPORTER_OTLP_ENDPOINT:-}
    networks: [db, backend]
    depends_on:
      migrations:
        condition: service_completed_successfully
    restart: unless-stopped

  # ── Layer 1b: Live listener connector (host audio devices) ────────────
  connector-live-listener:
    build:
      context: .
      dockerfile: Dockerfile
      args:
        EXTRAS: live-listener
    entrypoint: ["uv", "run", "python", "-m", "butlers.connectors.live_listener"]
    command: []
    environment:
      - CONNECTOR_PROVIDER=live-listener
      - CONNECTOR_CHANNEL=voice
      - CONNECTOR_HEALTH_PORT=40091
      - LIVE_LISTENER_DEVICES=${LIVE_LISTENER_DEVICES:-[{"name":"webcam","device":"hw:2,0"}]}
      - POSTGRES_HOST=postgres
      - POSTGRES_PORT=5432
      - POSTGRES_USER=butlers
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-butlers}
    networks: [db, backend]
    depends_on:
      migrations:
        condition: service_completed_successfully
    devices:
      - /dev/snd:/dev/snd
    restart: unless-stopped
    profiles: [audio]  # only start when audio hardware is present

  # ── Layer 2: OAuth gate (one-shot) ────────────────────────────────────
  oauth-gate:
    build:
      context: .
      dockerfile: Dockerfile
    entrypoint: ["uv", "run", "python", "scripts/oauth_gate.py"]
    command: []
    environment:
      - POSTGRES_HOST=postgres
      - POSTGRES_PORT=5432
      - POSTGRES_USER=butlers
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-butlers}
      - POSTGRES_DB=${POSTGRES_DB:-butlers}
      - OAUTH_GATE_TIMEOUT=${OAUTH_GATE_TIMEOUT:-0}
      - OAUTH_POLL_INTERVAL=${OAUTH_POLL_INTERVAL:-5}
      - SKIP_OAUTH_CHECK=${SKIP_OAUTH_CHECK:-false}
    networks: [db]
    depends_on:
      dashboard-api:
        condition: service_healthy
    volumes:
      - ./scripts:/app/scripts:ro

  # ── Layer 3: butlers up (main backend) ────────────────────────────────
  butlers-up:
    build:
      context: .
      dockerfile: Dockerfile
    command: ["up"]
    environment:
      - POSTGRES_HOST=postgres
      - POSTGRES_PORT=5432
      - POSTGRES_USER=butlers
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-butlers}
      - BUTLERS_SWITCHBOARD_URL=http://dashboard-api:41200
      - OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_EXPORTER_OTLP_ENDPOINT:-}
    volumes:
      - ./roster:/app/roster:ro
    networks: [db, backend]
    depends_on:
      oauth-gate:
        condition: service_completed_successfully
    restart: unless-stopped

  # ── Layer 3: Gmail connector (post-OAuth) ─────────────────────────────
  connector-gmail:
    build:
      context: .
      dockerfile: Dockerfile
    entrypoint: ["uv", "run", "python", "-m", "butlers.connectors.gmail"]
    command: []
    environment:
      - CONNECTOR_PROVIDER=gmail
      - CONNECTOR_CHANNEL=email
      - POSTGRES_HOST=postgres
      - POSTGRES_PORT=5432
      - POSTGRES_USER=butlers
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-butlers}
      - CONNECTOR_BUTLER_DB_NAME=${POSTGRES_DB:-butlers}
      - CONNECTOR_BUTLER_DB_SCHEMA=shared
      - BUTLER_SHARED_DB_NAME=${POSTGRES_DB:-butlers}
      - BUTLER_SHARED_DB_SCHEMA=shared
      - OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_EXPORTER_OTLP_ENDPOINT:-}
    networks: [db, backend]
    depends_on:
      oauth-gate:
        condition: service_completed_successfully
    restart: unless-stopped

  # ── Hotreload overrides (profile: hotreload) ──────────────────────────
  # When --profile hotreload is active, volume-mount source code into
  # services and run uv sync at container start for live changes.

  butlers-up-hotreload:
    extends:
      service: butlers-up
    profiles: [hotreload]
    entrypoint: ["sh", "-c", "uv sync --dev && uv run butlers up"]
    command: []
    volumes:
      - ./src:/app/src
      - ./roster:/app/roster:ro
      - ./pyproject.toml:/app/pyproject.toml:ro
      - uv_cache:/root/.cache/uv

  dashboard-api-hotreload:
    extends:
      service: dashboard-api
    profiles: [hotreload]
    entrypoint: ["sh", "-c", "uv sync --dev && uv run butlers dashboard --host 0.0.0.0 --port 41200"]
    command: []
    volumes:
      - ./src:/app/src
      - ./roster:/app/roster:ro
      - ./pyproject.toml:/app/pyproject.toml:ro
      - uv_cache:/root/.cache/uv

  frontend-dev-hotreload:
    extends:
      service: frontend-dev
    profiles: [hotreload]
    # frontend-dev already volume-mounts ./frontend in base compose

volumes:
  uv_cache:
```

**Step 2: Verify compose config parses**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet
```

**Step 3: Commit**

```bash
git add docker-compose.dev.yml
git commit -m "feat: add docker-compose.dev.yml with connectors, oauth gate, and backend"
```

---

## Task 5: Bind-Mounted Structured Logs

**Files:**
- Create: `scripts/dev_entrypoint.sh`
- Modify: `docker-compose.dev.yml`

Preserve the `logs/YYYYMMDD_HHMMSS/` directory structure from dev.sh by using a shared log volume with a timestamp-based run directory.

**Step 1: Write `scripts/dev_entrypoint.sh`**

```bash
#!/usr/bin/env bash
# Wrapper entrypoint for dev services that redirects stdout/stderr
# to the structured log directory while still streaming to docker logs.
#
# Usage (in compose): entrypoint: ["/app/scripts/dev_entrypoint.sh", "connectors/telegram_bot"]
#   $1 = log subdirectory path (e.g. "connectors/telegram_bot")
#   remaining args = the actual command to run
set -euo pipefail

LOG_SUBDIR="${1:?log subdirectory required}"
shift

# BUTLERS_LOG_RUN_DIR is set by the log-init service and shared via env/volume.
# Fall back to a timestamped dir if not set.
RUN_DIR="${BUTLERS_LOG_RUN_DIR:-/app/logs/$(date +%Y%m%d_%H%M%S)}"
LOG_DIR="${RUN_DIR}/${LOG_SUBDIR}"
mkdir -p "$LOG_DIR"

LOG_FILE="${LOG_DIR}/output.log"

# Tee to both docker stdout AND the log file.
exec "$@" 2>&1 | tee -a "$LOG_FILE"
```

**Step 2: Write a one-shot log-init service that creates the run dir + symlink**

Add to `docker-compose.dev.yml`:

```yaml
  # One-shot: create timestamped log directory and "latest" symlink
  log-init:
    image: alpine:3.19
    entrypoint: ["sh", "-c"]
    command:
      - |
        RUN_DIR="/logs/$(date +%Y%m%d_%H%M%S)"
        mkdir -p "$RUN_DIR"
        rm -f /logs/latest
        ln -s "$RUN_DIR" /logs/latest
        echo "$RUN_DIR" > /logs/.current_run_dir
        echo "Log directory: $RUN_DIR"
    volumes:
      - ./logs:/logs
```

**Step 3: Add log volume mount to all dev services**

Add to each service in `docker-compose.dev.yml` that should log:

```yaml
    volumes:
      - ./logs:/app/logs
```

And update entrypoints to use the wrapper:

```yaml
    entrypoint: ["/app/scripts/dev_entrypoint.sh", "connectors/telegram_bot", "uv", "run", "python", "-m", "butlers.connectors.telegram_bot"]
```

**Step 4: Test log creation**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up log-init
ls -la logs/latest/
```

**Step 5: Commit**

```bash
git add scripts/dev_entrypoint.sh docker-compose.dev.yml
git commit -m "feat: structured bind-mounted logs for dev compose"
```

---

## Task 6: Dev Launcher Script

**Files:**
- Create: `scripts/dev-compose.sh`

Thin wrapper replacing `dev.sh` that handles flag translation and tailscale prerequisite check.

**Step 1: Write `scripts/dev-compose.sh`**

```bash
#!/usr/bin/env bash
# Launch Butlers dev environment via Docker Compose.
# Replaces scripts/dev.sh (tmux-based) with compose orchestration.
#
# Usage:
#   ./scripts/dev-compose.sh                       # standard mode
#   ./scripts/dev-compose.sh --hotreload           # volume-mount source for live changes
#   ./scripts/dev-compose.sh --skip-oauth-check    # skip OAuth gate
#   ./scripts/dev-compose.sh --skip-tailscale-check
#   ./scripts/dev-compose.sh --audio               # include live-listener (needs /dev/snd)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

PROFILES=()
COMPOSE_ENV=()
SKIP_TAILSCALE=false

for arg in "$@"; do
  case "$arg" in
    --hotreload)           PROFILES+=(hotreload) ;;
    --audio)               PROFILES+=(audio) ;;
    --skip-oauth-check)    COMPOSE_ENV+=("SKIP_OAUTH_CHECK=true") ;;
    --skip-tailscale-check) SKIP_TAILSCALE=true ;;
    *)                     echo "Unknown flag: $arg" >&2; exit 1 ;;
  esac
done

# ── Tailscale prerequisite check ──────────────────────────────────────
if [ "$SKIP_TAILSCALE" = "false" ]; then
  if ! command -v tailscale &>/dev/null; then
    echo "ERROR: tailscale CLI not found. Install from https://tailscale.com/download" >&2
    echo "  Or skip: $0 --skip-tailscale-check" >&2
    exit 1
  fi
  ts_state=$(tailscale status --json 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('BackendState','Unknown'))" \
    2>/dev/null || echo "Unknown")
  if [ "$ts_state" = "NeedsLogin" ] || [ "$ts_state" = "Stopped" ]; then
    echo "ERROR: tailscale not authenticated (state: ${ts_state}). Run: tailscale up" >&2
    exit 1
  fi
  echo "Tailscale: OK (state: ${ts_state})"
fi

# ── Build compose command ─────────────────────────────────────────────
CMD=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)
for p in "${PROFILES[@]}"; do
  CMD+=(--profile "$p")
done

# Export env overrides
for e in "${COMPOSE_ENV[@]}"; do
  export "${e?}"
done

echo "Starting Butlers dev stack..."
echo "  Profiles: ${PROFILES[*]:-default}"
echo "  Compose:  ${CMD[*]} up"
echo ""

"${CMD[@]}" up --build "$@"
```

**Step 2: Make executable**

```bash
chmod +x scripts/dev-compose.sh
```

**Step 3: Commit**

```bash
git add scripts/dev-compose.sh
git commit -m "feat: add dev-compose.sh launcher replacing tmux-based dev.sh"
```

---

## Task 7: Dockerfile — Support `--extra` Build Arg

**Files:**
- Modify: `Dockerfile`

The live-listener connector needs `uv sync --extra live-listener`. Add a build arg.

**Step 1: Add EXTRAS build arg to Dockerfile**

After the `WORKDIR /app` line, before the `COPY` lines:

```dockerfile
# Optional: extra dependency groups (e.g. "live-listener" for audio connector)
ARG EXTRAS=""
```

Change the install line from:
```dockerfile
RUN uv sync --no-dev
```
To:
```dockerfile
RUN if [ -n "$EXTRAS" ]; then \
      uv sync --no-dev --extra "$EXTRAS"; \
    else \
      uv sync --no-dev; \
    fi
```

**Step 2: Also copy `scripts/` into the image** (needed for oauth_gate.py)

Add after the alembic COPY:
```dockerfile
COPY scripts/ scripts/
```

**Step 3: Test build**

```bash
docker compose build migrations
```

**Step 4: Commit**

```bash
git add Dockerfile
git commit -m "feat: Dockerfile EXTRAS build arg and scripts/ copy"
```

---

## Task 8: Integration Test — Full Stack Smoke

**Files:**
- Create: `tests/integration/test_compose_dev_smoke.py`

A pytest test that brings up the compose stack (minus audio/OAuth-gated services), waits for health, and tears down.

**Step 1: Write the smoke test**

```python
"""Smoke test: verify docker-compose dev overlay starts and services become healthy.

Requires Docker daemon. Skipped in CI unless COMPOSE_SMOKE=1 is set.
"""

import os
import subprocess
import time

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("COMPOSE_SMOKE") != "1",
    reason="Set COMPOSE_SMOKE=1 to run compose integration tests",
)

COMPOSE_CMD = [
    "docker", "compose",
    "-f", "docker-compose.yml",
    "-f", "docker-compose.dev.yml",
]


@pytest.fixture(scope="module")
def compose_stack():
    """Bring up the stack with OAuth check skipped, tear down after."""
    env = {**os.environ, "SKIP_OAUTH_CHECK": "true"}
    subprocess.run(
        [*COMPOSE_CMD, "up", "-d", "--build",
         "postgres", "migrations", "dashboard-api", "frontend-dev",
         "connector-telegram-bot"],
        check=True,
        env=env,
        timeout=180,
    )
    yield
    subprocess.run([*COMPOSE_CMD, "down", "-v", "--timeout", "10"], check=False)


def test_postgres_healthy(compose_stack):
    """Postgres should be accepting connections."""
    result = subprocess.run(
        [*COMPOSE_CMD, "exec", "postgres", "pg_isready", "-U", "butlers"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0


def test_dashboard_healthy(compose_stack):
    """Dashboard API /health should return 200."""
    for _ in range(30):
        try:
            result = subprocess.run(
                ["curl", "-sf", "http://localhost:41200/health"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(2)
    pytest.fail("Dashboard API did not become healthy within 60s")


def test_migrations_completed(compose_stack):
    """Migrations service should have exited 0."""
    result = subprocess.run(
        [*COMPOSE_CMD, "ps", "-a", "--format", "json", "migrations"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert "exited" in result.stdout.lower() or "Exit 0" in result.stdout
```

**Step 2: Run it (optional — requires docker)**

```bash
COMPOSE_SMOKE=1 uv run pytest tests/integration/test_compose_dev_smoke.py -v --timeout=300
```

**Step 3: Commit**

```bash
git add tests/integration/test_compose_dev_smoke.py
git commit -m "test: compose dev overlay smoke test"
```

---

## Task 9: Documentation and Cleanup

**Files:**
- Modify: `scripts/dev.sh` (add deprecation notice at top)
- Modify: `docker-compose.yml` header comment

**Step 1: Add deprecation notice to `scripts/dev.sh`**

At the top after the shebang:

```bash
# ⚠️  DEPRECATED: Use scripts/dev-compose.sh for Docker Compose-based dev environment.
# This tmux-based script is preserved for reference and will be removed in a future release.
```

**Step 2: Update `docker-compose.yml` header to mention dev overlay**

Replace the USAGE block:

```yaml
# USAGE:
#
# Development mode (full stack with connectors):
#   ./scripts/dev-compose.sh
#   ./scripts/dev-compose.sh --hotreload      # live code changes
#   ./scripts/dev-compose.sh --skip-oauth-check
#
# Development mode (just postgres for local butlers):
#   docker compose up -d postgres
#   butlers up
#
# Production mode (all services containerized):
#   cp .env.example .env
#   docker compose up -d
```

**Step 3: Commit**

```bash
git add scripts/dev.sh docker-compose.yml
git commit -m "docs: update compose usage, deprecate tmux-based dev.sh"
```

---

## Execution Order Summary

| Task | What | Depends on |
|------|------|-----------|
| 1 | OAuth gate script | — |
| 2 | Network segregation + dashboard healthcheck | — |
| 3 | Migrations init service | Task 2 |
| 4 | Dev overlay (connectors, gate, backend) | Tasks 1, 2, 3 |
| 5 | Structured logs | Task 4 |
| 6 | Launcher script | Task 4 |
| 7 | Dockerfile EXTRAS + scripts copy | Task 1 |
| 8 | Smoke test | Tasks 4, 7 |
| 9 | Docs + deprecation | Task 6 |

Tasks 1, 2, and 7 can be done in parallel. Tasks 5 and 6 can be done in parallel after Task 4.
