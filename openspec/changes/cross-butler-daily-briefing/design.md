## Context

The General butler's `eod-tomorrow-prep` schedule (cron `0 15 * * *`) currently spawns one LLM session to fetch tomorrow's calendar events and send a Telegram summary. Specialist butlers (Health, Finance, Relationship, Travel, Education, Home) each maintain rich domain data in their own PostgreSQL schemas, but none of this surfaces in the daily briefing.

The butler architecture enforces schema isolation: each butler can only access its own schema plus `shared`. Inter-butler communication is MCP-only through the Switchboard. A naive fan-out (General asking each specialist via Switchboard) would cost 7+ LLM sessions per day for what is fundamentally a deterministic data extraction.

The existing scheduler supports `dispatch_mode="job"` for zero-LLM deterministic Python functions, registered in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` in `daemon.py`. This is the foundation for the contribution pattern.

## Goals / Non-Goals

**Goals:**
- Upgrade the daily briefing from calendar-only to a cross-butler synthesis covering health, finance, relationships, travel, education, and home
- Maintain zero additional LLM cost (1 session/day, same as today)
- Graceful degradation: if any specialist contribution is missing, the briefing still works
- Mobile-friendly output under 500 words

**Non-Goals:**
- Real-time cross-butler queries (this is a daily batch pattern)
- Bi-directional data sync between butlers
- User-configurable briefing sections (future enhancement)
- Push notifications for individual specialist alerts (separate concern)

## Decisions

### D1: Job-based contributions over Switchboard fan-out

**Decision:** Each specialist butler runs a `daily_briefing_contribution` deterministic job that writes to its own state store. No Switchboard calls, no LLM sessions.

**Alternatives considered:**
- *Switchboard fan-out*: General sends MCP requests to each specialist. Each spawns an LLM session to answer. Cost: 7 LLM sessions/day. Rejected: excessive cost for deterministic queries.
- *Shared database table*: All butlers write to a `shared.briefing_contributions` table. Rejected: introduces write coupling to shared schema, harder to version per-butler.
- *Event-based pub/sub*: Specialists publish events, General subscribes. Rejected: over-engineered for a daily batch pattern, no event bus exists.

**Rationale:** Jobs are zero-cost, already proven (memory_consolidation, compute_analytics_snapshots), and keep data ownership within each butler's schema.

### D2: State store for contribution storage

**Decision:** Each specialist writes its contribution as structured JSON to its state store under key `briefing/daily/<YYYY-MM-DD>`. The state store already supports JSONB get/set with versioning.

**Alternatives considered:**
- *Dedicated table*: Create a `briefing_contributions` table per schema. Rejected: unnecessary schema migration for ephemeral daily data.
- *File-based*: Write to filesystem. Rejected: not available in container environments, no versioning.

**Rationale:** State store is the path of least resistance -- it exists, it's JSONB, and stale entries can be cleaned up via TTL or key-prefix deletion.

### D3: Cross-schema read via SQL view for aggregation

**Decision:** Create a `general.v_briefing_contributions` SQL view that unions `SELECT '<butler>' AS butler, key, value FROM <schema>.state WHERE key LIKE 'briefing/daily/%'` across all specialist schemas. Each UNION term includes an explicit `butler` string literal for auditability. The aggregation job queries this view and validates the `butler` column matches the JSON payload's `butler` field.

**Alternatives considered:**
- *Direct cross-schema queries in Python*: Hardcode schema names in the job. Rejected: fragile, not auditable.
- *Database role with cross-schema SELECT*: Grant a `briefing_reader` role SELECT on each specialist's `state` table. Viable but heavier to manage than a view.
- *Switchboard MCP calls*: General calls each specialist's `state_get` tool. Rejected: requires LLM sessions.

**Rationale:** A SQL view is declarative, auditable, and can be locked down to read-only. Adding a new specialist means adding one line to the view. The view lives in General's schema so it doesn't pollute specialist schemas.

### D4: Scheduling sequence with 5-minute contribution window

**Decision:** Specialist contribution jobs run at cron `55 6 * * *` (06:55 UTC = 14:55 SGT). General's aggregation job runs at `58 6 * * *` (06:58 UTC = 14:58 SGT). The existing EOD prompt fires at `0 7 * * *` (07:00 UTC = 15:00 SGT). This gives a 3-minute window for contributions and a 2-minute window for aggregation.

**Alternatives considered:**
- *Tighter window (1 min)*: Risk of race conditions if a job runs slowly.
- *Wider window (15 min)*: Unnecessary; jobs are simple SQL queries completing in <1s.

**Rationale:** 5-minute total window is conservative enough for reliability while keeping briefing data fresh.

### D5: Contribution schema with `has_updates` flag

**Decision:** Each contribution is a JSON object with a standard envelope:
```json
{
  "butler": "health",
  "date": "2026-03-25",
  "has_updates": true,
  "highlights": [
    {"category": "medication", "text": "Missed evening dose of Vitamin D", "priority": "high"}
  ],
  "summary": "Missed 1 dose today. Next appointment: Apr 2."
}
```

Sections with `has_updates: false` are omitted from the briefing entirely. The `summary` field is pre-rendered text the LLM can use directly, while `highlights` provides structured data for cross-domain correlation.

**Rationale:** Pre-rendering summaries in the deterministic job means the LLM session just needs to assemble, not analyze. The structured `highlights` array enables future cross-domain flags (e.g., "you have a flight tomorrow but a doctor's appointment conflicts").

## Schema Isolation Exception (Rule 3)

The cross-schema SQL view is a sanctioned exception to RFC 0006 schema isolation and the Rule 3 principle that inter-butler communication is MCP-only through the Switchboard. This exception is justified because the alternative (Switchboard fan-out) would cost 7 LLM sessions/day for what is a deterministic read-only data extraction.

**Guardrails constraining this exception:**

- **Read-only SQL view:** The `general.v_briefing_contributions` view is a UNION of SELECT statements. PostgreSQL does not permit INSERT/UPDATE/DELETE on UNION views, making write access structurally impossible.
- **Explicit butler source column:** Each UNION term includes a string literal `butler` column (e.g., `SELECT 'health' AS butler, ...`), providing tamper-resistant provenance that cannot be spoofed by the JSON payload. The aggregation job validates `value::jsonb->>'butler'` matches this source column to detect malformed contributions.
- **Date-filtered queries only:** The view filters rows to `key LIKE 'briefing/daily/%'` and the aggregation job further filters to today's date (SGT), preventing access to arbitrary state data.
- **Health check validates view accessibility:** The aggregation job validates that the view is queryable before processing, catching grant revocations or schema changes early.
- **Migration-based grants (auditable):** Cross-schema SELECT grants are created via Alembic migration, tracked in version control, and reversible on downgrade.

This exception does NOT set a general precedent for cross-schema access. Each future case must be evaluated independently.

## Risks / Trade-offs

- **[Cross-schema coupling]** The SQL view creates a read dependency from General to all specialist schemas. If a specialist's `state` table schema changes, the view may break. **Mitigation:** The view only reads `key` and `value` columns which are stable (core state store contract). Add a health check to the aggregation job that validates the view is queryable.

- **[Stale contributions]** If a specialist butler is down, its contribution job doesn't run, and the aggregation picks up yesterday's data (or nothing). **Mitigation:** The aggregation job filters contributions by today's date only. Missing contributions result in omitted sections, not stale data.

- **[Clock skew]** If system clocks drift, the 3-minute contribution window could be insufficient. **Mitigation:** All cron evaluation uses the same system clock (UTC). Docker containers share the host clock. The window is generous for sub-second jobs.

- **[State store bloat]** Daily contributions accumulate keys. **Mitigation:** Each specialist's contribution job deletes entries older than 7 days as part of its run. Cost: one extra DELETE query per day per butler.

## Migration Plan

1. **Phase 1 (DB):** Create Alembic migration adding the `general.v_briefing_contributions` SQL view with cross-schema SELECT grants
2. **Phase 2 (Jobs):** Register contribution jobs in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` for each specialist + aggregation job for General
3. **Phase 3 (Config):** Add `[[butler.schedule]]` entries to each specialist's `butler.toml` + General's aggregation schedule entry
4. **Phase 4 (Prompt):** Update General's `eod-tomorrow-prep` prompt to read `briefing/combined/<today>` and render the multi-domain format
5. **Rollback:** Remove schedule entries from TOML files. The view and jobs are inert without schedules.

## Open Questions

- Should the briefing contribution window be configurable per deployment, or is hardcoded UTC scheduling sufficient? (Leaning: hardcoded is fine for single-user deployment.)
- Should the combined briefing payload include raw highlights for future dashboard rendering, or just the pre-rendered summaries? (Leaning: include both -- highlights for dashboard, summaries for Telegram.)
