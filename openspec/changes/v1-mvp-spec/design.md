## Context

Butlers is a greenfield AI agent framework. Each butler is a long-running FastMCP server daemon backed by its own PostgreSQL database. Butlers share a common core (state store, scheduler, LLM CLI spawner, session log) and gain domain-specific capabilities through opt-in modules. When triggered, a butler spawns an ephemeral LLM CLI instance locked down to only that butler's MCP tools.

The v1 MVP ships 5 butlers (Switchboard, Heartbeat, Relationship, Health, General), a module system (Telegram, Email), two deployment modes, and OpenTelemetry instrumentation.

The codebase is Python 3.12+, uses `uv` for package management, `ruff` for linting, `pytest` + `pytest-asyncio` for testing, and `hatchling` as build backend.

## Goals / Non-Goals

**Goals:**

- Deliver a working butler framework where any butler can be started from a `butler.toml` config directory
- Core components (state, scheduler, spawner, sessions) work identically across all butlers
- Modules are truly pluggable: add/remove via config without touching core code
- Each butler is fully isolated at the database level — no shared tables
- runtime instances are locked down: they can only call their owning butler's MCP tools
- Single codebase supports both dev mode (single process) and production mode (one container per butler)
- End-to-end tracing from message ingress through routing to CC execution

**Non-Goals:**

- Authentication/authorization between butlers (v1 trusts the Docker network)
- Concurrent runtime instances per butler (serial dispatch; queuing is a future enhancement)
- OAuth flow management for integration modules (credentials provided as env vars, set up out-of-band)
- Web UI or dashboard (CLI + telemetry tracing via LGTM stack only)
- Butler hot-reload (restart required for config changes)
- Multi-tenant or multi-user support (single-user personal system)

## Decisions

### D1: FastMCP as the MCP server framework

**Choice:** FastMCP (Python) for all butler MCP servers.

**Rationale:** FastMCP provides decorator-based tool registration, SSE transport support, and async-native operation. It's the most mature Python MCP server library and aligns with the all-Python tech stack. Alternatives (custom MCP implementation, mcp-python-sdk raw) would require significantly more boilerplate.

### D2: One PostgreSQL database per butler (strict isolation)

**Choice:** Each butler owns a dedicated PostgreSQL database (e.g., `butler_switchboard`, `butler_health`). No cross-database queries.

**Rationale:** Isolation simplifies reasoning about data ownership, makes backups per-butler, and prevents accidental coupling. Inter-butler communication happens exclusively via MCP tool calls through the Switchboard. The cost is that cross-butler queries require MCP round-trips, which is acceptable for the v1 scale.

**Alternative considered:** Shared database with schema-per-butler. Rejected because it weakens isolation guarantees and makes it harder to reason about which butler owns which data.

### D3: asyncpg for database access (no ORM)

**Choice:** Use `asyncpg` for direct async PostgreSQL access. No SQLAlchemy or other ORM.

**Rationale:** The schema is well-defined and stable (migrations handle evolution). Direct SQL with `asyncpg` gives maximum performance and simplicity for the JSONB-heavy workload. An ORM adds complexity without proportional benefit for this use case. `asyncpg` provides prepared statements, connection pooling, and native JSONB support.

**Alternative considered:** SQLAlchemy async with asyncpg backend. Rejected as over-engineering for a system where the SQL is straightforward and the schema is tightly controlled.

### D4: croniter for cron expression parsing

**Choice:** `croniter` library for parsing and evaluating cron expressions in the task scheduler.

**Rationale:** Well-established Python library (10+ years), handles all standard cron syntax, and provides `get_next()` / `get_prev()` for computing next run times. No other Python cron library matches its reliability and feature set.

### D5: Butler class as the composition root

**Choice:** A single `Butler` class that composes config, database, core components, and modules. It owns the FastMCP server instance and wires everything together.

**Rationale:** Centralizing composition in one class makes butler lifecycle explicit (init → provision DB → apply migrations → load modules → register tools → start server). This avoids scattered initialization and makes testing straightforward (inject mocks at construction time).

### D6: Modules only register tools — no core access

**Choice:** Modules interact with the butler exclusively through the `register_tools(mcp, config, db)` interface. They can register MCP tools and access their own DB tables, but cannot modify core infrastructure (scheduler, spawner, state store).

**Rationale:** This constraint keeps modules isolated and composable. If a module needs state persistence, it uses the state store MCP tools (same as CC would). This prevents tight coupling between modules and core internals.

### D7: LLM CLI spawner generates ephemeral MCP config files

**Choice:** Each runtime invocation gets a freshly generated MCP config JSON written to a temp directory, pointing only to the owning butler's MCP endpoint.

**Rationale:** This is the lock-down mechanism. By controlling the MCP config, we ensure CC can only call tools on the butler that spawned it. The temp directory is cleaned up after the session completes. The CC SDK's `mcp_config` option accepts a file path, making this straightforward.

### D8: SSE transport for inter-butler MCP communication

**Choice:** Use SSE (Server-Sent Events) transport for MCP communication between butlers and from runtime instances back to butlers.

**Rationale:** SSE is the standard MCP transport for HTTP-based servers, supported natively by FastMCP. It works identically whether butlers are in the same process (dev mode) or separate containers (production). Streamable HTTP is newer but less battle-tested in the MCP ecosystem.

### D9: Click for CLI framework

**Choice:** `click` for the `butlers` CLI.

**Rationale:** Lightweight, well-documented, and widely used. The CLI is simple (4-5 commands), so a heavier framework like Typer adds unnecessary dependency weight. Click's decorator-based interface maps cleanly to the command structure (`butlers up`, `butlers run`, `butlers list`, `butlers init`).

### D10: OpenTelemetry with LGTM stack

**Choice:** OpenTelemetry SDK with OTLP exporter, LGTM stack (Grafana, Tempo, Loki, and Alloy) for local trace visualization.

**Rationale:** OTel is the industry standard for distributed tracing. The LGTM stack provides a free, lightweight solution for visualizing traces in development. Alloy handles log/metric collection, Tempo stores traces, and Grafana provides the UI. In production, the OTLP exporter can point to any compatible backend without code changes.

### D11: Telegram polling for dev, webhook for production

**Choice:** The Telegram module supports both polling (dev) and webhook (prod) modes, configured via `butler.toml`.

**Rationale:** Polling is simpler for local development (no public URL needed). Webhooks are more efficient in production (no polling overhead, instant delivery). Both use the same internal message handling path.

### D12: Alembic for database migrations

**Choice:** Use Alembic for all database migrations. A shared `alembic/` directory at the project root contains a single Alembic environment with multiple version chains: one `core` chain (applied to every butler) and per-butler chains (e.g., `relationship`, `health`, `general`, `switchboard`) applied only to the relevant butler's database. Migrations use raw SQL via `op.execute()` to stay consistent with D3 (no ORM at runtime).

**Rationale:** Alembic provides proper migration tooling that plain SQL files lack: version dependency tracking, upgrade/downgrade support, migration generation scaffolding (`alembic revision`), and branch/merge handling for parallel development. As the number of butlers and modules grows, ordered SQL files become fragile — Alembic's revision graph handles this robustly. While Alembic depends on SQLAlchemy, it is used only at migration time, not at runtime; the application still uses asyncpg directly for all queries.

**Structure:**
```
alembic/
├── alembic.ini
├── env.py              # Programmatic env that targets the correct butler DB
└── versions/
    ├── core/           # Core migrations (state, scheduled_tasks, sessions)
    ├── switchboard/    # Switchboard-specific migrations
    ├── relationship/   # Relationship-specific migrations
    ├── health/         # Health-specific migrations
    └── general/        # General-specific migrations
```

At startup, the daemon runs `alembic upgrade head` programmatically (via `alembic.command.upgrade`) against the butler's database, applying the core chain first, then the butler-specific chain. Module migrations are also managed as Alembic revisions, with each module contributing revisions that depend on the core chain head.

**Alternative considered:** Plain SQL files in lexicographic order with a custom `_migrations` tracking table. Rejected because it reinvents migration tracking poorly — no downgrade path, no dependency graph, no tooling for generating or inspecting migrations, and fragile ordering as the project scales.

## Risks / Trade-offs

**[Serial CC dispatch may bottleneck under load]** → Acceptable for v1 (single-user personal system). Future enhancement: add an asyncio queue with configurable concurrency per butler.

**[No auth between butlers on Docker network]** → Acceptable for v1 (private deployment). Mitigated by Docker network isolation. Future: add mTLS or API key auth.

**[LLM CLI spawner depends on Claude Code CLI being installed]** → Mitigated by including Node.js + claude-code in the Docker image. Dev mode requires local Claude Code installation — documented in setup instructions.

**[Database auto-provisioning requires superuser-like privileges]** → The `butlers` PostgreSQL user needs `CREATEDB` privilege. Documented in setup. For managed PostgreSQL (RDS, etc.), databases must be pre-created.

**[Testcontainers add CI time]** → PostgreSQL container startup adds ~3-5s to test suite. Mitigated by using `session`-scoped fixture (one container per test run, fresh database per test).

**[Large number of MCP tools per butler (Relationship has ~30)]** → Could overwhelm CC's tool selection. Mitigated by clear tool naming conventions and butler-specific CLAUDE.md instructions that guide CC toward relevant tools.

**[Module dependency resolution complexity]** → Topological sort handles DAGs. Circular dependencies are detected and raise an error at startup. v1 modules (Telegram, Email) have no inter-module dependencies, so this is low risk.

**[Trace context propagation across MCP boundaries]** → MCP doesn't have native trace context headers. Mitigated by passing `_trace_context` in tool call arguments and extracting on the receiving side. Slightly non-standard but functional.
