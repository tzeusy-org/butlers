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

#### Scenario: Resolution failure omits the anchor safely

- **WHEN** per-speaker resolution fails and routing continues fail-open
- **THEN** the excerpt MUST omit or null `sender_entity_id`
- **AND** it MUST retain a neutral label and MUST NOT expose the transport identifier as a name

#### Scenario: Non-batched messages remain compatible

- **WHEN** a message does not use conversation decomposition
- **THEN** its existing single-sender route contract MUST remain unchanged
- **AND** the additive excerpt identity fields MUST NOT become required for unrelated ingress paths

## MODIFIED Requirements

### Requirement: Signal Extraction for Decomposition

The decomposition step SHALL invoke signal-extraction to produce per-butler conceptual messages. As
built (`src/butlers/modules/pipeline.py`), the decomposition branch dispatches through the Spawner
(`_dispatch_fn`, the same path used for routing) with a **dedicated signal-extraction prompt**
(`_build_decomposition_prompt`, which drives the `/signal-extraction` skill and asks for a strict JSON
array of full-schema conceptual messages rather than `route_to_butler` tool calls) and
`complexity=CHEAP`. The dispatched runtime's JSON output is parsed (tolerating markdown fences and
wrapper objects) and each object is normalized to the full conceptual-message schema (`signal_type`,
`target_butler`, `tool_name`, `tool_args`, `excerpts`, `confidence`); entries without a routable
`target_butler` (accepting the legacy `butler` alias) are dropped. For ordinary concepts,
`tool_name` is normalized to `route.execute` regardless of a model-selected direct tool and
`tool_args` remains structured signal data inside the conceptual runtime context. The explicit
code-authoritative calendar proposal translation remains the only direct-tool special case.

#### Scenario: Signal extraction produces conceptual messages

- **WHEN** the decomposition step processes a conversation history batch
- **THEN** it dispatches the conversation content (as untrusted-data context in the dedicated
  signal-extraction prompt) through the Spawner
- **AND** when the runtime returns a JSON array, each object is normalized to the full
  conceptual-message schema (`signal_type`, `target_butler`, `tool_name`, `tool_args`, `excerpts`,
  `confidence`) before routing
- **AND** ordinary model-selected direct target tools MUST be replaced with `route.execute`

### Requirement: Cherry-Picked Message Excerpts

Each conceptual message SHALL contain only the conversation messages relevant to that concept,
cherry-picked from the full conversation window.

As built, the dedicated signal-extraction prompt instructs the runtime to cherry-pick per-concept
`excerpts` by `message_id`. The pipeline treats those IDs as selectors and projects each matching
message from the authoritative enriched input as `{message_id, sender, sender_identity,
sender_entity_id, text, timestamp}`. It carries the full conceptual-message metadata (`signal_type`,
`tool_args`, `excerpts`, `confidence`) to the target butler in `route.v1 input.context` under
`conceptual_message`. Selection of which messages are relevant remains the runtime's responsibility;
the pipeline enforces the authoritative excerpt shape but does not itself re-derive relevance.

#### Scenario: Relevant messages cherry-picked per concept

- **WHEN** signal extraction identifies a concept (e.g., "finance: shared expense discussion")
- **THEN** the conceptual message includes only the messages that are relevant to that concept
- **AND** irrelevant messages from the conversation window are excluded

#### Scenario: Messages duplicated across concepts

- **WHEN** a message is relevant to multiple concepts (e.g., "Let's split the dinner bill at that new
  Italian place" is both finance and lifestyle)
- **THEN** the message appears in the conceptual messages for each relevant concept
- **AND** this duplication is by design and expected

#### Scenario: Conceptual message structure

- **WHEN** a conceptual message is produced
- **THEN** it SHALL contain:
  - `signal_type`: domain type (e.g., "finance", "health", "relationship")
  - `target_butler`: destination butler name
  - `tool_name`: `route.execute` for ordinary conceptual messages
  - `tool_args`: JSON object of structured signal details carried in runtime context
  - `excerpts`: array of `{message_id, sender, sender_identity, sender_entity_id, text, timestamp}`
    messages cherry-picked from the authoritative conversation
  - `confidence`: one of HIGH, MEDIUM, LOW

### Requirement: Multi-Butler Fan-Out from Single Ingestion

The decomposition step SHALL route each conceptual message to its target butler via the existing
`route()` mechanism and the target's standard `route.execute` session boundary, producing multiple
routing calls from a single ingestion event. Every concept receives one target-visible subrequest
identity, and target deduplication MUST use the parent request plus that subrequest identity rather
than collapsing distinct concepts sent to the same butler.

#### Scenario: Fan-out to multiple butlers

- **WHEN** signal extraction produces conceptual messages targeting butlers A, B, and C
- **THEN** `route()` is called once for each target butler with the corresponding conceptual message
- **AND** each routing call is tracked in `dispatch_outcomes` on the parent `message_inbox` row

#### Scenario: Multiple concepts target one butler

- **WHEN** signal extraction produces more than one conceptual message for the same target butler
- **THEN** every concept MUST carry a distinct `subrequest_id` and `segment_id` in the target-visible
  `route.v1` envelope
- **AND** successful processing of one subrequest MUST NOT deduplicate another subrequest from the
  same parent request

#### Scenario: Conceptual fan-out uses the standard runtime boundary

- **WHEN** signal extraction selects a target and supplies a direct target tool name
- **THEN** ordinary conceptual fan-out MUST ignore that direct tool selection and use the target's
  standard `route.execute` session boundary
- **AND** the authoritative conceptual message MUST travel in `route.v1 input.context`
- **AND** explicitly code-authoritative special handling such as calendar proposals MUST remain in
  force

#### Scenario: Partial fan-out failure

- **WHEN** routing to butler A succeeds but routing to butler B fails
- **THEN** the successful route to A is preserved
- **AND** the failed route to B is recorded in `dispatch_outcomes` with error details
- **AND** the parent message `lifecycle_state` reflects partial success
