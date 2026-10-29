## MODIFIED Requirements

### Requirement: Ingestion Architecture

Live data SHALL enter the system through connectors and flow through the Switchboard before reaching any domain butler. Switchboard ingress SHALL submit every envelope that needs reasoning to goal admission; deterministic ingestion that needs none (ingestion-policy gating, channel-bound envelopes such as `wellness` readings delivered to the owning domain's service-plane ingest, and bulk backfill and sync) SHALL stay on the service plane and admit no goal (RFC 0038 D1). No domain butler SHALL receive an inbound message by classification or route dispatch. Modules on individual butlers provide runtime data lookup — they do not participate in ingestion.

Source: RFC 0038 D1, D5
Scope: v1-mandatory

#### Scenario: End-to-end ingestion flow
- **WHEN** an external message arrives (Telegram, email, API, etc.)
- **THEN** it is received by a connector process, normalized to an `ingest.v1` envelope, and submitted to Switchboard via MCP
- **AND** Switchboard assigns canonical request context (UUID7 request_id, timestamps, source identity), persists to `message_inbox`, and applies the deterministic ingestion policy
- **AND** an envelope the policy does not suppress and that no channel binds to a service-plane ingest is submitted to goal admission without an LLM classification or route-dispatch step
- **AND** an envelope bound by its channel to a deterministic service-plane ingest (such as `wellness`) is delivered to that ingest and admits no goal

#### Scenario: Connectors own ingestion
- **WHEN** a message is ingested from an external channel
- **THEN** the connector (a standalone OS process) is responsible for transport polling, message normalization, identity resolution, and at-least-once delivery
- **AND** modules on individual butlers do not ingest external messages — they provide runtime data access only

#### Scenario: Switchboard owns request context and deduplication
- **WHEN** an `ingest.v1` envelope arrives at Switchboard
- **THEN** Switchboard generates the canonical UUID7 request_id, resolves identity, performs channel-specific deduplication (Telegram update_id, email Message-ID, API caller key), and manages the full request lifecycle
- **AND** goal admission does not duplicate these responsibilities, and a duplicate envelope resolves to the goal already admitted from it

### Requirement: Conversation History for Routing Context

Conversation history that a goal invocation reads, whether through a capability or as part of a batched envelope's goal input, SHALL be channel-appropriate, SHALL be selected from `message_inbox` by `external_conversation_id`, and SHALL carry per-item stamps so that history not authored by the owner exposes the invocation that reads it.

Source: RFC 0038 D6, D15
Scope: v1-mandatory

#### Scenario: Realtime messaging history
- **WHEN** the source channel is `telegram`, `whatsapp`, `slack`, or `discord`
- **THEN** recent conversation history is loaded (max 15-minute window, max 30 messages) for the goal
- **AND** realtime history SHALL select `message_inbox` rows only by `request_context ->> 'external_conversation_id'`, never by a per-message reply target or `source_thread_identity`

#### Scenario: Consecutive messages in one chat share history
- **WHEN** two Telegram messages from the same chat arrive with distinct reply targets
- **THEN** the second message's realtime history SHALL include the first, because both carry the same `external_conversation_id`

#### Scenario: Outbound rows join their conversation's history
- **WHEN** a delivered outbound message is written to `message_inbox` for history
- **THEN** its request context SHALL carry `external_conversation_id`: the notify request context's value when present, otherwise the key derived from the delivery target (for Telegram, `telegram:<chat_id>` of the chat the message was delivered to; for WhatsApp, `whatsapp:<chat_jid>`, including proactive sends)

#### Scenario: Pre-split history remains readable
- **WHEN** `message_inbox` holds rows written before connectors split conversation identity from reply targets
- **THEN** a one-time migration SHALL have backfilled their `external_conversation_id` with the key the connector emits today (Telegram `telegram:<chat_id>` from the legacy `<chat_id>` or `<chat_id>:<message_id>` value, WhatsApp `whatsapp:<chat_jid>`, every other channel verbatim)
- **AND** the stored `raw_payload.event` of rows still awaiting buffer recovery for the Telegram bot, Telegram user-client, and WhatsApp user-client channels SHALL carry both split fields

#### Scenario: Email thread history
- **WHEN** the source channel is `email`
- **THEN** the full email chain is loaded (max 50,000 tokens, newest messages preserved) for the goal

#### Scenario: No history for API/MCP channels
- **WHEN** the source channel is `api` or `mcp`
- **THEN** no conversation history is loaded

#### Scenario: Third-party history exposes the reader
- **WHEN** history loaded for a goal contains messages not authored by the owner
- **THEN** those messages reach the invocation fenced as data with their stamps
- **AND** the invocation that receives them becomes exposed before it can request a consequential effect

## REMOVED Requirements

### Requirement: Pipeline as Post-Triage LLM Router
**Reason**: RFC 0038 D1 and the RFC 0003 disposition remove LLM message classification and route dispatch; no model-classification step precedes admission.
**Migration**: Envelopes go to goal admission and one lead fulfills each goal (`REQ-goal-orchestration-008`, `REQ-goal-orchestration-001`); model choice is per invocation by role and tier (D16).

### Requirement: Concurrent Pipeline Session Isolation
**Reason**: Routing sessions no longer exist (RFC 0038 D1); there is no concurrent classification context to isolate.
**Migration**: Each lead and child runs in its own sandboxed process domain with its own token (`REQ-capability-authorization-001`, `REQ-capability-authorization-008`).

### Requirement: Conversation Decomposition Branch
**Reason**: Conversation decomposition into per-butler routes is removed with classification and route dispatch (RFC 0038 D1, RFC 0003 disposition).
**Migration**: A `conversation_history` batch envelope is admitted as one goal whose lead handles every domain signal in it (`REQ-goal-orchestration-001`, `REQ-goal-orchestration-008`).

### Requirement: Decomposition-to-Routing Fan-Out
**Reason**: Fan-out of conceptual messages through `route()` is route dispatch, which RFC 0038 deletes at cutover.
**Migration**: The lead calls the owning domains' capabilities directly or delegates depth-1 children (`REQ-goal-orchestration-001`, `REQ-goal-orchestration-004`).

### Requirement: Empty Decomposition Short-Circuit
**Reason**: The decomposition branch it short-circuits is removed (RFC 0038 D1).
**Migration**: A goal that finds nothing to do completes with that outcome in the goal ledger (`REQ-goal-orchestration-006`, `REQ-capability-authorization-012`).

### Requirement: Daemon-to-PipelineModule Injection
**Reason**: The `MessagePipeline` classification router and its `pipeline.process` tool are removed with classification and route dispatch (RFC 0038 D1, Hard cutover).
**Migration**: Switchboard ingress submits envelopes to goal admission (`REQ-goal-orchestration-008`); connectors remain the only ingestion path.
