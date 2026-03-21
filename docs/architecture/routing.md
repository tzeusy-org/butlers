# Routing Architecture

> **Purpose:** Describes the message routing architecture — how incoming requests flow from ingress through classification to domain butler execution.
> **Audience:** Developers working on routing, operators debugging misrouted messages, architects understanding the fanout model.
> **Prerequisites:** [System Topology](system-topology.md), [Butler Daemon](butler-daemon.md).

## Overview

The Switchboard Butler is the single ingress and orchestration control plane for the butler system. All external interactions start here. The routing architecture implements a pipeline that assigns identity context, classifies intent via LLM, fans out work to domain butlers, and collects responses — all while maintaining durable request lineage.

## Routing Pipeline

The end-to-end routing flow has five major stages:

### Stage 1: Ingestion and Context Assignment

Every incoming message (from Telegram, email, MCP clients, or API calls) enters through the Switchboard's ingestion layer. Before any routing decision is made, a canonical request context is assigned:

- **request_id** — a UUID7, immutable for the full request lineage
- **received_at** — UTC timestamp of ingestion
- **source_channel** — the originating channel (e.g., `telegram_bot`, `email`, `mcp`)
- **source_endpoint_identity** — the ingress identity that received the message
- **source_sender_identity** — the actor who sent the message
- **source_thread_identity** — conversation/thread identifier when available

The ingestion layer persists the raw payload and canonical context in PostgreSQL month-partitioned tables for short-term retention (default 1 month). The ingestion acceptance is non-blocking — work is queued asynchronously via a durable buffer backed by an in-memory queue with database persistence for crash recovery.

### Stage 2: Identity Resolution

Before the routing LLM is invoked, the Switchboard resolves sender identity:

1. **Reverse-lookup**: The sender identifier is matched against `shared.contacts` and `shared.contact_info` tables to find a known contact record.
2. **Known sender**: An identity preamble is built (e.g., `[Source: Owner, via telegram_bot]` or `[Source: John (contact_id: ..., entity_id: ...), via email]`).
3. **Unknown sender**: A temporary contact is created with `metadata.needs_disambiguation = true`. The owner receives a one-time notification. An unknown-sender preamble is injected.

The resolved identity (contact_id, entity_id, sender_roles) is persisted to the `routing_log` for every routed message, establishing full identity lineage from ingress through downstream routing.

### Stage 3: Pre-Classification Triage

Before invoking the LLM classifier, a deterministic triage layer evaluates the message against rules and heuristics that can route without LLM involvement. This eliminates approximately 50-70% of classification calls for typical personal-email workloads.

The triage pipeline evaluates in order:

1. **Thread affinity** — if the message belongs to an email thread previously routed to a butler, and that routing is unambiguous (single target within TTL), route to the same butler.
2. **Triage rules** — persistent rules in `switchboard.triage_rules` matched by sender domain, sender address, header conditions, or MIME type. Rules are evaluated in priority order; first match wins.
3. **Pass-through** — if no deterministic rule matches, the message proceeds to LLM classification.

Triage rules support actions including `route_to:<butler>`, `skip`, `metadata_only`, `low_priority_queue`, and `pass_through`. See [Pre-Classification Triage](pre-classification-triage.md) and [Thread Affinity Routing](thread-affinity-routing.md) for detailed specifications.

### Stage 4: LLM-Based Classification

Messages that pass through triage enter LLM-based classification. The Switchboard spawns an ephemeral runtime instance (typically using a lightweight model for fast classification) with:

- The normalized message content as a data payload (not executable instructions)
- An explicit prompt that forbids obeying instructions inside user content (prompt injection defense)
- The identity preamble from Stage 2
- A strict output schema constraining the routing decision

The classifier produces one or more routing targets with decomposition semantics:

- A single message may produce multiple target segments (one-to-many fanout)
- Each segment carries a self-contained prompt and segment metadata
- Segments may overlap in content when intentionally needed
- The output is validated against the registry of known butlers; invalid targets trigger a fallback to the `general` butler

### Stage 5: Fanout and Response Collection

For each routing target, the Switchboard dispatches via the `route.execute` entrypoint on the target butler's MCP server:

1. The request is wrapped in a `route.v1` envelope containing the request context, segment prompt, and source metadata.
2. On the target butler, `route.execute` persists the envelope to a `route_inbox` table in `accepted` state and returns immediately.
3. A background task picks up the accepted request, transitions it to `processing`, and dispatches through the spawner.
4. On completion, the inbox row is marked `processed` (with session_id) or `errored` (with error message).

This durable inbox pattern ensures crash recovery — on startup, each butler scans for `accepted` or `processing` rows and re-dispatches them.

Response collection follows the `route_response.v1` envelope contract. The Switchboard consumes responses from each downstream butler, matching them by `request_id`. Terminal states are:

- All targets succeeded: aggregate results
- Any target failed: record the failure with canonical error class
- Timeout: synthesize a timeout-class error response

For interactive channels (Telegram), the Switchboard emits lifecycle signals: `PROGRESS` when processing starts, `PARSED` when all targets succeed, `ERRORED` when any target fails.

## Email Priority Queuing

Email ingestion supports policy-tier-based priority queuing. Messages are classified into tiers (`high_priority`, `interactive`, `default`) at connector ingest time based on signals like known-contact matching, reply-to-outbound detection, and direct correspondence indicators. The Switchboard's durable buffer dequeues by tier priority while enforcing starvation guards to prevent permanent deferral of lower tiers. See [Email Priority Queuing](email-priority-queuing.md).

## Prompt Injection Safety

Since ingress content is always untrusted, the routing architecture implements mandatory controls:

- User content is passed as an isolated data payload, never as executable instructions
- The router prompt explicitly forbids obeying instructions embedded in user content
- Router output is constrained to a strict schema and validated against registry-known butlers
- Invalid or malformed classification output triggers a safe fallback to the `general` butler

## Inter-Butler Communication

Domain butlers never communicate with each other directly. All inter-butler communication flows through the Switchboard:

- **Outbound delivery**: Non-messenger butlers use the `notify` tool, which the Switchboard routes to the `messenger_butler` for actual delivery (Telegram, email, etc.)
- **Routed execution**: The Switchboard calls domain butlers via `route.execute`
- **Registry participation**: Butlers register with the Switchboard on startup and send periodic liveness heartbeats

## Related Pages

- [System Topology](system-topology.md) — service ports and inter-service communication
- [Email Priority Queuing](email-priority-queuing.md) — tier-based queue ordering
- [Pre-Classification Triage](pre-classification-triage.md) — deterministic rule-based routing
- [Thread Affinity Routing](thread-affinity-routing.md) — email thread-based routing continuity
- [Spawner](../runtime/spawner.md) — how LLM classification sessions are invoked
