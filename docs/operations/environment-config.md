# Environment Configuration

> **Purpose:** Document the configuration files, secrets directories, and environment setup for Butlers.
> **Audience:** Operators, developers setting up local or production environments.
> **Prerequisites:** [Docker Deployment](docker-deployment.md).

## Overview

Butlers separates infrastructure configuration (database, telemetry) from application secrets (API keys, OAuth tokens). Infrastructure settings live in environment variables and `.env` files. Application secrets are managed through the dashboard UI and stored in the database -- environment variables serve only as bootstrap fallbacks.

## Configuration Files

### `.env.example`

The reference template for environment setup. Copy it to `.env` and fill in actual values:

```bash
cp .env.example .env
```

The example file documents that only infrastructure variables need to be set here:

```bash
# Optional: PostgreSQL connection (defaults shown)
# POSTGRES_HOST=localhost
# POSTGRES_PORT=5432
# POSTGRES_USER=butlers
# POSTGRES_PASSWORD=your-db-password

# Optional: OpenTelemetry exporter endpoint
# OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318

# Optional: Google OAuth app credentials (bootstrap only)
# GOOGLE_OAUTH_CLIENT_ID=your-oauth-client-id
# GOOGLE_OAUTH_CLIENT_SECRET=your-oauth-client-secret
# GOOGLE_OAUTH_REDIRECT_URI=http://localhost:40200/api/oauth/google/callback
```

All runtime secrets (API keys, Telegram tokens, email passwords) are managed through the dashboard Secrets page, not the `.env` file.

## Secrets Directory Structure

The development startup script (`dev.sh`) sources environment files from specific paths:

```
/secrets/.dev.env                         # Global dev secrets
secrets/connectors/telegram_bot           # BUTLER_TELEGRAM_TOKEN, etc.
secrets/connectors/telegram_user_client   # Telegram user-client credentials
secrets/connectors/gmail                  # Gmail connector credentials
```

Missing files are tolerated -- the corresponding services will fail to start without their credentials, but other services remain unaffected. The `secrets/` directory is entirely gitignored.

## Credential Resolution Order

Butlers uses a DB-first credential resolution model:

1. **Database** (`butler_secrets` table) -- highest priority
2. **Database** (fallback/shared pools)
3. **Environment variable** -- lowest priority (only when `env_fallback=True`)

Credentials stored via the dashboard always override environment variables.

## Butler-Specific Configuration

Each butler's configuration lives in its `roster/{butler}/` directory:

```
roster/{butler}/
  butler.toml       # Identity, schedule, modules, port
  MANIFESTO.md      # Public-facing purpose and value proposition
  CLAUDE.md         # System prompt / personality
  AGENTS.md         # Runtime notes
  api/              # Optional dashboard API routes
  .agents/skills/   # Skills available to runtime instances
```

## Infrastructure Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_HOST` | `localhost` | PostgreSQL hostname |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_USER` | `butlers` | PostgreSQL username |
| `POSTGRES_PASSWORD` | `butlers` | PostgreSQL password |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | -- | OTLP HTTP endpoint for traces/metrics |
| `DASHBOARD_API_KEY` | -- | API key for dashboard auth (unset = no auth) |
| `DASHBOARD_STATIC_DIR` | -- | Path to built frontend for SPA serving |

## Connector Variables

| Variable | Used By | Description |
|----------|---------|-------------|
| `SWITCHBOARD_MCP_URL` | All connectors | SSE endpoint for Switchboard MCP server |
| `CONNECTOR_PROVIDER` | All connectors | Provider name (`telegram`, `gmail`, etc.) |
| `CONNECTOR_CHANNEL` | All connectors | Canonical channel (`telegram`, `email`, etc.) |
| `CONNECTOR_POLL_INTERVAL_S` | Polling connectors | Poll interval in seconds |
| `CONNECTOR_MAX_INFLIGHT` | All connectors | Ingest concurrency cap (default: 8) |
| `CONNECTOR_HEARTBEAT_INTERVAL_S` | All connectors | Heartbeat interval (default: 120) |
| `SWITCHBOARD_API_TOKEN` | All connectors | Bearer token for Switchboard auth |

## Service Ports

| Service | Port |
|---------|------|
| Switchboard | 41100 |
| General | 41101 |
| Relationship | 41102 |
| Health | 41103 |
| Messenger | 41104 |
| Dashboard API | 41200 |
| Frontend (dev) | 41173 |
| PostgreSQL | 5432 (54320 in Docker) |

## Development vs Production

| Concern | Development | Production |
|---------|-------------|------------|
| Database | `docker compose up -d postgres` | Managed PostgreSQL or compose |
| Secrets | `secrets/` files + `.env` | Dashboard UI + credential store |
| Telemetry | Optional (no-op if unset) | OTLP endpoint configured |
| Frontend | Vite dev server on `:41173` | Static files via `DASHBOARD_STATIC_DIR` |
| Auth | Disabled (no `DASHBOARD_API_KEY`) | API key required |

## Related Pages

- [Environment Variables](../identity_and_secrets/environment-variables.md) -- Full variable reference by category
- [Docker Deployment](docker-deployment.md) -- Container configuration
- [CLI Runtime Auth](../identity_and_secrets/cli-runtime-auth.md) -- CLI credential setup
