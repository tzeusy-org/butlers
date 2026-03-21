# Modules and Connectors

> **Purpose:** Explain the distinction between modules (in-process capability units) and connectors (out-of-process transport adapters), and how each works.
> **Audience:** Developers building new modules or connectors, or trying to understand how capabilities are added to butlers.
> **Prerequisites:** [What Is Butlers?](../overview/what-is-butlers.md), [Butler Lifecycle](butler-lifecycle.md)

## Overview

Butlers has two extension mechanisms that serve fundamentally different purposes. **Modules** live inside butler daemons and extend what a butler can do --- they register MCP tools, manage database tables, and hook into the daemon lifecycle. **Connectors** live outside butler daemons and extend how messages reach the system --- they poll external services, normalize events, and submit them to the Switchboard's ingestion API. Modules extend butlers. Connectors feed butlers.

## Modules

### What Modules Are

A module is a pluggable capability unit that a butler loads at startup. It implements the `Module` abstract base class defined in `src/butlers/modules/base.py`. Every module provides:

- **`name`** --- A unique identifier (e.g., `"email"`, `"telegram"`, `"memory"`, `"calendar"`).
- **`config_schema`** --- A Pydantic model class that validates the module's section in `butler.toml`.
- **`dependencies`** --- A list of module names this module depends on. The daemon resolves modules in topological order, so a module's dependencies are guaranteed to be initialized first.
- **`register_tools(mcp, config, db)`** --- Registers MCP tools on the butler's FastMCP server. This is how a module adds capabilities that the LLM can call during sessions.
- **`migration_revisions()`** --- Returns an Alembic branch label for the module's database migrations, or `None` if the module has no tables.
- **`on_startup(config, db, credential_store)`** --- Called after dependency resolution and migrations. This is where modules open connections, start background tasks, or load cached data. The `credential_store` parameter enables DB-first credential resolution.
- **`on_shutdown()`** --- Called during butler shutdown in reverse topological order. Used for cleanup: closing connections, stopping background tasks.

Modules can also declare `tool_metadata()` to annotate which tool arguments are safety-critical (sensitive), enabling the approval gate system to enforce appropriate review policies.

### How Modules Load

1. The butler reads its `butler.toml` and identifies which modules are declared under `[modules.*]`.
2. The `ModuleRegistry` instantiates all built-in module classes.
3. The daemon filters to only modules that are both built-in and declared in config.
4. Config validation runs each module's Pydantic schema against its config section.
5. Modules with migration chains get their Alembic migrations run.
6. `on_startup()` is called in topological order.
7. `register_tools()` is called to add MCP tools to the butler's server.

At each step, failures are non-fatal per-module. A failed module is recorded with its failure phase and error, and any modules that depend on it are cascade-failed. The butler continues operating with whatever modules remain healthy.

### Module Configuration

Modules are configured in `butler.toml`:

```toml
[modules.email]
# Email module config

[modules.email.bot]
address_env = "BUTLER_EMAIL_ADDRESS"
password_env = "BUTLER_EMAIL_PASSWORD"

[modules.email.user]
enabled = false

[modules.calendar]
provider = "google"
calendar_id = "butler@group.calendar.google.com"
```

Each module section is validated against the module's `config_schema` Pydantic model. Some modules support sub-scopes (like `bot` and `user` for email and Telegram) that configure different operating modes.

### Module Credentials

Modules can declare environment variables they need via `credentials_env` fields in their config schema. These are validated during startup step 8b after the database pool is available, so the `CredentialStore` can resolve credentials from the database first with environment variable fallback. Bot-scope credentials that are missing cause the module to fail. User-scope credentials are resolved from the owner's contact info in the shared schema, not from environment variables.

### Key Properties of Modules

- Modules only add tools. They never touch core infrastructure (state store, scheduler, session log).
- Modules run in-process within the butler daemon's async event loop.
- Module tool calls are logged as part of the session's tool call history.
- Modules can access the butler's database pool for their own tables.
- Modules are loaded once at startup and persist for the daemon's lifetime.

## Connectors

### What Connectors Are

A connector is a standalone transport adapter that runs as its own process, separate from any butler daemon. Connectors are defined in `src/butlers/connectors/` and serve a single purpose: bridge external messaging systems into the Butlers ingestion pipeline.

The connector package's `__init__.py` states this clearly:

> Connectors are transport-only adapters that:
> - Read events from external systems (Telegram, Gmail, etc.)
> - Normalize to canonical ingest.v1 format
> - Submit to Switchboard ingest API
> - Handle checkpointing and resume logic

### Available Connectors

| Connector | Source | Transport |
| --- | --- | --- |
| `telegram_bot` | Telegram Bot API | Polling or webhook |
| `telegram_user_client` | Telegram MTProto (user account) | Persistent connection |
| `gmail` | Gmail API | Polling with checkpoint |
| `discord_user` | Discord user client | Event stream |
| `live_listener` | Audio input (microphone) | VAD + transcription pipeline |

### How Connectors Work

Taking the Telegram Bot connector as an example:

1. The connector starts as its own process (typically launched by `dev.sh` in a tmux pane).
2. It resolves credentials: the Telegram bot token is resolved DB-first via `CredentialStore`, with environment variable fallback.
3. It polls the Telegram Bot API for updates (in dev mode) or registers a webhook (in production mode).
4. Each incoming update is normalized to the canonical `ingest.v1` envelope format, which includes fields like source channel, sender identity, message content, and timestamps.
5. The normalized envelope is submitted to the Switchboard's MCP server via an `ingest` tool call.
6. A durable checkpoint is persisted so the connector can resume from where it left off after a crash.

Connectors maintain their own health sockets for liveness probes, heartbeat reporting to the Switchboard, and Prometheus-compatible metrics for observability.

### Key Properties of Connectors

- Connectors are **out-of-process** --- they do not run inside any butler daemon.
- Connectors are **transport-only** --- they never classify, route, or handle messages.
- Connectors normalize to a canonical format --- downstream code never deals with transport-specific message shapes.
- Connectors handle their own checkpointing and crash recovery.
- Connectors connect to the Switchboard's MCP server as a client, submitting events via the `ingest` tool.

### Connector Environment Variables

Each connector reads its configuration from environment variables:

- `SWITCHBOARD_MCP_URL` --- SSE endpoint URL for the Switchboard MCP server (required for all connectors)
- `CONNECTOR_PROVIDER` --- provider type (e.g., `"telegram"`)
- `CONNECTOR_CHANNEL` --- channel identifier (e.g., `"telegram_bot"`)
- `CONNECTOR_POLL_INTERVAL_S` --- poll interval for polling-based connectors
- `CONNECTOR_MAX_INFLIGHT` --- max concurrent ingest submissions (default 8)
- `CONNECTOR_HEALTH_PORT` --- HTTP port for the health endpoint

Plus provider-specific credentials (e.g., `BUTLER_TELEGRAM_TOKEN` for the Telegram bot connector).

## Modules vs. Connectors: When to Use Which

| Aspect | Module | Connector |
| --- | --- | --- |
| Runs where | Inside butler daemon | Separate process |
| Purpose | Add tools and capabilities | Feed messages into the system |
| Interface | Module ABC lifecycle hooks | Switchboard ingest API |
| State | Butler's database schema | Own checkpoint store |
| Examples | Email tools, Calendar tools, Memory | Telegram poller, Gmail poller |

If you are adding a new *capability* that a butler's LLM should be able to use (query a database, send a message, look up a calendar), write a **module**. If you are adding a new *input channel* that should feed messages into the Switchboard for classification and routing, write a **connector**.

## Related Pages

- [Butler Lifecycle](butler-lifecycle.md) --- how modules are loaded during startup
- [MCP Model](mcp-model.md) --- how module tools integrate with the MCP server
- [Switchboard Routing](switchboard-routing.md) --- how connector-submitted messages get routed
- [Trigger Flow](trigger-flow.md) --- how external triggers become sessions
