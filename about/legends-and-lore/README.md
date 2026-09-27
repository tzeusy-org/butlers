# Legends and Lore -- Design Contracts

This directory contains the normative design contracts for the Butlers system. Each RFC defines a technical contract at the wire, protocol, or API level. Together they describe HOW the system works.

## Reading Order

For a new reader, the recommended order follows data flow from startup through request handling:

1. **RFC 0001** -- Daemon startup, trigger dispatch, and session lifecycle
2. **RFC 0002** -- MCP tool surface, module system, and skills infrastructure
3. **RFC 0027** -- Runtime-native Tool Search and deferred schema loading over a bounded MCP corpus
4. **RFC 0003** -- Switchboard ingestion, triage, classification, and routing
5. **RFC 0004** -- Identity resolution and contact model
6. **RFC 0005** -- Observability, tracing, and metrics
7. **RFC 0006** -- Database schema isolation and migration machinery
8. **RFC 0007** -- Dashboard API and frontend architecture

## Index

| RFC | Title | Status | Summary |
|-----|-------|--------|---------|
| [0001](rfcs/0001-daemon-lifecycle-and-triggers.md) | Daemon Lifecycle and Triggers | Accepted | Multi-phase startup, dual trigger sources, spawner concurrency, session lifecycle, request context propagation. |
| [0002](rfcs/0002-mcp-tool-surface-and-modules.md) | MCP Tool Surface and Modules | Accepted | FastMCP server, core tool catalog, module ABC and topological resolution, tool call logging, skills, ephemeral MCP config. |
| [0003](rfcs/0003-switchboard-routing-and-ingestion.md) | Switchboard Routing and Ingestion | Accepted | ingest.v1 envelope, triage, thread affinity, LLM classification fallback, route.execute, route inbox crash recovery. |
| [0004](rfcs/0004-identity-and-contact-resolution.md) | Identity and Contact Resolution | Accepted | Entity-based identity, resolve_contact_by_channel() contract, unknown sender handling, identity preamble, tenant model. |
| [0005](rfcs/0005-observability-and-telemetry.md) | Observability and Telemetry | Accepted | OTel setup, OTLP export, cross-process trace propagation, tool_span instrumentation, metrics catalog, cardinality discipline. |
| [0006](rfcs/0006-database-schema-and-isolation.md) | Database Schema and Isolation | Accepted | Single-PG multi-schema model, shared identity tables, per-butler schemas, multi-chain Alembic migrations, credential store. |
| [0007](rfcs/0007-dashboard-and-api-surface.md) | Dashboard and API Surface | Accepted | FastAPI + Vite architecture, auto-discovered butler routes, route families, backend API contract, `/system` namespace. |
| [0008](rfcs/0008-deployment-network-security.md) | Deployment Network Security | Accepted | Four-network isolation, tailnet-allowlisted egress firewall, localhost port binding, container environment isolation. |
| [0009](rfcs/0009-situational-context-bus.md) | Situational Context Bus | Accepted | Shared TTL-based `user_context` signals, pull-based queries, per-signal write permissions, LLM context preamble. |
| [0010](rfcs/0010-cross-butler-briefing-exception.md) | Cross-Butler Briefing Exception | Accepted | Rule 3 exception: read-only SQL view for briefing aggregation, five guardrails, reuse criteria for future exceptions. |
| [0011](rfcs/0011-proactive-insight-delivery.md) | Proactive Insight Delivery Protocol | Accepted | Insight generation, Switchboard brokering, notify delivery; attention budget, quiet hours, cooldowns, `propose_insight_candidate`. |
| [0012](rfcs/0012-finance-transaction-data-model.md) | Finance Transaction Data Model | Accepted | Dedicated typed `finance.transactions` table as primary store, supporting tables, tiered deduplication, spending summaries. |
| [0013](rfcs/0013-dunbar-group-aware-interaction-scoring.md) | Dunbar Group-Aware Interaction Scoring | Accepted | Direction-weighted and group-size-diluted interaction scoring, participant gating, reciprocal engagement gating. |
| [0014](rfcs/0014-chronicler-time-butler.md) | Chronicler Retrospective Time Butler | Accepted | Retrospective projection of timestamped evidence into point events and episodes, with provenance, corrections, no per-event LLM. |
| [0015](rfcs/0015-qa-staffer-discovery-investigation-pipeline.md) | QA Staffer Discovery & Investigation Pipeline | Accepted | Automated error detection, investigation dispatch, and anonymized fix pipeline run by the QA staffer. |
| [0016](rfcs/0016-s3-blob-storage-contract.md) | S3 Blob Storage Contract | Accepted | Blob storage for binary artifacts backed by S3-compatible object storage. |
| [0017](rfcs/0017-owner-routing-safety-incident-reconciliation.md) | Owner-Routing Safety and Audit Hardening | Accepted | Ambiguity-preserving owner lookup and uniform owner-channel authorization for owner-directed egress. |
| [0018](rfcs/0018-connector-scope-and-deferral-rationale.md) | Connector Scope and Deferral Rationale | Accepted | Which connectors are in scope, which are deferred, and why. |
| [0019](rfcs/0019-proactive-egress-and-automation-parked.md) | Proactive Egress and Automation (Parked / Rejected) | Rejected / Parked | Calendar auto-responses rejected; event-driven automation rule engine parked pending a doctrine decision. |
| [0020](rfcs/0020-calendar-cross-domain-overlay-read-exception.md) | Calendar Cross-Domain Overlay Read Exception | Accepted | Calendar overlays use scheduled deterministic precompute into a read-only cached view, zero LLM at render (RFC 0010 criteria). |
| [0021](rfcs/0021-decision-loop-one-tap-approvals-and-decision-memory.md) | Decision Loop: One-Tap Approvals and Decision Memory | Accepted | One-tap Telegram approvals with signed callbacks, structured decision dossier, deterministic decision-memory writeback. |
| [0022](rfcs/0022-cross-process-event-transport.md) | Cross-Process Event Transport (NOTIFY/LISTEN Fleet Event Bridge) | Accepted | Producers `pg_notify` from their DB pools; a dashboard-side `LISTEN` bridge republishes onto the in-process fleet event bus. |
| [0023](rfcs/0023-durable-approval-delivery-intent-recovery.md) | Durable Approval Delivery Intent Recovery | Accepted | Pending actions atomically coupled to a delivery intent; fenced recovery that never mutates the parked domain action. |
| [0024](rfcs/0024-messenger-private-email-correspondence-ledger.md) | Messenger-Private Email Correspondence Ledger | Proposed | Privacy-minimized Messenger outbound evidence ledger with bounded, aggregate-only Relationship enrichment. |
| [0025](rfcs/0025-tracker-host-beads-projection-exporter.md) | Tracker-Host Beads Projection Exporter | Draft | Tracker-host exporter publishes a minimal active Beads projection to PostgreSQL for bounded runtime readers. |
| [0026](rfcs/0026-commitment-lifecycle.md) | Evidence-Backed Commitment Lifecycle | Draft | Owner-condition ledger gains resolution, commitment metadata, closure receipts, escalation, and lifecycle evidence. |
| [0027](rfcs/0027-runtime-tool-surface-discovery.md) | Runtime Tool Surface Discovery and Exposure | Accepted | Runtime-native Tool Search and deferred schema loading over an adapter-bounded corpus; canonical MCP listing stays complete. |
| [0028](rfcs/0028-home-physical-actuation-contract.md) | Home Physical Actuation Contract | Accepted | Fail-closed HA risk map, approval boundary, per-attempt receipt, post-condition proof, rollback hints. |
| [0029](rfcs/0029-expected-signals-and-honest-absence.md) | Expected Signals and Honest Absence | Accepted | Shared present/absent/unmeasurable ledger, producer-liveness join, producer-owned upserts, degraded rendering. |
| [0030](rfcs/0030-system-plane-read-exception.md) | System-Plane Read Exception | Accepted | Concierge answers system-plane questions via column-allowlisted read-only UNION views (RFC 0010 plus a sixth guardrail). |
| [0031](rfcs/0031-public-entity-graph-projection.md) | Public Entity Graph Projection | Draft | Write-behind `public.entity_graph_edges` projection with withheld stub edges, enabling zero-LLM graph traversal. |
| [0032](rfcs/0032-fleet-case-file.md) | Fleet Case File | Implemented | Durable per-situation case object for multi-butler correlated clusters; Switchboard-only case writes, open idempotent evidence. |
| [0033](rfcs/0033-fleet-cost-claims.md) | Fleet Cost Claims | Implemented | Typed sibling-asserted money claims, role-fenced Finance verdicts, honest reconciliation coverage. |
| [0034](rfcs/0034-messenger-voice-egress.md) | Messenger-Owned Voice Egress | Draft | Messenger-owned local-first voice delivery: explicit initiation, presence, DND suppression, replay fencing, text fallback. |
| [0035](rfcs/0035-bounded-post-delivery-approval-reminders.md) | Bounded Post-Delivery Approval Reminders | Proposed | At most two distinct-channel reminders after confirmed approval delivery, within expiry, quiet-hours, and burst limits. |

The RFC header is the single home for status detail (slices landed, gates, owner sign-off). The Status
column is a one-word summary of it.

## Related

- [ideas-ledger.md](ideas-ledger.md) — parked ideas from the JARVIS pursuit dossiers, each with why it was parked and its unpark condition.

## Conventions

- **Status values:**
  - **Draft** — design in progress; not yet approved as a contract.
  - **Proposed** — complete design awaiting owner acceptance (planning may proceed).
  - **Accepted** — owner-approved contract; implementation may be partial or complete.
  - **Implemented** — accepted and fully landed in code.
  - **Rejected / Parked** — a recorded disposition not to build, or to defer pending a decision.
- **Normative language:** "MUST", "SHOULD", "MAY" follow their usual meaning.
- **Cross-references:** By RFC number (e.g., "see RFC 0003").
- **Date:** ISO 8601 format.
