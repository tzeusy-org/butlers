---
name: law-and-lore
description: >
  Load design contracts (RFCs) to contextualize implementation work. The about/law-and-lore/
  directory contains numbered RFCs defining wire-level contracts, data models, state machines,
  and integration points. Consult relevant RFCs before implementing features, modifying
  protocols, designing state machines, or resolving cross-subsystem integration questions.
  Selectively load ONLY the RFCs relevant to your current task.
---

# Law and Lore -- Design Contracts

The `about/law-and-lore/` directory is the HOW pillar of the Butlers knowledge architecture. Each RFC defines a technical contract at the wire, protocol, or API level. Together they describe how the system works.

## Four-Pillar Model

| Pillar | Directory | Answers |
|--------|-----------|---------|
| Doctrine | `about/heart-and-soul/` | WHY -- vision, principles, scope |
| **Design Contracts** | `about/law-and-lore/` | HOW -- RFCs defining wire-level contracts |
| Capability Specs | `openspec/` | WHAT -- normative requirements |
| Topology | `about/lay-and-land/` | WHERE -- component maps, data flow, deployment |

## RFC Index

| RFC | File | Status | Summary |
|-----|------|--------|---------|
| 0001 | `about/law-and-lore/rfcs/0001-daemon-lifecycle-and-triggers.md` | EXISTS | Multi-phase startup, dual trigger sources (external MCP + internal cron), spawner concurrency model, session lifecycle, request context propagation |
| 0002 | `about/law-and-lore/rfcs/0002-mcp-tool-surface-and-modules.md` | EXISTS | FastMCP SSE server, core tool catalog, module ABC and topological resolution, tool call logging proxy, skills infrastructure, ephemeral MCP config generation |
| 0003 | `about/law-and-lore/rfcs/0003-switchboard-routing-and-ingestion.md` | EXISTS | ingest.v1 envelope format, pre-classification triage, thread affinity, LLM classification fallback, route.execute dispatch, route inbox crash recovery, email priority queuing |
| 0004 | `about/law-and-lore/rfcs/0004-identity-and-contact-resolution.md` | EXISTS | Three-table identity schema (contacts, contact_info, entities) in `public`, resolve_contact_by_channel() contract, unknown sender handling, identity preamble format, tenant model |
| 0005 | `about/law-and-lore/rfcs/0005-observability-and-telemetry.md` | EXISTS | OTel setup, OTLP export pipeline, trace propagation across process boundaries, tool_span instrumentation, metrics catalog, cardinality discipline |
| 0006 | `about/law-and-lore/rfcs/0006-database-schema-and-isolation.md` | EXISTS | Single-PG multi-schema model, shared identity tables, per-butler schema contents, multi-chain Alembic migrations, credential store design |
| 0007 | `about/law-and-lore/rfcs/0007-dashboard-and-api-surface.md` | EXISTS | FastAPI + Vite architecture, auto-discovered butler routes, route map, backend API contract, tab structures, data access patterns, command palette |

Consult `about/law-and-lore/README.md` for the canonical reading order (follows data flow from startup through request handling).

## Key Contracts

The most load-bearing design decisions defined by these RFCs:

- **Daemon startup is multi-phase** (RFC 0001): DB connect, run migrations, register modules (topological sort), start MCP server, begin scheduler. Order matters.
- **Two trigger sources** (RFC 0001): External MCP calls and internal cron ticks. Both flow through the same spawn path.
- **ingest.v1 envelope** (RFC 0003): The canonical format for all external events entering the system. Connectors produce it, Switchboard consumes it.
- **Thread affinity** (RFC 0003): Replies to an existing thread route to the same butler that handled the original message, bypassing classification.
- **resolve_contact_by_channel()** (RFC 0004): The single entry point for identity resolution. Maps (channel_type, channel_value) to a contact record.
- **Per-butler schemas with shared identity** (RFC 0006): Each butler gets its own PostgreSQL schema. The `public` schema holds contacts, contact_info. Schema isolation is the security boundary.
- **Auto-discovered dashboard routes** (RFC 0007): Butler API routes in `roster/*/api/router.py` are discovered and mounted automatically.

## When to Load

- Implementing features that touch daemon lifecycle, startup, or shutdown
- Working on MCP tool registration, module loading, or the module ABC
- Modifying ingestion, routing, or classification logic
- Changing the contact/identity model or resolution flow
- Adding or modifying telemetry, tracing, or metrics
- Working on database schema, migrations, or the credential store
- Building or modifying dashboard API routes or frontend

## How to Use

1. Identify which subsystem your work touches.
2. Load the specific RFC(s) for that subsystem -- not all seven.
3. Pay attention to normative language: MUST, SHOULD, MAY carry their usual weight.
4. Cross-reference by RFC number when contracts span subsystems (e.g., RFC 0003 references RFC 0004 for identity resolution during ingestion).
