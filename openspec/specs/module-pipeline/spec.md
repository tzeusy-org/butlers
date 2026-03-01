# Pipeline Module

## Purpose

The Pipeline module provides LLM-based message classification and routing for the Switchboard butler. It is the final routing stage in the ingestion flow: connectors ingest external messages, Switchboard applies deterministic triage, and unmatched messages fall through to the pipeline for LLM-driven classification and dispatch to specialist butlers.

## ADDED Requirements

### Requirement: Ingestion Architecture
Live data enters the system through connectors and flows through the Switchboard before reaching any domain butler. Modules on individual butlers provide runtime data lookup — they do not participate in ingestion.

#### Scenario: End-to-end ingestion flow
- **WHEN** an external message arrives (Telegram, email, API, etc.)
- **THEN** it is received by a connector process, normalized to an `ingest.v1` envelope, and submitted to Switchboard via MCP
- **AND** Switchboard assigns canonical request context (UUID7 request_id, timestamps, source identity), persists to `message_inbox`, and runs deterministic triage
- **AND** messages that match a triage rule are routed directly; messages that do not are forwarded to the pipeline for LLM classification

#### Scenario: Connectors own ingestion
- **WHEN** a message is ingested from an external channel
- **THEN** the connector (a standalone OS process) is responsible for transport polling, message normalization, identity resolution, and at-least-once delivery
- **AND** modules on individual butlers do not ingest external messages — they provide runtime data access only

#### Scenario: Switchboard owns request context and deduplication
- **WHEN** an `ingest.v1` envelope arrives at Switchboard
- **THEN** Switchboard generates the canonical UUID7 request_id, resolves identity, performs channel-specific deduplication (Telegram update_id, email Message-ID, API caller key), and manages the full request lifecycle
- **AND** the pipeline does not duplicate these responsibilities

### Requirement: Modules Enable Runtime Data Lookup
Modules on domain butlers provide tools for querying and manipulating domain-specific data. They sync with external sources independently and serve the LLM CLI at query time.

#### Scenario: Calendar module as runtime lookup
- **WHEN** a butler has the `calendar` module enabled
- **THEN** the module syncs with the external calendar provider (Google Calendar) on a configurable interval
- **AND** the LLM CLI can query events, create meetings, and detect conflicts through calendar MCP tools at runtime — without any ingestion pipeline involvement

#### Scenario: Contacts module as runtime lookup
- **WHEN** a butler has the `contacts` module enabled
- **THEN** the module performs incremental sync with the contacts provider (Google Contacts) and maintains local contact records
- **AND** the LLM CLI can search, resolve, and update contacts through contacts MCP tools

#### Scenario: Email module as runtime lookup
- **WHEN** a butler has the `email` module enabled
- **THEN** the module provides IMAP search and SMTP send tools for on-demand email operations
- **AND** email ingestion for routing is handled by the Gmail connector and Switchboard, not by the email module

#### Scenario: Memory module as runtime lookup
- **WHEN** a butler has the `memory` module enabled
- **THEN** the module provides semantic search, fact storage, and entity resolution tools
- **AND** memory is populated during LLM CLI sessions (store_episode, store_fact) and consolidated by scheduled jobs, not by an ingestion pipeline

### Requirement: Pipeline as Post-Triage LLM Router
The pipeline's sole responsibility is LLM-driven classification and routing for messages that pass through deterministic triage without a match. It runs exclusively on the Switchboard butler.

#### Scenario: Pipeline receives pre-processed messages
- **WHEN** a message passes through triage without matching any rule
- **THEN** the pipeline receives it with a fully formed `request_context` (request_id, source channel, identity, timestamps) already assigned by Switchboard
- **AND** the pipeline does not re-derive identity, generate request IDs, or perform deduplication

#### Scenario: Routing prompt construction
- **WHEN** the pipeline builds an LLM routing prompt
- **THEN** the prompt includes: safety instructions (treat user input as untrusted data), available butlers with descriptions and capabilities, the user message (JSON-serialized for injection defense), conversation history (channel-appropriate), and routing instructions directing the model to call `route_to_butler`

#### Scenario: Route tool call parsing
- **WHEN** the LLM session completes
- **THEN** `route_to_butler` tool calls (bare or MCP-namespaced) are parsed from session output
- **AND** the `butler` argument is extracted and validated against registry-known butlers
- **AND** if no tool call is found, a fallback inference from model text output is attempted (single unambiguous match only)

#### Scenario: Routing result
- **WHEN** classification and routing complete
- **THEN** a `RoutingResult` is returned with `target_butler`, `routed_targets`, `acked_targets`, and `failed_targets`
- **AND** classification or routing errors are captured in `classification_error` and `routing_error` fields

### Requirement: Conversation History for Routing Context
The pipeline loads channel-appropriate conversation history to improve LLM routing accuracy.

#### Scenario: Realtime messaging history
- **WHEN** the source channel is `telegram`, `whatsapp`, `slack`, or `discord`
- **THEN** recent conversation history is loaded (max 15-minute window, max 30 messages) for routing context

#### Scenario: Email thread history
- **WHEN** the source channel is `email`
- **THEN** the full email chain is loaded (max 50,000 tokens, newest messages preserved) for routing context

#### Scenario: No history for API/MCP channels
- **WHEN** the source channel is `api` or `mcp`
- **THEN** no conversation history is loaded

### Requirement: Concurrent Pipeline Session Isolation
The pipeline uses per-task context variables to prevent cross-contamination between concurrent routing sessions.

#### Scenario: Context isolation
- **WHEN** multiple messages are routed concurrently through the pipeline
- **THEN** each asyncio task has its own isolated routing context (source metadata, request context, conversation history)
- **AND** context is cleared when the routing session completes

### Requirement: Deprecated — Direct Module-to-Pipeline Wiring
The legacy pattern where input modules (Email, Telegram) call `set_pipeline()` to wire themselves directly to the classification pipeline is deprecated.

#### Scenario: Legacy set_pipeline() method
- **WHEN** a module calls `set_pipeline()` on the pipeline instance
- **THEN** the call is accepted for backward compatibility but the path is deprecated
- **AND** new ingestion must use the connector → Switchboard → pipeline flow
- **AND** the email module's `email_check_and_route_inbox` tool is deprecated in favor of the Gmail connector
