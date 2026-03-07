## Context

`request_id` (UUID7) is already minted by the Switchboard's pipeline module at ingestion time — it equals `message_inbox.id` in the switchboard schema. Sessions already carry it as a nullable `str` column. The problem is that `message_inbox` lives in the Switchboard's private schema, making cross-butler provenance queries impossible without crossing schema boundaries outside of spec. For internally-triggered sessions (tick, schedule, trigger), `request_id` is currently `None` or coerced from a fallback.

The change promotes `request_id` to a first-class, always-non-null UUID7 on every session, and adds a `shared.ingestion_events` table that acts as the provenance anchor for connector-sourced events — visible to all butlers via the `shared` schema. The dashboard's `/traces` page is retired in favour of a Timeline tab on `/ingestion` unified under `request_id`.

## Goals / Non-Goals

**Goals:**
- Every session has a non-null UUID7 `request_id` regardless of trigger source
- Connector-sourced sessions have a FK (`ingestion_event_id`) into `shared.ingestion_events` for referential integrity and cross-butler joins
- Lineage query: given any `request_id`, retrieve all downstream sessions across all butler schemas
- Dashboard Timeline tab replaces `/traces`, showing the full lifecycle of an event from ingestion through butler topology

**Non-Goals:**
- Changing how OTel trace IDs are minted or propagated — `trace_id` remains independent; `request_id` is a higher-level correlation key that may span multiple traces
- Backfilling historical sessions with `ingestion_event_id` — existing rows keep `ingestion_event_id = NULL`
- Exposing `request_id` to the connector over the wire — connectors already receive it in `IngestAcceptedResponse`; no API surface changes

## Decisions

### 1. `shared.ingestion_events.id` = `message_inbox.id`

**Decision:** The UUID7 minted for `message_inbox` is reused as the PK of `shared.ingestion_events`. Both rows are inserted in the same advisory-lock transaction.

**Why:** The UUID7 is already the canonical `request_id` returned to connectors. Reusing it avoids an indirection layer and keeps `sessions.ingestion_event_id` == `sessions.request_id` for connector-sourced sessions — a simple invariant to reason about and query.

**Alternative considered:** Mint a separate UUID7 for `shared.ingestion_events` and carry `ingestion_event_id` as a distinct value from `request_id`. Rejected — the duplication adds no value and complicates the write path.

### 2. Two columns on sessions: `request_id` (required) + `ingestion_event_id` (nullable FK)

**Decision:** Keep `request_id` as the universal correlation column (always set, no FK constraint) and add `ingestion_event_id` as a nullable FK into `shared.ingestion_events.id`.

**Why:** `request_id` is set for ALL sessions including internally-triggered ones (tick, schedule, trigger), which have no corresponding ingestion event. A single nullable FK column (`ingestion_event_id`) cleanly expresses the distinction: if set, referential integrity is enforced; if null, the session was internally triggered. Queries over `request_id` work uniformly for all session types.

**Alternative considered:** Make `request_id` the FK and require all sessions to have a `shared.ingestion_events` row (with a synthetic "internal" row for daemon-triggered sessions). Rejected — synthetic rows pollute the events table with non-ingestion data and complicate lineage queries.

### 3. Spawner mints UUID7 for internal sessions

**Decision:** `spawner.py` mints a UUID7 before calling `session_create()` whenever no `request_id` is provided from the pipeline context. The existing `_generate_uuid7_string()` helper in `pipeline.py` is extracted to a shared utility (e.g., `butlers.core.utils`).

**Why:** `session_create()` becomes strict (raises `ValueError` on `None` request_id). The minting responsibility moves to the call site — the spawner — which is the right place since it controls the session lifecycle. The tick handler and scheduler paths in `daemon.py` are the two callers that need updating.

**Alternative considered:** Generate inside `session_create()` when `request_id` is None. Rejected — silent coercion hides the distinction between "no request ID provided" (a programming error for external sessions) and "internal session with a fresh ID". Explicit minting at the call site is safer.

### 4. Cross-butler lineage via API fan-out (not a shared view)

**Decision:** `ingestion_event_sessions()` fans out to each butler's schema using the existing `DatabaseManager` pattern, then unions the results in Python.

**Why:** Consistent with the existing cross-butler session list (`GET /api/sessions`). Adding a cross-schema PostgreSQL view would require a `shared` schema migration and coupling the view definition to the butler registry — fragile when new butlers are added.

**Alternative considered:** A materialized view in `shared` that unions all `{butler}.sessions` tables. Rejected — requires rebuilding the view on every butler addition and introduces a shared schema dependency on butler schemas.

### 5. `/traces` page redirects to `/ingestion?tab=timeline`; no trace-specific detail page

**Decision:** Both `/traces` and `/traces/:traceId` redirect to `/ingestion?tab=timeline`. There is no per-trace detail route replacement.

**Why:** The Timeline tab will show sessions with their `trace_id` values as deep-link attributes (e.g., linking out to Grafana/Jaeger). A full trace detail page (span tree) is out of scope for this change and can be added later as a drill-down from the Timeline tab. Redirecting both routes to the tab-level avoids dead bookmarks.

## Risks / Trade-offs

- **Migration gap:** Existing sessions with a non-null `request_id` but no `shared.ingestion_events` row will have `ingestion_event_id = NULL`. Lineage queries degrade gracefully (fall back to direct `request_id` match) but the FK link is absent for historical data. → Acceptable; no backfill required.

- **Advisory lock window widened:** The Switchboard's dedup transaction now writes two rows (`message_inbox` + `shared.ingestion_events`) instead of one. The lock is still released after the transaction commits. → Negligible overhead; both inserts are simple single-row writes in the same transaction.

- **Fan-out query cost:** `ingestion_event_sessions()` queries every butler schema in parallel. As the butler count grows this scales linearly. → Acceptable at current scale; can be addressed with a denormalized union table if needed later.

- **`/api/traces` removal is breaking:** Any bookmarked URLs or external tooling that calls `/api/traces` directly will 404. → Mitigated by redirecting the frontend routes; the API endpoints themselves return 404 without a frontend redirect, so external consumers must be updated manually.

## Migration Plan

1. **Alembic migration:** Add `shared.ingestion_events` table; add `ingestion_event_id UUID REFERENCES shared.ingestion_events(id)` column to all `{butler}.sessions` tables; add NOT NULL constraint to `sessions.request_id` with a default of `gen_random_uuid()` for any existing null rows (or migrate them to a generated UUID7 first).

2. **Pipeline module (`pipeline.py`):** Inside the advisory-lock transaction, after `INSERT INTO message_inbox`, also insert into `shared.ingestion_events` using the same UUID7. The `switchboard` schema's DB pool already has access to the `shared` schema.

3. **Sessions module (`sessions.py`):** Change `session_create()` signature: `request_id: str` (required, no default), `ingestion_event_id: str | None = None`. Raise `ValueError` if `request_id` is None. Add `ingestion_event_id` to the INSERT.

4. **Spawner (`spawner.py`):** Extract `_generate_uuid7_string()` to `butlers.core.utils`. Mint a UUID7 before calling `session_create()` in the internal trigger paths (tick handler, scheduler dispatch).

5. **New query module:** Create `butlers.core.ingestion_events` with `ingestion_event_get`, `ingestion_events_list`, `ingestion_event_sessions`, `ingestion_event_rollup`.

6. **Dashboard API:** Add `routers/ingestion_events.py` with the four endpoints. Remove `routers/traces.py` (or remove the traces endpoint handlers if they share a router).

7. **Dashboard frontend:** Remove `/traces` and `/traces/:traceId` routes; add `<Navigate replace to="/ingestion?tab=timeline" />` redirects. Remove Traces nav item from sidebar Telemetry section. Remove `g then r` keybinding. Add Timeline tab to `/ingestion` page with hooks calling the new API endpoints.

**Rollback:** The `ingestion_event_id` column and `shared.ingestion_events` table can be dropped without affecting session reads (the column is nullable). Restoring the `request_id` optional default in `session_create()` reverts the API contract. The frontend changes are independent.

## Open Questions

- Should `ingestion_event_rollup` aggregate cost using the `cost` JSONB field on sessions, or should it derive cost from token counts × per-model pricing? (Current `cost` field is already computed by the spawner — prefer using it directly.)
- Does the Timeline tab replace the existing `/timeline` page in the Telemetry nav, or does `/timeline` remain as a separate unified event stream? (Current spec keeps `/timeline` as a separate route — Timeline tab on `/ingestion` is a different surface focused on ingestion event lineage, not the full event stream.)
