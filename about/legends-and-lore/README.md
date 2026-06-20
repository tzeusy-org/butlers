# Legends and Lore -- Design Contracts

This directory contains the normative design contracts for the Butlers system. Each RFC defines a technical contract at the wire, protocol, or API level. Together they describe HOW the system works.

## Reading Order

For a new reader, the recommended order follows data flow from startup through request handling:

1. **RFC 0001** -- Daemon startup, trigger dispatch, and session lifecycle
2. **RFC 0002** -- MCP tool surface, module system, and skills infrastructure
3. **RFC 0003** -- Switchboard ingestion, triage, classification, and routing
4. **RFC 0004** -- Identity resolution and contact model
5. **RFC 0005** -- Observability, tracing, and metrics
6. **RFC 0006** -- Database schema isolation and migration machinery
7. **RFC 0007** -- Dashboard API and frontend architecture

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
| [0020](rfcs/0020-calendar-cross-domain-overlay-read-exception.md) | Calendar Cross-Domain Overlay Read Exception | **Proposed.** Tests the calendar overlays/prep-rail/briefing design against RFC 0010's reuse criteria: the naive per-open, on-demand, LLM-synthesis read FAILS criteria #2 (deterministic/no LLM) and #3 (batch/not real-time). Recommends the RFC-0010-compliant path — scheduled deterministic precompute into a read-only cached view, zero LLM at render — or dropping synthesis entirely. Owner acceptance pending. |

## Conventions

- **Status values:** Draft, Accepted, Deprecated.
- **Normative language:** "MUST", "SHOULD", "MAY" follow their usual meaning.
- **Cross-references:** By RFC number (e.g., "see RFC 0003").
- **Date:** ISO 8601 format.
