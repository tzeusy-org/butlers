## ADDED Requirements

### Requirement: Structured speaker identity in conceptual excerpts

Conversation decomposition MUST preserve the structured sender identity and resolved entity anchor
for each cherry-picked message while presenting only a canonical or neutral speaker label to the
signal-extraction runtime and downstream butlers.

ID: REQ-conversation-decomposition-001
Source: docs/superpowers/specs/2026-08-24-whatsapp-identity-reconciliation-design.md §3
Scope: v1-mandatory

#### Scenario: Known speaker excerpt carries its entity anchor

- **WHEN** a cherry-picked message belongs to a speaker resolved to a live entity
- **THEN** the excerpt MUST carry `sender_identity` and `sender_entity_id`
- **AND** its `sender` label MUST be the safe canonical display name rather than a transport identifier

#### Scenario: Unknown speaker excerpt carries a transitory anchor

- **WHEN** a cherry-picked message belongs to a genuinely unknown sender whose transitory entity was
  reserved successfully
- **THEN** the excerpt MUST carry that transitory `sender_entity_id`
- **AND** the label MUST remain neutral and free of the raw transport identifier

#### Scenario: Multiple concepts preserve the same speaker anchor

- **WHEN** one message is duplicated into multiple conceptual messages
- **THEN** every copy MUST preserve the same `sender_identity` and `sender_entity_id`
- **AND** no fan-out target may substitute the top-level routing sender's entity

#### Scenario: Conceptual fan-out uses the standard runtime boundary

- **WHEN** signal extraction selects a target and supplies a direct target tool name
- **THEN** ordinary conceptual fan-out MUST ignore that direct tool selection and use the target's
  standard `route.execute` session boundary
- **AND** the authoritative conceptual message MUST travel in `route.v1 input.context`
- **AND** explicitly code-authoritative special handling such as calendar proposals MUST remain in
  force

#### Scenario: Resolution failure omits the anchor safely

- **WHEN** per-speaker resolution fails and routing continues fail-open
- **THEN** the excerpt MUST omit or null `sender_entity_id`
- **AND** it MUST retain a neutral label and MUST NOT expose the transport identifier as a name

#### Scenario: Non-batched messages remain compatible

- **WHEN** a message does not use conversation decomposition
- **THEN** its existing single-sender route contract MUST remain unchanged
- **AND** the additive excerpt identity fields MUST NOT become required for unrelated ingress paths
