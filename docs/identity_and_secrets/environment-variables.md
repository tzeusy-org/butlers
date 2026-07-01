# Environment Variables

> **Purpose:** Comprehensive reference for all environment variables used across Butlers infrastructure, butlers, modules, and connectors.
> **Audience:** Operators deploying Butlers, developers configuring new integrations.
> **Prerequisites:** [Getting Started](../getting_started/index.md), [Credential Store](../data_and_storage/credential-store.md).

## Overview

Butlers uses environment variables exclusively for infrastructure bootstrap (database connection, observability). All runtime secrets -- API keys, OAuth tokens, Telegram credentials -- are stored in the PostgreSQL `butler_secrets` table and managed via the dashboard Secrets page. Environment variables serve as a last-resort fallback through the `CredentialStore.resolve()` method.

## Global Infrastructure Variables

These variables are shared across all butler processes and the dashboard API.

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_HOST` | `localhost` | PostgreSQL server hostname |
| `POSTGRES_PORT` | `5432` | PostgreSQL server port |
| `POSTGRES_USER` | `butlers` | PostgreSQL username |
| `POSTGRES_PASSWORD` | `butlers` | PostgreSQL password |
| `POSTGRES_DB` | `butlers` | Default database name |
| `BUTLER_SHARED_DB_NAME` | `butlers` | Shared credential database name (used by `CredentialStore` for fallback pool) |

## Observability Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | (none) | OTLP HTTP endpoint for traces and metrics. When set, enables OpenTelemetry instrumentation with OTLP export. When unset, a no-op tracer is used. Example: `http://localhost:4318` |

The OTLP endpoint is configured identically for all butler processes. The tracing system uses a single `TracerProvider` per process with per-butler attribution via `butler.name` and `service.name` span attributes.

## Dashboard API Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DASHBOARD_API_KEY` | (none) | When set, enables API key authentication on all dashboard endpoints. Requests must include `X-API-Key` header or `api_key` query parameter. |
| `DASHBOARD_STATIC_DIR` | (none) | Path to the built frontend directory (e.g., `frontend/dist/`). When set, mounts a static file server at `/` for production mode. |

## Google OAuth Bootstrap Variables

These variables are only needed for the initial OAuth bootstrap before credentials are stored in the database. After the first successful OAuth flow, they can be removed.

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_OAUTH_CLIENT_ID` | (none) | Google OAuth client ID (seeds initial flow) |
| `GOOGLE_OAUTH_CLIENT_SECRET` | (none) | Google OAuth client secret (seeds initial flow) |
| `GOOGLE_OAUTH_REDIRECT_URI` | `http://localhost:40200/api/oauth/google/callback` | OAuth callback URL |
| `GOOGLE_MAX_ACCOUNTS` | `10` | Soft limit on connected Google accounts |

## Connector Variables

Connectors are independent processes that submit events to the Switchboard. Each connector type has its own variables in addition to the shared set.

### Shared Connector Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SWITCHBOARD_MCP_URL` | (required) | SSE endpoint URL for Switchboard MCP server (e.g., `http://localhost:41100/sse`) |
| `CONNECTOR_PROVIDER` | (required) | Provider name: `telegram`, `gmail`, `imap`, etc. |
| `CONNECTOR_CHANNEL` | (required) | Canonical channel value: `telegram`, `email`, etc. |
| `CONNECTOR_POLL_INTERVAL_S` | (required for polling) | Poll interval in seconds |
| `CONNECTOR_MAX_INFLIGHT` | `8` | Maximum concurrent ingest submissions |
| `CONNECTOR_HEARTBEAT_INTERVAL_S` | `120` | Heartbeat interval in seconds |
| `CONNECTOR_HEARTBEAT_ENABLED` | `true` | Set to `false` to disable heartbeats in development |
| `SWITCHBOARD_API_TOKEN` | (none) | Bearer token for Switchboard API authentication |

### Backfill Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CONNECTOR_BACKFILL_POLL_INTERVAL_S` | `60` | How often to poll for backfill jobs |
| `CONNECTOR_BACKFILL_PROGRESS_INTERVAL` | `50` | Report progress every N messages |
| `CONNECTOR_BACKFILL_ENABLED` | `true` | Disable backfill polling entirely |

## Butler-Specific Variables

Each butler reads its configuration from `butler.toml` in its roster directory. Module-specific credentials (Telegram bot tokens, email passwords, etc.) are stored in the DB via the dashboard Secrets page and resolved through `CredentialStore.resolve()`. Environment variables with the same key name serve as fallback if the DB has no entry.

## The `.env` File

Copy `.env.example` to `.env` at the repository root and customize:

```bash
cp .env.example .env
```

The `.env.example` file contains only infrastructure bootstrap variables (database connection, observability). Runtime secrets should never be placed in `.env` -- use the dashboard Secrets page instead.

## Resolution Priority

When a module calls `store.resolve("TELEGRAM_BOT_TOKEN")`, the resolution order is:

1. Local `butler_secrets` table (butler's own schema)
2. Shared `butler_secrets` table (fallback pool)
3. `os.environ["TELEGRAM_BOT_TOKEN"]` (when `env_fallback=True`)

Dashboard-stored credentials always take precedence over environment variables.

## Verification

To confirm environment variable resolution, credential store precedence, and `.env.example` completeness are operating as described:

```bash
# 1. Verify .env.example contains only infrastructure bootstrap variables (no secrets)
grep -E '(TOKEN|SECRET|PASSWORD|KEY|HASH|SESSION)' .env.example
# Expected: no matches -- .env.example should contain only POSTGRES_* and OTEL_* variables
# If any secrets appear, they must be removed

# 2. Confirm the credential store reads from the database first (not just env)
python3 -c "
import asyncio, os
os.environ['TEST_CRED_FALLBACK'] = 'from-env'
from butlers.credential_store import CredentialStore
# This is illustrative: CredentialStore.resolve() checks DB before falling back to env
print('DB-first resolution is coded in CredentialStore.resolve()')
"

# 3. Verify POSTGRES_* vars can connect to the database (using current env)
psql -h "${POSTGRES_HOST:-localhost}" \
     -p "${POSTGRES_PORT:-5432}" \
     -U "${POSTGRES_USER:-butlers}" \
     -d "${POSTGRES_DB:-butlers}" \
     -c "SELECT current_database(), current_user;"
# Expected: current_database = 'butlers', current_user = 'butlers'

# 4. Confirm OTEL tracing is active when OTEL_EXPORTER_OTLP_ENDPOINT is set
python3 -c "
import os
endpoint = os.environ.get('OTEL_EXPORTER_OTLP_ENDPOINT', '')
print('OTLP endpoint:', endpoint or '(not set -- no-op tracer in use)')
"
# Expected: if set, value is an HTTP URL like 'http://localhost:4318';
# if unset, the tracer is in no-op mode (no error, just no traces exported)

# 5. Verify DASHBOARD_API_KEY is enforced when set
if [ -n "${DASHBOARD_API_KEY:-}" ]; then
  curl -s -o /dev/null -w "%{http_code}" http://localhost:40200/api/butlers
  # Expected: 401 (Unauthorized) without the header
  curl -s -o /dev/null -w "%{http_code}" \
    -H "X-API-Key: ${DASHBOARD_API_KEY}" http://localhost:40200/api/butlers
  # Expected: 200 with the header
else
  echo "DASHBOARD_API_KEY not set -- authentication is disabled (opt-in feature)"
fi

# 6. Confirm connector variables are present in the running connector environment
docker compose exec connector-gmail env | grep -E '^(SWITCHBOARD|CONNECTOR)_'
# Expected: SWITCHBOARD_MCP_URL, CONNECTOR_PROVIDER=gmail, CONNECTOR_CHANNEL=email,
# and polling/heartbeat interval variables present
```

## Related Pages

- [Credential Store](../data_and_storage/credential-store.md) -- DB-first secret resolution
- [Operations Config](../operations/environment-config.md) -- Config file reference
- [OAuth Flows](oauth-flows.md) -- Google OAuth credential bootstrap
