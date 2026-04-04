## MODIFIED Requirements

### Requirement: Butler as Architectural Primitive
A butler is a long-lived MCP server daemon backed by a dedicated PostgreSQL schema. When triggered, it spawns an ephemeral LLM CLI session wired exclusively to itself via a locked-down MCP config. The butler is the unit of deployment, isolation, and capability composition. Butlers are one of two agent types in the ecosystem; the other is staffers (see `staffer-archetype` spec).

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

### Requirement: butler.toml Configuration Schema
The `butler.toml` file declares butler identity, runtime, database, modules, schedules, switchboard registration, and buffer configuration. All agents (butlers and staffers) share the same schema with agent-specific values.

#### Scenario: Identity section
- **WHEN** `butler.toml` is loaded
- **THEN** `[butler]` provides: `name` (unique identifier), `port` (FastMCP SSE server port), `description` (human-readable purpose string), `type` (agent type: `"butler"` or `"staffer"`, default `"butler"`)

#### Scenario: Runtime configuration
- **WHEN** `[butler.runtime]` and `[runtime]` sections are present
- **THEN** `butler.runtime.model` specifies the LLM model for runtime instances
- **AND** `butler.runtime.max_concurrent_sessions` controls spawner concurrency
- **AND** `runtime.type` selects the adapter (`codex`, `claude`, `gemini`)

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

### Requirement: Core vs Domain Butlers
The roster contains three categories of agents: staffers that provide essential infrastructure services and must always be present, domain butlers that provide specialist capabilities and can be added or removed, and a catch-all butler for freeform tasks.

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

### Requirement: MANIFESTO.md as Public Identity
Each butler has a `MANIFESTO.md` that defines its value proposition, target user persona, and feature scope. Staffers have a `MANIFESTO.md` with infrastructure-contract framing (SLAs, responsibilities, failure modes). The manifesto/contract is the governing document for scope decisions.

#### Scenario: Manifesto-driven scope governance (butlers)
- **WHEN** a new feature or tool is proposed for a butler
- **THEN** it must be evaluated against the butler's MANIFESTO.md for alignment with the stated purpose and scope boundaries
- **AND** features that fall outside the manifesto's scope should be directed to a different butler or require a manifesto amendment

#### Scenario: Contract-driven scope governance (staffers)
- **WHEN** a new capability is proposed for a staffer
- **THEN** it must be evaluated against the staffer's MANIFESTO.md infrastructure contract for alignment with stated responsibilities
- **AND** capabilities outside the contract's scope require a contract amendment
