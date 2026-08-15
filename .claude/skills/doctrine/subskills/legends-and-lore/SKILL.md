---
name: legends-and-lore
description: >
  Load design contracts (RFCs) to contextualize implementation work. The about/legends-and-lore/
  directory contains numbered RFCs defining wire-level contracts, data models, state machines,
  and integration points. Consult relevant RFCs before implementing features, modifying
  protocols, designing state machines, or resolving cross-subsystem integration questions.
  Selectively load ONLY the RFCs relevant to your current task.
---

# Legends and Lore -- Design Contracts

The `about/legends-and-lore/` directory is the HOW pillar of the Butlers knowledge architecture. Each RFC defines a technical contract at the wire, protocol, or API level. Together they describe how the system works.

## Five-Pillar Model

| Pillar | Directory | Answers |
|--------|-----------|---------|
| Doctrine | `about/heart-and-soul/` | WHY -- vision, principles, scope |
| Engineering Standards | `about/craft-and-care/` | WHO WE ARE WHEN WE BUILD -- engineering character in practice: implementation quality, verification, review, operability, maintainability |
| **Design Contracts** | `about/legends-and-lore/` | HOW -- RFCs defining wire-level contracts |
| Capability Specs | `openspec/` | WHAT -- normative requirements |
| Topology | `about/lay-and-land/` | WHERE -- component maps, data flow, deployment |

## RFC Index

| RFC | File | Read when... |
|-----|------|-------------|
| 0001 | `about/legends-and-lore/rfcs/0001-daemon-lifecycle-and-triggers.md` | Touching daemon startup, shutdown, trigger dispatch, session lifecycle, or spawner concurrency |
| 0002 | `about/legends-and-lore/rfcs/0002-mcp-tool-surface-and-modules.md` | Working on MCP tool registration, module loading, module ABC, skills, or ephemeral MCP config |
| 0003 | `about/legends-and-lore/rfcs/0003-switchboard-routing-and-ingestion.md` | Modifying ingestion, triage, classification, thread affinity, or route dispatch |
| 0004 | `about/legends-and-lore/rfcs/0004-identity-and-contact-resolution.md` | Changing contacts, contact_info, identity resolution, or unknown sender handling |
| 0005 | `about/legends-and-lore/rfcs/0005-observability-and-telemetry.md` | Adding or modifying OTel tracing, metrics, cardinality rules, or export pipeline |
| 0006 | `about/legends-and-lore/rfcs/0006-database-schema-and-isolation.md` | Working on schema isolation, Alembic migrations, credential store, or per-butler schema contents |
| 0007 | `about/legends-and-lore/rfcs/0007-dashboard-and-api-surface.md` | Building or modifying dashboard API routes, frontend tabs, or auto-discovery |
| 0008 | `about/legends-and-lore/rfcs/0008-deployment-network-security.md` | Changing network isolation, egress rules, container environment, or port binding |
| 0009 | `about/legends-and-lore/rfcs/0009-situational-context-bus.md` | Working on shared user_context signals, context preamble, or cross-butler context queries |
| 0010 | `about/legends-and-lore/rfcs/0010-cross-butler-briefing-exception.md` | Implementing cross-schema read exceptions or daily briefing aggregation |
| 0011 | `about/legends-and-lore/rfcs/0011-proactive-insight-delivery.md` | Working on insight generation, Switchboard brokering, anti-spam budgets, or notify delivery |
| 0012 | `about/legends-and-lore/rfcs/0012-finance-transaction-data-model.md` | Touching finance transactions, deduplication, materialized summaries, or SPO migration |
| 0013 | `about/legends-and-lore/rfcs/0013-dunbar-group-aware-interaction-scoring.md` | Working on interaction scoring, group-size dilution, direction weighting, or participant gating |

Consult `about/legends-and-lore/README.md` for the canonical reading order (follows data flow from startup through request handling).

## Do Not Use This Skill For

- Project purpose or scope arguments: use `heart-and-soul`
- Feature behavior and acceptance scenarios: use `spec-and-spine`
- Test scope, verification bar, or documentation hygiene: use `craft-and-care`
- Code ownership or placement questions: use `lay-and-land`

## When to Load

- Implementing features that touch daemon lifecycle, startup, or shutdown
- Working on MCP tool registration, module loading, or the module ABC
- Modifying ingestion, routing, or classification logic
- Changing the contact/identity model or resolution flow
- Adding or modifying telemetry, tracing, or metrics
- Working on database schema, migrations, or the credential store
- Building or modifying dashboard API routes or frontend
- Changing network isolation, deployment, or container configuration
- Working on cross-butler context, insight delivery, or interaction scoring

## How to Use

1. Identify which subsystem your work touches.
2. Load the specific RFC(s) for that subsystem -- not all thirteen.
3. Pay attention to normative language: MUST, SHOULD, MAY carry their usual weight.
4. Cross-reference by RFC number when contracts span subsystems (e.g., RFC 0003 references RFC 0004 for identity resolution during ingestion).
