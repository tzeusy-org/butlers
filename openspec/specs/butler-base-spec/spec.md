# Butler Framework and Roster Conventions

## Purpose
Defines what a "butler" is as an architectural primitive, the shared conventions all butlers follow, and the extensibility model that allows new domain-specialist butlers to be added. This is the foundational spec — individual butler role specs (`butler-{name}/spec.md`) describe each role's domain-specific behavior.

## ADDED Requirements

### Requirement: Butler as Architectural Primitive
A butler is a long-lived MCP server daemon backed by a dedicated PostgreSQL schema. When triggered, it spawns an ephemeral LLM CLI session wired exclusively to itself via a locked-down MCP config. The butler is the unit of deployment, isolation, and capability composition.

#### Scenario: Butler identity contract
- **WHEN** a butler daemon starts
- **THEN** it is uniquely identified by a `name` string (e.g., `"general"`, `"health"`, `"switchboard"`)
- **AND** it binds a FastMCP SSE server to its assigned port (e.g., 40101)
- **AND** it operates within a single PostgreSQL database (`butlers`) in its own schema (e.g., `general`, `health`)
- **AND** it also has access to the `shared` schema for cross-butler data (secrets, shared contacts, etc.)
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
A butler's tool surface is composed from three layers: core tools (always present), module tools (opt-in per butler), and butler-specific tools (unique to one role). Tools are registered during startup and immutable at runtime.

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
Modules are pluggable units that add domain-specific MCP tools and database tables to a butler. They follow an abstract base class contract and are resolved via topological dependency sort.

#### Scenario: Module ABC contract
- **WHEN** a module is implemented
- **THEN** it extends the `Module` abstract base class providing: `name` (unique string identifier), `config_schema` (Pydantic model for `[modules.{name}]` TOML section), `dependencies` (list of module names this module depends on)
- **AND** it implements: `register_tools(mcp, config, db)` to add MCP tools, `migration_revisions()` returning an Alembic branch label or None, `on_startup(config, db, credential_store)` for initialization, `on_shutdown()` for cleanup

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
- **THEN** shared modules (in `src/butlers/modules/`) include: `calendar` (unified calendar view, Google Calendar integration), `contacts` (Google sync, shared schema, entity linkage), `memory` (episodes/facts/rules storage, hybrid search, embedding, consolidation), `telegram` (bot/user client tools), `email` (Gmail integration, IMAP/SMTP tools), `approvals` (gate wrapper, pending actions, standing rules)
- **AND** roster modules (in `roster/{butler-name}/modules/`) wire butler-specific tools as MCP tools — every butler that has domain tools in `roster/{butler-name}/tools/` must have a corresponding roster module package
- **AND** each module has its own nested TOML configuration (e.g., `modules.calendar.provider`, `modules.telegram.bot.token_env`, `modules.memory.consolidation.interval_hours`)
- **AND** roster modules typically need only an empty `[modules.{butler-name}]` section in `butler.toml`

### Requirement: Skills System
Skills are structured behavioral guides (per agentskills.io spec) that are loaded into runtime sessions to provide domain expertise, workflows, and decision frameworks. They are documentation-as-configuration — not executable code within the daemon.

#### Scenario: Skill file format
- **WHEN** a skill is defined
- **THEN** it lives at `roster/{butler-name}/skills/{skill-name}/SKILL.md` (butler-specific) or `roster/shared/skills/{skill-name}/SKILL.md` (shared across butlers)
- **AND** the SKILL.md file contains YAML frontmatter (`name`, `description`, `version`, optional `trigger_patterns`) followed by markdown content defining usage guides, templates, workflows, and examples

#### Scenario: Skill loading into runtime sessions
- **WHEN** the spawner composes a system prompt for an ephemeral LLM CLI session
- **THEN** the butler's available skills are discoverable by the runtime instance
- **AND** the runtime can follow skill instructions to execute multi-step workflows using the butler's MCP tools
- **AND** skills may reference specific tools by name (e.g., "call `entity_resolve` before storing a fact")

#### Scenario: Shared skills
- **WHEN** a skill is used across multiple butlers
- **THEN** it lives in `roster/shared/skills/{skill-name}/SKILL.md`
- **AND** it is symlinked into each consuming butler's `skills/` directory
- **AND** current shared skills include:
  - `butler-memory`: Entity resolution before storage, permanence levels (`permanent`, `stable`, `standard`, `volatile`), fact anchoring to `entity_id`, JSON array tags for cross-cutting queries
  - `butler-notifications`: Notification delivery via `notify()` with required parameters (`channel`, `message`/`emoji`, `intent`), intent selection (`send`, `reply`, `react`), `request_context` propagation

#### Scenario: Butler-specific skills
- **WHEN** a butler has domain-specific workflows
- **THEN** it places skills in `roster/{butler-name}/skills/{skill-name}/SKILL.md`
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
All butlers share a single PostgreSQL database (`butlers`) with per-butler schema isolation. The `shared` schema provides cross-butler data access. Inter-butler communication is MCP-only through the Switchboard.

#### Scenario: Per-butler schema isolation
- **WHEN** a butler connects to the database
- **THEN** its asyncpg connection pool sets `server_settings = {"search_path": "{butler_schema},shared,public"}`
- **AND** each butler can only see its own tables plus the `shared` schema tables
- **AND** no butler can directly read or write another butler's schema

#### Scenario: Shared schema
- **WHEN** cross-butler data is needed
- **THEN** it lives in the `shared` schema (e.g., shared secrets, shared contacts, credential store)
- **AND** all butlers have read access to `shared` via their search_path
- **AND** write access to `shared` tables is governed by the credential store and specific module migrations

#### Scenario: Migration strategy
- **WHEN** database migrations run during butler startup
- **THEN** three migration chains execute in order: `core` (shared infrastructure tables), `{butler_name}` (butler-specific tables, if the chain exists), and each module's chain (scoped to the butler's schema)
- **AND** Alembic manages all chains with branch labels for isolation

#### Scenario: Inter-butler data exchange
- **WHEN** one butler needs data from another
- **THEN** it communicates via MCP calls through the Switchboard routing plane — never by direct schema access
- **AND** the `shared` schema is reserved for truly shared reference data, not for passing messages between butlers

### Requirement: Core vs Domain Butlers
The roster contains two categories of butlers: core butlers that provide essential infrastructure services and must always be present, and domain butlers that provide specialist capabilities and can be added or removed.

#### Scenario: Core butler — Switchboard
- **WHEN** the system is running
- **THEN** the Switchboard butler (port 40100) must be present as the sole entry point for all inbound messages
- **AND** it classifies incoming messages and routes them to the appropriate domain butler
- **AND** it maintains the durable ingestion buffer with priority-tiered queuing and crash-recovery scanning
- **AND** it manages the connector registry (which connectors are active, their health, eligibility)
- **AND** no other butler receives external messages directly — all inbound traffic flows through Switchboard
- **AND** Switchboard itself does not register with another switchboard (it IS the switchboard)

#### Scenario: Core butler — Messenger
- **WHEN** the system is running
- **THEN** the Messenger butler (port 40104) must be present as the sole owner of outbound channel delivery
- **AND** it owns all channel egress tools: `telegram_send_message`, `telegram_reply_to_message`, `email_send_message`, `email_reply_to_thread`
- **AND** non-messenger butlers that attempt to register channel egress tools have them silently stripped during startup
- **AND** all other butlers must use the `notify()` core tool which routes delivery requests through Switchboard to Messenger
- **AND** Messenger has no schedules, no domain skills, and no autonomous behavior — it is a pure execution plane

#### Scenario: Domain butlers (extensible roster)
- **WHEN** a new domain specialist is needed
- **THEN** a new butler is added to `roster/{butler-name}/` following the roster conventions
- **AND** it registers with the Switchboard at startup (`[butler.switchboard]` with `advertise = true`)
- **AND** it launches a heartbeat task and liveness reporter to maintain its registration
- **AND** it can be added or removed without affecting other butlers' operation
- **AND** current domain butlers include: General (40101, catch-all), Relationship (40102, personal CRM), Health (40103, health tracking), Finance (40105, personal finance), Travel (40106, trip logistics)

### Requirement: Spawner and Runtime Adapters
The spawner generates ephemeral MCP configurations and invokes LLM CLI runtimes as subprocesses. Each runtime adapter translates the common invocation contract into adapter-specific CLI arguments.

#### Scenario: Ephemeral MCP config generation
- **WHEN** a session is triggered
- **THEN** the spawner generates a JSON MCP config containing the butler's own SSE endpoint URL (`http://localhost:{port}/mcp`) with an optional `runtime_session_id` query parameter for correlation
- **AND** the config is injected via the `MCP_SERVERS` environment variable
- **AND** no other MCP servers are included — the runtime is locked to its parent butler

#### Scenario: Environment sandboxing
- **WHEN** the spawner builds the subprocess environment
- **THEN** it starts with a minimal baseline: only `PATH` plus explicitly declared credentials
- **AND** core API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) are always included if available
- **AND** module credentials are resolved via the CredentialStore (DB-first with env fallback)
- **AND** no undeclared environment variables leak from the host to the runtime subprocess

#### Scenario: Runtime adapter selection
- **WHEN** `butler.toml` specifies `[runtime] type = "codex"` (or `"claude-code"`, `"gemini"`)
- **THEN** the corresponding adapter is used: CodexAdapter (GPT models), ClaudeCodeAdapter (Claude models), GeminiAdapter (Gemini models)
- **AND** each adapter implements `async invoke(prompt, system_prompt, mcp_servers, env, ...)` returning `(result_text, tool_calls, usage_dict)`

#### Scenario: Concurrency control
- **WHEN** multiple triggers arrive concurrently
- **THEN** the spawner enforces `butler.runtime.max_concurrent_sessions` as a concurrency limit
- **AND** excess triggers are queued up to `max_queued` (configurable)
- **AND** queue overflow triggers are rejected with a capacity error

### Requirement: Roster Directory Structure Convention
Every butler lives in `roster/{butler-name}/` and follows a fixed directory layout. The roster is the single source of truth for butler identity, personality, and configuration. Files are git-versioned and loaded at daemon startup.

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
- **THEN** skills are placed in `roster/{butler-name}/skills/{skill-name}/SKILL.md` with optional companion scripts
- **AND** dashboard API routes are placed in `roster/{butler-name}/api/router.py` (exporting a module-level `router` APIRouter variable) with optional `models.py`
- **AND** no `__init__.py` is required in `api/`; auto-discovery handles registration via `src/butlers/api/router_discovery.py`

### Requirement: butler.toml Configuration Schema
The `butler.toml` file declares butler identity, runtime, database, modules, schedules, switchboard registration, and buffer configuration. All butlers share the same schema with butler-specific values.

#### Scenario: Identity section
- **WHEN** `butler.toml` is loaded
- **THEN** `[butler]` provides: `name` (unique identifier), `port` (FastMCP SSE server port), `description` (human-readable purpose string)

#### Scenario: Runtime configuration
- **WHEN** `[butler.runtime]` and `[runtime]` sections are present
- **THEN** `butler.runtime.model` specifies the LLM model for runtime instances
- **AND** `butler.runtime.max_concurrent_sessions` controls spawner concurrency
- **AND** `runtime.type` selects the adapter (`codex`, `claude-code`, `gemini`)

#### Scenario: Database configuration
- **WHEN** `[butler.db]` specifies `name = "butlers"` and `schema = "{butler-name}"`
- **THEN** the butler operates in a consolidated PostgreSQL database with per-butler schema isolation plus access to the `shared` schema

#### Scenario: Module declarations
- **WHEN** `[modules.*]` sections are present
- **THEN** each section enables an opt-in module with module-specific nested configuration
- **AND** examples: `modules.calendar.provider = "google"`, `modules.calendar.conflicts.policy = "suggest"`, `modules.contacts.sync.interval_minutes = 60`, `modules.telegram.bot.token_env = "BUTLER_TELEGRAM_TOKEN"`, `modules.memory.consolidation.interval_hours = 24`

#### Scenario: Schedule declarations
- **WHEN** `[[butler.schedule]]` entries are present
- **THEN** each entry declares a scheduled task with `name`, `cron` expression, and dispatch mode
- **AND** `dispatch_mode = "prompt"` with `prompt` string triggers a runtime session with that prompt
- **AND** `dispatch_mode = "job"` with `job_name` string executes a native Python job function without spawning a runtime

#### Scenario: Switchboard registration (non-switchboard butlers)
- **WHEN** `[butler.switchboard]` section is present with `advertise = true`
- **THEN** the butler registers itself with the Switchboard at startup, enabling routing discovery
- **AND** configuration includes: `url` (Switchboard MCP endpoint), `liveness_ttl_s` (registration expiry), `route_contract_min`/`route_contract_max` (supported contract versions)

#### Scenario: Buffer configuration (switchboard only)
- **WHEN** `[buffer]` section is present on the switchboard butler
- **THEN** it configures the durable ingestion buffer: `queue_capacity` (bounded in-memory queue size), `worker_count` (concurrent dispatch workers), `scanner_interval_s` (cold-path DB recovery scan interval), `scanner_grace_s` (minimum age before scanner reclaims), `scanner_batch_size` (max items per scan)

### Requirement: Scheduled Task Companion Skills
Scheduled tasks declared in `butler.toml` with `dispatch_mode = "prompt"` should have corresponding skills that document the complete tool sequence and decision logic the runtime should follow.

#### Scenario: Prompt-dispatched scheduled task has a companion skill
- **WHEN** a `[[butler.schedule]]` entry uses `dispatch_mode = "prompt"`
- **THEN** a companion skill exists in `roster/{butler-name}/skills/` documenting the full workflow for that scheduled task
- **AND** the schedule entry's `prompt` field references the skill and provides the trigger context (e.g., "Run the upcoming-bills-check workflow. Horizon: 14 days, include overdue.")
- **AND** the companion skill contains: the complete tool call sequence, output formatting, notification delivery via `notify(intent="send")`, and the no-op path (when there is nothing to report)

#### Scenario: Job-dispatched scheduled tasks are exempt
- **WHEN** a `[[butler.schedule]]` entry uses `dispatch_mode = "job"`
- **THEN** no companion skill is required because the job executes native Python code without spawning a runtime session

### Requirement: MANIFESTO.md as Public Identity
Each butler has a `MANIFESTO.md` that defines its value proposition, target user persona, and feature scope. The manifesto is the governing document for scope decisions.

#### Scenario: Manifesto-driven scope governance
- **WHEN** a new feature or tool is proposed for a butler
- **THEN** it must be evaluated against the butler's MANIFESTO.md for alignment with the stated purpose and scope boundaries
- **AND** features that fall outside the manifesto's scope should be directed to a different butler or require a manifesto amendment

### Requirement: CLAUDE.md as System Prompt Entry Point
Each butler's `CLAUDE.md` is loaded as the system prompt for every runtime instance spawned by that butler. In practice, `CLAUDE.md` delegates to `AGENTS.md` via an `@AGENTS.md` file reference, and `AGENTS.md` in turn pulls shared instructions via `@../shared/AGENTS.md`. This indirection chain allows butler-specific and shared content to be maintained independently while composing into a single system prompt at runtime.

#### Scenario: System prompt composition
- **WHEN** the spawner invokes a runtime instance
- **THEN** it reads `roster/{butler-name}/CLAUDE.md` and uses its contents as the base system prompt
- **AND** if the memory module is active, memory context is appended after a blank line separator
- **AND** no other system prompt sources are injected

#### Scenario: Interactive response mode
- **WHEN** a runtime instance receives a REQUEST CONTEXT JSON block with a `source_channel` field (e.g., `telegram`, `email`)
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
AGENTS.md is loaded into every runtime session via the CLAUDE.md indirection chain. Because it contributes to every session's context window, it must contain only general-purpose butler information. Detailed workflows, multi-step procedures, and extensive examples belong in skills, which are loaded on demand.

#### Scenario: AGENTS.md contains only general-purpose information
- **WHEN** a butler's AGENTS.md is authored or updated
- **THEN** it contains: butler identity (name, role, one-paragraph purpose), tool surface summary (tool names with one-line descriptions), behavioral guidelines (concise rules, not multi-step procedures), calendar usage (domain-specific lines only — shared rules come from `@../shared/AGENTS.md`), skill references (one-liner pointers to skills for specific workflows), and a `# Notes to self` section for runtime agent notes
- **AND** it does NOT inline: multi-step workflow procedures with tool call sequences, extensive examples (more than one brief example per section), decision frameworks with branching logic, classification rule tables, memory classification taxonomies with example code, or scheduled task execution logic

#### Scenario: Workflow content lives in skills
- **WHEN** a butler has a multi-step workflow (e.g., fact extraction pipeline, message classification rules, health check-in flow, bill review triage)
- **THEN** that workflow is documented as a skill in `roster/{butler-name}/skills/{skill-name}/SKILL.md`
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
Butler credentials follow a layered resolution strategy: database-first from the `shared.secrets` table, with environment variable fallback.

#### Scenario: Credential resolution order
- **WHEN** a butler or module needs a credential (API key, OAuth token, etc.)
- **THEN** the CredentialStore first checks the `shared.secrets` table in PostgreSQL
- **AND** if not found, falls back to the environment variable specified in the module config (e.g., `token_env = "BUTLER_TELEGRAM_TOKEN"`)
- **AND** `ANTHROPIC_API_KEY` validation is fatal — butler will not start without it

#### Scenario: Credential isolation in runtime sessions
- **WHEN** the spawner builds the subprocess environment for a runtime session
- **THEN** only explicitly declared credentials are included — no host environment variables leak
- **AND** this prevents runtime sessions from accessing credentials they were not granted

### Requirement: Port Assignment Convention
Butlers use a contiguous port range starting at 40100, with the dashboard API cleanly separated.

#### Scenario: Butler port assignments
- **WHEN** butlers are running
- **THEN** ports are assigned as: switchboard=40100, general=40101, relationship=40102, health=40103, messenger=40104, finance=40105, travel=40106
- **AND** the dashboard API runs at port 40200, cleanly separated from the butler MCP port range
- **AND** new butlers are assigned the next available port in the 401xx range

### Requirement: Module Runtime State
Each module's health is tracked at runtime, enabling graceful degradation without butler-wide failure.

#### Scenario: Module health states
- **WHEN** a module is loaded during butler startup
- **THEN** its runtime state includes: `health` (`active` | `failed` | `cascade_failed`), `enabled` (bool, persisted to state store for stickiness across restarts), `failure_phase` (which startup phase failed: `credentials`, `config`, `migration`, `startup`, `tools`), `failure_error` (error message string)

#### Scenario: Non-fatal module degradation
- **WHEN** a module fails during any startup phase
- **THEN** the butler marks it as `failed` and continues starting remaining modules
- **AND** any module that depends on the failed module is marked as `cascade_failed`
- **AND** tools from failed modules are not registered on the MCP server
- **AND** the butler remains operational with reduced capability
