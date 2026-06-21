## 1. Search index migration (bu-kowq7e)

- [ ] 1.1 Add core Alembic migration (next id in the `core_*` chain, after `core_134`) that runs `CREATE EXTENSION IF NOT EXISTS pg_trgm` and creates a GIN trigram index over `calendar_events(title, description, location)` per butler schema (`IF NOT EXISTS`, schema-qualified, `gin_trgm_ops`)
- [ ] 1.2 Implement `downgrade()` that drops the index (`DROP INDEX IF EXISTS`); leave the `pg_trgm` extension in place (shared, may be used elsewhere)
- [ ] 1.3 Verify the migration is idempotent (re-runnable) and applies cleanly across all existing butler schemas

## 2. Search query + endpoint (bu-kowq7e)

- [ ] 2.1 Add a fan-out search query function in `calendar_workspace_v1.py` over `calendar_events` (joined to `calendar_sources` for lane/scope), matching `q` against `title`/`description`/`location` with trigram similarity, ranked, and carrying each match's date(s)
- [ ] 2.2 Add `GET /api/calendar/workspace/search` to `calendar_workspace.py` accepting `q` (required), `view`, optional `butlers`/`sources`, and a bounded `limit`; reuse the lane/scope semantics of the workspace read
- [ ] 2.3 Empty/blank `q` returns an empty match list (do NOT return the whole calendar) without erroring
- [ ] 2.4 Degraded behavior: if `pg_trgm`/the GIN index is unavailable in a probed schema, fall back to a `ILIKE`-style substring match (or skip that schema) fail-open rather than 500
- [ ] 2.5 Unit/integration tests: ranked title/description/location matches; empty-query → empty; lane + source scoping honored; degraded fallback path

## 3. Server-side facets on the workspace read (bu-xr1i95)

- [x] 3.1 Add optional `status`, `source_type`, and `editable` query params to `GET /api/calendar/workspace`; validate `status`/`source_type` against the known enums
- [x] 3.2 Apply the facets server-side in the fan-out query (`status` over instance/event status, `source_type` over the computed entry kind, `editable` over `s.writable`) instead of returning everything and filtering client-side
- [x] 3.3 Unit tests: each facet narrows the result set; omitted facets preserve current behavior; combined facets AND together

## 4. Keyset (cursor) pagination on the workspace read (bu-xr1i95)

- [x] 4.1 Add `limit` (bounded, with a default) and `cursor` params; encode the cursor as the opaque last-seen `(starts_at, id)` keyset position consistent with the existing `ORDER BY i.starts_at, i.id`
- [x] 4.2 Query `LIMIT limit + 1`, derive `has_more`, and emit `next_cursor` (opaque) when more rows remain; do NOT compute or return a `total`
- [x] 4.3 Extend `CalendarWorkspaceReadResponse` with `next_cursor: str | null` and `has_more: bool`; keep `entries`/`source_freshness`/`lanes` unchanged
- [x] 4.4 Unit tests: first page returns `next_cursor` + `has_more=true`; passing `cursor` returns the next page with no overlap; last page returns `has_more=false`; malformed cursor → 400

## 5. Spec + quality gate

- [ ] 5.1 Reconcile the `dashboard-api` and `module-calendar` spec deltas with the implemented behavior (this change's deltas are the source of truth)
- [ ] 5.2 Run `cd /home/tze/gt/butlers && openspec validate calendar-search-and-server-filtering --strict`
- [ ] 5.3 Quality gate: `ruff check`/`format --check` on touched files + targeted calendar-workspace + migration tests, then full `pytest` (excluding e2e) before merge
