# Repository Thesis

Butlers is a personal AI agent system built from long-running MCP server daemons, domain-specific butlers, transport-only connectors, a switchboard router, spawned LLM CLI sessions, and a shared PostgreSQL database with per-butler schemas. It leverages async Python, FastMCP, subprocess runtime adapters, Alembic, asyncpg, PostgreSQL JSONB, OpenTelemetry, Docker Compose, first-party modules, OAuth-backed integrations, approval gates, a memory subsystem with embeddings, and a dashboard/API surface.

The major technical domains are:

- Agent/tool architecture: MCP servers, tool registration, spawned LLM clients, and scoped capability surfaces.
- Runtime systems: async task lifecycles, semaphores, queues, subprocesses, timeouts, retries, and backpressure.
- Data systems: PostgreSQL schemas, roles, JSONB, migration chains, schema evolution, and object/blob storage.
- Trust and integration boundaries: owner identity, contacts, OAuth, credential storage, approval gates, sensitive tool metadata, and API authorization.
- Time and autonomy: cron, scheduler semantics, calendar projection, reminders, recurrence, deadlines, and event chains.
- Operations and verification: structured logging, OpenTelemetry traces/metrics, Docker Compose, pytest-asyncio, xdist, testcontainers, and costful E2E tests.
- Retrieval and product surfaces: fact/provenance modeling, vector search, frontend/API contracts, and roster-specific domain modules.

The main mental-model gap is that the repository is not a normal web app and not a generic plugin host. It is a set of personal, first-party daemons where LLM reasoning is deliberately isolated in short-lived subprocess sessions, while durable state, routing, tools, identity, and policy live in deterministic Python code and PostgreSQL.

The curriculum is evidence-backed. The strongest evidence comes from `README.md`, `docs/concepts/`, `docs/architecture/`, `docs/data_and_storage/`, `docs/runtime/`, `pyproject.toml`, `src/butlers/`, `roster/`, `alembic/versions/`, `tests/`, and the three independent discovery passes recorded in `research-ledger.md`. Narrow areas such as WhatsApp sidecar internals are included as deferable because they are real repo surfaces but not central to understanding the main system.

Repo orientation appears only to explain why each technical concept matters here. For directory tours, setup commands, or current implementation details, use the project documentation after completing the relevant prerequisite modules.
