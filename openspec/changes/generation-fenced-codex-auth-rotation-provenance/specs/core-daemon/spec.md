## ADDED Requirements

### Requirement: Fence-Aware Codex Authority Recovery at Startup

The system SHALL require daemon, dashboard-lifespan, connector, and former
direct-dispatcher startup wiring to delegate Codex goal-runtime authentication
to the control-plane authority and session-host execution boundary. Only the
control plane SHALL resolve the selected system-global current generation
binding and project it onto the session-host credential volume through its
authenticated control channel. Dashboard CLI login and OAuth synchronization
SHALL use the control plane's service listener; the session host SHALL expose
no inbound dashboard port and SHALL have no database/credential-store access.
Domain daemons SHALL receive neither provider credentials nor runtime OAuth
volumes. These paths SHALL not use schema-local
Codex rows, an in-memory cache, a local canonical auth file, a staged file,
PID, or timestamp as a recovery authority. Only the control plane may project
or stage a complete current binding onto the session host; absent, malformed,
unavailable, or inconsistent
evidence SHALL leave Codex launch unavailable without altering local files into
authority.

ID: REQ-core-daemon-006
Source: RFC 0038 D3; approved opaque-generation Option-A authority and generation-fenced-codex-auth-rotation-provenance design D0-D10
Scope: v1-mandatory

#### Scenario: Fresh daemon restores the current shared generation
- **WHEN** a daemon starts after another daemon has created a current opaque
  Codex generation
- **THEN** its deterministic wiring delegates to the control plane, whose
  read-only complete-current-binding query selects only the raw value bound
  to that current system-global generation for staging on the session host
- **AND** the domain daemon receives no provider credential or volume mount
- **AND** it does not require a process-local rotation cache from the prior
  daemon
- **AND** startup creates no durable prepared or launched operation unless it
  is actually going to launch a child

#### Scenario: Fresh daemon finds an orphaned local rotation
- **WHEN** a daemon starts with a local Codex file that differs from the
  complete shared current binding and no live completion path proves it
- **THEN** the daemon does not promote or attach health to the local file
- **AND** it delegates a later fenced operation to the control plane and
  session host, or fails closed when the shared binding is unavailable
- **AND** the legacy local file is never restored as domain provider authority

#### Scenario: Repeated startup projection leaves no operation behind
- **WHEN** daemon restarts request compatibility projection refreshes on the
  session-host credential volume without launching a Codex child
- **THEN** each refresh uses only the control plane's read-only complete current
  binding and authenticated transport, not a domain-local credential read
- **AND** no nonterminal health-probe or other Codex operation is created

#### Scenario: Connector restoration preserves the same boundary
- **WHEN** a Codex-dependent connector restores CLI auth during startup
- **THEN** it delegates restoration to the control plane, which selects the
  system-global current generation and stages only onto the session host
- **AND** the connector neither receives goal provider credentials nor
  constructs a local credential authority as a fallback

### Requirement: Deterministic Provenance Cleanup Wiring

The deterministic lifecycle wiring SHALL make the value-free Codex operation
expiry/cleanup path available through the control-plane core maintenance owner without an LLM
session, a dashboard mutation, or a provider call.  It SHALL tolerate a
pre-migration/core-only environment by reporting safe unavailable state and
shall not delete a current credential or generation as a fallback.

ID: REQ-core-daemon-007
Source: RFC 0038 D3; approved opaque-generation Option-A authority and generation-fenced-codex-auth-rotation-provenance design D0-D10
Scope: v1-mandatory

#### Scenario: Cleanup handles a pre-migration relation safely
- **WHEN** the daemon's deterministic cleanup wiring runs before the Codex
  provenance relations are available
- **THEN** it reports the cleanup source unavailable without changing a Codex
  credential or local auth projection
- **AND** ordinary daemon startup remains able to report its existing health
  state honestly
