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

## Related Pages

- [Credential Store](../data_and_storage/credential-store.md) -- DB-first secret resolution
- [Operations Config](../operations/environment-config.md) -- Config file reference
- [OAuth Flows](oauth-flows.md) -- Google OAuth credential bootstrap
