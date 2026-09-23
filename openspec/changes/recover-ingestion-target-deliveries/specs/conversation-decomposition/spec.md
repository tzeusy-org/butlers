## MODIFIED Requirements

### Requirement: Decomposition Output Storage

Decomposition results SHALL be stored in the existing `decomposition_output` JSONB field on `message_inbox`. For ordinary non-dashboard domain `route.execute` segments, the completed extraction result and its per-segment delivery intents SHALL commit before the first target call; later dispatch outcomes SHALL be recorded without replacing that classified result.

ID: REQ-conversation-decomposition-002
Source: RFC 0003 §Conversation-History Decomposition and Fan-Out; design.md Decision 1
Scope: v1-mandatory

#### Scenario: Successful decomposition stored

- **WHEN** signal extraction produces one or more conceptual messages for an explicitly code-authoritative non-`route.execute` special case
- **THEN** the full extraction result (JSON array of conceptual messages) is stored in `decomposition_output`
- **AND** the field is updated atomically with the routing outcomes

#### Scenario: Ordinary target plan is stored before delivery

- **WHEN** signal extraction produces one or more ordinary domain `route.execute` conceptual messages
- **THEN** the full extraction result and stable segment identities SHALL be stored in `decomposition_output` atomically with every corresponding delivery intent before any target call
- **AND** each eventual target outcome SHALL update delivery evidence without changing the committed extraction decision

#### Scenario: Decomposition output includes metadata

- **WHEN** decomposition completes (empty or not)
- **THEN** `decomposition_output` SHALL include `signals` (the extraction array), `model` (LLM model used), `latency_ms` (extraction duration), and `token_usage` (input/output tokens)

#### Scenario: Other decomposition outcomes remain intact

- **WHEN** extraction returns no signals or an explicitly code-authoritative non-`route.execute` special case
- **THEN** its existing empty-result or special-case behavior SHALL remain in force
- **AND** it SHALL NOT acquire an ordinary target-delivery intent merely because the parent event was decomposed
