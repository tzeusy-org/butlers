# Troubleshooting

> **Purpose:** Common failures, debugging techniques, and health check commands for Butlers.
> **Audience:** Operators, developers debugging issues in development or production.
> **Prerequisites:** [Docker Deployment](docker-deployment.md), [Environment Config](environment-config.md).

## Overview

This page covers the most common failure modes encountered when running Butlers, along with diagnostic commands and resolution steps. Issues typically fall into four categories: database connectivity, missing credentials, binary/dependency problems, and butler communication failures.

## Database Connection Failures

### Symptom: "connection refused" or "could not connect to server"

**Cause:** PostgreSQL is not running or not reachable at the configured host/port.

**Diagnosis:**
```bash
# Check if Postgres container is running
docker compose ps postgres

# Test connectivity directly
pg_isready -h localhost -p 54320 -U butlers

# Check container logs
docker compose logs postgres --tail=50
```

**Resolution:**
```bash
# Start the database
docker compose up -d postgres

# Wait for healthcheck to pass
docker compose ps  # Should show "healthy"
```

### Symptom: "database does not exist" or migration errors

**Cause:** Butler schema has not been initialized or migrations are out of date.

**Resolution:**
```bash
# Run all migrations
butlers migrate

# Or for a specific butler
butlers migrate --butler switchboard
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
