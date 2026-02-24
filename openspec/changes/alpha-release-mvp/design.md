## Design Decisions

### 1. Baseline-Only (ADDED) Delta

This change is an ADDED-only baseline — every spec describes current system state. No MODIFIED or REMOVED sections exist because there are no prior main specs (`openspec/specs/` is empty). Once synced via `/opsx:sync`, these become the canonical main specs.

### 2. Capability Decomposition

The system is decomposed into 39 capability specs across 6 domains:

**Core Infrastructure (10 specs):**
- `core-daemon` — Daemon lifecycle, config loading, 17-phase startup, graceful shutdown
- `core-state` — KV JSONB state store with CAS operations
- `core-scheduler` — Cron scheduling, dual dispatch modes (prompt/job), staggering, TOML sync, calendar projection, remind tool
- `core-spawner` — Multi-runtime LLM CLI spawner (Claude Code, Codex, Gemini), ephemeral MCP config, session serialization
- `core-sessions` — Session lifecycle, audit fields, trigger source tracking, token/cost capture
- `core-modules` — Module ABC, topological dependency resolution, tool registration, migration chains
- `core-credentials` — Credential store, Google OAuth, environment variable contracts
- `core-skills` — Skill loading from roster, SKILL.md format, skill execution
- `core-telemetry` — OpenTelemetry tracing, structured logging, metrics
- `core-notify` — notify.v1 envelope, delivery intents (send/reply/react), Switchboard routing

**Switchboard & Connectors (6 specs):**
- `switchboard` — Full routing control plane: ingestion, LLM-driven decomposition, registry, lifecycle states, error taxonomy, backfill, DurableBuffer, priority queuing
- `connectors` — Shared interface contract: ingest.v1 envelope schema, deduplication strategy, CachedMCPClient transport, heartbeat protocol, liveness/eligibility, statistics rollups, rate limiting, Prometheus metrics, horizontal scaling
- `connector-telegram-bot` — Butler's Telegram bot: user-facing chat interface, polling/webhook modes, 4-tier text extraction, lifecycle reactions (eyes/checkmark/alien), error handling (429/409/backoff), credential resolution, health states
- `connector-telegram-user-client` — User's personal Telegram: readonly contextualization via Telethon MTProto, live-stream ingestion, bounded backfill, privacy/consent safeguards, explicit separation from bot connector
- `connector-gmail` — Gmail inbox: OAuth DB-first, polling/Pub/Sub modes, label filtering (LabelFilterPolicy), 3-tier ingestion policy, PolicyTierAssigner, triage rules, ATTACHMENT_POLICY, backfill mode (job model, rate limiting, cost tracking)
- `connector-discord` — Draft v2-only Discord user-account ingestion for passive contextualization (not production-ready)

**Modules (8 specs):**
- `module-approvals` — Gate wrapper, pending actions, standing rules, risk tiers, shared executor, immutable audit events, redaction, retention
- `module-calendar` — Unified calendar view, event CRUD, RRULE/cron, Google Calendar integration
- `module-contacts` — Google sync, shared schema, contact_info, backfill, entity linkage
- `module-email` — Gmail, IMAP/SMTP tools
- `module-mailbox` — Message storage, ingestion tracking, status lifecycle
- `module-memory` — 3 memory types, hybrid search, embedding, consolidation, entities, 17 MCP tools
- `module-telegram` — Bot/user tools, lifecycle reactions
- `module-pipeline` — Message pipeline, routing prompt construction, identity resolution

**Dashboard (6 specs):**
- `dashboard-shell` — Application shell, OKLCH design system, shadcn/ui inventory, route map, keyboard shortcuts, command palette, auto-refresh tiers
- `dashboard-visibility` — End-to-end trace story, session drill-down, trace waterfall, unified timeline, topology graph, heartbeat monitoring
- `dashboard-butler-management` — Butler detail pages (10+ tabs), schedule/state CRUD, MCP debug, switchboard-specific views
- `dashboard-admin-gateway` — Secrets management, Google OAuth bootstrap, approval decisions, connector fleet, frontend-gated operations
- `dashboard-domain-pages` — Health charts, contacts, calendar dual-view, memory tier health, costs, search, issues aggregation
- `dashboard-api` — FastAPI factory, 80+ endpoints, auto-discovery, DatabaseManager, fan-out, TanStack Query, SSE, OAuth, pricing

**Butler Roles (8 specs):**
- `butler-roles` — Shared roster conventions, directory structure, butler.toml schema, port assignment
- `butler-switchboard` — Classification rules, decomposition, ingestion buffer, 6 scheduled jobs, 2 skills
- `butler-general` — Catch-all assistant, collection/entity tools, data-organizer skill
- `butler-relationship` — Personal CRM, 40+ tools, entity resolution pipeline, gift/reconnect skills
- `butler-health` — Measurements, medications, compound JSONB, trend-interpreter skill
- `butler-messenger` — Delivery execution plane, channel ownership, no schedules/skills
- `butler-finance` — Transactions, subscriptions, bills, NUMERIC(14,2), bill/spending skills
- `butler-travel` — Trip container model, status transitions, pre-trip/planner skills

**Testing (1 spec):**
- `testing` — pytest infrastructure, testcontainer harness, E2E declarative scenarios, 10 E2E domains

### 3. Code-First, Docs-Informed

Each spec was generated by reading both source code and `docs/` markdown. Where they diverge:
- **Code behavior takes precedence** for implemented requirements
- **Docs-only requirements** are marked with `[TARGET-STATE]` prefix
- This ensures the spec set is a truthful representation of current state while preserving design intent

### 4. Spec Sizing Rationale

Larger specs (memory: 980 lines, switchboard: 653, dashboard-domain-pages: 636, dashboard-shell: 602, dashboard-api: 601) reflect domains with high implementation density. The dashboard alone spans 3,310 lines across 6 specs, reflecting its role as the primary administrative gateway and observability surface. Connectors span 6 specs: one shared interface contract plus per-connector profiles (Telegram bot, Telegram user client, Gmail, Discord) that capture transport-specific behavior. Core infrastructure specs are more compact (79-143 lines) because each covers a focused, well-bounded component.

## Alternatives Considered

### One Spec Per File vs Grouped Specs
Considered grouping all core specs into one file, but individual files enable independent sync and easier review. 39 files is manageable and matches the system's natural capability boundaries.

### Omitting TARGET-STATE Items
Considered capturing only implemented behavior. Kept target-state items (marked clearly) because they represent documented design decisions that inform future work and prevent re-discovery.
