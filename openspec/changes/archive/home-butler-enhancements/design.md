## Context

The Home butler currently runs three monitoring scheduled tasks — `device-health-check`, `environment-report`, and `weekly-energy-digest` — all using `dispatch_mode="prompt"`. Each fires a full LLM session that calls HA tools, interprets results, formats a message, and sends via `notify()`. The logic in these sessions is formulaic: poll entities, compare against thresholds or stored preferences, build a report, notify. This is the same pattern already used by `memory_consolidation` and other butlers' deterministic jobs, which run as Python functions with no LLM involvement.

The Home butler also lacks proactive maintenance scheduling — there is no mechanism to track filter replacement intervals, seasonal HVAC service dates, or appliance warranty expirations and remind the owner when items are due.

The dashboard currently exposes entity state, areas, command logs, and statistics, but has no dedicated device inventory view, energy consumption time-series, or maintenance calendar.

## Goals / Non-Goals

**Goals:**
- Convert `device-health-check`, `environment-report`, and `weekly-energy-digest` from prompt-based to job-based dispatch to eliminate LLM costs for deterministic monitoring work.
- Maintain identical user-facing output (same Telegram notification format and content) so the change is invisible to the owner.
- Add a `maintenance-schedule-check` deterministic job for proactive home maintenance reminders.
- Add dashboard API endpoints for device inventory, energy charts, and maintenance calendar.
- Keep the existing skill SKILL.md files valid for interactive (user-triggered) invocations — only the scheduled execution path changes.

**Non-Goals:**
- Refactoring the HA module's WebSocket/REST transport layer.
- Adding new HA MCP tools — the job handlers read from the connector-populated entity cache and only call the HA REST API directly for historical statistics, not through MCP.
- Scene optimization or LLM-assisted troubleshooting — these remain prompt-based interactive skills.
- Multi-home support or mobile app integration.
- Changing memory module internals — jobs use the existing `store_fact` and `recall` storage APIs.

## Decisions

### D1: Job handlers consume connector-populated entity cache, not direct HA REST API

**Decision:** Deterministic job handlers read current entity state from the `ha_entity_snapshot` table (populated by the HA connector's ingestion pipeline) rather than making independent `GET /api/states` calls to the HA REST API. Jobs fall back to direct HA REST API calls only for queries the connector does not provide — specifically `recorder/get_statistics_during_period` for historical energy data and any area-registry lookups not yet in the snapshot.

**Rationale:** The HA connector (see `connector-home-assistant` change) already maintains a real-time event stream and periodically refreshes the `ha_entity_snapshot` table. Having jobs read from this shared cache avoids redundant polling of the HA instance, reduces coupling to the HA REST API surface, and keeps the connector as the single source of truth for entity state. This aligns with Rule 7's spirit: connectors own ingestion, jobs consume ingested data.

**Alternative considered:** (a) Jobs call `GET /api/states` directly (original design). Rejected because it duplicates the connector's work and creates two independent polling paths to the same HA instance. (b) Having jobs call MCP tools programmatically. Rejected because MCP tools enforce string I/O and add unnecessary indirection for in-process code.

### D1b: Monitoring thresholds are configurable, not hardcoded

**Decision:** All monitoring thresholds used by deterministic job handlers — battery severity levels, temperature/humidity deviation boundaries, energy anomaly percentages, offline duration cutoffs — are stored as configurable values in the state store (key namespace `home:thresholds:*`) rather than hardcoded in Python. Default values are seeded on first run via an Alembic migration. Users can adjust thresholds via the dashboard settings API or conversational commands (e.g., "set battery critical threshold to 15%").

**Rationale:** Hardcoded thresholds make the system inflexible — a user with devices that regularly report 12% battery would get constant false-positive critical alerts with no way to tune the sensitivity without code changes. Storing thresholds as configurable state store values lets users personalize monitoring via the dashboard or conversation. The state store's KV JSONB model is a natural fit for typed configuration values.

**Default threshold values (seeded on first run):**
- `home:thresholds:battery` — `{"critical": 10, "warning": 20, "info": 30}`
- `home:thresholds:offline_hours` — `{"critical": 24, "warning": 1}`
- `home:thresholds:comfort_defaults` — `{"temp_min_f": 68, "temp_max_f": 76, "humidity_min": 30, "humidity_max": 60, "co2_max_ppm": 1000}`
- `home:thresholds:comfort_deviation` — `{"minor_temp_f": 2, "moderate_temp_f": 5, "minor_humidity": 10, "moderate_humidity": 20, "critical_temp_low_f": 60, "critical_temp_high_f": 85, "critical_co2_ppm": 1500, "critical_humidity_low": 15, "critical_humidity_high": 80}`
- `home:thresholds:energy` — `{"anomaly_pct": 20, "high_severity_pct": 100}`

**Alternative considered:** Using memory facts with `predicate="monitoring_preference"`. Rejected because thresholds are structured configuration (numeric ranges) not fuzzy knowledge — the state store's typed KV model is more appropriate and avoids the need to parse free-text fact content into numbers at runtime.

### D2: Job handlers live in `src/butlers/jobs/home.py`

**Decision:** All home job handlers are defined in a new `src/butlers/jobs/home.py` module, following the pattern of `_MEMORY_MAINTENANCE_JOB_HANDLERS` but scoped to the home domain.

**Rationale:** Centralizes home-specific job logic in one module. The daemon's `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY["home"]` dict imports and registers these handlers. This keeps `daemon.py` lean and home logic encapsulated.

**Alternative considered:** Defining handlers in the HA module `__init__.py`. Rejected because job handlers need access to cross-module services (memory storage, notify) and should not be coupled to the module's transport layer.

### D3: Shared HA client accessor for job context

**Decision:** Job handlers receive the `asyncpg.Pool` (standard job handler signature) and access the HA module's REST client and entity cache via a well-defined accessor. The daemon passes a lightweight `HomeJobContext` dataclass containing the HA base URL, auth token, and a fresh `httpx.AsyncClient`.

**Rationale:** Jobs need HTTP access to HA for historical statistics queries (D1: `recorder/get_statistics_during_period`) that the connector does not ingest. For current entity state, jobs read from the connector-populated `ha_entity_snapshot` table via the pool — no REST call needed. The `HomeJobContext` HTTP client is only used for the REST-only fallback queries. Rather than importing the module's singleton client (which couples jobs to module lifecycle), each job invocation gets a short-lived client.

**Alternative considered:** Passing the live HA module instance. Rejected because job handlers should be testable in isolation without spinning up WebSocket connections.

### D4: Maintenance items stored in `home.maintenance_items` table

**Decision:** Recurring maintenance items are stored in a new `home.maintenance_items` table with columns: `id` (UUID PK), `name` (text, unique), `category` (text — `filter`, `hvac`, `appliance`, `general`), `interval_days` (int), `last_completed_at` (timestamptz, nullable), `next_due_at` (timestamptz, computed), `notes` (text, nullable), `created_at` (timestamptz), `updated_at` (timestamptz).

**Rationale:** Maintenance scheduling is inherently tabular — items have fixed intervals, completion dates, and due dates. A dedicated table is simpler and more queryable than encoding this in the SPO memory system, which is designed for fuzzy facts, not structured scheduling.

**Alternative considered:** Using memory facts with `predicate="maintenance_schedule"`. Rejected because computing "next due" requires arithmetic on `last_completed_at + interval_days`, which is awkward to express as fact content and would require parsing free-text to determine due dates.

### D5: Notify via the shared notify helper, not MCP

**Decision:** Job handlers send notifications by calling the `notify` Python API directly (the same function backing the MCP `notify` tool), using `intent="send"` and `channel="telegram"`.

**Rationale:** The notify helper is a Python function that can be called directly from job handlers. No LLM session is needed to format and send a Telegram message.

### D6: Dashboard endpoints query DB tables directly

**Decision:** New dashboard API endpoints query `ha_entity_snapshot`, `ha_command_log`, energy statistics (via HA REST proxy), and `maintenance_items` directly through asyncpg, following the existing pattern in `roster/home/api/router.py`.

**Rationale:** Consistent with existing dashboard architecture. The `ha_entity_snapshot` table provides the device inventory, energy data is fetched by proxying to HA's statistics API, and `maintenance_items` is a simple table query.

## Risks / Trade-offs

**[Risk] Job handlers bypass LLM reasoning for anomaly interpretation** → Mitigation: The deterministic jobs implement explicit threshold-based classification (configurable via state store — see D1b) which covers the existing skill workflows exactly. Default thresholds are seeded on first run and can be tuned by the user via dashboard or conversation. For cases requiring nuanced interpretation (e.g., "water heater usage seems unusual"), the weekly energy digest job flags anomalies numerically (above the configurable `anomaly_pct` threshold, default 20%) rather than asking an LLM to interpret. Users who want deeper analysis can trigger the interactive `energy` or `troubleshooting` skills.

**[Risk] HA REST API changes could break job handlers silently** → Mitigation: Job handlers include explicit response validation and log warnings on unexpected response shapes. The existing MCP tools face the same risk. Integration tests against a mock HA server validate expected response structures.

**[Risk] Maintenance items table adds schema migration complexity** → Mitigation: Single-table migration with no foreign keys. Rollback is `DROP TABLE home.maintenance_items`. Initial seed data is empty; owners populate items via dashboard or interactive commands.

**[Risk] Entity cache staleness for job handlers using `ha_entity_snapshot`** → Mitigation: With the HA connector active, the snapshot table receives real-time updates from the WebSocket event stream (sub-second latency) in addition to the periodic full refresh every `snapshot_interval_seconds` (default 300s). Jobs run at most once daily, so even the periodic refresh provides sufficient freshness. If the connector is down, the last snapshot is still usable for daily monitoring purposes.

## Migration Plan

1. **Add Alembic migration** for `home.maintenance_items` table and `home:thresholds:*` state store seed values (default monitoring thresholds per D1b).
2. **Add `src/butlers/jobs/home.py`** with the four job handler functions. Job handlers read entity state from `ha_entity_snapshot` (connector-populated) and load thresholds from state store at invocation time.
3. **Register handlers** in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY["home"]` in `daemon.py`.
4. **Update `roster/home/butler.toml`** to change three schedule entries from `dispatch_mode="prompt"` to `dispatch_mode="job"` and add the `maintenance-schedule-check` entry.
5. **Add dashboard endpoints** in `roster/home/api/router.py` and models in `models.py`, including threshold management endpoints.
6. **Update `openspec/specs/butler-home/spec.md`** with modified schedule requirements.
7. **Rollback:** Revert `butler.toml` schedule entries to `dispatch_mode="prompt"` to restore LLM-based execution. Drop `maintenance_items` table. Remove seeded threshold state store keys. Remove job handlers and dashboard endpoints.

## Open Questions

- Should maintenance items be seeded with common defaults (e.g., "HVAC filter replacement every 90 days") or start empty and rely on user/LLM population? Leaning toward empty with a first-run prompt asking the owner to configure items.
- ~~Should the energy digest job support configurable thresholds for anomaly detection (e.g., "flag devices >X% above baseline"), or are the hardcoded 20%/2x thresholds sufficient for v1?~~ **Resolved:** All thresholds (including energy anomaly detection) are configurable via state store values, with defaults seeded on first run (D1b).
