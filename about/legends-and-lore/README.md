# Legends and Lore -- Design Contracts

This directory contains the normative design contracts for the Butlers system. Each RFC defines a technical contract at the wire, protocol, or API level. Together they describe HOW the system works.

## Reading Order

For a new reader, the recommended order follows data flow from startup through request handling:

1. **RFC 0001** -- Daemon startup, trigger dispatch, and session lifecycle
2. **RFC 0002** -- MCP tool surface, module system, and skills infrastructure
3. **RFC 0027** -- LLM tool presentation, portable eager filtering, and verified native deferred discovery
4. **RFC 0003** -- Switchboard ingestion, triage, classification, and routing
5. **RFC 0004** -- Identity resolution and contact model
6. **RFC 0005** -- Observability, tracing, and metrics
7. **RFC 0006** -- Database schema isolation and migration machinery
8. **RFC 0007** -- Dashboard API and frontend architecture

## Index

| RFC | Title | Summary |
|-----|-------|---------|
| [0001](rfcs/0001-daemon-lifecycle-and-triggers.md) | Daemon Lifecycle and Triggers | Multi-phase startup, dual trigger sources, spawner concurrency model, session lifecycle, request context propagation. |
| [0002](rfcs/0002-mcp-tool-surface-and-modules.md) | MCP Tool Surface and Modules | FastMCP SSE server, core tool catalog, module ABC and topological resolution, tool call logging proxy, skills, ephemeral MCP config. |
| [0003](rfcs/0003-switchboard-routing-and-ingestion.md) | Switchboard Routing and Ingestion | ingest.v1 envelope, pre-classification triage, thread affinity, LLM classification fallback, route.execute, route inbox crash recovery, email priority queuing. |
| [0004](rfcs/0004-identity-and-contact-resolution.md) | Identity and Contact Resolution | Three-table public schema, resolve_contact_by_channel() contract, unknown sender handling, identity preamble format, tenant model. |
| [0005](rfcs/0005-observability-and-telemetry.md) | Observability and Telemetry | OTel setup, OTLP export pipeline, trace propagation across process boundaries, tool_span instrumentation, metrics catalog, cardinality discipline. |
| [0006](rfcs/0006-database-schema-and-isolation.md) | Database Schema and Isolation | Single-PG multi-schema model, shared identity tables, per-butler schema contents, multi-chain Alembic migrations, credential store. |
| [0007](rfcs/0007-dashboard-and-api-surface.md) | Dashboard and API Surface | FastAPI + Vite architecture, auto-discovered butler routes, route map, backend API contract, tab structures, data access patterns, command palette. **Amendment 1:** `/system` dashboard route and `/api/system/*` namespace (instance, database, backups, egress catalog, butler heartbeats). |
| [0008](rfcs/0008-deployment-network-security.md) | Deployment Network Security | Four-network isolation model, egress firewall with tailnet allowlist, localhost port binding, container environment isolation, persistent runtime state. |
| [0009](rfcs/0009-situational-context-bus.md) | Situational Context Bus | Shared user_context table with TTL-based signals, pull-based context queries, per-signal write permissions, context preamble for LLM sessions. |
| [0010](rfcs/0010-cross-butler-briefing-exception.md) | Cross-Butler Briefing Exception | Sanctioned Rule 3 exception: read-only SQL view for daily briefing aggregation, five guardrails, reuse criteria for future cross-schema exceptions. |
| [0011](rfcs/0011-proactive-insight-delivery.md) | Proactive Insight Delivery Protocol | Three-phase insight pipeline (butler generation, Switchboard brokering, notify delivery), anti-spam budget/cooldown/adaptive ratchet, `propose_insight_candidate` MCP tool, `intent='insight'` notify extension. |
| [0012](rfcs/0012-finance-transaction-data-model.md) | Finance Transaction Data Model | Dedicated `finance.transactions` table with typed columns replacing SPO-primary storage, eight supporting tables, tiered deduplication, materialized spending summaries, 4-phase migration path. |
| [0013](rfcs/0013-dunbar-group-aware-interaction-scoring.md) | Dunbar Group-Aware Interaction Scoring | Direction-weighted scoring (outgoing 10x, mutual 5x, incoming 1x), group-size-divided scoring (1/n dilution), connector-level participant gating (>20 excluded), interaction_log_group batch tool, interaction_sync group-aware pre-grouping. |
| [0014](rfcs/0014-chronicler-time-butler.md) | Chronicler Retrospective Time Butler | Retrospective-only domain butler that projects timestamped evidence (`core.sessions`, completed calendar instances, durable Spotify summaries, etc.) into point events + overlapping episodes. Preserves source provenance, precision, privacy/retention; correction overlay model; no per-event LLM; `/api/chronicler/*` namespace distinct from operational `/api/timeline`. |
| [0015](rfcs/0015-qa-staffer-discovery-investigation-pipeline.md) | QA Staffer Discovery & Investigation Pipeline | Automated error detection, investigation dispatch, and anonymized fix pipeline run by the QA staffer. |
| [0016](rfcs/0016-s3-blob-storage-contract.md) | S3 Blob Storage Contract | Blob storage contract for binary artifacts backed by S3-compatible object storage. |
| [0017](rfcs/0017-owner-routing-safety-incident-reconciliation.md) | Owner-Routing Safety and Audit Hardening | Incident reconciliation: owner-channel verification, routing safety guarantees, and audit hardening for owner-directed egress. |
| [0018](rfcs/0018-connector-scope-and-deferral-rationale.md) | Connector Scope and Deferral Rationale | Records which connectors are in scope, which are deferred, and why. |
| [0019](rfcs/0019-proactive-egress-and-automation-parked.md) | Proactive Egress and Automation (Parked / Rejected) | Doctrine dispositions: calendar auto-responses **rejected** (owner, 2026-06-14); event-driven automation rule engine **parked** pending doctrine decision. Also catalogues five non-doctrine-gated egress gaps folded into live specs as `[TARGET-STATE]` (incl. Telegram inline approval buttons). |
| [0020](rfcs/0020-calendar-cross-domain-overlay-read-exception.md) | Calendar Cross-Domain Overlay Read Exception | **Proposed.** Tests the calendar overlays/prep-rail/briefing design against RFC 0010's reuse criteria: the naive per-open, on-demand, LLM-synthesis read FAILS criteria #2 (deterministic/no LLM) and #3 (batch/not real-time). Recommends the RFC-0010-compliant path — scheduled deterministic precompute into a read-only cached view, zero LLM at render — or dropping synthesis entirely. Owner acceptance pending. |
| [0021](rfcs/0021-decision-loop-one-tap-approvals-and-decision-memory.md) | Decision Loop: One-Tap Approvals and Decision Memory | **Proposed.** Parked-action push with one-tap Telegram approve/reject (signed callback tokens, owner-channel verification, deterministic routing), structured decision dossier (blast radius, reversibility, typed evidence), deterministic decision-fact writeback to butler memory, and safety-critical-args fingerprint generalization. Human-confirmed ratchet preserved; does NOT un-park RFC 0019's automation engine. |
| [0022](rfcs/0022-cross-process-event-transport.md) | Cross-Process Event Transport (NOTIFY/LISTEN Fleet Event Bridge) | Daemon session/spend/notification/approval events, accepted Switchboard ingests, and committed connector filtered-event batches publish via Postgres `pg_notify` from their owning DB pools; a dashboard-api-side `LISTEN` bridge re-publishes them onto the existing in-process fleet event bus (`WS /api/events/stream`) unchanged. Fixes cross-process live events being silently discarded at the container boundary (bu-01r64) - additive transport; does not replace the in-process bus's existing same-process producers/consumers. |
| [0023](rfcs/0023-durable-approval-delivery-intent-recovery.md) | Durable Approval Delivery Intent Recovery | **Proposed.** Atomically couples every pending action to one schema-local delivery intent, recovers notification with fenced presentation generations and provider-handoff ambiguity truth, preserves dashboard defer re-presentation plus RFC 0021 quiet-hours/cohort-burst policy, and never lets recovery mutate a parked domain action. |
| [0024](rfcs/0024-messenger-private-email-correspondence-ledger.md) | Messenger-Private Email Correspondence Ledger | **Proposed — planning only.** Defines a privacy-minimized Messenger-owned outbound evidence ledger and bounded, aggregate-only Relationship enrichment path; exact provider-Sent proof, same-account/peer inbound correlation, and 180-day coverage are required before a bidirectional result. |
| [0025](rfcs/0025-tracker-host-beads-projection-exporter.md) | Tracker-Host Beads Projection Exporter | **Draft.** A deterministic tracker-host exporter publishes a minimal active Beads projection to PostgreSQL; bounded runtime readers receive atomic snapshots, fixed freshness/retention, and explicit JSONL rollback without tracker access. |
| [0026](rfcs/0026-commitment-lifecycle.md) | Evidence-Backed Commitment Lifecycle | **Draft.** Extends the owner-condition ledger with explicit resolution, commitment metadata, closure receipts, escalation, and owner-confirmed lifecycle evidence. |
| [0027](rfcs/0027-runtime-tool-surface-discovery.md) | Runtime Tool Surface Discovery and Exposure | **Accepted.** Adds a runtime-neutral LLM tool-list projection, verified native deferred discovery, eager cross-CLI fallback, content-blind receipts, and measured rollout gates without changing MCP handler authority. |
| [0028](rfcs/0028-home-physical-actuation-contract.md) | Home Physical Actuation Contract | **Accepted.** Defines the fail-closed HA risk map, approval boundary, per-attempt receipt, live post-condition proof, rollback hints, and minimized domain event for LLM-to-physical-world actions. |
| [0029](rfcs/0029-expected-signals-and-honest-absence.md) | Expected Signals and Honest Absence | **Accepted.** Defines the shared present/absent/unmeasurable ledger, producer-liveness join, producer-owned upserts, and degraded rendering contract. |

## Related

- [ideas-ledger.md](ideas-ledger.md) — parked ideas from the JARVIS pursuit dossiers, each with why it was parked and its unpark condition.

## Conventions

- **Status values:** Draft, Accepted, Deprecated.
- **Normative language:** "MUST", "SHOULD", "MAY" follow their usual meaning.
- **Cross-references:** By RFC number (e.g., "see RFC 0003").
- **Date:** ISO 8601 format.
