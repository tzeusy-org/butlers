## ADDED Requirements

### Requirement: Memory module-driven context injection before CC spawn

When a butler has memory module enabled, the CC spawner SHALL call `memory_context(trigger_prompt, butler_name)` on that same butler MCP server before spawning a CC instance. Calls SHALL propagate authenticated request context so memory retrieval can enforce tenant and lineage boundaries. The returned memory block SHALL be injected into the system prompt after `CLAUDE.md`.

#### Scenario: CC instance receives memory context
- **WHEN** the health butler (with memory module enabled) spawns a CC instance for prompt "Help user log weight"
- **THEN** the spawner SHALL call `memory_context(trigger_prompt="Help user log weight", butler="health")`
- **AND** the request context SHALL be propagated
- **AND** the system prompt SHALL include the returned memory block

#### Scenario: memory_context failure is fail-open
- **WHEN** the spawner attempts `memory_context`
- **AND** the call fails due to runtime/tool error
- **THEN** CC spawn SHALL continue without memory context
- **AND** a warning SHALL be logged

### Requirement: Episode storage after CC session completion

After every CC session completes, when memory module is enabled, the daemon SHALL call `memory_store_episode` with key observations, butler identity, and session linkage. Tenant identity SHALL come from authenticated request context.

#### Scenario: Session completion triggers episode write
- **WHEN** a session for `general` completes
- **THEN** `memory_store_episode` SHALL be called with `butler="general"` and matching `session_id`

#### Scenario: Episode write failure does not block session completion
- **WHEN** session completion calls `memory_store_episode`
- **AND** the write fails
- **THEN** session completion SHALL still finalize successfully
- **AND** a warning SHALL be logged

### Requirement: Memory tools are local to hosting butler MCP server

Memory tools SHALL be registered on each hosting butler MCP server when `[modules.memory].enabled = true`. No dedicated external memory MCP server SHALL be required for runtime memory calls.

#### Scenario: CC instance calls memory_recall mid-session
- **WHEN** a CC instance for butler `health` calls `memory_recall(topic="user medications")`
- **THEN** the call SHALL resolve against the health butler's locally registered memory tools
- **AND** results SHALL be tenant-bounded and scope-filtered (`global` + `health`)

### Requirement: Memory module config in butler.toml

Memory configuration SHALL be declared under `[modules.memory]` (including retrieval defaults, confidence thresholds, episode retention, and token budget) in each butler that enables memory.

#### Scenario: Butler starts with valid memory module config
- **WHEN** a butler with `[modules.memory].enabled = true` starts
- **THEN** the memory module SHALL validate and load module configuration
- **AND** register configured scheduled jobs (consolidate, decay_sweep, episode_cleanup)

### Requirement: Module-disabled path skips memory hooks

If `[modules.memory].enabled` is false or omitted, runtime memory hooks SHALL be bypassed and no memory tools SHALL be registered.

#### Scenario: Butler without memory module runs normally
- **WHEN** a butler starts without memory module enabled
- **THEN** no `memory_*` tools SHALL be registered
- **AND** CC spawn/session completion SHALL proceed without memory hook calls

### Requirement: Butler-specific retrieval weight configuration

Each enabled butler MAY configure retrieval weights via `[modules.memory.retrieval]`. If omitted, defaults SHALL apply (`relevance=0.4`, `importance=0.3`, `recency=0.2`, `confidence=0.1`).

#### Scenario: Butler with custom retrieval weights
- **WHEN** health butler config sets custom retrieval score weights
- **AND** `memory_context` runs for health
- **THEN** retrieval SHALL use health's configured weights
