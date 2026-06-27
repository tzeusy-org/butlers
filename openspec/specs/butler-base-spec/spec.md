# Butler Architecture and Roster Conventions

## Purpose
Defines what a "butler" is as an architectural primitive, the shared conventions all butlers follow, and the extensible module system that allows new domain-specialist butlers to be added. This is the foundational spec — individual butler role specs (`butler-{name}/spec.md`) describe each role's domain-specific behavior.

## Requirements

### Requirement: Butler as Architectural Primitive
A butler SHALL be a long-lived MCP server daemon backed by a dedicated PostgreSQL schema. When triggered, it SHALL spawn an ephemeral LLM CLI session wired exclusively to itself via a locked-down MCP config. The butler SHALL be the unit of deployment, isolation, and capability composition. Butlers SHALL be one of two agent types in the ecosystem; the other is staffers (see `staffer-archetype` spec).

#### Scenario: Butler identity contract
- **WHEN** a butler daemon starts
- **THEN** it is uniquely identified by a `name` string (e.g., `"general"`, `"health"`)
- **AND** its `butler.toml` has `type = "butler"` (or omits the `type` field, defaulting to `"butler"`)
- **AND** it binds a FastMCP SSE server to its assigned port (e.g., 41101)
- **AND** it operates within a single PostgreSQL database (`butlers`) in its own schema (e.g., `general`, `health`)
- **AND** it also has access to the `public` schema for cross-butler data (secrets, shared contacts, etc.)
- **AND** its search_path is set to `[butler_schema, shared, public]` — preventing direct access to other butlers' schemas

#### Scenario: Butler as MCP server
- **WHEN** the butler daemon is running
- **THEN** it exposes a FastMCP SSE endpoint at `http://localhost:{port}/mcp`
- **AND** this endpoint serves the butler's full tool surface: core tools + module tools + butler-specific tools
- **AND** each incoming MCP connection is wrapped with a session guard that binds a runtime session context

#### Scenario: Ephemeral LLM CLI sessions
- **WHEN** a trigger arrives (scheduled task, routed message, manual trigger)
- **THEN** the spawner generates an ephemeral MCP config pointing exclusively to the butler's own MCP server
- **AND** it composes a system prompt from `roster/{butler-name}/CLAUDE.md`, optionally appending memory context
- **AND** it invokes an LLM CLI runtime (Claude Code, Codex, or Gemini) as a subprocess with the MCP config and system prompt
- **AND** the runtime session is short-lived — it runs, calls tools, and exits
- **AND** the session is logged with prompt, output, success/error, token counts, cost, tool calls, duration, and trace ID

#### Scenario: Butler daemon lifecycle phases
- **WHEN** the butler starts up
- **THEN** it progresses through phases in order: config loading → telemetry init → module loading (topological sort) → config validation → env validation → database provisioning → core migrations → butler-specific migrations → module migrations → credential store setup → core credential validation → module startup → spawner creation → pipeline wiring (switchboard only) → schedule sync → switchboard connection (non-switchboard only) → FastMCP server start → approval gate application → buffer start (switchboard only) → route inbox recovery → heartbeat task → scheduler loop → liveness reporter
- **AND** module failures during any phase are non-fatal — a failed module is marked as unavailable while the butler continues operating

#### Scenario: Graceful shutdown
- **WHEN** the butler receives a shutdown signal
- **THEN** it stops accepting new MCP connections
- **AND** drains in-flight sessions within `shutdown_timeout_s` (configurable, default varies by butler)
- **AND** cancels background tasks (scheduler, heartbeat, liveness) in reverse startup order
- **AND** calls `on_shutdown()` on each module in reverse topological order
- **AND** closes the database connection pool

### Requirement: Tool Composition Model
A butler's tool surface SHALL be composed from three layers: core tools (always present), module tools (opt-in per butler), and butler-specific tools (unique to one role). Tools SHALL be registered during startup and immutable at runtime.

#### Scenario: Core tools (always present)
- **WHEN** the butler daemon starts
- **THEN** it registers core tools that every butler has: `status` (identity + health), `trigger` (manual session spawn), `route.execute` (accept routed requests), `tick` (scheduler dispatch), `notify` (outbound delivery via Switchboard→Messenger), `remind` (schedule a future prompt), `get_attachment` (retrieve stored blobs)
- **AND** state tools: `state_get`, `state_set`, `state_delete`, `state_list` (KV JSONB store)
- **AND** schedule tools: `schedule_list`, `schedule_create`, `schedule_update`, `schedule_delete`
- **AND** session tools: `sessions_list`, `sessions_get`
- **AND** module introspection: `module.states` (health of all modules), `module.set_enabled` (toggle a module at runtime)

#### Scenario: Module tools (opt-in per butler)
- **WHEN** a butler declares modules in `butler.toml` (e.g., `[modules.memory]`, `[modules.telegram]`)
- **THEN** each enabled module's `register_tools()` method is called during startup
- **AND** the module adds its tools to the butler's MCP server (e.g., memory adds `memory_store_fact`, `memory_search`; email adds `email_send_message`, `email_search_inbox`)
- **AND** a proxy validates that modules only register tools they declared in their `tool_metadata()`

#### Scenario: Butler-specific tools
- **WHEN** a butler role has domain-specific tools not covered by shared modules
- **THEN** it implements them as pure async functions in `roster/{butler-name}/tools/` and wires them via a roster module package at `roster/{butler-name}/modules/` (with `__init__.py` for the Module class and additional `*.py` files for tool wiring)
- **AND** the roster module subclasses `Module`, registers each tool via `@mcp.tool()` closures in `register_tools()`, and injects the DB pool via `module._get_pool()` — the MCP caller never sees the pool parameter
- **AND** the butler's `butler.toml` declares `[modules.{butler-name}]` so the module is loaded at startup
- **AND** these tools are only available on that butler's MCP server (e.g., Relationship butler has `contact_resolve`, Health butler has `measurement_log`)
- **AND** without a roster module, tools in `roster/{butler-name}/tools/` are importable Python but NOT callable via MCP — the runtime LLM instance will not see them

#### Scenario: Tool gating via approvals
- **WHEN** a butler has the approvals module enabled with gated_tools configuration
- **THEN** configured tools are wrapped with an approval interceptor during startup
- **AND** calling a gated tool creates a pending approval instead of executing immediately
- **AND** each gated tool has configurable `expiry_hours` and `risk_tier` (low, medium, high, critical)
- **AND** standing rules can pre-approve matching patterns without human intervention

#### Scenario: Tool observability
- **WHEN** any tool is called on a butler's MCP server
- **THEN** the call is wrapped with an OpenTelemetry span (`tool_span()`) capturing tool name, arguments, result, and duration
- **AND** tool calls are captured in the session's `tool_calls` audit log

### Requirement: Module System
Modules SHALL be pluggable units that add domain-specific MCP tools and database tables to a butler. They SHALL follow an abstract base class contract and be resolved via topological dependency sort.

#### Scenario: Module ABC contract
- **WHEN** a module is implemented
- **THEN** it extends the `Module` abstract base class providing: `name` (unique string identifier), `config_schema` (Pydantic model for `[modules.{name}]` TOML section), `dependencies` (list of module names this module depends on)
- **AND** it implements: `register_tools(mcp, config, db, butler_name)` to add MCP tools, `migration_revisions()` returning an Alembic branch label or None, `on_startup(config, db, credential_store=None, blob_store=None)` for initialization, `on_shutdown()` for cleanup
- **AND** modules that need inter-butler communication (e.g., self_healing QA relay) implement the optional `wire_runtime()` hook, which the daemon calls after `on_startup()` and after the Switchboard connection is established (lifecycle step 13d) to inject the spawner and the daemon's Switchboard MCPClient (or None when switchboard is not configured); the module uses that client to relay findings via Switchboard's `route()` tool. The Switchboard client is NOT passed as an `on_startup()` kwarg

#### Scenario: Module tool metadata
- **WHEN** a module has tools with safety-sensitive arguments
- **THEN** it implements `tool_metadata()` returning a dict of tool names to `ToolMeta` with safety sensitivity annotations

#### Scenario: Module dependency resolution
- **WHEN** modules are loaded during butler startup
- **THEN** they are topologically sorted by their `dependencies` list
- **AND** if a module fails during any startup phase, all modules that depend on it are marked as `cascade_failed`
- **AND** each module's runtime state is tracked: health (`active` | `failed` | `cascade_failed`), enabled (bool), failure phase (`credentials` | `config` | `migration` | `startup` | `tools`), failure error (string)

#### Scenario: Module migration chains
- **WHEN** a module declares `migration_revisions()` returning a branch label (e.g., `"memory"`, `"contacts"`)
- **THEN** Alembic runs that module's migration chain during butler startup, scoped to the butler's schema
- **AND** migration failures are non-fatal — the module is marked as failed but the butler continues

#### Scenario: Available modules
- **WHEN** configuring a butler's `butler.toml`
- **THEN** shared modules (in `src/butlers/modules/`) include: `calendar` (unified calendar view, Google Calendar integration), `contacts` (Google sync, public schema, entity linkage), `memory` (episodes/facts/rules storage, hybrid search, embedding, consolidation), `telegram` (bot/user client tools), `email` (Gmail integration, IMAP/SMTP tools), `approvals` (gate wrapper, pending actions, standing rules)
- **AND** roster modules (in `roster/{butler-name}/modules/`) wire butler-specific tools as MCP tools — every butler that has domain tools in `roster/{butler-name}/tools/` must have a corresponding roster module package
- **AND** each module has its own nested TOML configuration (e.g., `modules.calendar.provider`, `modules.telegram.bot.token_env`, `modules.memory.consolidation.interval_hours`)
- **AND** roster modules typically need only an empty `[modules.{butler-name}]` section in `butler.toml`

### Requirement: Skills System
Skills SHALL be structured behavioral guides (per agentskills.io spec) that are loaded into runtime sessions to provide domain expertise, workflows, and decision frameworks. They SHALL be documentation-as-configuration — not executable code within the daemon.

#### Scenario: Skill file format
- **WHEN** a skill is defined
- **THEN** it lives at `roster/{butler-name}/.agents/skills/{skill-name}/SKILL.md` (butler-specific) or `roster/shared/skills/{skill-name}/SKILL.md` (shared across butlers)
- **AND** the SKILL.md file contains YAML frontmatter (`name`, `description`, `version`, optional `trigger_patterns`) followed by markdown content defining usage guides, templates, workflows, and examples

#### Scenario: Skill loading into runtime sessions
- **WHEN** the spawner composes a system prompt for an ephemeral LLM CLI session
- **THEN** the butler's available skills are discoverable by the runtime instance
- **AND** the runtime can follow skill instructions to execute multi-step workflows using the butler's MCP tools
- **AND** skills may reference specific tools by name (e.g., "call `entity_resolve` before storing a fact")

#### Scenario: Shared skills
- **WHEN** a skill is used across multiple butlers
- **THEN** it lives in `roster/shared/skills/{skill-name}/SKILL.md`
- **AND** it is symlinked into each consuming butler's `.agents/skills/` directory
- **AND** current shared skills include:
  - `butler-memory`: Entity resolution before storage, permanence levels (`permanent`, `stable`, `standard`, `volatile`), fact anchoring to `entity_id`, JSON array tags for cross-cutting queries
  - `butler-notifications`: Notification delivery via `notify()` with required parameters (`channel`, `message`/`emoji`, `intent`), intent selection (`send`, `reply`, `react`), `request_context` propagation

#### Scenario: Butler-specific skills
- **WHEN** a butler has domain-specific workflows
- **THEN** it places skills in `roster/{butler-name}/.agents/skills/{skill-name}/SKILL.md`
- **AND** these skills are only available to that butler's runtime sessions
- **AND** skills may have optional companion scripts alongside the SKILL.md

#### Scenario: Skills contain workflow-specific content
- **WHEN** a skill is authored
- **THEN** it contains content that would be too verbose for AGENTS.md: multi-step procedures with explicit MCP tool call sequences, decision frameworks with branching logic, classification rules and routing tables, complete worked examples with tool calls and expected outputs, output templates and formatting guides, memory classification taxonomies, and error handling procedures
- **AND** this content is loaded into the runtime only when the skill is relevant to the current trigger

#### Scenario: Scheduled task skills
- **WHEN** a butler has prompt-dispatched scheduled tasks in `butler.toml`
- **THEN** each scheduled task has a corresponding skill documenting its full execution workflow
- **AND** the skill documents both the action path (when there is work to report) and the no-op path (when there is nothing to report)

### Requirement: Database Isolation Model
All butlers SHALL share a single PostgreSQL database (`butlers`) with per-butler schema isolation. The `public` schema SHALL provide cross-butler data access. Inter-butler communication SHALL be MCP-only through the Switchboard.

#### Scenario: Per-butler schema isolation (MODIFIED)
- **WHEN** a butler connects to the database
- **THEN** its asyncpg connection pool sets `server_settings = {"search_path": "{butler_schema},public"}`
- **AND** the pool's `setup` callback executes `SET ROLE "butler_{schema}_rw"` on every connection acquired from the pool
- **AND** PostgreSQL enforces that the butler can only write to its own schema and to specifically authorized public tables
- **AND** no butler can directly read or write another butler's schema
- **AND** asyncpg's built-in `RESET ALL` on connection return resets the role for pool safety

#### Scenario: SET ROLE graceful fallback (ADDED)
- **WHEN** a butler starts up and the runtime role (`butler_{schema}_rw`) does not exist in `pg_roles`
- **THEN** the butler logs a warning: "Role {role} not found; SET ROLE enforcement disabled"
- **AND** the connection pool is created without the `setup` callback
- **AND** the butler operates with the shared database user's privileges (same as pre-enforcement behavior)
- **AND** this fallback ensures development environments without CREATEROLE work without additional setup

#### Scenario: Public schema write authorization (ADDED)
- **WHEN** a butler operates under `SET ROLE` enforcement
- **THEN** it can INSERT, UPDATE, or DELETE rows in specifically authorized public tables (per the write authorization matrix in the database-security spec)
- **AND** it can SELECT from all public tables (unchanged from prior behavior)
- **AND** attempting to write to a public table not in the authorization matrix raises a PostgreSQL permission error

#### Scenario: Public schema (MODIFIED)
- **WHEN** cross-butler data is needed
- **THEN** it lives in the `public` schema (e.g., shared secrets, shared contacts, credential store)
- **AND** all butlers have read access to `public` via their search_path
- **AND** write access to specific `public` tables is granted by the `core_065` migration to all butler runtime roles
- **AND** the write authorization matrix is maintained in the `database-security` spec

#### Scenario: Dashboard API privileged access (ADDED)
- **WHEN** the dashboard API connects to the database
- **THEN** it uses the privileged shared database user without `SET ROLE`
- **AND** it intentionally has cross-schema read/write access for fan-out queries and aggregate views
- **AND** the `DatabaseManager` in `src/butlers/api/db.py` is not affected by SET ROLE enforcement

#### Scenario: Migration strategy
- **WHEN** database migrations run during butler startup
- **THEN** three migration chains execute in order: `core` (shared infrastructure tables), `{butler_name}` (butler-specific tables, if the chain exists), and each module's chain (scoped to the butler's schema)
- **AND** Alembic manages all chains with branch labels for isolation

#### Scenario: Inter-butler data exchange
- **WHEN** one butler needs data from another
- **THEN** it communicates via MCP calls through the Switchboard routing plane — never by direct schema access
- **AND** the `public` schema is reserved for truly shared reference data, not for passing messages between butlers

### Requirement: Staffers vs Domain Butlers
The roster SHALL contain two categories of butlers: staffers that provide essential infrastructure services and SHALL always be present (configured with `type = "staffer"`), and domain butlers that provide specialist capabilities and can be added or removed.

#### Scenario: Staffer — Switchboard
- **WHEN** the system is running
- **THEN** the Switchboard staffer (port 41100) must be present as the sole entry point for all inbound messages
- **AND** it classifies incoming messages and routes them to the appropriate domain butler (never to other staffers for user-message routing)
- **AND** it maintains the durable ingestion buffer with priority-tiered queuing and crash-recovery scanning
- **AND** it manages the connector registry (which connectors are active, their health, eligibility)
- **AND** no other agent receives external messages directly — all inbound traffic flows through Switchboard
- **AND** Switchboard itself does not register with another switchboard (it IS the switchboard)

#### Scenario: Staffer — Messenger
- **WHEN** the system is running
- **THEN** the Messenger staffer (port 41104) must be present as the sole owner of outbound channel delivery
- **AND** it owns all channel egress tools: `telegram_send_message`, `telegram_reply_to_message`, `email_send_message`, `email_reply_to_thread`
- **AND** non-messenger agents that attempt to register channel egress tools have them silently stripped during startup
- **AND** all other agents must use the `notify()` core tool which routes delivery requests through Switchboard to Messenger
- **AND** Messenger has no schedules, no domain skills, and no autonomous behavior — it is a pure execution plane

#### Scenario: Domain butlers (extensible roster)
- **WHEN** a new domain specialist is needed
- **THEN** a new butler is added to `roster/{butler-name}/` following the roster conventions
- **AND** it registers with the Switchboard at startup (`[butler.switchboard]` with `advertise = true`)
- **AND** it launches a heartbeat task and liveness reporter to maintain its registration
- **AND** it can be added or removed without affecting other agents' operation
- **AND** current domain butlers include: General (41101, catch-all), Relationship (41102, personal CRM), Health (41103, health tracking), Finance (41105, personal finance), Travel (41106, trip logistics)

### Requirement: Spawner and Runtime Adapters
The spawner SHALL generate ephemeral MCP configurations and invoke LLM CLI runtimes as subprocesses. Each runtime adapter SHALL translate the common invocation contract into adapter-specific CLI arguments.

#### Scenario: Ephemeral MCP config generation
- **WHEN** a session is triggered
- **THEN** the spawner generates a JSON MCP config containing the butler's own SSE endpoint URL (`http://localhost:{port}/mcp`) with an optional `runtime_session_id` query parameter for correlation
- **AND** the config is injected via the `MCP_SERVERS` environment variable
- **AND** no other MCP servers are included — the runtime is locked to its parent butler

#### Scenario: Environment sandboxing
- **WHEN** the spawner builds the subprocess environment
- **THEN** it starts with a minimal baseline: only `PATH` plus explicitly declared `[butler.env]` vars
- **AND** module credentials are resolved via the CredentialStore (DB-first with env fallback)
- **AND** runtime authentication uses CLI-level OAuth tokens (device-code flow via the dashboard Settings page), not API keys
- **AND** no undeclared environment variables leak from the host to the runtime subprocess

#### Scenario: Runtime adapter selection
- **WHEN** the daemon seeds its Spawner adapter pool at startup
- **THEN** the default adapter is resolved from the process-wide constant `DEFAULT_RUNTIME_TYPE` in `butlers.core.runtimes` (currently `"codex"`) — there is no per-butler `[runtime]` knob in `butler.toml`
- **AND** per-session runtime type overrides come from `public.model_catalog` via `resolve_model()`, which may instantiate any registered adapter (CodexAdapter, ClaudeCodeAdapter, GeminiAdapter, OpenCodeAdapter) lazily on demand
- **AND** each adapter implements `async invoke(prompt, system_prompt, mcp_servers, env, ...)` returning `(result_text, tool_calls, usage_dict)`

#### Scenario: Concurrency control
- **WHEN** multiple triggers arrive concurrently
- **THEN** the spawner enforces `butler.runtime.max_concurrent_sessions` as a concurrency limit
- **AND** excess triggers are queued up to `max_queued` (configurable)
- **AND** queue overflow triggers are rejected with a capacity error

### Requirement: Roster Directory Structure Convention
Every butler SHALL live in `roster/{butler-name}/` and follow a fixed directory layout. The roster SHALL be the single source of truth for butler identity, personality, and configuration. Files SHALL be git-versioned and loaded at daemon startup.

#### Scenario: Required config files
- **WHEN** a new butler directory is created under `roster/`
- **THEN** it contains at minimum: `butler.toml` (identity, runtime, database, modules, schedules), `MANIFESTO.md` (public-facing purpose and value proposition), `CLAUDE.md` (system prompt entry point — contains `@AGENTS.md`), and `AGENTS.md` (butler identity, tool summary, behavioral guidelines, skill references, and runtime agent notes — see AGENTS.md Content Principles)

#### Scenario: Roster module package (required when butler has domain tools)
- **WHEN** a butler defines domain-specific tools in `roster/{butler-name}/tools/`
- **THEN** a roster module package at `roster/{butler-name}/modules/` is required to wire those tools as MCP tools
- **AND** the package contains `__init__.py` (Module class with boilerplate: config, lifecycle, `_get_pool()`) and one or more `*.py` files with the `@mcp.tool()` closure registrations
- **AND** the module is auto-discovered by `_register_roster_modules()` in `src/butlers/modules/registry.py` — no manual registration needed
- **AND** the butler's `butler.toml` declares `[modules.{butler-name}]` so the module is loaded at startup

#### Scenario: Optional subdirectories
- **WHEN** a butler has domain-specific features
- **THEN** skills are placed in `roster/{butler-name}/.agents/skills/{skill-name}/SKILL.md` with optional companion scripts
- **AND** dashboard API routes are placed in `roster/{butler-name}/api/router.py` (exporting a module-level `router` APIRouter variable) with optional `models.py`
- **AND** no `__init__.py` is required in `api/`; auto-discovery handles registration via `src/butlers/api/router_discovery.py`

### Requirement: butler.toml Configuration Schema
The `butler.toml` file SHALL declare butler identity, runtime, database, modules, schedules, switchboard registration, and buffer configuration. All agents (butlers and staffers) SHALL share the same schema with agent-specific values.

#### Scenario: Identity section
- **WHEN** `butler.toml` is loaded
- **THEN** `[butler]` provides: `name` (unique identifier), `port` (FastMCP SSE server port), `description` (human-readable purpose string), `type` (agent type: `"butler"` or `"staffer"`, default `"butler"`)

#### Scenario: Runtime configuration
- **WHEN** the runtime-related sections of `butler.toml` are parsed
- **THEN** `[butler.runtime_seed]` is the sole butler-scoped runtime block and contains operational seed fields only: `core_groups`, `max_concurrent_sessions`, `max_queued_sessions`, `liveness_ttl_seconds`, `route_contract_min`, `route_contract_max`
- **AND** the default runtime adapter type is fixed for the whole roster by the `DEFAULT_RUNTIME_TYPE` constant in `butlers.core.runtimes`; there is no per-butler adapter knob in git
- **AND** the legacy sections `[butler.runtime]`, `[butler.seed_configs]`, and top-level `[runtime]` are all rejected at load time with a `ConfigError` (a roster butler.toml that re-introduces any of them fails fast)
- **AND** model identity, per-session timeout, CLI args, and runtime type overrides live in `public.model_catalog` (resolved per spawn by `resolve_model()`); they must not be duplicated in `butler.toml`

#### Scenario: Database configuration
- **WHEN** `[butler.db]` specifies `name = "butlers"` and `schema = "{butler-name}"`
- **THEN** the agent operates in a consolidated PostgreSQL database with per-agent schema isolation plus access to the `public` schema

#### Scenario: Module declarations
- **WHEN** `[modules.*]` sections are present
- **THEN** each section enables an opt-in module with module-specific nested configuration
- **AND** examples: `modules.calendar.provider = "google"`, `modules.calendar.conflicts.policy = "suggest"`, `modules.contacts.sync.interval_minutes = 60`, `modules.telegram.bot.token_env = "BUTLER_TELEGRAM_TOKEN"`, `modules.memory.consolidation.interval_hours = 24`

#### Scenario: Schedule declarations
- **WHEN** `[[butler.schedule]]` entries are present
- **THEN** each entry declares a scheduled task with `name`, `cron` expression, and dispatch mode
- **AND** `dispatch_mode = "prompt"` with `prompt` string triggers a runtime session with that prompt
- **AND** `dispatch_mode = "job"` with `job_name` string executes a native Python job function without spawning a runtime

#### Scenario: Switchboard registration (non-switchboard agents)
- **WHEN** `[butler.switchboard]` section is present with `advertise = true`
- **THEN** the agent registers itself with the Switchboard at startup, enabling routing discovery
- **AND** the registration includes the agent's `type` field so the switchboard can distinguish butlers from staffers
- **AND** configuration includes: `url` (Switchboard MCP endpoint), `liveness_ttl_s` (registration expiry), `route_contract_min`/`route_contract_max` (supported contract versions)

#### Scenario: Permissions section (staffers)
- **WHEN** `[butler.permissions]` section is present
- **THEN** `cross_butler_access` specifies a list of agent names this agent may connect to or act on behalf of
- **AND** `["*"]` grants access to all agents
- **AND** this section is typically used by staffers; butlers default to empty (no cross-butler access)

#### Scenario: Buffer configuration (switchboard only)
- **WHEN** `[buffer]` section is present on the switchboard agent
- **THEN** it configures the durable ingestion buffer: `queue_capacity` (bounded in-memory queue size), `worker_count` (concurrent dispatch workers), `scanner_interval_s` (cold-path DB recovery scan interval), `scanner_grace_s` (minimum age before scanner reclaims), `scanner_batch_size` (max items per scan)

### Requirement: Scheduled Task Companion Skills
Scheduled tasks declared in `butler.toml` with `dispatch_mode = "prompt"` SHALL have corresponding skills that document the complete tool sequence and decision logic the runtime should follow.

#### Scenario: Prompt-dispatched scheduled task has a companion skill
- **WHEN** a `[[butler.schedule]]` entry uses `dispatch_mode = "prompt"`
- **THEN** a companion skill exists in `roster/{butler-name}/.agents/skills/` documenting the full workflow for that scheduled task
- **AND** the schedule entry's `prompt` field references the skill and provides the trigger context (e.g., "Run the upcoming-bills-check workflow. Horizon: 14 days, include overdue.")
- **AND** the companion skill contains: the complete tool call sequence, output formatting, notification delivery via `notify(intent="send")`, and the no-op path (when there is nothing to report)

#### Scenario: Job-dispatched scheduled tasks are exempt
- **WHEN** a `[[butler.schedule]]` entry uses `dispatch_mode = "job"`
- **THEN** no companion skill is required because the job executes native Python code without spawning a runtime session

### Requirement: MANIFESTO.md as Public Identity
Each butler SHALL have a `MANIFESTO.md` that defines its value proposition, target user persona, and feature scope. Staffers SHALL have a `MANIFESTO.md` with infrastructure-contract framing (SLAs, responsibilities, failure modes). The manifesto/contract SHALL be the governing document for scope decisions.

#### Scenario: Manifesto-driven scope governance (butlers)
- **WHEN** a new feature or tool is proposed for a butler
- **THEN** it must be evaluated against the butler's MANIFESTO.md for alignment with the stated purpose and scope boundaries
- **AND** features that fall outside the manifesto's scope should be directed to a different butler or require a manifesto amendment

#### Scenario: Contract-driven scope governance (staffers)
- **WHEN** a new capability is proposed for a staffer
- **THEN** it must be evaluated against the staffer's MANIFESTO.md infrastructure contract for alignment with stated responsibilities
- **AND** capabilities outside the contract's scope require a contract amendment

### Requirement: CLAUDE.md as System Prompt Entry Point
Each butler's `CLAUDE.md` SHALL be loaded as the system prompt for every runtime instance spawned by that butler. In practice, `CLAUDE.md` delegates to `AGENTS.md` via an `@AGENTS.md` file reference, and `AGENTS.md` in turn pulls shared instructions via `@../shared/AGENTS.md`. This indirection chain SHALL allow butler-specific and shared content to be maintained independently while composing into a single system prompt at runtime.

#### Scenario: System prompt composition
- **WHEN** the spawner invokes a runtime instance
- **THEN** it reads `roster/{butler-name}/CLAUDE.md` and uses its contents as the base system prompt
- **AND** if the memory module is active, memory context is appended after a blank line separator
- **AND** no other system prompt sources are injected

#### Scenario: Interactive response mode
- **WHEN** a runtime instance receives a REQUEST CONTEXT JSON block with a `source_channel` field (e.g., `telegram_bot`, `email`)
- **THEN** it engages interactive response mode as defined in the butler's CLAUDE.md
- **AND** selects from response styles: React (emoji only), Affirm (acknowledgment), Follow-up (clarifying question), Answer (substantive response), or React+Reply (emoji + response)

#### Scenario: CLAUDE.md delegates to AGENTS.md via file reference
- **WHEN** a butler's `CLAUDE.md` is authored
- **THEN** it contains `@AGENTS.md` as its sole content — a runtime file reference that the LLM CLI resolves to the contents of the butler's `AGENTS.md`
- **AND** this `@file` reference is resolved by the runtime (Claude Code, Codex, Gemini), not by the butler's include resolution logic
- **AND** the separate `<!-- @include path -->` directive handled by `read_system_prompt()` remains available for HTML-comment-style includes resolved at the butler layer

#### Scenario: AGENTS.md composes shared instructions
- **WHEN** a butler's `AGENTS.md` is authored
- **THEN** its first line is `@../shared/AGENTS.md` — pulling shared butler instructions (tool execution contract, calendar usage, notification references)
- **AND** the remainder of the file contains butler-specific content following the AGENTS.md Content Principles

### Requirement: AGENTS.md Content Principles
AGENTS.md SHALL be loaded into every runtime session via the CLAUDE.md indirection chain. Because it contributes to every session's context window, it SHALL contain only general-purpose butler information. Detailed workflows, multi-step procedures, and extensive examples SHALL belong in skills, which are loaded on demand.

#### Scenario: AGENTS.md contains only general-purpose information
- **WHEN** a butler's AGENTS.md is authored or updated
- **THEN** it contains: butler identity (name, role, one-paragraph purpose), tool surface summary (tool names with one-line descriptions), behavioral guidelines (concise rules, not multi-step procedures), calendar usage (domain-specific lines only — shared rules come from `@../shared/AGENTS.md`), skill references (one-liner pointers to skills for specific workflows), and a `# Notes to self` section for runtime agent notes
- **AND** it does NOT inline: multi-step workflow procedures with tool call sequences, extensive examples (more than one brief example per section), decision frameworks with branching logic, classification rule tables, memory classification taxonomies with example code, or scheduled task execution logic

#### Scenario: Workflow content lives in skills
- **WHEN** a butler has a multi-step workflow (e.g., fact extraction pipeline, message classification rules, health check-in flow, bill review triage)
- **THEN** that workflow is documented as a skill in `roster/{butler-name}/.agents/skills/{skill-name}/SKILL.md`
- **AND** the AGENTS.md references the skill with a one-liner (e.g., "For the conversational fact extraction workflow, consult the `fact-extraction` skill.")
- **AND** the skill contains the complete procedure: tool call sequences, decision frameworks, classification rules, complete worked examples, and output templates

#### Scenario: Memory classification taxonomy lives in skills
- **WHEN** a butler defines a domain-specific memory classification taxonomy (subjects, predicates, permanence levels, tags, example facts)
- **THEN** the taxonomy is documented in a dedicated skill or incorporated into the shared `butler-memory` skill
- **AND** the AGENTS.md contains at most a brief summary (e.g., "Uses subject/predicate memory model — see `memory-taxonomy` skill for domain taxonomy and examples")

#### Scenario: Token efficiency motivation
- **WHEN** a runtime session is spawned for a specific trigger (scheduled task, routed message, user question)
- **THEN** only the AGENTS.md general-purpose content is loaded automatically into the context window
- **AND** the runtime loads relevant skills on demand based on the trigger context
- **AND** this minimizes context window usage for sessions that only need a subset of the butler's capabilities

### Requirement: Credential Resolution
Butler credentials SHALL follow a layered resolution strategy: database-first from the `shared.secrets` table, with environment variable fallback.

#### Scenario: Credential resolution order
- **WHEN** a butler or module needs a credential (API key, OAuth token, etc.)
- **THEN** the CredentialStore first checks the `shared.secrets` table in PostgreSQL
- **AND** if not found, falls back to the environment variable specified in the module config (e.g., `token_env = "BUTLER_TELEGRAM_TOKEN"`)
- **AND** runtime authentication is via CLI-level OAuth tokens; no API key validation is performed at startup

#### Scenario: Credential isolation in runtime sessions
- **WHEN** the spawner builds the subprocess environment for a runtime session
- **THEN** only explicitly declared credentials are included — no host environment variables leak
- **AND** this prevents runtime sessions from accessing credentials they were not granted

### Requirement: Port Assignment Convention
Butlers SHALL use a contiguous port range starting at 41100, with the dashboard API cleanly separated.

#### Scenario: Butler port assignments
- **WHEN** butlers are running
- **THEN** ports are assigned as: switchboard=41100, general=41101, relationship=41102, health=41103, messenger=41104, finance=41105, travel=41106
- **AND** the dashboard API runs at port 41200, cleanly separated from the butler MCP port range
- **AND** new butlers are assigned the next available port in the 411xx range

### Requirement: Module Runtime State
Each module's health SHALL be tracked at runtime, enabling graceful degradation without butler-wide failure.

#### Scenario: Module health states
- **WHEN** a module is loaded during butler startup
- **THEN** its runtime state includes: `health` (`active` | `failed` | `cascade_failed`), `enabled` (bool, persisted to state store for stickiness across restarts), `failure_phase` (which startup phase failed: `credentials`, `config`, `migration`, `startup`, `tools`), `failure_error` (error message string)

#### Scenario: Non-fatal module degradation
- **WHEN** a module fails during any startup phase
- **THEN** the butler marks it as `failed` and continues starting remaining modules
- **AND** any module that depends on the failed module is marked as `cascade_failed`
- **AND** tools from failed modules are not registered on the MCP server
- **AND** the butler remains operational with reduced capability

### Requirement: Instance Facts Internal Interface

Each butler daemon SHALL expose an internal interface by which the dashboard API layer
can read instance-level facts about that butler. These facts are already computed by the
daemon during normal operation (heartbeat, liveness registration, session creation);
this requirement codifies the contract so the System Overview page aggregator has a
normative interface to consume.

This requirement covers only the contractual shape of the data the System page expects.
The physical access path (liveness registry table, `{schema}.sessions` table) is
documented in the `system-overview-page` spec. asyncpg pool stats are explicitly out
of scope for v1 (they require in-process access the dashboard API layer does not have).
This requirement defines what the daemon is responsible for maintaining.

#### Scenario: Heartbeat registration is kept current

- **WHEN** a butler daemon is running
- **THEN** its heartbeat task fires at least once every `liveness_ttl_seconds / 2` seconds
- **AND** each heartbeat upserts the butler's liveness record in the switchboard
  liveness registry with the current UTC timestamp
- **AND** if the heartbeat task fails, the butler logs the failure but does not shut
  down -- liveness degradation is observable but not fatal

#### Scenario: Session completion updates the per-butler session record

- **WHEN** an ephemeral LLM session completes (success or failure)
- **THEN** the session row in `{schema}.sessions` is updated with:
  - `completed_at: timestamptz` -- the UTC timestamp at session completion (was NULL
    while the session was active; a non-NULL value signals terminal state)
  - `success: boolean` -- `true` if the session completed successfully, `false` if it
    failed. Note: there is no `status` text column; the actual schema uses `success`
    (boolean) and `completed_at` (timestamptz) as the two terminal-state fields.
- **AND** this row is the source of truth for `last_session_at` in the System page
  heartbeat endpoint

#### Scenario: Active session count is derivable from the sessions table

- **WHEN** the System page queries active sessions for a butler
- **THEN** it derives the count from `SELECT COUNT(*) FROM {schema}.sessions WHERE
  completed_at IS NULL` -- no dedicated active-session counter table is required.
  Note: there is no `status` column; a session is active when `completed_at IS NULL`
  (see `src/butlers/core/sessions.py` `sessions_active` for the canonical query).
- **AND** this query is safe to run concurrently with session creation and completion
  without locking

#### Scenario: DB connection pool stats are not exposed in v1

- **WHEN** the System page reads per-butler facts in v1
- **THEN** asyncpg connection pool statistics (min_size, max_size, pool_size, in-use
  connections) are NOT surfaced via the System page endpoints
- **AND** this is a deliberate v1 simplification -- pool stats require in-process
  access that the dashboard API layer does not have without an additional internal
  API
- **AND** pool stats are marked as a forward-path item to be addressed if the System
  page adds real-time resource monitoring
