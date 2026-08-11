# Troubleshooting

> **Purpose:** Common failures, debugging techniques, and health check commands for Butlers.
> **Audience:** Operators, developers debugging issues in development or production.
> **Prerequisites:** [Docker Deployment](docker-deployment.md), [Environment Config](environment-config.md).

## Overview

This page covers the most common failure modes encountered when running Butlers, along with diagnostic commands and resolution steps. Issues typically fall into four categories: database connectivity, missing credentials, binary/dependency problems, and butler communication failures.

## Database Connection Failures

### Symptom: "connection refused" or "could not connect to server"

**Cause:** the external PostgreSQL is unreachable at the configured host/port.
The database is **not** a Compose service; it lives on a remote host set by
`POSTGRES_HOST` / `POSTGRES_PORT` in the `.env.<mode>` file that
`scripts/compose.sh` sources (`.env.dev` by default, `.env.prod` with `--prod`).

**Diagnosis:**
```bash
# Load the target DB config, then probe the real host directly.
set -a && . ./.env.dev && set +a         # or ./.env.prod
pg_isready -h "$POSTGRES_HOST" -p "${POSTGRES_PORT:-5432}" -U "${POSTGRES_USER:-butlers}"

# If the host is on the tailnet (e.g. butlers-db-dev), confirm it resolves/pings.
tailscale ping "$POSTGRES_HOST"
```

**Resolution:**
- Confirm `POSTGRES_HOST` / `POSTGRES_PASSWORD` are set correctly in the active
  `.env.<mode>` file, and that the remote database host is up and reachable
  (on the tailnet, and in `ALLOWED_TAILNET_HOSTS` if the egress firewall is on).
- **Naming caution:** `butlers-db-dev` (`.env.dev`, the default target) is the
  **LIVE** system with real data; `butlers-db` (`.env.prod`) is the other host.
  The mode label tracks the env file, not which host is live, so confirm the
  target before any destructive step.

### Symptom: "database does not exist" or migration errors

**Cause:** Butler schema has not been initialized or migrations are out of date.

**Resolution:**
```bash
# Run migrations for all butler schemas
butlers db migrate

# Or for a specific butler schema
butlers db migrate --only switchboard
```

### Symptom: Slow queries or high latency

**Diagnosis:**
```bash
# Check active connections
psql -h localhost -p 54320 -U butlers -c "SELECT count(*) FROM pg_stat_activity;"

# Check for long-running queries
psql -h localhost -p 54320 -U butlers -c \
  "SELECT pid, now() - query_start AS duration, query FROM pg_stat_activity WHERE state = 'active' ORDER BY duration DESC LIMIT 10;"
```

The `docker-compose.yml` sets `max_connections=200`. If connection exhaustion is suspected, check for leaked pools.

## Missing Credentials

### Symptom: Butler starts but cannot spawn LLM CLI

**Cause:** CLI runtime (Claude, Codex, OpenCode) is not authenticated.

**Diagnosis:**
```bash
# Check CLI auth health via dashboard API
curl http://localhost:41200/api/cli-auth/health

# Check if binary is available
which claude
which codex
which opencode
```

**Resolution:** Use the dashboard Settings page to initiate a device-code auth flow, or authenticate manually:
```bash
claude login
codex login --device-auth
opencode auth login -p openai
```

### Symptom: "required environment variable missing" at startup

**Cause:** Butler's `butler.toml` declares a `required` env var that is not set.

**Diagnosis:** Check the butler's TOML config for `[butler.env]` required entries.

**Resolution:** Set the variable in your `.env` file or via the dashboard Secrets page.

### Symptom: Google OAuth fails or calendar/contacts not syncing

**Cause:** Google OAuth credentials not bootstrapped.

**Resolution:**
1. Set `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` in `.env`, or
2. Navigate to the dashboard OAuth settings and enter them there.
3. Complete the OAuth flow through the dashboard.

## Binary Not Found

### Symptom: "Binary 'codex' not found on PATH"

**Cause:** The LLM CLI runtime binary is not installed or not on PATH.

**Resolution:** Install the required CLI:
```bash
# Claude Code
npm install -g @anthropic-ai/claude-code

# Codex
npm install -g @openai/codex

# OpenCode
go install github.com/opencode-ai/opencode@latest
```

### Symptom: Docker build fails

**Diagnosis:**
```bash
docker compose build --no-cache <service>
docker compose logs <service> --tail=100
```

## Butler Communication Failures

### Symptom: "Butler unreachable" (502) in dashboard

**Cause:** A butler's MCP server is down or not responding.

**Diagnosis:**
```bash
# Check if the butler process is running
docker compose ps

# Check butler logs
docker compose logs <butler-name> --tail=100

# Test MCP endpoint
curl http://localhost:<port>/health
```

### Symptom: Messages not being routed

**Cause:** Switchboard cannot reach target butlers, or butler is quarantined.

**Diagnosis:**
```bash
# Check Switchboard routing log via dashboard
curl http://localhost:41200/api/switchboard/routing-log?limit=20

# Check butler registry
curl http://localhost:41200/api/switchboard/registry
```

## Health Check Commands

```bash
# Dashboard API health
curl http://localhost:41200/api/health

# PostgreSQL health
pg_isready -h localhost -p 54320 -U butlers

# All services status
docker compose ps

# Butler-specific logs
docker compose logs <butler-name> --tail=50 --follow

# CLI auth status for all providers
curl http://localhost:41200/api/cli-auth/health
```

## Observability Debugging

### No traces appearing in Grafana

1. Verify `OTEL_EXPORTER_OTLP_ENDPOINT` is set and reachable.
2. Check for quotes in the value (the code strips them, but double-check).
3. Verify the endpoint accepts HTTP OTLP (not gRPC -- Butlers uses the HTTP exporter).
4. Check butler startup logs for "Telemetry initialized" or "OTEL_EXPORTER_OTLP_ENDPOINT not set".

### Trace context not propagating between butlers

Ensure the spawned LLM CLI subprocess inherits the `TRACEPARENT` environment variable. Check `get_traceparent_env()` in telemetry.py.

## Test-Related Issues

### Testcontainer Docker errors

The `conftest.py` includes resilient startup and teardown patches. Clean up with `docker container prune -f`.

### pytest-xdist port conflicts

Port conflicts during parallel execution are suppressed via `filterwarnings`. Run with `-n 1` to isolate.

## Related Pages

- [Docker Deployment](docker-deployment.md) -- Service configuration
- [Grafana Monitoring](grafana-monitoring.md) -- Observability stack
- [Environment Config](environment-config.md) -- Configuration reference
- [CLI Runtime Auth](../identity_and_secrets/cli-runtime-auth.md) -- CLI authentication
