## Why

The Butlers framework has a detailed project plan but no formal specifications. Before implementation begins, the v1 MVP needs concrete, testable specs for each capability so that milestones 0–12 can be built against a clear contract. Specs prevent scope drift, define acceptance criteria, and enable TDD.

## What Changes

- Establish the full v1 MVP specification covering the butler daemon framework, core components, module system, specialized butlers, deployment, and observability
- Define the core daemon lifecycle: config loading, database provisioning, MCP server startup, module composition, and shutdown
- Specify the module system: abstract base class, registry with topological dependency resolution, tool registration
- Specify all core components: state store (KV JSONB), task scheduler (cron-driven with TOML sync), LLM CLI spawner (locked-down ephemeral LLM CLI instances), session log
- Specify the Switchboard butler: butler registry, message routing, inter-butler MCP communication
- Specify the Heartbeat butler: periodic tick cycle across all registered butlers
- Specify three domain butlers: Relationship (personal CRM), Health (tracking), General (freeform catch-all)
- Specify the CLI and deployment modes: `butlers up` (dev, single-process) and `butlers run` (prod, per-butler)
- Specify OpenTelemetry instrumentation: trace context propagation, span creation, LGTM stack integration
- Define the testing strategy: MockSpawner, testcontainers, test layers
- Specify per-butler credential management: env var declaration, scoping, module credential references, runtime instance passthrough, startup validation
- Specify skill directory structure: SKILL.md format, script access, CC discovery, CLAUDE.md personality, AGENTS.md runtime notes

## Capabilities

### New Capabilities

- `butler-daemon`: Core butler daemon lifecycle — config loading from `butler.toml`, MCP server startup (FastMCP), database auto-provisioning, module composition, shutdown. The foundational runtime that all butlers share.
- `module-system`: Module abstract base class, registry with topological dependency sort, tool registration via `register_tools()`, Alembic migrations, startup/shutdown hooks. How opt-in capabilities plug into a butler.
- `state-store`: Key-value JSONB persistence in each butler's PostgreSQL database. Core MCP tools: `state_get`, `state_set`, `state_delete`, `state_list`.
- `task-scheduler`: Cron-driven task dispatch. TOML bootstrap tasks synced to DB on startup, runtime task creation via MCP tools. `tick()` entry point checks for due tasks and dispatches prompts to LLM CLI spawner. Tools: `schedule_list`, `schedule_create`, `schedule_update`, `schedule_delete`.
- `cc-spawner`: Ephemeral LLM CLI instance management via the CC SDK. Generates locked-down MCP configs, spawns CC with constrained tool access, logs sessions. Tools: `trigger`. Includes MockSpawner for testing.
- `session-log`: Logs every runtime invocation — prompt, tool calls, outcome, duration, trace ID. Tools: `sessions_list`, `sessions_get`.
- `switchboard`: Public ingress butler with butler registry, message classification, and inter-butler routing via MCP. Tools: `route`, `list_butlers`, `discover`. Owns Telegram and Email module integration for message intake.
- `heartbeat`: Infrastructure butler that calls `tick()` on every registered butler on a 10-minute cycle. Enumerates butlers from Switchboard, logs results.
- `butler-relationship`: Personal CRM butler — contacts, relationships, important dates, notes, interactions, reminders, gifts, loans, groups, labels, quick facts, activity feed. Dedicated PostgreSQL schema and scheduled tasks.
- `butler-health`: Health tracking butler — measurements, medications, conditions, diet/meals, symptoms, research notes, reports. Dedicated PostgreSQL schema and scheduled tasks.
- `butler-general`: Catch-all butler with freeform JSONB entities and collections. Schema-light storage for anything that doesn't fit a specialist butler, with export tools for future migration.
- `cli-and-deployment`: CLI commands (`butlers up`, `butlers run`, `butlers list`, `butlers init`), Dockerfile with Claude Code, docker-compose with per-butler containers + PostgreSQL + LGTM stack, database auto-provisioning.
- `telemetry`: OpenTelemetry integration — tracer initialization, span wrappers for MCP tool handlers, trace context propagation across inter-butler calls and runtime sessions, OTLP export to LGTM stack.
- `butler-credentials`: Per-butler credential and environment variable management. How each butler declares required env vars, how modules reference credentials via `credentials_env`, how credentials are scoped in dev mode (single process) vs production (per-container), how env vars are passed through to runtime instances, and startup validation.
- `butler-skills`: Skill directory structure and discovery within each butler's config dir. `skills/<name>/SKILL.md` + optional scripts, how CC discovers available skills, how `CLAUDE.md` shapes CC personality/instructions, and how `AGENTS.md` provides runtime notes.

### Modified Capabilities

(none — greenfield project, no existing specs)

## Impact

- **Code**: Entire `src/butlers/` package — core, modules, tools, CLI
- **Dependencies**: FastMCP, claude-code-sdk, asyncpg/psycopg, croniter, opentelemetry-sdk, click/typer (CLI), testcontainers, docker
- **Infrastructure**: PostgreSQL (one database per butler), Docker, LGTM stack (Grafana, Tempo, Loki)
- **APIs**: Each butler exposes an MCP server (SSE transport) with core + module-specific tools
- **External integrations**: Telegram Bot API, Gmail/IMAP (via modules on Switchboard)
