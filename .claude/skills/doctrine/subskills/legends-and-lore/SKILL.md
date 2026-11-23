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
| 0014 | `about/legends-and-lore/rfcs/0014-chronicler-time-butler.md` | Working on Chronicler point events, episodes, source projection adapters, or correction overlays |
| 0015 | `about/legends-and-lore/rfcs/0015-qa-staffer-discovery-investigation-pipeline.md` | Changing the QA staffer patrol loop, discovery sources, triage, investigation dispatch, or anonymized fix PRs |
| 0016 | `about/legends-and-lore/rfcs/0016-s3-blob-storage-contract.md` | Storing or fetching binary artifacts through the S3-compatible blob store |
| 0017 | `about/legends-and-lore/rfcs/0017-owner-routing-safety-incident-reconciliation.md` | Changing owner lookup, owner-channel authorization, or owner-directed egress |
| 0018 | `about/legends-and-lore/rfcs/0018-connector-scope-and-deferral-rationale.md` | Proposing, scoping, or deferring a connector |
| 0019 | `about/legends-and-lore/rfcs/0019-proactive-egress-and-automation-parked.md` | Considering calendar auto-responses or an event-driven automation rule engine (rejected / parked) |
| 0020 | `about/legends-and-lore/rfcs/0020-calendar-cross-domain-overlay-read-exception.md` | Building calendar cross-domain overlays or another deterministic precomputed read exception |
| 0021 | `about/legends-and-lore/rfcs/0021-decision-loop-one-tap-approvals-and-decision-memory.md` | Working on one-tap approvals, signed callbacks, decision dossiers, or decision-memory writeback |
| 0022 | `about/legends-and-lore/rfcs/0022-cross-process-event-transport.md` | Touching the NOTIFY/LISTEN fleet event bridge or cross-process event delivery |
| 0023 | `about/legends-and-lore/rfcs/0023-durable-approval-delivery-intent-recovery.md` | Working on durable approval delivery intents or fenced Messenger recovery |
| 0024 | `about/legends-and-lore/rfcs/0024-messenger-private-email-correspondence-ledger.md` | Working on Messenger outbound email evidence or aggregate Relationship correspondence enrichment |
| 0025 | `about/legends-and-lore/rfcs/0025-tracker-host-beads-projection-exporter.md` | Revisiting a tracker/beads product bridge (withdrawn) |
| 0026 | `about/legends-and-lore/rfcs/0026-commitment-lifecycle.md` | Working on commitment resolution, closure receipts, escalation, or lifecycle evidence |
| 0027 | `about/legends-and-lore/rfcs/0027-runtime-tool-surface-discovery.md` | Changing runtime Tool Search, deferred schema loading, or tool exposure policy |
| 0028 | `about/legends-and-lore/rfcs/0028-home-physical-actuation-contract.md` | Touching Home Assistant actuation, its risk map, attempt receipts, or post-condition proof |
| 0029 | `about/legends-and-lore/rfcs/0029-expected-signals-and-honest-absence.md` | Working on expected signals, producer liveness, or honest-absence rendering |
| 0030 | `about/legends-and-lore/rfcs/0030-system-plane-read-exception.md` | Working on Concierge system-plane reads or column-allowlisted read-only views |
| 0031 | `about/legends-and-lore/rfcs/0031-public-entity-graph-projection.md` | Working on the public entity graph, edge writers, sensitivity stubs, or traversal |
| 0032 | `about/legends-and-lore/rfcs/0032-fleet-case-file.md` | Working on the fleet case file, its evidence, or honest read outages |
| 0033 | `about/legends-and-lore/rfcs/0033-fleet-cost-claims.md` | Working on sibling-asserted money claims or Finance cost reconciliation |
| 0034 | `about/legends-and-lore/rfcs/0034-messenger-voice-egress.md` | Working on Messenger voice delivery, presence, DND suppression, or text fallback |
| 0035 | `about/legends-and-lore/rfcs/0035-bounded-post-delivery-approval-reminders.md` | Working on reminders after an approval request is delivered |
| 0036 | `about/legends-and-lore/rfcs/0036-models-exact-path-vision-proof.md` | Working on models exact-path vision proof or its diagnostic controls |
| 0037 | `about/legends-and-lore/rfcs/0037-general-capture-and-private-source-boundaries.md` | Working on General capture admission, receipts, private-safe search, or private-source fences |
| 0038 | `about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md` | Working on goal admission, lead and child sessions, capability grants, broker-signed calls, or the classify-and-route cutover |
| 0039 | `about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md` | Working on the public MCP read surface: gateway assertions, the projection database and its manifest, public tools, or `propose_fact` |

Consult `about/legends-and-lore/README.md` for the canonical reading order (follows data flow from startup through request handling) and for each RFC's status. A new RFC adds a row to both that index and this table.

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
- Working on approvals, goal admission and capability grants, or a named read exception
- Exposing Butlers data outside the system (the public MCP read surface)

## How to Use

1. Identify which subsystem your work touches.
2. Load the specific RFC(s) for that subsystem -- never the whole set.
3. Pay attention to normative language: MUST, SHOULD, MAY carry their usual weight.
4. Cross-reference by RFC number when contracts span subsystems (e.g., RFC 0003 references RFC 0004 for identity resolution during ingestion).
