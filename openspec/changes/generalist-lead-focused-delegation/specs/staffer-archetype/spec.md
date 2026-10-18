## MODIFIED Requirements

### Requirement: Staffer as Architectural Primitive
A staffer SHALL be a long-lived MCP server daemon sharing the same core engine as a butler (FastMCP, modules, scheduler, state store) but distinguished by its permissions model and connectivity topology. Staffers serve the ecosystem rather than the user directly. Like every daemon, a staffer SHALL NOT spawn a reasoning session; staffer work that needs model judgment runs as staffer-origin goals (REQ-staffer-archetype-002). The staffer type is declared in `butler.toml` and governs briefing exclusion, staffer-origin goal admission, and cross-butler connectivity.

Source: RFC 0038 D2, D5 and D18
Scope: v1-mandatory

#### Scenario: Staffer identity contract
- **WHEN** a staffer daemon starts
- **THEN** it is uniquely identified by a `name` string and a `type = "staffer"` field in its `butler.toml`
- **AND** it binds a FastMCP SSE server to its assigned port (same port assignment convention as butlers)
- **AND** it operates within the same PostgreSQL database (`butlers`) in its own schema
- **AND** its search_path is set to `[staffer_schema, shared, public]` — identical to butler schema isolation
- **AND** the daemon engine is identical to a butler daemon; no separate `StafferDaemon` class exists

#### Scenario: Staffer uses the same engine as butler
- **WHEN** comparing a staffer daemon to a butler daemon
- **THEN** both use the same `ButlerDaemon` class, module system, tool composition model, scheduler loop and capability registration, and neither spawns a reasoning session
- **AND** a staffer that dispatches engineering executors (QA investigation) does so under their own contract, outside goals
- **AND** behavioral differences are expressed through type-aware conditionals at specific decision points, not through class inheritance or separate codepaths

#### Scenario: Staffer type is declared in butler.toml
- **WHEN** an agent's `butler.toml` contains `type = "staffer"` in the `[butler]` table
- **THEN** the config parser sets `config.type = ButlerType.STAFFER`
- **AND** the daemon applies staffer-specific behaviors at briefing-exclusion, registration and staffer-goal admission decision points

#### Scenario: Default type is butler
- **WHEN** an agent's `butler.toml` omits the `type` field
- **THEN** `config.type` defaults to `ButlerType.BUTLER`
- **AND** the agent behaves as a standard domain butler with no staffer-specific behaviors

### Requirement: Cross-Butler Permissions Model
Staffers MAY declare cross-butler access permissions that SHALL specify which other agents they may connect to or act on behalf of. This formalizes the implicit cross-butler connectivity that switchboard and messenger already exercise. `cross_butler_access` SHALL govern daemon connectivity only and SHALL grant no capability to any goal; a staffer-origin goal's authority SHALL come only from the capability set its infrastructure contract declares (REQ-staffer-archetype-002).

Source: RFC 0038 D1, D5 and D7
Scope: v1-mandatory

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
- **AND** the butler communicates with other agents exclusively through the Switchboard control plane (notification delivery, the service-plane relay, and goal and obligation admission), never directly (non-negotiable rule #3)

#### Scenario: Wildcard connectivity grants no goal authority
- **WHEN** a staffer with `cross_butler_access = ["*"]` submits a staffer-origin goal
- **THEN** the goal's ceiling is the capability set declared in that staffer's contract
- **AND** `cross_butler_access` adds no capability to the goal or to any of its invocations

### Requirement: Extensibility for Future Staffers
The staffer archetype SHALL accommodate future infrastructure agents beyond switchboard and messenger without requiring architectural changes.

Source: RFC 0038 D1 and D18
Scope: v1-mandatory

#### Scenario: Adding a new staffer to the roster
- **WHEN** a new infrastructure agent is needed (e.g., QA staffer for log inspection and issue triage)
- **THEN** it is created following the same roster conventions as any agent: `roster/{staffer-name}/` with `butler.toml` (with `type = "staffer"`), `MANIFESTO.md` (infrastructure contract), `CLAUDE.md`, `AGENTS.md`
- **AND** the daemon engine, module system, and tool composition model work without modification
- **AND** staffer-specific behaviors (briefing exclusion, staffer-origin goal admission) are automatically applied based on `config.type`

#### Scenario: Future staffers may have unique permissions
- **WHEN** a future staffer has different access requirements (e.g., QA staffer needs read-only cross-butler log access plus codebase R/W)
- **THEN** the `[butler.permissions]` section accommodates this via scoped `cross_butler_access` lists
- **AND** additional permission dimensions MAY be added to the permissions section in future changes without modifying the core type system

#### Scenario: QA Staffer as concrete example
- **WHEN** the QA staffer is added to the roster at `roster/qa/`
- **THEN** it validates the extensibility contract: `type = "staffer"`, `cross_butler_access = ["*"]`, infrastructure contract MANIFESTO.md, scheduler-driven patrol loop, all running on the standard daemon engine
- **AND** no changes to the core staffer archetype code were required to accommodate it
- **AND** the QA staffer demonstrates that staffer-specific modules (here: `modules.qa` with patrol loop, discovery sources, triage, and investigation dispatch) are implemented as standard Module subclasses, with any triage reasoning submitted as a staffer-origin goal and investigation agents run as engineering executors
- **AND** the concrete staffers in the ecosystem are switchboard (control plane), messenger (outbound delivery), QA (automated quality assurance) and concierge (system-plane answers)

## REMOVED Requirements

### Requirement: Staffer Routing Exclusion

**Reason**: RFC 0038 D1 removes user-message classification and route dispatch with no successor, so there is no classification candidate set to exclude staffers from.

**Migration**: Staffer registration with `type = "staffer"` and notification delivery to Messenger survive in butler-switchboard "Agent Registry for Capability Forwarding" (`REQ-butler-switchboard-005`); daemon-to-staffer service calls use the Switchboard service-plane relay (`REQ-butler-switchboard-008`).

## ADDED Requirements

### Requirement: Staffer Reasoning Runs as Staffer-Origin Goals
Staffer work that needs model judgment, such as QA triage or a Concierge answer, SHALL be submitted to goal admission as a staffer-origin goal whose authority ceiling is the capability set declared in git, in the staffer's infrastructure contract, for that kind of work. Admission SHALL refuse a staffer goal for work with no declared capability set. A staffer goal whose inputs include third-party content or error payloads SHALL start exposed, and every QA goal SHALL start exposed. QA investigation agents and self-healing agents are engineering executors outside goals: they SHALL keep their own sandboxed contracts, SHALL receive no MCP configuration and SHALL never reach the broker or a domain capability.

ID: REQ-staffer-archetype-002
Source: RFC 0038 D5 and D18; RFC 0015; RFC 0030
Scope: v1-mandatory

#### Scenario: QA triage runs as an exposed staffer goal
- **WHEN** the QA staffer needs model judgment to triage a finding
- **THEN** it submits a staffer-origin goal that starts exposed
- **AND** the goal holds only the capability set declared for QA triage in QA's infrastructure contract

#### Scenario: A Concierge answer is bounded by its contract
- **WHEN** Concierge answers a system question
- **THEN** the answer runs as a staffer-origin goal limited to the read capabilities over RFC 0030's fixed views that Concierge's contract declares

#### Scenario: Undeclared staffer work is refused
- **WHEN** a staffer submits a goal for a kind of work for which its contract declares no capability set
- **THEN** admission refuses the goal and records the refusal
- **AND** no lead is launched

#### Scenario: Investigation agents stay outside goals
- **WHEN** the QA staffer dispatches an investigation agent
- **THEN** the agent runs as an engineering executor under its existing sandboxed contract
- **AND** it receives no MCP configuration and cannot reach the broker or any domain capability
