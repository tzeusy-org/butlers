## Why

The Butlers project has reached a functional alpha state with substantial implementation across core infrastructure, modules, connectors, dashboard, and butler roles. However, specifications have lived exclusively in `docs/` markdown files and code — never synced to OpenSpec. This creates a gap: there is no single source of truth that captures the system's current behavior in a structured, diffable, capability-oriented format.

This change creates a comprehensive baseline spec set ("Alpha Release MVP") by analyzing both the existing documentation and the implemented code to produce a logically coherent superset. Where docs describe target-state behavior and code has diverged, the spec captures what actually exists today plus what is documented but not yet implemented (clearly marked as target-state).

## What Changes

This is an **ADDED-only baseline** — no modifications or removals, since there are no existing main specs.

Every capability that exists in code or normative docs is captured as an OpenSpec delta spec under `specs/<capability>/spec.md` with `## ADDED Requirements` sections.

## Capabilities

### New Capabilities

**Core Infrastructure:**
- `core-daemon`: Butler daemon lifecycle — config loading, startup phases, shutdown, health status
- `core-state`: KV JSONB state store — get/set/delete/list operations with per-butler schema isolation
- `core-scheduler`: Cron-based task scheduling — dispatch modes, staggering, TOML sync, calendar projection
- `core-spawner`: LLM CLI spawner — multi-runtime (Claude Code, Codex, Gemini), ephemeral config gen, session serialization
- `core-sessions`: Session lifecycle — creation, completion, audit fields, trigger source tracking, cost/token capture
- `core-modules`: Module system — ABC contract, dependency resolution, tool registration, migration chains
- `core-credentials`: Credential store — Google OAuth, secret management, environment variable contracts
- `core-skills`: Butler skills — loading from roster config, skill execution in runtime
- `core-telemetry`: Observability — OpenTelemetry tracing, structured logging, metrics
- `core-notify`: Outbound notification contract — notify.v1 envelope, Switchboard routing to messenger

**Connectors:**
- `connector-base-spec`: Shared connector interface contract — ingest.v1 envelope schema, deduplication strategy, CachedMCPClient transport, heartbeat protocol, liveness/eligibility, statistics rollups, rate limiting, Prometheus metrics, horizontal scaling
- `connector-telegram-bot`: Butler's Telegram bot connector — user-facing chat interface, polling/webhook modes, tiered text extraction, lifecycle reactions, credential resolution, health states
- `connector-telegram-user-client`: User's personal Telegram connector — readonly contextualization via Telethon MTProto, live-stream ingestion, bounded backfill, privacy/consent safeguards
- `connector-gmail`: Gmail inbox connector — OAuth DB-first, polling/Pub/Sub modes, label filtering, 3-tier ingestion policy, policy tier assignment, triage rules, attachment policy, backfill mode
- `connector-discord`: Draft v2-only Discord user-account connector — passive ingestion for contextualization, not production-ready

**Modules:**
- `module-approvals`: Approval gating — gate wrapper, pending actions, standing rules, executor, risk tiers, redaction, audit events
- `module-calendar`: Calendar module — unified calendar view, scheduled task projection, RRULE events, butler event CRUD
- `module-contacts`: Contacts module — Google sync, shared schema, contact_info, backfill, entity linkage
- `module-email`: Email module — Gmail integration, inbox search, send/reply tools
- `module-mailbox`: Mailbox module — message storage, ingestion tracking
- `module-memory`: Memory subsystem — storage (episodes/facts/rules), search (hybrid/semantic/keyword), embedding, consolidation, entities, entity events, MCP tools
- `module-telegram`: Telegram module — bot/user client tools, approval integration
- `module-pipeline`: Message pipeline — routing prompt construction, identity resolution, UUIDv7 message IDs

**Dashboard:**
- `dashboard-shell`: Application shell — layout, navigation, sidebar, route map, OKLCH design system, shadcn/ui component inventory, keyboard shortcuts, command palette, auto-refresh tiers, responsive breakpoints
- `dashboard-visibility`: Visibility and traceability — end-to-end trace story, session drill-down, trace waterfall, unified timeline, topology graph, connector heartbeat monitoring, notification audit trail
- `dashboard-butler-management`: Butler management — butler detail pages (10+ tabs), schedule/state CRUD, MCP tool debug, switchboard-specific views (registry, routing log, triage, backfill)
- `dashboard-admin-gateway`: Administrative gateway — secrets management (16 templates), Google OAuth bootstrap with CSRF, approval decisions, connector fleet management, frontend-gated operations
- `dashboard-domain-pages`: Domain feature pages — health (Recharts charting), contacts (label hash coloring), calendar (dual-view), memory (tier health ratios), costs (period selectors), search, issues aggregation
- `dashboard-api`: Backend API and data layer — FastAPI app factory, 80+ endpoints across 18 domain groups, auto-discovery, DatabaseManager, cross-butler fan-out, TanStack Query patterns, SSE streaming, OAuth flow, pricing engine

**Butler Roles:**
- `butler-base-spec`: Shared roster conventions — directory structure, butler.toml schema, MANIFESTO/CLAUDE.md contracts, shared skills, port assignment
- `butler-switchboard`: Full switchboard contract — routing, decomposition, registry, lifecycle states, ingestion, backfill, error taxonomy, SLOs, butler configuration, scheduled tasks, skills
- `butler-general`: General role — catch-all assistant, collection/entity tools, data-organizer skill
- `butler-relationship`: Relationship role — personal CRM, 40+ tools, entity resolution pipeline, gift/reconnect skills
- `butler-health`: Health role — measurements, medications, conditions, symptoms, meals, trend interpretation
- `butler-messenger`: Messenger role — delivery execution plane, channel ownership, no schedules/skills
- `butler-finance`: Finance role — transactions, subscriptions, bills, NUMERIC(14,2) amounts
- `butler-travel`: Travel role — trip container model, booking/itinerary tools, pre-trip/planner skills

**Testing:**
- `testing`: Test infrastructure — pytest configuration, e2e test plans (security, state, contracts, observability, approvals, resilience, flows, scheduling, performance, infrastructure)

## Impact

**Code:** No code changes — this is a spec-only baseline capture.

**Schema:** No schema changes.

**Dependencies:** None.
