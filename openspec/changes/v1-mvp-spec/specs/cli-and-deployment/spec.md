# CLI and Deployment Specification

The `butlers` CLI is the primary interface for running the Butlers AI agent framework. Built with Click, it provides commands for starting butlers in dev or production mode, listing discovered butlers, and scaffolding new butler configurations. The same codebase supports two deployment modes: dev mode (single process, all butlers in one asyncio event loop) and production mode (one butler daemon per container, orchestrated via docker-compose). Both modes use MCP-over-HTTP (SSE) for inter-butler communication, so behavior is identical regardless of process topology.

---

## ADDED Requirements

### Requirement: butlers up (Dev Mode)

The `butlers up` command SHALL start all discovered butlers in a single process, running concurrently within one asyncio event loop. It MUST discover all `butler.toml` files under the `butlers/` subdirectory, provision per-butler databases, apply Alembic migrations, and start all MCP servers concurrently on their configured ports.

The command SHALL accept an optional `--only` flag with a comma-separated list of butler names to start a subset of the discovered butlers.

#### Scenario: Start all butlers with no arguments

WHEN `butlers up` is invoked with no arguments,
THEN the CLI MUST discover all `butlers/*/butler.toml` config files,
AND for each discovered butler, the CLI MUST create the butler's database if it does not exist,
AND apply core Alembic migrations followed by butler-specific Alembic migrations,
AND sync TOML scheduled tasks to the database,
AND start all butler MCP servers concurrently on their configured ports within a single asyncio event loop.

#### Scenario: Dev mode startup log output

WHEN `butlers up` starts all discovered butlers successfully,
THEN the CLI MUST log a timestamped message for each butler indicating its name and listening port, for example:
```
[2026-02-09 08:00:00] switchboard   listening on :40100
[2026-02-09 08:00:00] general       listening on :40101
[2026-02-09 08:00:00] relationship  listening on :40102
[2026-02-09 08:00:00] health        listening on :40103
[2026-02-09 08:00:00] heartbeat     listening on :40199
```

#### Scenario: Start a subset of butlers with --only

WHEN `butlers up --only switchboard,health` is invoked,
THEN the CLI MUST start only the `switchboard` and `health` butlers,
AND all other discovered butlers MUST NOT be started,
AND each started butler MUST still go through the full startup sequence (database provisioning, Alembic migrations, TOML sync, MCP server start).

#### Scenario: --only references a non-existent butler

WHEN `butlers up --only switchboard,nonexistent` is invoked and no `butlers/nonexistent/butler.toml` exists,
THEN the CLI MUST report an error indicating that `nonexistent` was not found among the discovered butlers,
AND the CLI MUST NOT start any butlers.

#### Scenario: No butler.toml files discovered

WHEN `butlers up` is invoked and no `butlers/*/butler.toml` files are found,
THEN the CLI MUST report an error indicating that no butlers were discovered,
AND the CLI MUST exit with a non-zero status code.

---

### Requirement: butlers run (Production Mode)

The `butlers run` command SHALL start a single butler daemon from a specified config directory. This command is designed for production use where each butler runs in its own container, invoked via docker-compose.

The command MUST accept a `--config` option pointing to the butler's config directory containing `butler.toml`.

#### Scenario: Start a single butler daemon

WHEN `butlers run --config butlers/health` is invoked,
THEN the CLI MUST load the `butler.toml` from `butlers/health/`,
AND the CLI MUST execute the full butler startup sequence (provision database, apply Alembic migrations, sync TOML tasks, load modules, register tools, start MCP server),
AND the butler MUST run as a single long-lived daemon until a shutdown signal is received.

#### Scenario: Missing --config flag

WHEN `butlers run` is invoked without the `--config` flag,
THEN the CLI MUST report an error indicating that `--config` is required,
AND the CLI MUST exit with a non-zero status code.

#### Scenario: Config directory does not contain butler.toml

WHEN `butlers run --config butlers/empty` is invoked and `butlers/empty/butler.toml` does not exist,
THEN the CLI MUST report a configuration error indicating the missing `butler.toml`,
AND the CLI MUST exit with a non-zero status code.

#### Scenario: Production mode behavior is identical to dev mode per butler

WHEN a butler is started via `butlers run --config butlers/health`,
THEN its MCP server SHALL use the same SSE transport as in dev mode,
AND inter-butler MCP communication SHALL function identically to dev mode,
AND the butler SHALL expose the same tools and behavior regardless of which command started it.

---

### Requirement: butlers list

The `butlers list` command SHALL discover all butlers by scanning `butlers/*/butler.toml` config files and display their name, port, modules, and status.

#### Scenario: List discovered butlers

WHEN `butlers list` is invoked and `butlers/` contains config directories for `switchboard`, `general`, `relationship`, `health`, and `heartbeat`,
THEN the CLI MUST output a table or listing showing each butler's name, configured port, enabled modules, and current status (e.g., whether it is running or stopped).

#### Scenario: No butlers discovered

WHEN `butlers list` is invoked and no `butlers/*/butler.toml` files are found,
THEN the CLI MUST report that no butlers were discovered,
AND the CLI MUST exit cleanly (zero status code).

#### Scenario: Status detection for running butlers

WHEN `butlers list` is invoked and some butlers are currently running,
THEN the CLI MUST indicate which butlers are running and which are not,
AND status detection SHOULD be based on whether the butler's configured port is accepting connections.

---

### Requirement: butlers init

The `butlers init` command SHALL scaffold a new butler config directory with the minimal required files. It MUST accept a butler name as a positional argument and a `--port` option for the MCP server port.

#### Scenario: Scaffold a new butler

WHEN `butlers init mybutler --port 40104` is invoked,
THEN the CLI MUST create the directory `butlers/mybutler/`,
AND the directory MUST contain a `butler.toml` file with `[butler]` section containing `name = "mybutler"` and `port = 40104`,
AND the directory MUST contain an empty `CLAUDE.md` file (or one with a placeholder comment),
AND the directory MUST contain an empty `AGENTS.md` file (or one with a placeholder comment),
AND the directory MUST contain an empty `skills/` subdirectory.

#### Scenario: Init with default port

WHEN `butlers init mybutler` is invoked without `--port`,
THEN the CLI MUST still create the butler config directory,
AND the `butler.toml` MUST contain a `port` field with a reasonable default value.

#### Scenario: Init refuses to overwrite existing directory

WHEN `butlers init mybutler --port 40104` is invoked and `butlers/mybutler/` already exists,
THEN the CLI MUST report an error indicating the directory already exists,
AND the CLI MUST NOT modify the existing directory,
AND the CLI MUST exit with a non-zero status code.

#### Scenario: Generated butler.toml is valid

WHEN `butlers init mybutler --port 40104` creates a `butler.toml`,
THEN the generated file MUST be valid TOML,
AND the file MUST be parseable by the daemon's config loader,
AND the generated butler MUST be startable via `butlers run --config butlers/mybutler` without modification.

---

### Requirement: DATABASE_URL Configuration

All CLI commands that interact with PostgreSQL SHALL use the `DATABASE_URL` environment variable for the PostgreSQL connection string. The default value MUST be `postgres://butlers:butlers@localhost/postgres`.

#### Scenario: DATABASE_URL is set

WHEN `butlers up` or `butlers run` is invoked and `DATABASE_URL` is set in the environment,
THEN the CLI MUST use the provided `DATABASE_URL` to connect to PostgreSQL for database provisioning and Alembic migrations.

#### Scenario: DATABASE_URL is not set

WHEN `butlers up` or `butlers run` is invoked and `DATABASE_URL` is not set in the environment,
THEN the CLI MUST fall back to the default connection string `postgres://butlers:butlers@localhost/postgres`.

#### Scenario: Invalid DATABASE_URL

WHEN `butlers up` or `butlers run` is invoked and `DATABASE_URL` contains an invalid or unreachable connection string,
THEN the CLI MUST report a connection error indicating the PostgreSQL server could not be reached,
AND the CLI MUST exit with a non-zero status code.

---

### Requirement: Database Auto-Provisioning on Startup

On startup, both `butlers up` and `butlers run` SHALL automatically provision per-butler PostgreSQL databases. The provisioning sequence MUST follow a strict order for each butler.

#### Scenario: Full provisioning sequence for a new butler

WHEN a butler starts for the first time,
THEN the CLI MUST connect to PostgreSQL as the butlers user,
AND the CLI MUST execute `CREATE DATABASE butler_<name>` if the database does not already exist,
AND the CLI MUST apply all core Alembic revisions from the `core` version chain,
AND the CLI MUST apply butler-specific Alembic revisions from the `<name>` version chain if it exists,
AND the CLI MUST sync TOML scheduled tasks to the `scheduled_tasks` table.

#### Scenario: Database already exists on subsequent startup

WHEN a butler starts and its database `butler_<name>` already exists,
THEN the CLI MUST NOT attempt to re-create the database,
AND the CLI MUST still apply any unapplied Alembic revisions,
AND the CLI MUST still sync TOML scheduled tasks.

#### Scenario: Dev mode provisions all databases

WHEN `butlers up` starts five butlers,
THEN the CLI MUST provision each butler's database independently,
AND each butler MUST have its own dedicated database (e.g., `butler_switchboard`, `butler_general`, `butler_relationship`, `butler_health`, `butler_heartbeat`).

---

### Requirement: Graceful Shutdown

Both `butlers up` and `butlers run` SHALL support graceful shutdown on SIGINT and SIGTERM signals. The shutdown sequence MUST follow a strict order.

#### Scenario: Graceful shutdown on SIGINT

WHEN the CLI process receives SIGINT (e.g., Ctrl+C),
THEN each butler MUST stop accepting new MCP connections,
AND each butler MUST wait for any in-flight Claude Code sessions to complete,
AND each butler MUST call `on_shutdown()` on its modules in reverse initialization order,
AND each butler MUST close its database connection pool,
AND the CLI MUST exit cleanly.

#### Scenario: Graceful shutdown on SIGTERM

WHEN the CLI process receives SIGTERM (e.g., from docker-compose or process manager),
THEN the shutdown behavior MUST be identical to SIGINT handling.

#### Scenario: Dev mode shuts down all butlers

WHEN `butlers up` is running multiple butlers and SIGTERM is received,
THEN all running butlers MUST be shut down gracefully,
AND the shutdown of one butler MUST NOT interfere with the shutdown of another.

#### Scenario: Production mode shuts down the single butler

WHEN `butlers run` is running a single butler and SIGTERM is received,
THEN the butler MUST execute its full graceful shutdown sequence,
AND the process MUST exit with status code 0 after shutdown completes.

---

### Requirement: Butler Discovery

The `butlers up` and `butlers list` commands SHALL discover butlers by scanning for `butler.toml` files in immediate subdirectories of the `butlers/` directory.

#### Scenario: Discovery finds all butler configs

WHEN the `butlers/` directory contains subdirectories `switchboard/`, `general/`, `relationship/`, `health/`, and `heartbeat/`, each with a `butler.toml`,
THEN discovery MUST return all five butlers with their parsed configurations.

#### Scenario: Subdirectories without butler.toml are ignored

WHEN the `butlers/` directory contains a subdirectory `drafts/` that has no `butler.toml`,
THEN discovery MUST ignore the `drafts/` directory without error,
AND only directories with a valid `butler.toml` SHALL be included in the discovered set.

#### Scenario: Invalid butler.toml prevents discovery of that butler

WHEN a `butlers/broken/butler.toml` contains malformed TOML syntax,
THEN discovery MUST report an error for the `broken` butler,
AND other valid butlers MUST still be discovered and returned.

---

### Requirement: Dockerfile

The Dockerfile SHALL produce a container image capable of running a single butler daemon with all required dependencies: Python 3.12, Node.js 22, the claude-code npm package, and Python dependencies installed via uv.

#### Scenario: Base image and language runtimes

WHEN the Dockerfile is built,
THEN the resulting image MUST be based on `python:3.12-slim`,
AND the image MUST include Node.js 22 installed and available on PATH,
AND the image MUST include the `claude-code` npm package installed globally,
AND the image MUST include Python dependencies installed via `uv`.

#### Scenario: Butler config is mounted at runtime

WHEN a butler container starts,
THEN the butler's config directory MUST be mounted at `/etc/butler` as read-only,
AND the `butlers run --config /etc/butler` command MUST be the container's entrypoint or CMD.

#### Scenario: Image does not include butler configs

WHEN the Dockerfile is built,
THEN the image MUST NOT bake in any butler-specific config directories,
AND butler configs MUST be provided at runtime via volume mounts.

---

### Requirement: docker-compose.yml

The `docker-compose.yml` SHALL define services for PostgreSQL and one service per butler. Each butler service MUST use the same Docker image and be configured via environment variables and volume mounts.

#### Scenario: Infrastructure services

WHEN `docker compose up -d` is invoked,
THEN a `postgres` service MUST be started using the `postgres:17` image,
AND the `postgres` service MUST include a healthcheck.

#### Scenario: Butler services

WHEN `docker compose up -d` is invoked,
THEN one service MUST be defined for each butler (`switchboard`, `general`, `relationship`, `health`, `heartbeat`),
AND each butler service MUST depend on the `postgres` service with condition `service_healthy`,
AND each butler service MUST mount its config directory as `/etc/butler:ro`.

#### Scenario: Environment variables

WHEN a butler service starts in docker-compose,
THEN the `DATABASE_URL` environment variable MUST be set to the PostgreSQL connection string for the compose network,
AND the `ANTHROPIC_API_KEY` environment variable MUST be passed through from the host,
AND the `OTEL_EXPORTER_OTLP_ENDPOINT` environment variable MUST be set to the Alloy gateway OTLP endpoint (http://otel.parrot-hen.ts.net:4318).

#### Scenario: PostgreSQL healthcheck gates butler startup

WHEN the `postgres` service is starting,
THEN butler services MUST NOT start until the `postgres` healthcheck passes,
AND the healthcheck MUST verify that PostgreSQL is accepting connections.

---

### Requirement: Dev and Production Quick Start

The framework SHALL support two standard startup workflows: dev mode with local Python and production mode with full Docker deployment.

#### Scenario: Dev mode quick start

WHEN a developer runs `docker compose up -d postgres` followed by `butlers up`,
THEN PostgreSQL MUST be running as a Docker container,
AND all butlers MUST start in a single local process connecting to the Dockerized PostgreSQL,
AND inter-butler MCP communication MUST work over localhost ports.

#### Scenario: Production quick start

WHEN a developer runs `docker compose up -d`,
THEN all services MUST start: postgres and one container per butler,
AND each butler container MUST connect to the postgres service,
AND inter-butler MCP communication MUST work over the Docker compose network.

#### Scenario: Identical behavior across modes

WHEN the same set of butlers is running in dev mode and production mode,
THEN MCP tool calls between butlers MUST produce the same results,
AND the SSE transport MUST function identically in both modes,
AND the only difference SHALL be process topology (single process vs. multiple containers).

---

### Requirement: MCP-over-SSE Transport Consistency

Both deployment modes SHALL use MCP-over-HTTP with SSE transport for all inter-butler communication. The transport layer MUST be transparent to butler logic.

#### Scenario: Dev mode uses SSE over localhost

WHEN butlers are running via `butlers up` in a single process,
THEN each butler's MCP server MUST accept SSE connections on its configured port,
AND inter-butler communication (e.g., Switchboard routing to Health) MUST use SSE connections over `localhost`.

#### Scenario: Production mode uses SSE over Docker network

WHEN butlers are running via docker-compose in separate containers,
THEN each butler's MCP server MUST accept SSE connections on its configured port,
AND inter-butler communication MUST use SSE connections over the Docker compose network using service names as hostnames.

#### Scenario: Butler code is transport-agnostic

WHEN a butler processes an MCP tool call,
THEN the butler MUST NOT need to know whether it is running in dev mode or production mode,
AND no code path SHALL branch on the deployment mode for MCP request handling.

---

### Requirement: Port Assignment Convention

Each butler SHALL be assigned a unique port number in its `butler.toml`. The port assignment MUST avoid conflicts between butlers running concurrently.

#### Scenario: Default v1 butler port assignments

WHEN the v1 MVP butlers are configured,
THEN the Switchboard MUST use port 40100,
AND the General butler MUST use port 40101,
AND the Relationship butler MUST use port 40102,
AND the Health butler MUST use port 40103,
AND the Heartbeat butler MUST use port 40199.

#### Scenario: Port conflict detected at startup

WHEN `butlers up` discovers two butlers configured with the same port number,
THEN the CLI MUST report a port conflict error identifying both butlers and the conflicting port,
AND the CLI MUST NOT start any butlers.

---

### Requirement: CLI Built with Click

The `butlers` CLI SHALL be implemented using the Click library. All commands (`up`, `run`, `list`, `init`) SHALL be Click commands or groups registered under the `butlers` entry point.

#### Scenario: CLI entry point

WHEN `butlers` is invoked with no arguments,
THEN the CLI MUST display a help message listing all available commands: `up`, `run`, `list`, and `init`.

#### Scenario: --help flag on subcommands

WHEN `butlers up --help` is invoked,
THEN the CLI MUST display usage information for the `up` command, including the `--only` option.

#### Scenario: Unknown command

WHEN `butlers unknown` is invoked,
THEN the CLI MUST display an error message indicating that `unknown` is not a valid command,
AND the CLI MUST exit with a non-zero status code.
