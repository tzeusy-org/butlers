## 1. Project Skeleton

- [ ] 1.1 Create `pyproject.toml` with hatchling build, ruff config, pytest-asyncio, and dev dependencies (testcontainers, ruff)
- [ ] 1.2 Create `src/butlers/__init__.py` and `src/butlers/py.typed`
- [ ] 1.3 Create `tests/__init__.py` and `tests/conftest.py` with `SpawnerResult` dataclass and `MockSpawner` fixture
- [ ] 1.4 Create `.python-version` (3.12)
- [ ] 1.5 Create `Makefile` with lint, format, test, check targets
- [ ] 1.6 Create `.github/workflows/ci.yml` with PostgreSQL service, uv setup, `make check`
- [ ] 1.7 Run `uv sync --dev` and verify `make check` passes

## 2. Config Loading

- [ ] 2.1 Create `src/butlers/config.py` with `ButlerConfig` dataclass — parse `butler.toml` ([butler], [butler.db], [butler.env], [[butler.schedule]], [modules.*])
- [ ] 2.2 Add `[butler.env]` support — `required` and `optional` env var lists
- [ ] 2.3 Add config validation: required fields (name, port), defaults (db name = butler_{name})
- [ ] 2.4 Write `tests/test_config.py` — valid config, minimal config, missing file, invalid TOML, missing required fields, env section parsing

## 3. Module System

- [ ] 3.1 Create `src/butlers/modules/base.py` — Module ABC with name, config_schema, dependencies, register_tools, migration_revisions, on_startup, on_shutdown
- [ ] 3.2 Create `src/butlers/modules/registry.py` — module registry with topological sort, circular dependency detection, load-from-config
- [ ] 3.3 Write `tests/test_module_base.py` — minimal module implementation, missing members
- [ ] 3.4 Write `tests/test_module_registry.py` — registration, dependency ordering, circular detection, missing dependencies, config validation

## 4. Database Layer

- [ ] 4.1 Create `src/butlers/db.py` — asyncpg connection pool, database auto-provisioning (CREATE DATABASE IF NOT EXISTS), Alembic migration runner (programmatic `alembic.command.upgrade`)
- [ ] 4.2 Create `alembic/` directory — `alembic.ini`, `env.py` (programmatic env targeting butler DB), `alembic/versions/core/` with initial revision creating state, scheduled_tasks, sessions tables
- [ ] 4.3 Write `tests/test_db.py` — database creation, Alembic migration application, idempotent re-run (testcontainers PostgreSQL)

## 5. Credential Management

- [ ] 5.1 Add credential validation to butler startup — check ANTHROPIC_API_KEY, [butler.env].required, [butler.env].optional, module credentials_env
- [ ] 5.2 Implement aggregated error reporting — collect all missing vars before failing, name components that need each var
- [ ] 5.3 Add secret-in-config-file detection — scan butler.toml string values for suspected inline secrets, log advisory warnings
- [ ] 5.4 Write `tests/test_credentials.py` — all-present, missing required, missing optional (warning), missing module creds, multiple missing, secret detection

## 6. State Store

- [ ] 6.1 Create `src/butlers/core/state.py` — state_get, state_set (upsert), state_delete, state_list with optional prefix
- [ ] 6.2 Write `tests/test_core_state.py` — get existing, get missing (null), set insert, set update, delete existing, delete missing (no-op), list all, list with prefix, JSONB value types

## 7. Session Log

- [ ] 7.1 Create `src/butlers/core/sessions.py` — session creation on runtime spawn, update on completion, sessions_list (paginated), sessions_get
- [ ] 7.2 Write `tests/test_core_sessions.py` — create, complete success, complete failure, duration_ms computation, trigger_source values, list pagination, get by id, append-only (no delete)

## 8. LLM CLI Spawner

- [ ] 8.1 Create `src/butlers/core/spawner.py` — SpawnerResult dataclass, CCSpawner class with ephemeral MCP config generation, CC SDK invocation, temp dir cleanup
- [ ] 8.2 Implement credential passthrough — build explicit env dict with only declared vars (ANTHROPIC_API_KEY + butler.env + module credentials_env)
- [ ] 8.3 Implement CLAUDE.md reading — pass as system_prompt, fallback to default if missing/empty
- [ ] 8.4 Implement serial dispatch with asyncio lock
- [ ] 8.5 Wire session logging — create session on spawn, update on completion/failure
- [ ] 8.6 Write `tests/test_core_spawner.py` — MCP config generation, temp dir cleanup, serial dispatch, MockSpawner assertions, env passthrough, CLAUDE.md handling

## 9. Task Scheduler

- [ ] 9.1 Create `src/butlers/core/scheduler.py` — TOML-to-DB sync, cron evaluation (croniter), tick() handler, serial dispatch to LLM CLI spawner
- [ ] 9.2 Implement schedule CRUD MCP tools — schedule_list, schedule_create (validate cron), schedule_update, schedule_delete (reject toml-source deletion)
- [ ] 9.3 Write `tests/test_core_scheduler.py` — TOML sync (first run, update, removal), tick dispatch, no-op tick, disabled tasks, error handling, next_run_at computation, CRUD operations

## 10. OpenTelemetry

- [ ] 10.1 Create `src/butlers/core/telemetry.py` — init_telemetry(service_name), tracer setup, no-op when endpoint not set
- [ ] 10.2 Add span wrappers for MCP tool handlers — butler.tool.<name> with butler.name attribute
- [ ] 10.3 Add trace context propagation helpers — _trace_context in MCP args, TRACEPARENT env var for CC
- [ ] 10.4 Write `tests/test_core_telemetry.py` — InMemorySpanExporter, span creation, parent-child relationships, error recording, no-op mode

## 11. Butler Daemon

- [ ] 11.1 Create `src/butlers/daemon.py` — ButlerDaemon class orchestrating startup sequence: config load → credential validation → DB provision → core Alembic migrations → butler Alembic migrations → module init → tool registration → server start
- [ ] 11.2 Implement core MCP tool registration — wire status(), tick(), trigger(), state_*, schedule_*, sessions_* tools on FastMCP server
- [ ] 11.3 Implement module tool registration — call register_tools(mcp, config, db) for each module in topological order
- [ ] 11.4 Implement graceful shutdown — stop connections, wait for in-flight CC, module on_shutdown (reverse order), close DB pool
- [ ] 11.5 Implement status() tool — butler identity, loaded modules, health, uptime
- [ ] 11.6 Write `tests/test_daemon.py` — startup sequence, tool registration, shutdown order, status tool

## 12. Butler Skills

- [ ] 12.1 Implement CLAUDE.md reading in LLM CLI spawner — read file contents, pass as system_prompt, default fallback
- [ ] 12.2 Ensure skills/ directory is accessible via CC cwd — LLM CLI spawner sets cwd to butler config dir
- [ ] 12.3 Write `tests/test_skills.py` — CLAUDE.md system prompt, missing CLAUDE.md fallback, AGENTS.md read/write, skill directory structure validation

## 13. Telegram Module

- [ ] 13.1 Create `src/butlers/modules/telegram.py` — Module implementation with identity-prefixed send/update tools
- [ ] 13.2 Implement polling mode for dev and webhook mode for production (configurable via butler.toml)
- [ ] 13.3 Write `tests/test_module_telegram.py` — tool registration, mocked API calls

## 14. Email Module

- [ ] 14.1 Create `src/butlers/modules/email.py` — Module implementation with identity-prefixed send/search/read tools
- [ ] 14.2 Write `tests/test_module_email.py` — tool registration, mocked API calls

## 15. Switchboard Butler

- [ ] 15.1 Create `alembic/versions/switchboard/` with initial Alembic revision — butler_registry and routing_log tables
- [ ] 15.2 Create `src/butlers/tools/switchboard.py` — route(), list_butlers(), discover() tools
- [ ] 15.3 Implement MCP client for inter-butler communication — SSE transport, trace context propagation via _trace_context
- [ ] 15.4 Implement routing flow — message intake → CC classification → route to target butler → return response
- [ ] 15.5 Create `butlers/switchboard/butler.toml` and `butlers/switchboard/CLAUDE.md`
- [ ] 15.6 Write `tests/test_tools_switchboard.py` — registry CRUD, routing, trace propagation, default-to-general fallback, discover()

## 16. Heartbeat Butler

- [ ] 16.1 Create `butlers/heartbeat/butler.toml` and `butlers/heartbeat/CLAUDE.md`
- [ ] 16.2 Write `tests/test_heartbeat.py` — tick cycle, butler enumeration, self-exclusion, error resilience, session logging

## 17. Relationship Butler

- [ ] 17.1 Create `alembic/versions/relationship/` with initial Alembic revision — all 16 tables + indexes
- [ ] 17.2 Create `src/butlers/tools/relationship.py` — contact CRUD tools (create, update, get, search, archive)
- [ ] 17.3 Add relationship tools — relationship_add (bidirectional), relationship_list, relationship_remove
- [ ] 17.4 Add date tools — date_add (partial dates), date_list, upcoming_dates (month/day matching)
- [ ] 17.5 Add note tools — note_create (with emotion), note_list, note_search
- [ ] 17.6 Add interaction tools — interaction_log, interaction_list
- [ ] 17.7 Add reminder tools — reminder_create (one_time/recurring), reminder_list, reminder_dismiss
- [ ] 17.8 Add gift tools — gift_add, gift_update_status (pipeline), gift_list
- [ ] 17.9 Add loan tools — loan_create, loan_settle, loan_list
- [ ] 17.10 Add group tools — group_create, group_add_member, group_list
- [ ] 17.11 Add label tools — label_create, label_assign, contact_search_by_label
- [ ] 17.12 Add quick facts tools — fact_set (upsert), fact_list
- [ ] 17.13 Add activity feed — feed_get + auto-population from mutating tools
- [ ] 17.14 Create `butlers/relationship/butler.toml` and `butlers/relationship/CLAUDE.md`
- [ ] 17.15 Write `tests/test_tools_relationship.py` — full CRUD coverage for all tool categories, bidirectional relationships, upcoming dates, feed auto-population

## 18. Health Butler

- [ ] 18.1 Create `alembic/versions/health/` with initial Alembic revision — measurements, medications, medication_doses, conditions, meals, symptoms, research tables + indexes
- [ ] 18.2 Create `src/butlers/tools/health.py` — measurement tools (log, history, latest)
- [ ] 18.3 Add medication tools — medication_add, medication_list, medication_log_dose, medication_history (with adherence rate)
- [ ] 18.4 Add condition tools — condition_add, condition_list, condition_update
- [ ] 18.5 Add diet tools — meal_log, meal_history, nutrition_summary (aggregation)
- [ ] 18.6 Add symptom tools — symptom_log, symptom_history, symptom_search
- [ ] 18.7 Add research tools — research_save, research_search, research_summarize
- [ ] 18.8 Add report tools — health_summary, trend_report
- [ ] 18.9 Create `butlers/health/butler.toml` and `butlers/health/CLAUDE.md`
- [ ] 18.10 Write `tests/test_tools_health.py` — full coverage for all tool categories, adherence rate, nutrition aggregation, trend reports

## 19. General Butler

- [ ] 19.1 Create `alembic/versions/general/` with initial Alembic revision — collections and entities tables + GIN indexes
- [ ] 19.2 Create `src/butlers/tools/general.py` — entity CRUD (create, get, update with deep merge, search, delete), collection CRUD, export tools
- [ ] 19.3 Create `butlers/general/butler.toml` and `butlers/general/CLAUDE.md`
- [ ] 19.4 Write `tests/test_tools_general.py` — entity CRUD, deep merge, search (GIN), collection management, export, freeform JSONB

## 20. CLI

- [ ] 20.1 Create `src/butlers/cli.py` — Click CLI with `butlers up`, `butlers run`, `butlers list`, `butlers init` commands
- [ ] 20.2 Implement `butlers up` — discover all butler.toml configs, start all in single asyncio event loop, `--only` filtering
- [ ] 20.3 Implement `butlers run --config` — single butler daemon startup
- [ ] 20.4 Implement `butlers list` — discover and display butler names, ports, modules
- [ ] 20.5 Implement `butlers init <name> --port` — scaffold config directory with butler.toml, CLAUDE.md, AGENTS.md, skills/
- [ ] 20.6 Implement graceful shutdown on SIGINT/SIGTERM
- [ ] 20.7 Add `[project.scripts]` entry point to pyproject.toml
- [ ] 20.8 Write `tests/test_cli.py` — command invocation, discovery, init scaffolding, signal handling

## 21. Docker & Deployment

- [ ] 21.1 Create `Dockerfile` — python:3.12-slim, Node.js 22, claude-code, uv
- [ ] 21.2 Create `docker-compose.yml` — postgres:17, LGTM stack (grafana, tempo, loki, alloy), switchboard, general, relationship, health, heartbeat services
- [ ] 21.3 Verify `docker compose up -d postgres` with LGTM stack && butlers up` works (dev quick start)
- [ ] 21.4 Verify `docker compose up -d` works (production quick start)

## 22. Integration Tests

- [ ] 22.1 Write integration test: full butler startup with testcontainer PostgreSQL + MockSpawner — config load → DB provision → Alembic migrations → tool registration → status()
- [ ] 22.2 Write integration test: scheduler tick dispatches to MockSpawner and logs session
- [ ] 22.3 Write integration test: switchboard route() forwards to target butler and returns result
- [ ] 22.4 Write integration test: trace context propagates from switchboard.route → butler.trigger → runtime session
