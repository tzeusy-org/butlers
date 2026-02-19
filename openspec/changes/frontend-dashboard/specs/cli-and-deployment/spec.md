# CLI and Deployment (Delta)

Delta spec for the `cli-and-deployment` capability. Adds the `butlers dashboard` CLI command and Docker Compose services for the dashboard API and frontend dev server.

---

## ADDED Requirements

### Requirement: butlers dashboard command

The `butlers dashboard` CLI command SHALL start the FastAPI dashboard API server via uvicorn. The command MUST accept `--host` and `--port` options to configure the listening address.

#### Scenario: Start dashboard with default settings

- **WHEN** `butlers dashboard` is invoked with no arguments
- **THEN** the CLI MUST start the FastAPI dashboard API server via uvicorn
- **AND** the server MUST listen on `0.0.0.0:40200` (the default host and port)
- **AND** the CLI MUST log a message indicating the dashboard is running and the URL it is accessible at

#### Scenario: Start dashboard with custom host and port

- **WHEN** `butlers dashboard --host 127.0.0.1 --port 9000` is invoked
- **THEN** the CLI MUST start the dashboard API server listening on `127.0.0.1:9000`
- **AND** the server MUST NOT listen on the default `0.0.0.0:40200`

#### Scenario: Dashboard requires DATABASE_URL

- **WHEN** `butlers dashboard` is invoked
- **THEN** the dashboard API server MUST use the `DATABASE_URL` environment variable (or the default `postgres://butlers:butlers@localhost/postgres`) to connect to PostgreSQL for multi-butler database access
- **AND** if the database is unreachable the server MUST log an error but MAY still start (for health-check endpoints to report unhealthy status)

#### Scenario: Dashboard command appears in CLI help

- **WHEN** `butlers --help` is invoked
- **THEN** the `dashboard` command MUST be listed alongside existing commands (`up`, `run`, `list`, `init`)
- **AND** `butlers dashboard --help` MUST display usage information including the `--host` and `--port` options

---

### Requirement: Dashboard docker-compose service

The `docker-compose.yml` SHALL include a `dashboard-api` service that runs the dashboard API server.

#### Scenario: dashboard-api service definition

- **WHEN** `docker compose up -d dashboard-api` is invoked
- **THEN** a `dashboard-api` service MUST start using the same Docker image as butler services
- **AND** the service MUST run `butlers dashboard` as its command
- **AND** the service MUST expose port `40200` mapped to the container's port `40200`
- **AND** the service MUST depend on the `postgres` service with condition `service_healthy`

#### Scenario: dashboard-api environment variables

- **WHEN** the `dashboard-api` service starts
- **THEN** the `DATABASE_URL` environment variable MUST be set to the PostgreSQL connection string for the compose network
- **AND** the `ANTHROPIC_API_KEY` environment variable MUST be passed through from the host

#### Scenario: dashboard-api mounts butler config directory

- **WHEN** the `dashboard-api` service starts
- **THEN** the `butlers/` config directory MUST be mounted into the container (read-only)
- **AND** the dashboard API MUST be able to discover butler configurations by scanning the mounted directory

#### Scenario: dashboard-api starts after postgres is healthy

- **WHEN** `docker compose up -d` is invoked
- **THEN** the `dashboard-api` service MUST NOT start until the `postgres` service healthcheck passes
- **AND** this is consistent with the existing butler service dependency behavior

---

### Requirement: Frontend docker-compose service

The `docker-compose.yml` SHALL include an optional `frontend` service for local frontend development.

#### Scenario: frontend service definition

- **WHEN** `docker compose up -d frontend` is invoked
- **THEN** a `frontend` service MUST start using the `node:22-slim` image
- **AND** the service MUST run `npm run dev` as its command
- **AND** the service MUST expose port `40173` mapped to the container's port `40173`

#### Scenario: frontend service mounts source code

- **WHEN** the `frontend` service starts
- **THEN** the `frontend/` directory MUST be mounted into the container
- **AND** the Vite dev server MUST serve the frontend application with hot module replacement enabled

#### Scenario: frontend service is optional

- **WHEN** `docker compose up -d` is invoked without explicitly naming the `frontend` service
- **THEN** the `frontend` service SHOULD NOT start by default (using a compose profile such as `dev`)
- **AND** the dashboard API and butler services MUST function without the frontend service running

#### Scenario: frontend connects to dashboard API

- **WHEN** the `frontend` service is running alongside the `dashboard-api` service
- **THEN** the frontend application MUST be configured to proxy or connect to the dashboard API at its compose network address
- **AND** API requests from the frontend MUST reach the `dashboard-api` service
