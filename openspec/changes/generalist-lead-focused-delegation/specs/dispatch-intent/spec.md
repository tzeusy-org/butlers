## MODIFIED Requirements

### Requirement: Dispatch Intent Derivation
The system SHALL derive a `DispatchIntent` for every goal invocation and every
engineering-executor launch deterministically from the invocation's role
(`lead`, `child`, `monitor`) and requested tier, or from the engineering
executor's kind, with no model call, no clock, no randomness, and no read of
prompt content or goal input. The intent records what the dispatch requires
(required capabilities, optional context floor, deadline, per-call budget), what
it would prefer, and how consequential the run is.

Source: RFC 0038 D16, D18
Scope: v1-mandatory

#### Scenario: Trigger source reduces to a trigger class
- **WHEN** a dispatch intent is derived for a goal invocation
- **THEN** its role reduces to the `lead`, `child` or `monitor` trigger class, because
  the goal or obligation name identifies which work runs, never what that work
  needs from a model
- **AND** an unrecognized role or engineering-executor kind reduces to the
  `unknown` class

#### Scenario: Tool-wired triggers require tool use
- **WHEN** a dispatch intent is derived for any goal invocation
- **THEN** the intent requires the `tool_use` capability, because every goal
  invocation reaches the broker through MCP tool calls
- **AND** intents for engineering executors (self-healing and QA investigation
  agents) require no capabilities, matching their empty MCP wiring

#### Scenario: Unknown trigger is treated as the most consequential
- **WHEN** a dispatch intent is derived from an unrecognized role or
  engineering-executor kind
- **THEN** its consequence is `external`, not `observe`, so an unclassified caller
  never gets the most permissive fit rules by default

#### Scenario: Intent is prompt-free and JSON-safe
- **WHEN** an intent is projected for a resolution receipt
- **THEN** every field is a role-derived, executor-derived or caller-supplied
  scalar or feature name, so the projection can be stored and rendered without
  redaction
