## Context

The butler framework currently consists of five butler daemons (Switchboard, General, Relationship, Health, Heartbeat), each running as a FastMCP SSE server with a dedicated PostgreSQL database. The only visibility into the system is through CLI commands, direct database queries, and LGTM stack traces. There is no unified administrative interface.

The existing codebase provides the data we need: `sessions` table logs every runtime invocation, `scheduled_tasks` table tracks cron-driven tasks, `state` table holds KV data, and each domain butler has rich entity tables (contacts, measurements, collections, etc.). Butler status is available via the MCP `status()` tool. OpenTelemetry spans are exported to the LGTM stack (Grafana Tempo).

The dashboard introduces two new codebases — a FastAPI backend (`src/butlers/api/`) and a React SPA (`frontend/`) — plus framework-level changes to support token tracking and cross-butler notifications.

## Goals / Non-Goals

**Goals:**

- Provide read-only visibility into all butler data (sessions, schedules, state, domain entities) from a single interface
- Enable write operations (trigger, schedule CRUD, state CRUD) routed through MCP tools (never direct DB writes)
- Surface system health issues (unreachable butlers, failing tasks, module errors, cost anomalies)
- Track token usage and estimate costs across all butlers
- Provide a simplified trace viewer for cross-butler request flows
- Enable outbound notifications from any butler through a core `notify()` framework tool

**Non-Goals:**

- Real-time streaming of runtime session output (polling/SSE for status updates is sufficient for v1)
- Authentication/authorization (personal system, localhost-only for v1)
- Replacing Grafana Tempo UI for deep trace analysis (the dashboard provides a simplified view)
- Direct database writes from the dashboard API (all mutations go through MCP)
- Mobile-first design (responsive sidebar collapse is sufficient, not a native-quality mobile UI)
- Memory system views (blocked on memory plan finalization — deferred to M11)

## Decisions

### D1: Dual Data Access Pattern — MCP Client + Direct DB Reads

**Decision:** The dashboard API uses two access patterns: MCP client calls for live operations and direct asyncpg reads for data browsing.

**Rationale:** MCP calls are necessary for operations that require the butler daemon (status checks, triggering CC, schedule writes). But paginated data browsing (session history, contact lists, measurement charts) through MCP tools would be inefficient — MCP tools return small payloads by design and lack pagination/filtering. Direct DB reads are pragmatic for an admin tool.

**Alternatives considered:**
- MCP-only: Would require adding pagination/filtering to every MCP tool. Overly constrained for an admin dashboard.
- DB-only: Can't check live status, trigger CC, or perform writes without the daemon.

**Implementation:** `src/butlers/api/deps.py` manages both an MCP client pool (one per butler) and a DB pool manager (one asyncpg pool per butler DB). Routers choose the appropriate pattern per endpoint.

### D2: Multi-DB Connection Manager

**Decision:** A `DatabaseManager` class in `src/butlers/api/db.py` maintains one asyncpg connection pool per butler database, discovered from butler config directories at startup.

**Rationale:** Each butler owns a separate PostgreSQL database (hard architectural constraint). The dashboard needs to query across all of them for cross-butler views (sessions, timeline, search). A managed pool-per-DB avoids connection churn and enables concurrent fan-out queries.

**Alternatives considered:**
- Single connection with `SET search_path`: Doesn't work — these are separate databases, not schemas.
- SQLAlchemy async: Adds unnecessary ORM overhead for read-only queries. The existing codebase uses raw asyncpg for runtime queries and SQLAlchemy/Alembic only for migrations.

**Implementation:**
```python
class DatabaseManager:
    pools: dict[str, asyncpg.Pool]  # butler_name → pool

    async def startup(self, butler_configs: list[ButlerConfig]):
        for config in butler_configs:
            self.pools[config.name] = await asyncpg.create_pool(config.database_url)

    def pool(self, butler_name: str) -> asyncpg.Pool:
        return self.pools[butler_name]

    async def fan_out(self, query, butler_names=None) -> dict[str, list[Record]]:
        """Execute same query across multiple butler DBs concurrently."""
        targets = butler_names or list(self.pools.keys())
        results = await asyncio.gather(*(
            self.pools[name].fetch(query) for name in targets
        ))
        return dict(zip(targets, results))
```

### D3: Butler Discovery from Config Directories

**Decision:** The dashboard discovers butlers by reading `butler.toml` files from the configured butlers directory (same as the existing config loader), not by querying the Switchboard.

**Rationale:** Config-based discovery doesn't require a running Switchboard. The dashboard should be able to show butler configs even when daemons are down. The existing `ButlerConfig` dataclass in `src/butlers/config.py` already parses `butler.toml`.

**Alternatives considered:**
- Switchboard `list_butlers()`: Requires Switchboard to be running. Fails ungracefully when it's not.
- Hardcoded list: Too brittle.

**Implementation:** Reuse `load_config()` from `src/butlers/config.py` to discover butler configs. MCP client connections are attempted lazily — if a butler is unreachable, its status shows as "down" but its config and DB data remain accessible.

### D4: Cost Estimation — Derived, Not Stored

**Decision:** Cost is computed at query time from `input_tokens × input_price + output_tokens × output_price`. Per-model pricing lives in a TOML config file loaded by the dashboard API.

**Rationale:** Pricing changes frequently and shouldn't require a migration. Storing cost in the DB would mean historical costs become wrong when pricing changes (or require complex versioned pricing). Computing at query time with current prices is simpler and always up-to-date.

**Alternatives considered:**
- Store cost at session creation: Wrong when pricing changes. Requires spawner to know pricing.
- Store pricing history in DB: Over-engineered for a personal system.

**Implementation:** A `pricing.toml` file maps model IDs to input/output per-token prices. The dashboard API loads this at startup. Cost endpoints compute estimates using token counts from the sessions table.

### D5: Core `notify()` Tool — Synchronous Through Switchboard

**Decision:** Add a core `notify(channel, message, recipient?)` tool available to every butler's CC. The butler daemon holds an MCP client to the Switchboard and forwards notifications via a new `deliver()` tool. Notifications log to a `notifications` table in the Switchboard DB.

**Rationale:** runtime instances are locked down to their own butler's MCP server. They can't send Telegram messages directly — only the Switchboard has those modules. The `notify()` tool provides a clean, synchronous interface: CC calls `notify()`, it blocks until delivery succeeds or fails, CC can handle errors.

**Alternatives considered:**
- Store notifications in butler's own state for later pickup: No delivery mechanism; requires polling.
- Direct inter-butler MCP calls from CC: Breaks the locked-down MCP config constraint.
- Async queue: Adds complexity. Synchronous is simpler and CC can handle errors in-line.

**Implementation:**
1. `src/butlers/core/notify.py` — registers `notify()` tool on every butler daemon
2. Butler daemon startup opens an MCP client connection to the Switchboard (from config)
3. `notify()` calls `self.switchboard_client.call_tool("deliver", ...)`, returns result to CC
4. Switchboard's `deliver()` tool dispatches to the appropriate module (telegram/email) and logs to `notifications` table
5. If Switchboard is unreachable, `notify()` returns an error — CC decides fallback (e.g., store in state)

### D6: Token Tracking — Spawner Captures from SDK Response

**Decision:** Add `input_tokens`, `output_tokens`, `model`, `trace_id`, and `parent_session_id` columns to the `sessions` table. The LLM CLI spawner captures token usage from the Claude Agent SDK response and stores it alongside the session record.

**Rationale:** Token counts are the raw data for cost estimation, usage trends, and anomaly detection. The CC SDK response includes usage data. Storing at session creation time is the natural point.

**Alternatives considered:**
- Parse from session transcripts: Fragile, SDK-version-dependent.
- Instrument at MCP tool level: Doesn't capture the CC orchestration tokens, only tool call overhead.

**Implementation:** Alembic migration adds columns to `sessions`. Update `spawner.py` to extract `usage.input_tokens`, `usage.output_tokens`, and `model` from the SDK response and pass to `sessions.log_session()`.

### D7: Trace Reconstruction from Session Records

**Decision:** The dashboard reconstructs traces from `trace_id` and `parent_session_id` columns on session records, plus routing log entries from the Switchboard. No separate span storage.

**Rationale:** Full OpenTelemetry span data goes to Grafana Tempo for deep analysis. The dashboard provides a simplified view by reconstructing the session-level call graph from existing data. This avoids duplicating Tempo's storage while covering the common case (which butler triggered which, how long each step took).

**Alternatives considered:**
- Query Tempo API: Adds a runtime dependency on Grafana Tempo. Tempo's API is complex.
- Store spans in PostgreSQL: Duplicates Tempo. High write volume for detailed spans.

**Implementation:** `GET /api/traces/:trace_id` queries sessions across all butler DBs where `trace_id` matches, then assembles a tree using `parent_session_id`. Routing log entries from the Switchboard fill in the routing hops.

### D8: Unified Timeline — Fan-out Aggregation

**Decision:** The timeline aggregates events by querying multiple tables across multiple butler DBs at request time, with cursor-based pagination.

**Rationale:** Events live in their natural tables (sessions, routing_log, scheduled_tasks, notifications). A separate events table would require dual-writing and add schema maintenance. Fan-out queries are acceptable given the small number of butlers (5 in v1).

**Alternatives considered:**
- Dedicated `events` table in each butler DB: Requires framework changes to dual-write every event. Higher maintenance.
- Materialized view: PostgreSQL can't materialize across databases.

**Trade-off:** Fan-out across 5 DBs is fine. If butler count grows significantly, a lightweight event aggregation table on the dashboard API side could cache results.

### D9: Frontend Architecture — Page-Router with TanStack Query

**Decision:** React Router for page navigation, TanStack Query for all server state management. No global client state store (Redux, Zustand). Component state via React hooks.

**Rationale:** TanStack Query handles caching, refetching, optimistic updates, and loading/error states. The dashboard is almost entirely server-derived data — there's negligible client-only state beyond UI preferences (sidebar collapsed, dark mode, dismissed issues). Adding a global state store would be over-engineering.

**Alternatives considered:**
- Redux/Zustand + fetch: Unnecessary boilerplate for a server-state-heavy app.
- Next.js/Remix: SSR adds complexity. This is a localhost admin tool, not a public-facing app.

### D10: Production Deployment — Static Files from FastAPI

**Decision:** In production, `vite build` produces static files served by FastAPI's `StaticFiles` mount. Single container, single port.

**Rationale:** A personal system doesn't need a CDN or separate frontend deployment. Serving static files from the API process simplifies deployment to a single Docker container and avoids CORS configuration in production.

**Alternatives considered:**
- Nginx reverse proxy: Over-engineered for a single-user system.
- Separate frontend container: Extra container for no benefit.

**Implementation:** `butlers dashboard` CLI command starts uvicorn with the FastAPI app. In production, the app mounts `frontend/dist/` at `/` as a StaticFiles fallback. In development, the Vite dev server runs separately with API proxy.

### D11: Write Operations — MCP-Only, No Direct DB Writes

**Decision:** All dashboard write operations (trigger, schedule CRUD, state set/delete) go through MCP tool calls to the butler daemon. The dashboard API never writes to butler databases directly.

**Rationale:** The butler daemon is the authority for its own data. Direct writes would bypass validation, hooks, and the daemon's internal state. MCP tools already implement the correct write semantics.

**Alternatives considered:**
- Direct SQL writes for simple operations (state_set): Bypasses daemon. Could cause consistency issues if the daemon caches state.

### D12: API Structure — Router-Per-Domain

**Decision:** FastAPI routers organized by domain: `butlers.py`, `sessions.py`, `schedules.py`, `state.py`, `traces.py`, `timeline.py`, `costs.py`, `notifications.py`, `search.py`, `issues.py`, plus butler-specific routers (`relationship.py`, `health.py`, `general.py`, `switchboard.py`).

**Rationale:** Each router maps to a page or capability in the frontend. Butler-specific routers hardcode schema knowledge for their domain (relationship contacts, health measurements, etc.). Adding a new butler type requires adding a new router.

**Alternatives considered:**
- Generic entity API: Would lose type safety and domain-specific query patterns.
- GraphQL: Adds complexity. REST is simpler for a known, stable API surface.

## Risks / Trade-offs

**[Fan-out query performance]** → Cross-butler views (timeline, search, sessions) query N databases concurrently. With 5 butlers this is fine. If butler count grows to 20+, consider a lightweight event aggregation cache on the dashboard API side.

**[MCP client reliability]** → Live status checks and write operations depend on butler daemons being reachable. → Mitigation: graceful degradation. Status shows "down" for unreachable butlers. DB data remains browseable. Write operations return clear errors.

**[Token tracking dependency]** → Cost and trace features require the `input_tokens`, `output_tokens`, `model`, and `trace_id` columns on sessions. These need the Alembic migration and spawner update before dashboard cost views work. → Mitigation: Cost views show "no data" until migration runs. Build the API/UI regardless.

**[Notify framework change scope]** → The `notify()` core tool is a framework-level change affecting every butler daemon, not just the dashboard. → Mitigation: Implement as a separate milestone (can be before or in parallel with dashboard work). Dashboard notifications view simply reads from the `notifications` table — it works as soon as the table exists, even if no notifications have been sent yet.

**[Butler-specific routers are hardcoded]** → Adding a new butler type (e.g., "finance") requires a new API router and frontend page. → Mitigation: Acceptable for a personal system with a small, known set of butlers. The General butler's freeform entities cover ad-hoc use cases.

**[No authentication for v1]** → The dashboard is accessible to anyone on the network. → Mitigation: localhost-only access. Add API key auth in a future milestone if network exposure is needed.

**[Search performance]** → Fan-out full-text search across N butler DBs could be slow for large datasets. → Mitigation: PostgreSQL `ILIKE` with `gin_trgm_ops` indexes is fast enough for personal-scale data. If needed later, add a lightweight search index (pg_trgm or SQLite FTS) on the dashboard API side.

## Migration Plan

**Database migrations** (Alembic):
1. Core migration: Add `input_tokens`, `output_tokens`, `model`, `trace_id`, `parent_session_id` to `sessions` table (runs on all butler DBs)
2. Switchboard migration: Add `notifications` table

**Deployment:**
1. Run Alembic migrations against all butler databases
2. Update spawner code to capture token usage (backward-compatible — new columns are nullable)
3. Deploy dashboard API (new service, no existing service disrupted)
4. Build and serve frontend (either as static files from API or separate dev server)

**Rollback:** Dashboard API and frontend are additive — removing them has no impact on butler daemons. The database migrations add nullable columns and a new table, both backward-compatible.

### D13: Module Health Checks — Status from MCP `status()` Response

**Decision:** Module health is determined from the MCP `status()` tool response. The existing `status()` tool already returns module information. Its response is extended to include per-module health indicators with values: `connected` (module is operational), `degraded` (module is partially functional), `error` (module has failed), or `unknown` (status could not be determined).

**Rationale:** Avoids adding a new `health_check()` method to the Module ABC. Leverages the existing status mechanism that the dashboard already polls for the topology view. Each module's `on_startup()` and runtime state naturally feed into its health — the `status()` tool simply exposes this.

**Alternatives considered:**
- (a) Module ABC `health_check()` method — requires framework changes to every module implementation. Adds a new lifecycle concern.
- (b) External probing by dashboard API — the dashboard shouldn't know module internals (connection strings, health check protocols). Violates the boundary between dashboard and butler.

**Implementation:** The `status()` MCP tool response gains a `modules` field: `{"modules": {"telegram": {"status": "connected"}, "email": {"status": "error", "error": "SMTP connection refused"}}}`. Each module's status is derived from its internal state (e.g., whether its client connection is alive).

### D14: Dual Cost Anomaly Thresholds

**Decision:** Two thresholds for different scopes:
- **Session-level (3x):** Individual sessions are flagged when their estimated cost exceeds 3x the butler's 7-day average session cost. Used in session tables and the cost page for highlighting expensive individual sessions.
- **Butler-level (2x):** A butler is flagged as a system issue when its aggregate daily spend exceeds 2x its 7-day daily average. Used in the Issues panel on the Overview page to surface systemic cost concerns.

**Rationale:** The project plan specified both thresholds for different contexts. Butler-level is coarser (daily aggregate) and more sensitive (2x) because it represents a systemic concern — consistent overspend. Session-level is finer-grained and tolerates more variance (3x) because individual sessions naturally have high cost variance (e.g., a one-time complex task vs. routine checks).

**Alternatives considered:**
- Single threshold for both: Would either be too noisy at session level (2x) or miss systemic issues at butler level (3x).
- Configurable thresholds: Over-engineering for v1. Hardcoded values can be adjusted later.

## Open Questions

| Question | Status |
|----------|--------|
| Does the Claude Agent SDK expose `input_tokens` and `output_tokens` in its response? | Need to verify SDK response shape. If not available, fall back to parsing session transcripts or instrument at MCP level. |
| How should the Switchboard connection be configured for `notify()`? | Each butler's `butler.toml` could have a `switchboard_url` field, or it could be derived from the Switchboard's butler.toml port. Config-based is simpler. |
| Should the dashboard poll for status updates or use SSE? | Start with polling (TanStack Query's refetchInterval). Add SSE in M12 for real-time status indicators. |
| Pricing config format and location? | Propose `pricing.toml` in the butlers config root, loaded by the dashboard API. Simple key-value: `model-id = {input = 0.000003, output = 0.000015}`. |
| How does the dashboard detect active runtime sessions? | Propose: spawner writes session record with `completed_at = NULL` before spawning, updates on completion. Dashboard queries for `completed_at IS NULL`. |
