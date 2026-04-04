# Staffer Archetype

## Purpose
Defines what a "staffer" is as an architectural primitive — the infrastructure-specialist counterpart to domain butlers. Staffers share the same daemon engine but have a distinct permissions model, connectivity topology, and governing document (infrastructure contract vs. manifesto). This spec covers the type system, routing exclusion, briefing exclusion, cross-butler permissions, infrastructure contracts, and extensibility for future staffers.

## ADDED Requirements

### Requirement: Staffer as Architectural Primitive
A staffer is a long-lived MCP server daemon sharing the same core engine as a butler (FastMCP, modules, ephemeral LLM sessions, scheduler, state store) but distinguished by its permissions model and connectivity topology. Staffers serve the ecosystem rather than the user directly. The staffer type is declared in `butler.toml` and governs routing exclusion, briefing exclusion, and cross-butler connectivity.

#### Scenario: Staffer identity contract
- **WHEN** a staffer daemon starts
- **THEN** it is uniquely identified by a `name` string and a `type = "staffer"` field in its `butler.toml`
- **AND** it binds a FastMCP SSE server to its assigned port (same port assignment convention as butlers)
- **AND** it operates within the same PostgreSQL database (`butlers`) in its own schema
- **AND** its search_path is set to `[staffer_schema, shared, public]` — identical to butler schema isolation
- **AND** the daemon engine is identical to a butler daemon; no separate `StafferDaemon` class exists

#### Scenario: Staffer uses the same engine as butler
- **WHEN** comparing a staffer daemon to a butler daemon
- **THEN** both use the same `ButlerDaemon` class, module system, tool composition model, spawner, scheduler loop, and session logging
- **AND** behavioral differences are expressed through type-aware conditionals at specific decision points, not through class inheritance or separate codepaths

#### Scenario: Staffer type is declared in butler.toml
- **WHEN** an agent's `butler.toml` contains `type = "staffer"` in the `[butler]` table
- **THEN** the config parser sets `config.type = ButlerType.STAFFER`
- **AND** the daemon applies staffer-specific behaviors at routing-exclusion, briefing-exclusion, and registration decision points

#### Scenario: Default type is butler
- **WHEN** an agent's `butler.toml` omits the `type` field
- **THEN** `config.type` defaults to `ButlerType.BUTLER`
- **AND** the agent behaves as a standard domain butler with no staffer-specific behaviors

### Requirement: Staffer Routing Exclusion
Staffers SHALL NOT be direct targets for user-message classification by the switchboard. Butlers MAY route to staffers via the switchboard using existing mechanisms (e.g., `notify → messenger`).

#### Scenario: Staffers excluded from user-message routing
- **WHEN** the switchboard classifies an incoming user message for routing
- **THEN** agents with `type = "staffer"` SHALL be excluded from the candidate set
- **AND** the message SHALL only be routed to agents with `type = "butler"`

#### Scenario: Butler-to-staffer routing via switchboard
- **WHEN** a butler needs to invoke a staffer's services (e.g., outbound delivery via messenger)
- **THEN** it uses existing mechanisms such as `notify()` which routes through switchboard to the messenger staffer
- **AND** this butler-to-staffer routing is not affected by the routing exclusion — only direct user-message classification is excluded

#### Scenario: Staffers register with switchboard for reachability
- **WHEN** a staffer daemon starts with `[butler.switchboard]` configuration
- **THEN** it registers with the switchboard as a reachable agent
- **AND** the switchboard's registry marks it as `type = "staffer"` so the classifier can exclude it from user-message routing
- **AND** the staffer remains reachable for butler-to-staffer routing via switchboard

### Requirement: Staffer Briefing Exclusion
Staffers SHALL NOT participate in the daily briefing contribution system. They do not contribute domain summaries because they serve the ecosystem, not the user's domains.

#### Scenario: Staffers excluded from briefing contribution registration
- **WHEN** the daemon syncs TOML schedules during startup
- **AND** the agent's `config.type` is `ButlerType.STAFFER`
- **THEN** any `daily_briefing_contribution` schedule entries SHALL be skipped during registration
- **AND** the staffer SHALL NOT appear in the aggregation butler's contribution collection

#### Scenario: Briefing aggregation skips staffers
- **WHEN** the briefing aggregation job (`collect_briefing_contributions`) queries specialist agents
- **THEN** it SHALL only collect contributions from agents with `type = "butler"`
- **AND** staffer-typed agents SHALL be excluded from the collection loop

### Requirement: Cross-Butler Permissions Model
Staffers MAY declare cross-butler access permissions that specify which other agents they may connect to or act on behalf of. This formalizes the implicit cross-butler connectivity that switchboard and messenger already exercise.

#### Scenario: Permissions declared in butler.toml
- **WHEN** a staffer's `butler.toml` contains a `[butler.permissions]` section
- **THEN** it specifies `cross_butler_access` as a list of agent names (or `["*"]` for all agents)
- **AND** this field is the declarative source of truth for the staffer's connectivity scope

#### Scenario: Wildcard access
- **WHEN** a staffer declares `cross_butler_access = ["*"]`
- **THEN** it is authorized to connect to and act on behalf of any agent in the ecosystem
- **AND** this is the expected configuration for switchboard and messenger

#### Scenario: Scoped access
- **WHEN** a staffer declares `cross_butler_access = ["general", "health"]`
- **THEN** it is authorized to connect to only the listed agents
- **AND** attempts to access other agents SHALL be flagged in logs as unauthorized (advisory in v1, enforced in future)

#### Scenario: Butlers have no cross-butler access by default
- **WHEN** a butler-typed agent's `butler.toml` has no `[butler.permissions]` section
- **THEN** `cross_butler_access` defaults to an empty list
- **AND** the butler communicates with other agents exclusively through switchboard routing (non-negotiable rule #3)

### Requirement: Infrastructure Contract (Staffer Manifesto)
Staffers use `MANIFESTO.md` with infrastructure-contract framing rather than user-relationship framing. The file retains the same name and location for roster convention consistency.

#### Scenario: Infrastructure contract content structure
- **WHEN** a staffer's `MANIFESTO.md` is authored
- **THEN** it contains: service name and purpose, responsibilities (what this staffer owns), SLAs (availability, throughput, latency expectations), failure modes and recovery procedures, dependency graph (what this staffer depends on and what depends on it), capacity limits, and escalation procedures

#### Scenario: Scope governance still applies
- **WHEN** a new capability is proposed for a staffer
- **THEN** it SHALL be evaluated against the staffer's `MANIFESTO.md` infrastructure contract for alignment with stated responsibilities
- **AND** capabilities outside the contract's scope SHALL require a contract amendment

### Requirement: Extensibility for Future Staffers
The staffer archetype SHALL accommodate future infrastructure agents beyond switchboard and messenger without requiring architectural changes.

#### Scenario: Adding a new staffer to the roster
- **WHEN** a new infrastructure agent is needed (e.g., QA staffer for log inspection and issue triage)
- **THEN** it is created following the same roster conventions as any agent: `roster/{staffer-name}/` with `butler.toml` (with `type = "staffer"`), `MANIFESTO.md` (infrastructure contract), `CLAUDE.md`, `AGENTS.md`
- **AND** the daemon engine, module system, and tool composition model work without modification
- **AND** staffer-specific behaviors (routing exclusion, briefing exclusion) are automatically applied based on `config.type`

#### Scenario: Future staffers may have unique permissions
- **WHEN** a future staffer has different access requirements (e.g., QA staffer needs read-only cross-butler log access plus codebase R/W)
- **THEN** the `[butler.permissions]` section accommodates this via scoped `cross_butler_access` lists
- **AND** additional permission dimensions MAY be added to the permissions section in future changes without modifying the core type system
