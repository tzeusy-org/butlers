## ADDED Requirements

### Requirement: Fence-Aware Codex Authority Recovery at Startup

The system SHALL require daemon, dashboard-lifespan, connector, and
direct-dispatcher startup paths that restore or construct Codex runtime
authentication to use only the selected system-global current generation binding.  They SHALL not use schema-local
Codex rows, an in-memory cache, a local canonical auth file, a staged file,
PID, or timestamp as a recovery authority.  A complete current binding may be
projected or staged safely; absent, malformed, unavailable, or inconsistent
evidence SHALL leave Codex launch unavailable without altering local files into
authority.

#### Scenario: Fresh daemon restores the current shared generation
- **WHEN** a daemon starts after another daemon has created a current opaque
  Codex generation
- **THEN** a read-only complete-current-binding query returns only the raw
  value bound to that current system-global generation
- **AND** it does not require a process-local rotation cache from the prior
  daemon
- **AND** startup creates no durable prepared or launched operation unless it
  is actually going to launch a child

#### Scenario: Fresh daemon finds an orphaned local rotation
- **WHEN** a daemon starts with a local Codex file that differs from the
  complete shared current binding and no live completion path proves it
- **THEN** the daemon does not promote or attach health to the local file
- **AND** it uses the shared binding for a later fenced operation or fails
  closed when that binding is unavailable

#### Scenario: Repeated startup projection leaves no operation behind
- **WHEN** a daemon repeatedly restarts and refreshes the compatibility
  projection without launching a Codex child
- **THEN** each refresh uses only a read-only complete current binding
- **AND** no nonterminal health-probe or other Codex operation is created

#### Scenario: Connector restoration preserves the same boundary
- **WHEN** a Codex-dependent connector restores CLI auth during startup
- **THEN** it receives the explicitly selected system-global authority and
  follows the current generation binding
- **AND** it does not construct a local credential authority as a fallback

### Requirement: Deterministic Provenance Cleanup Wiring

The deterministic lifecycle wiring SHALL make the value-free Codex operation
expiry/cleanup path available through the core maintenance owner without an LLM
session, a dashboard mutation, or a provider call.  It SHALL tolerate a
pre-migration/core-only environment by reporting safe unavailable state and
shall not delete a current credential or generation as a fallback.

#### Scenario: Cleanup handles a pre-migration relation safely
- **WHEN** the daemon's deterministic cleanup wiring runs before the Codex
  provenance relations are available
- **THEN** it reports the cleanup source unavailable without changing a Codex
  credential or local auth projection
- **AND** ordinary daemon startup remains able to report its existing health
  state honestly
