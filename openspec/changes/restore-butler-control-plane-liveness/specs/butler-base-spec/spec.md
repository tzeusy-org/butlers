## MODIFIED Requirements

### Requirement: Butler as Architectural Primitive
A butler SHALL be a long-lived MCP capability-service daemon backed by a dedicated PostgreSQL schema. It SHALL remain the unit of service deployment, isolation and capability composition, retaining its identity, modules, deterministic jobs, schedules, subscriptions, obligations, manifesto and domain guidance. A domain daemon SHALL NOT spawn reasoning sessions: work needing reasoning SHALL enter goal admission under RFC 0038, and goal-runner invocations execute on the separate session host. Staffers retain their essential infrastructure roles (see `staffer-archetype`); explicitly permitted D18 engineering executors remain distinct from reasoning goals and the other already-governed tool-less D18 model uses. Service health and capability eligibility SHALL NOT be derived from domain reasoning-session activity.

ID: REQ-butler-base-spec-003
Source: RFC 0038 D1/D2/D7/D17/D18/D19; generalist-lead-focused-delegation collision disposition for this change; heart-and-soul/vision.md Rules 3-5; RFC 0001 §Startup Phases; docs/plans/2026-09-23-liveness-control-plane-reliability-packet.md §5.1
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
- **AND** before argument coercion a model-reachable handler verifies the broker assertion binding the goal, invocation, audience and authorized call, as required by RFC 0038 D7
- **AND** deterministic service-plane callers retain their separately authenticated, allowlisted operation contracts; a healthy service or connection identity is not a capability grant

#### Scenario: Ephemeral LLM CLI sessions
- **WHEN** a scheduled prompt, session-waking subscription or inbound request needs reasoning
- **THEN** the owning service submits the existing obligation, caused or ingress goal admission, with its declared capabilities and lineage
- **AND** a domain daemon does not generate a reasoning-session MCP config, compose a private per-butler reasoning prompt, or start an LLM CLI subprocess
- **AND** the goal runner launches ephemeral lead and permitted child invocations on the session host through runtime adapters and the broker
- **AND** domain guidance remains versioned and available under the goal-runtime guidance contract
- **AND** invocation outcome, token counts, cost, calls, duration and trace provenance remain auditable through the goal/invocation records; deterministic sweeps, syncs and projections remain local jobs
- **AND** the goal ledger, not a per-domain prompt/output session log, is the record of the goal; telemetry carries invocation identifiers/digests and never arguments or results under RFC 0038 D17
- **AND** this retained historical scenario heading does not authorize the removed per-butler spawning mechanism

#### Scenario: Butler daemon lifecycle phases
- **WHEN** the butler starts up
- **THEN** it progresses through RFC 0001 phases 1–17 in order: config/logging → telemetry and secret scan → module loading (topological sort) → config validation → env validation → database provisioning and identity → core and butler migrations → module migration, credential, storage, and bootstrap work → runtime config → TOML schedule sync → module startup → permitted engineering-executor/audit, pipeline, and control-plane wiring → FastMCP and core tools → module tools and gates → FastMCP SSE server → durable boot-epoch registration, deterministic recovery, and scheduler/obligation services → internal identity/readiness endpoint and accepting connections
- **AND** module failures during any phase are non-fatal — a failed module is marked as unavailable while the butler continues operating
- **AND** the retained RFC 0001 phase sequence does not instantiate a domain reasoning Spawner or the deleted classify-and-route recovery path; Switchboard goal/broker isolation and session-host launch follow RFC 0038 D1-D3

#### Scenario: Graceful shutdown
- **WHEN** the butler receives a shutdown signal
- **THEN** it stops accepting new MCP connections
- **AND** drains in-flight service work and any explicitly permitted staffer engineering executors within `shutdown_timeout_s` (configurable, default varies by butler); a domain has no reasoning sessions to drain
- **AND** cancels local background tasks (scheduler and deterministic recovery) in reverse startup order; the separate controller owns periodic liveness probing
- **AND** calls `on_shutdown()` on each module in reverse topological order
- **AND** closes the database connection pool

### Requirement: Staffers vs Domain Butlers
The roster SHALL contain two categories of butlers: staffers that provide essential infrastructure services and SHALL always be present (configured with `type = "staffer"`), and domain butlers that provide specialist capabilities and can be added or removed.

ID: REQ-butler-base-spec-004
Source: RFC 0038 D1/D2/D7/D17/D18/D19; generalist-lead-focused-delegation collision disposition for this change; heart-and-soul/vision.md Rules 3-5; RFC 0001 §Startup Phases; RFC 0003 routing
Scope: v1-mandatory

#### Scenario: Staffer — Switchboard
- **WHEN** the system is running
- **THEN** the Switchboard staffer must be present in its own process and container (`butlers-control`), with service listener port 41100 and the separate session listener specified by RFC 0038
- **AND** it is the sole ingress and hosts goal admission, the goal ledger, goal runner and capability broker; reasoning ingress admits a goal instead of classifying and routing messages to domain sessions
- **AND** deterministic ingestion and authenticated service-plane relays remain deterministic and do not create a goal merely to reach a staffer
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
- **AND** it registers with the Switchboard at startup (`[butler.switchboard]` with `advertise = true`), publishing its declared model-reachable capabilities to the catalog
- **AND** it registers its boot epoch through the narrow database operation before advertising service acceptance; roster endpoint identity remains Git-owned
- **AND** its liveness is receiver-observed by the controller rather than maintained by a daemon heartbeat task or reporter
- **AND** it can be added or removed without affecting other agents' operation
- **AND** current domain butlers include: General (41101, capture and collections), Relationship (41102, personal CRM), Health (41103, health tracking), Finance (41105, personal finance), Travel (41106, trip logistics)

### Requirement: Instance Facts Internal Interface

Each butler daemon SHALL expose an internal interface by which the dashboard API layer
can read instance-level facts about that butler. These facts are already computed by the
daemon during normal operation (identity and service acceptance);
this requirement codifies the contract so the System Overview page aggregator has a
normative interface to consume. The daemon SHALL NOT assert its own registry liveness
by heartbeat POST.
Current reasoning activity SHALL come from goal/invocation records, not a domain session counter. Retained `{schema}.sessions` records remain historical, or describe explicitly permitted staffer engineering executors; they SHALL NOT imply domain reasoning-session health. Region activity presentation belongs to the separately signed D19 dashboard change.
This requirement covers only the contractual shape of the data the System page expects.
The service-fact and historical-session access paths (liveness registry table, retained `{schema}.sessions` table) are
documented in the `system-overview-page` spec. asyncpg pool stats are explicitly out
of scope for v1 (they require in-process access the dashboard API layer does not have).
This requirement defines what the daemon is responsible for maintaining.

ID: REQ-butler-base-spec-002
Source: RFC 0038 D1/D2/D7/D17/D18/D19; generalist-lead-focused-delegation collision disposition for this change; RFC 0001 §Startup Phases; heart-and-soul/vision.md Rules 4-5; docs/plans/2026-09-23-liveness-control-plane-reliability-packet.md §5.1
Scope: v1-mandatory

#### Scenario: Heartbeat registration is kept current

- **WHEN** a butler daemon is running
- **THEN** its same-port internal identity interface reports configured name, boot UUID, server-allocated boot epoch, service compatibility contract and current service acceptance state (legacy identity fields `route_contract` and `accepting_routes` are compatibility names, not authority to execute `route.execute`) to the receiver at a bounded response size
- **AND** the receiver probes at least once every `liveness_ttl_seconds / 2` seconds and writes its own DB-server observation time to the switchboard liveness registry
- **AND** a probe failure records degraded observation without shutting down the daemon; no daemon heartbeat POST is authoritative

#### Scenario: Not-ready daemon is truthful
- **WHEN** startup, shutdown, service-handler failure, or incompatible contract prevents safe target acceptance
- **THEN** the internal response reports not accepting work rather than a healthy service-ready state

#### Scenario: Session completion updates the per-butler session record

- **WHEN** an explicitly permitted staffer engineering-executor session completes (success or failure)
- **THEN** the session row in `{schema}.sessions` is updated with:
  - `completed_at: timestamptz` -- the UTC timestamp at session completion (was NULL
    while the session was active; a non-NULL value signals terminal state)
  - `success: boolean` -- `true` if the session completed successfully, `false` if it
    failed. Note: there is no `status` text column; the actual schema uses `success`
    (boolean) and `completed_at` (timestamptz) as the two terminal-state fields.
- **AND** this row remains the source of truth for the historical/engineering `last_session_at` projection in the System page heartbeat endpoint, not current domain reasoning activity
- **AND** a domain creates no new reasoning-session row; current goal activity is read from the goal/invocation ledger

#### Scenario: Active session count is derivable from the sessions table

- **WHEN** the existing System page queries retained session facts, including permitted staffer engineering-executor sessions
- **THEN** it derives the count from `SELECT COUNT(*) FROM {schema}.sessions WHERE
  completed_at IS NULL` -- no dedicated active-session counter table is required.
  Note: there is no `status` column; a session is active when `completed_at IS NULL`
  (see `src/butlers/core/sessions.py` `sessions_active` for the canonical query).
- **AND** this query is safe to run concurrently with session creation and completion
  without locking
- **AND** retained incomplete legacy rows are not treated as current domain reasoning or service health; current reasoning activity follows goal/invocation state

#### Scenario: DB connection pool stats are not exposed in v1

- **WHEN** the System page reads per-butler facts in v1
- **THEN** asyncpg connection pool statistics (min_size, max_size, pool_size, in-use
  connections) are NOT surfaced via the System page endpoints
- **AND** this is a deliberate v1 simplification -- pool stats require in-process
  access that the dashboard API layer does not have without an additional internal
  API
- **AND** pool stats are marked as a forward-path item to be addressed if the System
  page adds real-time resource monitoring
