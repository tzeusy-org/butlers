# Docker Deployment

> **Purpose:** Document the Docker Compose setup for running Butlers in development and production modes.
> **Audience:** Operators, DevOps engineers, anyone deploying Butlers.
> **Prerequisites:** [Environment Config](environment-config.md), Docker and Docker Compose installed.

## Overview

Butlers provides a `docker-compose.yml` at the repository root that defines all services needed for a complete deployment. The setup supports two modes: development (local butler processes with containerized DB and observability) and production (all services containerized). The `Dockerfile` builds a Python 3.12 image with Node.js 22, the `claude-code` CLI, and `uv` for dependency management.

## Quick Start

**Development mode** (local butler processes, containerized database):

```bash
docker compose up -d postgres
butlers up
```

**Production mode** (everything containerized):

```bash
cp .env.example .env
# Edit .env with your configuration
docker compose up -d
```

## Dockerfile

The production image is built on `python:3.12-slim`:

1. **System dependencies**: curl, ca-certificates, gnupg.
2. **Node.js 22** via NodeSource -- required by the `claude-code` CLI.
3. **claude-code** installed globally via `npm install -g claude-code`.
4. **uv** package manager via the official install script.
5. **Project files**: `pyproject.toml`, `src/`, `alembic.ini`, `alembic/`.
6. **Production dependencies** via `uv sync --no-dev`.

Entrypoint: `uv run butlers`. Default command: `run --config /etc/butler`.

## Services

### PostgreSQL (`postgres`)

| Setting | Value |
|---------|-------|
| Image | `pgvector/pgvector:pg17` |
| Port (host) | `54320` |
| Port (container) | `5432` |
| Default user | `butlers` |
| Auth method | `trust` |
| Max connections | `200` |
| Volume | `butlers_postgres_data` (external) |

Healthcheck: `pg_isready -U butlers` every 5 seconds, 5 retries. All butler services depend on Postgres being healthy.

### Butler Services

| Service | Port | Config Mount |
|---------|------|-------------|
| `switchboard` | 41100 | `roster/switchboard` -> `/etc/butler:ro` |
| `general` | 41101 | `roster/general` -> `/etc/butler:ro` |
| `relationship` | 41102 | `roster/relationship` -> `/etc/butler:ro` |
| `health` | 41103 | `roster/health` -> `/etc/butler:ro` |

All butler containers use the same Docker image, mount their roster directory as read-only, and run `butlers run --config /etc/butler`.

### Dashboard API (`dashboard-api`)

| Setting | Value |
|---------|-------|
| Port | `41200` |
| Config mount | `roster/` -> `/app/roster:ro` |
| Command | `dashboard --host 0.0.0.0 --port 41200` |

### Frontend Dev Server (`frontend-dev`)

| Setting | Value |
|---------|-------|
| Profile | `dev` (must be explicitly activated) |
| Port | `41173` |
| Image | `node:22-slim` |

Start with: `docker compose --profile dev up`

## Environment Variables

All butler containers share:

```yaml
POSTGRES_HOST: postgres
POSTGRES_PORT: 5432
POSTGRES_USER: butlers
POSTGRES_PASSWORD: butlers
OTEL_EXPORTER_OTLP_ENDPOINT: http://otel.parrot-hen.ts.net:4318
```

## Volumes

| Volume | Type | Purpose |
|--------|------|---------|
| `butlers_postgres_data` | External | Persistent PostgreSQL data (must pre-exist) |
| `frontend_node_modules` | Anonymous | Node.js dependencies for frontend dev |

Create the external volume before first use: `docker volume create butlers_postgres_data`

## Aspirational Services

Butlers in the roster not yet in `docker-compose.yml`: education, finance, home, messenger, travel.

## Related Pages

- [Environment Config](environment-config.md) -- Full environment variable reference
- [Grafana Monitoring](grafana-monitoring.md) -- Observability setup
- [Troubleshooting](troubleshooting.md) -- Common deployment issues
