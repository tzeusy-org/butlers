# Conversation Decomposition

## Purpose

Switchboard pipeline step that decomposes conversation history batches into per-butler conceptual messages with cherry-picked excerpts. Triggered by `control.payload_type == "conversation_history"` envelopes (produced by batching connectors such as `telegram_user_client` and `whatsapp_user_client`), it runs signal-extraction to fan out a single ingestion event to multiple specialist butlers, each receiving only the messages relevant to that butler's domain.

## Requirements

### Requirement: Conversation Decomposition Pipeline Step
The switchboard pipeline SHALL decompose conversation history batches into per-butler conceptual messages before LLM classification. This step triggers only for envelopes tagged with `control.payload_type == "conversation_history"`.

#### Scenario: Decomposition triggered by payload_type
- **WHEN** `pipeline.process()` receives a message with `control.payload_type == "conversation_history"`
- **THEN** the pipeline enters the decomposition branch instead of standard single-target LLM classification
- **AND** signal-extraction is invoked on the conversation content

#### Scenario: Standard messages bypass decomposition
- **WHEN** `pipeline.process()` receives a message without `control.payload_type == "conversation_history"`
- **THEN** the pipeline follows the existing classification and routing flow unchanged

#### Scenario: Decomposition runs post-persist
- **WHEN** a conversation history envelope is ingested
- **THEN** the envelope is persisted to `message_inbox` and acknowledged (202) before decomposition begins
- **AND** decomposition runs in the background pipeline processing task, not in `ingest_v1()`

### Requirement: Signal Extraction for Decomposition
The decomposition step SHALL invoke signal-extraction to produce per-butler conceptual messages. As built (`src/butlers/modules/pipeline.py`), the decomposition branch dispatches through the Spawner (`_dispatch_fn`, the same path used for routing) with a **dedicated signal-extraction prompt** (`_build_decomposition_prompt`, which drives the `/signal-extraction` skill and asks for a strict JSON array of full-schema conceptual messages rather than `route_to_butler` tool calls) and `complexity=CHEAP`. The dispatched runtime's JSON output is parsed (tolerating markdown fences and wrapper objects) and each object is normalized to the full conceptual-message schema (`signal_type`, `target_butler`, `tool_name`, `tool_args`, `excerpts`, `confidence`); entries without a routable `target_butler` (accepting the legacy `butler` alias) are dropped.

#### Scenario: Signal extraction produces conceptual messages
- **WHEN** the decomposition step processes a conversation history batch
- **THEN** it dispatches the conversation content (as untrusted-data context in the dedicated signal-extraction prompt) through the Spawner
- **AND** when the runtime returns a JSON array, each object is normalized to the full conceptual-message schema (`signal_type`, `target_butler`, `tool_name`, `tool_args`, `excerpts`, `confidence`) before routing

### Requirement: Cherry-Picked Message Excerpts
Each conceptual message SHALL contain only the conversation messages relevant to that concept, cherry-picked from the full conversation window.

As built, the dedicated signal-extraction prompt instructs the runtime to cherry-pick per-concept `excerpts`, and the pipeline normalizes each excerpt to the `{sender, text, timestamp, message_id}` projection and carries the full conceptual-message metadata (`signal_type`, `excerpts`, `confidence`) to the target butler via the route arguments (`__conceptual_message`). Selection of which messages are relevant remains the runtime's responsibility; the pipeline enforces the excerpt shape but does not itself re-derive relevance.

#### Scenario: Relevant messages cherry-picked per concept
- **WHEN** signal extraction identifies a concept (e.g., "finance: shared expense discussion")
- **THEN** the conceptual message includes only the messages that are relevant to that concept
- **AND** irrelevant messages from the conversation window are excluded

#### Scenario: Messages duplicated across concepts
- **WHEN** a message is relevant to multiple concepts (e.g., "Let's split the dinner bill at that new Italian place" is both finance and lifestyle)
- **THEN** the message appears in the conceptual messages for each relevant concept
- **AND** this duplication is by design and expected

#### Scenario: Conceptual message structure
- **WHEN** a conceptual message is produced
- **THEN** it SHALL contain:
  - `signal_type`: domain type (e.g., "finance", "health", "relationship")
  - `target_butler`: destination butler name
  - `tool_name`: MCP tool to call on target butler
  - `tool_args`: JSON object of tool arguments
  - `excerpts`: array of `{sender, text, timestamp, message_id}` cherry-picked from the conversation
  - `confidence`: one of HIGH, MEDIUM, LOW

### Requirement: Multi-Butler Fan-Out from Single Ingestion
The decomposition step SHALL route each conceptual message to its target butler via the existing `route()` mechanism, producing multiple routing calls from a single ingestion event.

#### Scenario: Fan-out to multiple butlers
- **WHEN** signal extraction produces conceptual messages targeting butlers A, B, and C
- **THEN** `route()` is called once for each target butler with the corresponding conceptual message
- **AND** each routing call is tracked in `dispatch_outcomes` on the parent `message_inbox` row

#### Scenario: Partial fan-out failure
- **WHEN** routing to butler A succeeds but routing to butler B fails
- **THEN** the successful route to A is preserved
- **AND** the failed route to B is recorded in `dispatch_outcomes` with error details
- **AND** the parent message `lifecycle_state` reflects partial success

### Requirement: Empty Decomposition Handling
When signal-extraction returns an empty array, the result SHALL be logged for dashboard visibility without invoking any LLM classification or routing.

#### Scenario: Empty decomposition logged
- **WHEN** signal-extraction returns `[]` for a conversation history batch
- **THEN** `decomposition_output` is set to `{"signals": [], "reason": "no_signals_extracted"}`
- **AND** `lifecycle_state` is set to `"decomposed_empty"`
- **AND** no LLM classification or routing is invoked

#### Scenario: Empty decomposition visible in dashboard
- **WHEN** the dashboard queries for ingestion events
- **THEN** events with `lifecycle_state == "decomposed_empty"` are visible with their decomposition output
- **AND** operators can monitor the rate of empty decompositions per connector/chat

### Requirement: Decomposition Output Storage
Decomposition results SHALL be stored in the existing `decomposition_output` JSONB field on `message_inbox`.

#### Scenario: Successful decomposition stored
- **WHEN** signal extraction produces one or more conceptual messages
- **THEN** the full extraction result (JSON array of conceptual messages) is stored in `decomposition_output`
- **AND** the field is updated atomically with the routing outcomes

#### Scenario: Decomposition output includes metadata
- **WHEN** decomposition completes (empty or not)
- **THEN** `decomposition_output` includes: `signals` (the extraction array), `model` (LLM model used), `latency_ms` (extraction duration), `token_usage` (input/output tokens)
