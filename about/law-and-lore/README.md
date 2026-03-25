# Law and Lore -- Design Contracts

This directory contains the normative design contracts for the Butlers framework. Each RFC defines a technical contract at the wire, protocol, or API level. Together they describe HOW the system works.

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
| [0004](rfcs/0004-identity-and-contact-resolution.md) | Identity and Contact Resolution | Three-table shared schema, resolve_contact_by_channel() contract, unknown sender handling, identity preamble format, tenant model. |
| [0005](rfcs/0005-observability-and-telemetry.md) | Observability and Telemetry | OTel setup, OTLP export pipeline, trace propagation across process boundaries, tool_span instrumentation, metrics catalog, cardinality discipline. |
| [0006](rfcs/0006-database-schema-and-isolation.md) | Database Schema and Isolation | Single-PG multi-schema model, shared identity tables, per-butler schema contents, multi-chain Alembic migrations, credential store. |
| [0007](rfcs/0007-dashboard-and-api-surface.md) | Dashboard and API Surface | FastAPI + Vite architecture, auto-discovered butler routes, route map, backend API contract, tab structures, data access patterns, command palette. |

## Conventions

- **Status values:** Draft, Accepted, Deprecated.
- **Normative language:** "MUST", "SHOULD", "MAY" follow their usual meaning.
- **Cross-references:** By RFC number (e.g., "see RFC 0003").
- **Date:** ISO 8601 format.
