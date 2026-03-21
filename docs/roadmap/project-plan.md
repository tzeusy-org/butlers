# Project Plan

> **Purpose:** Summarize the development milestones and current status of the Butlers project.
> **Audience:** Contributors, stakeholders, anyone tracking project progress.
> **Prerequisites:** None.

## Overview

Butlers follows a milestone-driven development plan. The project has grown from initial prototyping to a functional alpha with 10 butler roles, 8+ modules, 6+ connectors, a React dashboard, E2E benchmarking, and an OpenSpec planning system. Development is guided by `PROJECT_PLAN.md` (core infrastructure) and `MEMORY_PROJECT_PLAN.md` (tiered memory subsystem).

## Current State

The project has reached a functional alpha state with substantial implementation across all layers:

### Core Infrastructure (Implemented)
- **Butler daemon lifecycle:** Config loading, startup phases, shutdown, health status.
- **State store:** KV JSONB operations (get/set/delete/list) with per-butler schema isolation.
- **Scheduler:** Cron-based task dispatch with staggering, TOML sync, and calendar projection.
- **Spawner:** Multi-runtime LLM CLI spawner (Claude Code, Codex, OpenCode/Gemini), ephemeral MCP config generation, session serialization.
- **Session management:** Creation, completion, audit fields, trigger source tracking, cost/token capture.
- **Module system:** Abstract base class contract, dependency resolution via topological sort, Alembic migration chains.
- **Credential store:** DB-first resolution with environment variable fallback, Google OAuth integration.
- **Telemetry:** OpenTelemetry tracing, structured logging, Prometheus metrics, Grafana dashboards.

### Connectors (Implemented)
- **Telegram bot connector:** Polling/webhook modes, tiered text extraction, lifecycle reactions.
- **Telegram user client:** Readonly Telethon MTProto, live-stream ingestion, bounded backfill.
- **Gmail connector:** OAuth DB-first, polling with history-based sync, label filtering, triage rules.
- **Discord connector:** Draft v2-only, passive ingestion for contextualization.

### Modules (Implemented)
- **Approvals:** Gate wrapper, pending actions, standing rules, risk tiers, redaction, audit events.
- **Calendar:** Unified view, scheduled task projection, RRULE events, CRUD tools.
- **Contacts:** Google sync, shared schema, contact_info, entity linkage.
- **Email:** Gmail integration, inbox search, send/reply tools.
- **Mailbox:** Message storage, ingestion tracking.
- **Memory:** Tiered storage (episodes/facts/rules), hybrid search, embedding, consolidation, entity graph.
- **Telegram:** Bot/user client tools, approval integration.
- **Pipeline:** Routing prompt construction, identity resolution, UUIDv7 message IDs.

### Dashboard (Implemented)
- React frontend with OKLCH design system and shadcn/ui components.
- 80+ API endpoints across 18 domain groups.
- Auto-discovered butler routers from roster.
- SSE streaming for live updates.
- Butler detail pages with 10+ tabs.
- Switchboard-specific views (registry, routing log, triage, backfill).

### Butler Roles (10 in Roster)
- **Switchboard:** Message routing, decomposition, registry, lifecycle management.
- **General:** Catch-all assistant.
- **Relationship:** Personal CRM, 40+ tools, entity resolution.
- **Health:** Medications, measurements, conditions, symptoms, meals.
- **Finance:** Transactions, subscriptions, bills.
- **Messenger:** Delivery execution plane.
- **Education:** Learning and study assistant.
- **Travel:** Trip planning, bookings, itineraries.
- **Home:** Home automation integration (aspirational).

## Memory Subsystem Plan

The tiered memory subsystem follows a separate plan (`MEMORY_PROJECT_PLAN.md`):

- **Eden tier:** Hot storage for recent episodes and facts.
- **Mid-Term tier:** Consolidated knowledge promoted from Eden via LRU.
- **Long-Term tier:** Permanent knowledge base with semantic search.
- **Promotion/Eviction:** LRU-based movement between tiers with consolidation.

## Active Development Areas

Current focus areas based on recent OpenSpec changes:

- **Adapter integration test suites:** Standardized testing for connector adapters.
- **Memory residual gaps:** Closing remaining memory subsystem implementation gaps.
- **CRUD-to-SPO migration:** Migrating entity storage from CRUD to subject-predicate-object triples.
- **Predicate registry enforcement:** Enforcing a controlled vocabulary for entity predicates.
- **Documentation information architecture:** Reorganizing docs for contributor-friendly navigation (this documentation set is part of that effort).

## Related Pages

- [OpenSpec Overview](openspec-overview.md) -- How specifications drive development
- [Testing Strategy](../testing/testing-strategy.md) -- Quality gates and test pyramid
