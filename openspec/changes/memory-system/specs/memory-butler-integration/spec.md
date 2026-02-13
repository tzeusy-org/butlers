## ADDED Requirements

### Requirement: Memory context injection before CC spawn

The CC spawner SHALL call `memory_context(trigger_prompt, butler_name)` on the Memory MCP server before spawning any CC instance. Calls SHALL propagate authenticated request context so Memory Butler can resolve tenant and lineage. The returned memory block SHALL be injected into the CC system prompt after the butler's `CLAUDE.md` content.

#### Scenario: CC instance receives memory context
- **WHEN** the health butler spawns a CC instance with prompt "Help user log weight"
- **THEN** the spawner SHALL call memory_context with trigger_prompt="Help user log weight" and butler="health"
- **AND** request context SHALL include tenant identity for retrieval boundary enforcement
- **AND** the CC instance's system prompt SHALL contain the returned memory block

#### Scenario: Memory Butler unavailable graceful fallback
- **WHEN** the CC spawner attempts to call memory_context
- **AND** the Memory Butler is not running or unreachable
- **THEN** the CC instance SHALL be spawned without memory context (degraded but functional)
- **AND** a warning SHALL be logged

### Requirement: Episode storage after CC session completion

After every CC session completes, the butler daemon SHALL call `memory_store_episode` on the Memory MCP server with: `content` (key observations extracted from the session), `butler` (butler name), `session_id` (session UUID), and `importance` (LLM-rated importance 1-10 or default 5.0). Tenant identity SHALL be derived from authenticated request context (not caller-provided free-text args for non-admin paths).

#### Scenario: Session completion triggers episode storage
- **WHEN** a CC session for the general butler completes
- **THEN** the daemon SHALL call memory_store_episode with butler="general" and session_id matching the session record

#### Scenario: Episode storage failure does not block session completion
- **WHEN** a CC session completes
- **AND** memory_store_episode fails (Memory Butler unreachable)
- **THEN** the session SHALL be marked complete normally
- **AND** a warning SHALL be logged

### Requirement: Memory MCP server in ephemeral CC configs

Every butler's ephemeral MCP config SHALL include the Memory MCP server alongside the butler's own MCP tools. CC instances SHALL be able to call memory tools (memory_recall, memory_store_fact, memory_confirm, etc.) during their sessions.

#### Scenario: CC instance calls memory_recall mid-session
- **WHEN** a CC instance spawned by the health butler calls memory_recall(topic="user medications")
- **THEN** the call SHALL be routed to the Memory MCP server
- **AND** results SHALL be scoped to caller tenant + ('global' and 'health')

### Requirement: Memory Butler configuration in butler.toml

The Memory Butler's `butler.toml` SHALL include: `name = "memory"`, `port = 8150`, `db.name = "butler_memory"`, scheduled tasks for consolidation (6h), decay sweep (daily 3am), and episode cleanup (daily 4am). A `[butler.memory]` section SHALL configure the embedding model, episode TTL, confidence thresholds, maturity promotion thresholds, and retrieval defaults.

#### Scenario: Memory Butler starts with valid config
- **WHEN** the Memory Butler starts
- **THEN** it SHALL read configuration from `butlers/memory/butler.toml`
- **AND** register 3 scheduled tasks (consolidate, decay_sweep, episode_cleanup)

### Requirement: Memory Butler registration with Switchboard

The Memory Butler SHALL be registered with the Switchboard Butler as a routable butler, following the same pattern as other butlers.

#### Scenario: External MCP request routed to Memory Butler
- **WHEN** an external MCP request targets the memory butler
- **THEN** the Switchboard SHALL route the request to the Memory Butler at port 8150

### Requirement: Butler-specific retrieval weight configuration

Each butler MAY configure custom retrieval score weights via `[butler.memory.retrieval]` in its `butler.toml`. If not specified, the defaults SHALL be used (relevance=0.4, importance=0.3, recency=0.2, confidence=0.1).

#### Scenario: Butler with custom retrieval weights
- **WHEN** the health butler's butler.toml specifies `score_weights = { relevance = 0.6, importance = 0.1, recency = 0.2, confidence = 0.1 }`
- **AND** memory_context is called for the health butler
- **THEN** retrieval SHALL use the custom weights
