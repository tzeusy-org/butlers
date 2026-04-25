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
The decomposition step SHALL invoke signal-extraction logic programmatically (direct LLM API call with the signal-extraction prompt template) to produce per-butler conceptual messages.

#### Scenario: Signal extraction produces conceptual messages
- **WHEN** the decomposition step processes a conversation history batch
- **THEN** it invokes the signal-extraction prompt with the conversation content and registered butler schemas
- **AND** receives a JSON array of extraction objects, each containing `type`, `confidence`, `tool_name`, `tool_args`, `target_butler`

#### Scenario: Signal extraction invoked via direct API call
- **WHEN** the decomposition step runs
- **THEN** it calls the LLM API directly with the signal-extraction prompt template (not via Claude Code skill framework)
- **AND** the prompt template and butler schemas are loaded from the signal-extraction skill directory

### Requirement: Cherry-Picked Message Excerpts
Each conceptual message SHALL contain only the conversation messages relevant to that concept, cherry-picked from the full conversation window.

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
