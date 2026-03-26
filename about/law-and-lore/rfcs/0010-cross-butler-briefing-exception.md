# RFC 0010: Cross-Butler Briefing Exception

**Status:** Accepted
**Date:** 2026-03-25

## Summary

This RFC documents a sanctioned exception to the MCP-only inter-butler communication principle (Rule 3). The General butler's daily briefing aggregation job reads specialist butlers' state stores via a read-only SQL view (`general.v_briefing_contributions`) that unions briefing contribution entries across six specialist schemas. The exception is justified by an 8:1 LLM session cost ratio: the compliant alternative (Switchboard fan-out) would require 8 LLM sessions per day for what is a deterministic, zero-reasoning data extraction. Five guardrails constrain scope creep. This RFC defines when this pattern MAY be reused and when it MUST NOT.

## Motivation

The General butler produces a daily end-of-day briefing (cron `0 7 * * *` UTC). Today it covers only calendar events. Specialist butlers -- Health, Finance, Relationship, Travel, Education, Home -- each maintain domain-specific data that would make the briefing significantly more useful: upcoming bills, missed medication doses, birthdays, departures, learning streaks, device alerts.

The architecturally compliant approach is Switchboard fan-out: General sends an MCP request to each specialist butler via the Switchboard, each specialist spawns an LLM session to formulate its response, and General spawns a final session to synthesize the results. This costs 1 (General request) + 6 (specialist responses) + 1 (General synthesis) = 8 LLM sessions per day.

But every specialist's contribution is a deterministic SQL query against its own domain tables. No LLM reasoning is required to extract "bills due in 48 hours" or "missed medication doses today." The data extraction is pure infrastructure code -- the same class of work as a database migration or a cron-triggered cleanup job.

The cross-schema SQL view approach costs exactly 1 LLM session per day (the same as today's calendar-only briefing). The 7 additional sessions eliminated are pure waste: they would spawn LLM instances solely to execute deterministic SQL queries that infrastructure code handles in milliseconds.

## Design

### Exception Scope

The exception permits the General butler to read specialist butlers' `state` table entries via a SQL view. The scope is narrowly defined:

- **What is accessed:** State store entries whose keys match `briefing/daily/%` (structured JSON contributions written by deterministic jobs).
- **Direction:** Read-only, General reads from specialists. No specialist reads General's data. No writes cross schema boundaries.
- **Mechanism:** A SQL view (`general.v_briefing_contributions`) in General's schema, not direct cross-schema queries in application code.
- **When:** Once per day, as a batch job 2 minutes before the EOD briefing prompt fires.

### The View

```sql
CREATE VIEW general.v_briefing_contributions AS
    SELECT 'health' AS butler, key, value FROM health.state
        WHERE key LIKE 'briefing/daily/%'
    UNION ALL
    SELECT 'finance' AS butler, key, value FROM finance.state
        WHERE key LIKE 'briefing/daily/%'
    UNION ALL
    SELECT 'relationship' AS butler, key, value FROM relationship.state
        WHERE key LIKE 'briefing/daily/%'
    UNION ALL
    SELECT 'travel' AS butler, key, value FROM travel.state
        WHERE key LIKE 'briefing/daily/%'
    UNION ALL
    SELECT 'education' AS butler, key, value FROM education.state
        WHERE key LIKE 'briefing/daily/%'
    UNION ALL
    SELECT 'home' AS butler, key, value FROM home.state
        WHERE key LIKE 'briefing/daily/%';
```

The view lives in the `general` schema. Each UNION term provides an explicit `butler` string literal, hardcoded in the SQL definition rather than derived from the JSON payload. This makes the provenance tamper-resistant -- the `butler` column value is set by the view definition, not by the data.

### Five Guardrails

These guardrails exist specifically to prevent this exception from becoming a general-purpose cross-schema access pattern.

**1. Read-only SQL view.** The view is a UNION of SELECT statements. PostgreSQL does not permit INSERT, UPDATE, or DELETE on UNION views, making write access structurally impossible at the database level. There is no application-level enforcement to bypass -- the constraint is in the database engine itself.

**2. Explicit butler source column.** Each UNION term includes a hardcoded string literal `butler` column (e.g., `SELECT 'health' AS butler`). The aggregation job validates that `value::jsonb->>'butler'` matches this source column, detecting malformed or tampered contributions. The source column is not derived from user-controlled data.

**3. Date-filtered queries only.** The view filters rows to keys matching `briefing/daily/%`, and the aggregation job further filters to today's date (SGT). This prevents the view from being used to access arbitrary state data. A specialist butler's state store may contain hundreds of keys; only briefing contributions are visible through this view.

**4. Health check validates view accessibility.** The aggregation job validates that the view is queryable before processing rows. This catches grant revocations, schema changes, or dropped specialist schemas early, producing a clear error rather than silent data loss.

**5. Migration-based grants (auditable).** Cross-schema SELECT grants are created via an Alembic migration, tracked in version control, and reversible on downgrade. The grants are not applied via ad-hoc SQL or runtime code. Any change to cross-schema access produces a diff in the migration history.

### Data Flow

```
14:55 SGT (cron 55 6 * * *)
  health.daily_briefing_contribution    -> health.state['briefing/daily/2026-03-25']
  finance.daily_briefing_contribution   -> finance.state['briefing/daily/2026-03-25']
  relationship.daily_briefing_contribution -> relationship.state['briefing/daily/2026-03-25']
  travel.daily_briefing_contribution    -> travel.state['briefing/daily/2026-03-25']
  education.daily_briefing_contribution -> education.state['briefing/daily/2026-03-25']
  home.daily_briefing_contribution      -> home.state['briefing/daily/2026-03-25']

14:58 SGT (cron 58 6 * * *)
  general.collect_briefing_contributions
    -> reads general.v_briefing_contributions (cross-schema view)
    -> validates each contribution envelope
    -> writes general.state['briefing/combined/2026-03-25']

15:00 SGT (cron 0 7 * * *)
  general.eod-tomorrow-prep (LLM session)
    -> reads general.state['briefing/combined/2026-03-25']
    -> sends multi-domain briefing via Telegram
```

All contribution jobs and the aggregation job are registered in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` with `dispatch_mode="job"`. They execute as pure Python functions with zero LLM cost. Only the final EOD prompt at 15:00 spawns an LLM session.

### Contribution Envelope

Each specialist writes a structured JSON contribution:

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

Contributions with `has_updates: false` are omitted from the briefing entirely. The `summary` field is pre-rendered text the LLM can use directly, eliminating the need for the LLM to analyze raw domain data.

### Graceful Degradation

If a specialist butler is down, its contribution job does not run and the aggregation picks up nothing for that butler. The combined payload lists missing butlers in a `missing_butlers` array. The EOD prompt gracefully degrades: if no combined briefing state exists, it falls back to calendar-only format (preserving current behavior).

## Reuse Criteria

This exception pattern is not a blanket authorization for cross-schema access. Each future use must be evaluated against these criteria independently.

### MAY Be Reused When ALL of These Hold

1. **Read-only.** The cross-schema access is strictly read-only, enforced at the database level (view or restricted role), not just by application convention.
2. **Deterministic.** The code performing the cross-schema read is deterministic infrastructure code (pure Python, SQL), not LLM reasoning. No LLM session is involved in the data extraction or aggregation.
3. **Batch.** The access pattern is batch-oriented (daily, hourly) with a fixed schedule, not real-time or on-demand. There is a clear temporal boundary.
4. **Auditable.** The cross-schema access is implemented via migration-tracked database objects (views, grants) with explicit source attribution, not embedded SQL in application code.
5. **Cost-justified.** The compliant alternative (Switchboard MCP fan-out) would require a materially higher number of LLM sessions for work that involves zero LLM reasoning.

### MUST NOT Be Reused When ANY of These Hold

1. **LLM sessions are involved.** If the cross-schema data needs LLM reasoning to extract, transform, or interpret, use Switchboard fan-out. The whole justification for this exception is avoiding LLM sessions for deterministic work.
2. **Write operations.** Any cross-schema write MUST go through the Switchboard. There are no exceptions to this. The write path is where coordination, conflict resolution, and authorization live.
3. **Real-time queries.** If the data is needed on-demand during an LLM session (e.g., "what does the Health butler know about X?"), use MCP tool calls through the Switchboard. The exception is for pre-scheduled batch aggregation, not interactive queries.
4. **Unbounded key access.** If the access pattern requires reading arbitrary state keys rather than a well-defined, filtered subset, this pattern does not apply. The key filter (`briefing/daily/%`) is a critical constraint.
5. **Application-level enforcement only.** If the read-only or scope constraints can only be enforced by application code (not database-level views or grants), the guardrails are insufficient.

## Integration

- **RFC 0002:** The MCP-only inter-butler communication principle remains the default. This RFC documents a specific, guarded exception -- it does not modify the principle itself. Butlers that need to communicate interactively (request/response, state mutation, coordination) MUST continue to use MCP through the Switchboard.
- **RFC 0003:** Switchboard fan-out remains the correct pattern for interactive cross-butler requests. This exception does not apply to route-based communication.
- **RFC 0006:** The SQL view and cross-schema grants are implemented as an Alembic migration within the existing multi-chain migration model. The view lives in the `general` schema. SELECT grants on specialist `state` tables are scoped to the General butler's database role. The migration is reversible: downgrade drops the view and revokes grants. The `search_path` constraint in RFC 0006 ("A butler CANNOT access another butler's schema") is overridden for this specific view via explicit grants, not by modifying search_path.
- **RFC 0009:** The Situational Context Bus (public.user_context) uses a different mechanism -- the public schema table readable by all butlers. That pattern does not require this exception because public schema tables are already within every butler's search_path by design. The briefing exception is distinct: it reads from per-butler schemas, which are normally inaccessible.

## Alternatives Considered

**Switchboard MCP fan-out.** General sends an MCP request to each specialist, each spawns an LLM session to answer. Architecturally pure but costs 8 LLM sessions per day (1 request + 6 responses + 1 synthesis) for zero-reasoning work. At typical LLM pricing, this is approximately 8x the cost for the same output. Rejected on cost grounds.

**Shared briefing table (public.briefing_contributions).** All specialists write to a table in the public schema. Rejected because it introduces write coupling to the public schema, which is currently read-only for most butlers (RFC 0006). It would also require extending the public schema access model, creating precedent for arbitrary shared tables.

**Event-based pub/sub.** Specialists publish briefing events, General subscribes. Rejected because no event bus exists in the architecture, and building one for a daily batch pattern is over-engineered. This would also create implicit coupling between publishers and subscribers.

**Direct cross-schema queries in application code.** General's aggregation job hardcodes `SELECT ... FROM health.state` in Python. Rejected because it is fragile, not auditable via migration history, and makes the cross-schema access invisible to database administrators reviewing grants and views.

**Database role with broad cross-schema SELECT.** Grant a `briefing_reader` role SELECT on each specialist's full `state` table (not filtered by a view). Viable but provides broader access than necessary. The view constrains access to `briefing/daily/%` keys only, enforcing the principle of least privilege at the database level.
