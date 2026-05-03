## Context

The dashboard currently has no surface for instance ownership facts. When the system is healthy, the owner has no easy way to answer: "Is my version up to date? How much disk am I using? Has my data gone anywhere I did not expect?" These are sentinel questions an owner-operator asks. Answering them requires aggregating facts from multiple places: the Python package version, asyncpg pool stats, PostgreSQL catalog tables, session history, and the audit log.

The egress catalog is the most novel and privacy-sensitive fact on the page. "Data seen by" means: which external actor endpoints have received payloads from this instance (LLM providers, Telegram API, Gmail SMTP, Google Calendar, etc.)? The audit log already records outbound API calls with actor information. The egress catalog is a read-only aggregation of that existing data, grouped by actor.

The page is owner-only. The dashboard today has a single trusted viewer. There is no session-role system, no bearer-token-scoped view, no shared-link flow. This simplifies the access contract in v1 to: the page is mounted inside the existing dashboard session/cookie boundary, and that boundary is already owner-only.

## Goals / Non-Goals

**Goals:**

- Define the `/system` route, the data model it surfaces, and the privacy contract that governs the egress catalog.
- Document the internal interface contract for instance-level facts that butler daemons already expose, so the aggregating API layer has a normative contract to consume.
- Specify the six ownership-fact domains as distinct API endpoints, each queryable independently.
- Establish the egress catalog actor enumeration and visibility rules for v1 and the forward path.

**Non-Goals:**

- Implementing the page, API endpoints, or tile components. That is the implementation change's job (`bu-ngfzz.2` through `bu-ngfzz.7`).
- Designing the UI layout or tile visual structure. The spec covers data contracts, not rendering.
- Specifying a per-session viewer-context system. This page is owner-only in v1; any multi-viewer model is a separate capability.
- Adding new database tables. All facts are derived from existing infrastructure at query time.
- Specifying a real-time push mechanism. The page polls via TanStack Query like all other dashboard pages.

## Decisions

### D1: Six ownership-fact domains, each a separate API endpoint.

The System page surfaces six domains: instance identity, database state, backup state, egress catalog, and per-butler heartbeats. Each domain maps to one `GET /api/system/<domain>` endpoint with its own Pydantic response model.

**Alternative considered:** a single `GET /api/system` endpoint returning a unified payload. **Rejected**: the domains have different latency profiles (database stats are fast; backup recency may require S3/filesystem I/O; egress catalog aggregates potentially many audit rows). Separate endpoints allow the frontend to load domains independently with different stale-time and retry policies.

### D2: Egress catalog is sourced from the existing audit log, not a new table.

The `audit.events` table (written by `src/butlers/api/audit_emit.py`) already records outbound operations with actor metadata. The egress catalog groups these by actor and computes `last_seen_at` and `total_calls`. No new write path is introduced.

**Alternative considered:** A dedicated `egress_facts` table written at call time by each connector. **Rejected**: that would require every connector to be aware of the egress catalog, coupling the connector layer to a dashboard feature. The audit log is already the canonical write path for observable operations.

**Open question**: The audit log may not capture all egress paths uniformly (LLM API calls vs. Telegram outbound vs. Google Calendar writes). The spec should make this a normative requirement but the implementation bead must verify coverage and file follow-up beads for any gaps.

### D3: Actor enumeration in the egress catalog is owner-contact-only in v1.

In v1, the only contact that can be enumerated in the egress catalog viewer field is the owner contact (the single entry in `public.contacts` with `roles @> ARRAY['owner']`). The forward path to listing other actors (family members, delegated access) is documented as an open question.

**Rationale**: the egress catalog reveals that the owner's data was processed by external APIs. In a single-tenant, owner-only system, the only person who needs to see this is the owner. Exposing it to other contacts would require a per-contact permission model that does not exist today.

### D4: Per-butler heartbeat data comes from the existing liveness table, not from live MCP calls.

The butler-base-spec already describes a heartbeat task that fires during daemon operation. The System page reads the last-known heartbeat timestamp from the switchboard liveness registry (the same data the dashboard's butler list already uses). It does not fan out live MCP status calls.

**Alternative considered:** issuing a live `status` tool call to each butler to get a fresh heartbeat. **Rejected**: this would make the System page load dependent on all butlers being reachable, turning a monitoring surface into a surface that fails when the system is degraded. Read from the registry; degrade gracefully when a butler's last heartbeat is stale.

### D5: Database size facts come from PostgreSQL catalog queries, not a butler-owned table.

`pg_database_size(current_database())` and per-schema stats from `pg_stat_user_tables` are available to the dashboard API's existing DB pool without schema changes.

**Open question**: `pg_stat_user_tables` requires either superuser or object-ownership for accurate `n_live_tup` estimates. If the dashboard API role does not have this access, the schema-level size query must fall back to `pg_catalog.pg_total_relation_size()` per relation, which requires only `pg_read_all_stats` or SELECT on the relation. The implementation bead must verify access and document the fallback in `AGENTS.md`.

## Risks / Trade-offs

- **[Risk]** The egress catalog gives the owner a false sense of completeness if the audit log does not capture all outbound calls. **Mitigation**: the spec marks the egress catalog as "derived from the audit log" so the owner understands it is a view of recorded events, not a packet-capture guarantee. The spec also requires a "last verified" timestamp on each actor entry so staleness is visible.
- **[Risk]** Backup recency requires reading from an external system (S3/Minio or the filesystem). If that system is unreachable, the backup tile should degrade gracefully. **Mitigation**: the spec requires the endpoint to return `null` for `last_backup_at` when backup metadata is unavailable, with a companion `backup_source_reachable: bool` field.
- **[Trade-off]** Deriving database growth rate from `pg_stat_user_tables` requires either a periodic snapshot table (not present today) or a computed delta between current size and some historical baseline. **Resolution**: v1 surfaces current size only; growth rate is an open question for the implementation bead to resolve (likely by sampling size at page-load time against a `{schema}.system_snapshots` table that gets one row per day from a scheduled job).
- **[Risk]** Exposing `/api/system/*` without additional access controls means the endpoint is reachable by any authenticated dashboard session. Since the dashboard is owner-only today, this is acceptable for v1. A non-owner multi-viewer extension (Settled Direction #1 anticipates "close family members later") would require gating. **Mitigation**: the spec explicitly calls this out as a v1 constraint and requires the endpoint to assert owner-contact identity before returning egress catalog data.

## Migration Plan

No database schema changes in this change. The spec is documentation-only.

The implementation bead (`bu-ngfzz.2`) may introduce a scheduled snapshot job for database growth rate tracking. That would require a new migration; it is not specced here and is left to the implementation bead's discretion.

## Open Questions

- **[Unknown]** Does the audit log uniformly record all egress paths (LLM providers, Telegram, Gmail, Google APIs)? If coverage is incomplete, should the egress catalog show "recorded egress" with a caveat, or should the implementation bead first add missing audit calls before shipping the page?
- **[Unknown]** How is backup recency determined? Is there a Minio/S3 bucket with timestamped snapshot objects, a `pg_dump` cron job writing to the filesystem, or something else? The implementation bead must discover the backup strategy and either spec the endpoint shape precisely or raise a follow-up bead.
- **[Unknown]** Should database growth rate be shown in v1? If yes, the implementation bead must introduce a periodic snapshot mechanism. If no, the tile can show current size only and defer growth to a follow-up.
- **[Unknown]** The forward path for multi-viewer access to the egress catalog (Settled Direction #1: "close family members later"). When family-member access lands, should the egress catalog be hidden entirely from non-owner viewers, or should it show only the facts relevant to data the viewer has access to? Raise this in the implementation bead when multi-viewer access is designed.
