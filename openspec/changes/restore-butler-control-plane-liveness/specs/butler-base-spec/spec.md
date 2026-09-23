## MODIFIED Requirements

### Requirement: Butler as Architectural Primitive
A butler SHALL be a long-lived MCP server daemon backed by a dedicated PostgreSQL schema. When triggered, it SHALL spawn an ephemeral LLM CLI session wired exclusively to itself via a locked-down MCP config. The butler SHALL be the unit of deployment, isolation, and capability composition. Butlers SHALL be one of two agent types in the ecosystem; the other is staffers (see `staffer-archetype` spec).

ID: REQ-butler-base-spec-003
Source: heart-and-soul/vision.md Rules 3-5; RFC 0001 §Startup Phases; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §5.1
Scope: v1-mandatory

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
- **THEN** it progresses through RFC 0001 phases 1–17 in order: config/logging → telemetry and secret scan → module loading (topological sort) → config validation → env validation → database provisioning and identity → core and butler migrations → module migration, credential, storage, and bootstrap work → runtime config → TOML schedule sync → module startup → Spawner, audit/runtime, pipeline, and Switchboard wiring → FastMCP and core tools → module tools and gates → FastMCP SSE server → route recovery, durable boot-epoch registration, and scheduler services → internal identity/readiness endpoint and accepting connections
- **AND** module failures during any phase are non-fatal — a failed module is marked as unavailable while the butler continues operating

#### Scenario: Graceful shutdown
- **WHEN** the butler receives a shutdown signal
- **THEN** it stops accepting new MCP connections
- **AND** drains in-flight sessions within `shutdown_timeout_s` (configurable, default varies by butler)
- **AND** cancels local background tasks (scheduler and route recovery) in reverse startup order; the separate controller owns periodic liveness probing
- **AND** calls `on_shutdown()` on each module in reverse topological order
- **AND** closes the database connection pool

### Requirement: Staffers vs Domain Butlers
The roster SHALL contain two categories of butlers: staffers that provide essential infrastructure services and SHALL always be present (configured with `type = "staffer"`), and domain butlers that provide specialist capabilities and can be added or removed.

ID: REQ-butler-base-spec-004
Source: heart-and-soul/vision.md Rules 3-5; RFC 0001 §Startup Phases; RFC 0003 routing
Scope: v1-mandatory

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
- **AND** it registers its boot epoch through the narrow database operation before advertising route acceptance; roster endpoint identity remains Git-owned
- **AND** its liveness is receiver-observed by the controller rather than maintained by a daemon heartbeat task or reporter
- **AND** it can be added or removed without affecting other agents' operation
- **AND** current domain butlers include: General (41101, catch-all), Relationship (41102, personal CRM), Health (41103, health tracking), Finance (41105, personal finance), Travel (41106, trip logistics)

### Requirement: Instance Facts Internal Interface

Each butler daemon SHALL expose an internal interface by which the dashboard API layer
can read instance-level facts about that butler. These facts are already computed by the
daemon during normal operation (identity, route acceptance, session creation);
this requirement codifies the contract so the System Overview page aggregator has a
normative interface to consume. The daemon SHALL NOT assert its own registry liveness
by heartbeat POST.
This requirement covers only the contractual shape of the data the System page expects.
The physical access path (liveness registry table, `{schema}.sessions` table) is
documented in the `system-overview-page` spec. asyncpg pool stats are explicitly out
of scope for v1 (they require in-process access the dashboard API layer does not have).
This requirement defines what the daemon is responsible for maintaining.

ID: REQ-butler-base-spec-002
Source: RFC 0001 §Startup Phases; heart-and-soul/vision.md Rules 4-5; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §5.1
Scope: v1-mandatory

#### Scenario: Heartbeat registration is kept current

- **WHEN** a butler daemon is running
- **THEN** its same-port internal identity interface reports configured name, boot UUID, server-allocated boot epoch, route contract range, and current `route.execute` acceptance state to the receiver at a bounded response size
- **AND** the receiver probes at least once every `liveness_ttl_seconds / 2` seconds and writes its own DB-server observation time to the switchboard liveness registry
- **AND** a probe failure records degraded observation without shutting down the daemon; no daemon heartbeat POST is authoritative

#### Scenario: Not-ready daemon is truthful
- **WHEN** startup, shutdown, route-handler failure, or incompatible contract prevents safe target acceptance
- **THEN** the internal response reports not accepting work rather than a healthy route-ready state

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
