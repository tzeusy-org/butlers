# Required Elements Per Diagram Category

Load this when drafting a child bead's description (Phase 3 of `SKILL.md`) —
it lists what each diagram category must show so the bead description gives
the worker an exhaustive spec.

**01 — System topology:**
- All butlers with port numbers
- All connectors as external processes
- Data flow arrows: ingress, egress, persistence
- PostgreSQL with schema isolation
- LLM runtimes as ephemeral subprocesses
- Dashboard gateway
- Color legend (butlers=blue, connectors=green, DB=orange,
  LLM=purple, dashboard=teal, external channels=gray)

**02 — Butler specification:**
- Two-layer design (core ring + modules ring)
- MCP SSE transport + tool registration
- Ephemeral LLM spawning sequence
- DB schema + Alembic migrations
- Config directory tree (roster/{name}/)
- Module interface methods
- Core tools enumeration

**03x — Fixed butler designs:**
- Butler-specific MCP tools and endpoints
- Ingestion/routing/dispatch flows (Switchboard)
- Key user flow as numbered sequence diagram with swim lanes
- Scheduled job sidebar

**04x — Rostered butler user flows:**
- Enabled modules listed
- Inbound user interaction flow (Telegram → Switchboard → Butler)
- Each scheduled task as a flow
- Primary user flow as numbered sequence
- Data model callout (schema tables)

**05 — Connector design:**
- Connector as standalone process (NOT a butler)
- Transport-only lifecycle loop
- ingest.v1 envelope exploded view (source, event, sender, payload, control)
- Deduplication decision tree (3 tiers)
- Crash-safe checkpoints, rate limiting, heartbeat
- All implemented connectors as examples

**06x — Core deep-dives:**
- Component internals with data structures
- Sequence diagrams for key operations
- Error/edge cases where relevant
- Cross-references to other components

**07x — Dashboard:**
- FastAPI gateway + middleware stack
- All core and butler-specific routers listed
- Router discovery mechanism
- Key data flows as sequences (session viewer, memory browser,
  approval workflow, cost tracking, calendar workspace, SSE streaming)
