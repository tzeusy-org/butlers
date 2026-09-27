# Dependency Map

Internal and external dependencies, startup ordering constraints, and failure
blast radius analysis.

This page is a snapshot, not a contract. Butler and module lists here are
examples; `ls roster/` and `ls src/butlers/modules/` are authoritative.

---

## Internal Dependency Graph

### Module Dependencies (topological sort at startup)

Module dependencies are declared via the `dependencies` property on each Module
subclass. The `ModuleRegistry` resolves them via topological sort before
calling `on_startup()`; shutdown runs in reverse order. No module currently
declares a dependency (`grep -rn -A2 "def dependencies" src/butlers/modules`),
so startup order among enabled modules is unconstrained.

### Butler-to-Switchboard Dependency

```mermaid
graph LR
    GEN["general :41101"] -- "MCP client" --> SW["switchboard :41100"]
    REL["relationship :41102"] -- "MCP client" --> SW
    OTHER["... every non-switchboard butler (e.g.)"] -- "MCP client" --> SW
    OBS["dashboard shadow observer"] -- "GET /internal/control-plane/identity" --> GEN
    OBS -- "GET /internal/control-plane/identity" --> SW
```

Every non-switchboard butler opens an MCP client to the Switchboard during
startup and registers its endpoint. Routing reads the Switchboard registry,
kept fresh by daemon heartbeats; the Dashboard shadow observer probes each
daemon's identity route but does not yet hold route authority. The staged
cutover is in the
[control-plane design](../../openspec/changes/restore-butler-control-plane-liveness/design.md).

**Failure mode**: If the Switchboard is down, domain butlers cannot receive
routed messages. Their local deterministic schedules and direct MCP triggers
continue unless a separate administrative policy disables them.

### Connector-to-Switchboard Dependency

```mermaid
graph LR
    TGBot["telegram-bot connector"] -- "MCP: ingest()" --> SW["switchboard :41100"]
    Gmail["gmail connector"] -- "MCP: ingest()" --> SW
    LiveL["live-listener connector"] -- "MCP: ingest()" --> SW

    TGBot -- "readiness probe" --> SW
    Gmail -- "readiness probe" --> SW
```

Connectors call `wait_for_switchboard_ready()` on startup, polling the
Switchboard health endpoint with exponential backoff (up to ~5 minutes).

**Failure mode**: If the Switchboard is unreachable, connectors block at startup
and cannot ingest events.

### Dashboard-to-Database Dependency

```mermaid
graph LR
    API["FastAPI :41200"] -- "asyncpg pools" --> PG["PostgreSQL"]
    API -- "reads all schemas" --> PG
```

The dashboard backend creates connection pools to each butler's schema on
startup. It reads directly from butler databases -- it does not go through
butler MCP servers.

**Failure mode**: If PostgreSQL is down, the dashboard returns 500 errors.
Butler daemons also fail to start.

---

## Startup Order Constraints

The following must start before dependents can function:

```
1. PostgreSQL
   └── 2. MinIO (+ bucket setup)
       ├── 3. Switchboard butler
       │   ├── 4a. Domain butlers (general, relationship, health, ...)
       │   └── 4b. Connectors (telegram-bot, gmail, live-listener, ...)
       └── 3'. Dashboard API
```

PostgreSQL is the hard dependency for everything. MinIO / S3 is required for
blob storage, but butler daemons start without a usable blob store when the S3
configuration is absent or the configured endpoint/bucket fails validation
(blob operations fail at runtime). The Switchboard must be up before connectors
attempt ingestion, enforced by the readiness probe.

---

## External Dependencies

### Runtime Services (required)

| Dependency | Used By | Purpose | Failure Impact |
|---|---|---|---|
| **PostgreSQL** (pgvector/pg17) | All butlers, dashboard | Schema-isolated data storage, vector search | Total system failure -- nothing starts |
| **LLM API** (Anthropic/OpenAI/Google) | Spawner (all butlers) | LLM CLI session execution | Sessions cannot spawn; scheduled tasks queue indefinitely |

### Runtime Services (optional, degraded without)

| Dependency | Used By | Purpose | Failure Impact |
|---|---|---|---|
| **MinIO / S3** | Blob storage (attachments) | Attachment storage and retrieval | Attachment operations fail; non-blob daemon startup and core text messaging continue |
| **OpenTelemetry Collector** | Telemetry | OTLP trace/metric collection | No observability; no-op tracer/meter used instead |
| **Tempo** | Trace queries | Trace storage backend | Cannot query traces; collection unaffected |
| **Prometheus** | Metric queries | Metric storage backend | Cannot query metrics; emission unaffected |

### External APIs (per-connector/module)

Representative examples; each connector service in `docker-compose.yml` and
each module under `src/butlers/modules/` depends on its own provider API.

| Dependency | Used By | Purpose | Failure Impact |
|---|---|---|---|
| **Telegram Bot API** | telegram-bot connector, telegram module | Message polling/sending | Telegram ingestion and responses stop |
| **Telegram MTProto** | telegram-user-client connector | Userbot message access | Userbot ingestion stops |
| **Gmail API** | gmail connector | Watch/history delta email ingestion | Email ingestion stops |
| **Google Calendar API** | calendar module | Calendar CRUD | Calendar tools return errors |
| **Google Contacts API** | contacts module | Contact sync | Contact sync pauses |
| **Discord Gateway** | discord connector | WebSocket event stream | Discord ingestion stops (Draft status) |

### Build-Time Dependencies

| Dependency | Purpose |
|---|---|
| **Python 3.12+** | Runtime language |
| **uv** | Package management and virtual environments |
| **Node.js 24** | Frontend build toolchain (matches CI) |
| **Docker** | Container runtime for infrastructure services |
| **Ruff** | Linting and formatting |
| **pytest** | Test execution with pytest-asyncio |
| **Alembic** | Database schema migrations |
| **Hatchling** | Python package build backend |

---

## Failure Blast Radius

### PostgreSQL down

Everything stops. No butler can start or continue operating. Dashboard returns
errors. Connectors cannot resolve credentials.

### Switchboard down

- Domain butlers continue running (local schedulers work, direct MCP calls work).
- No new external messages are routed to domain butlers.
- Connectors block or fail at ingestion.

### Single domain butler down

- Routes to that butler fail; the Switchboard registry marks it stale once its
  heartbeat TTL lapses.
- Other butlers are unaffected.

### Connector down

- The specific channel stops ingesting (e.g., no new Telegram messages).
- All other channels continue.
- Switchboard connector registry marks it stale after heartbeat TTL.

### LLM API down

- No new sessions can spawn across the fleet.
- Scheduled prompt-mode tasks queue behind the spawner semaphore.
- Job-mode scheduled tasks (Python functions) continue running.
- Existing data (state, memory, sessions) remains accessible via MCP tools.

### MinIO/S3 down

- Daemon startup continues with `daemon.blob_store = None` after the S3
  validation warning.
- Attachment upload/download fails at runtime.
- Core messaging continues for text-based ingestion and routing.
- Gmail connector attachment lazy-fetch fails.
