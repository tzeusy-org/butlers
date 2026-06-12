# Tasks — entity-v3-lifecycle-and-depth

Backend groups (1–5) block frontend groups (6–8); group 9 closes the change. Spec traceability is noted per group.

## 1. Schema and lifecycle foundation (specs: relationship-facts delta, relationship-entity-lifecycle)

- [ ] 1.1 Alembic migration (relationship chain): add `observed_at TIMESTAMPTZ NULL` + `metadata JSONB NULL` to `relationship.entity_facts`; add `cardinality` to `relationship.entity_predicate_registry` (seed: single for has-birthday/dunbar_tier_override, multi otherwise); create `relationship.entity_view_marks` and `relationship.merge_reviews` (additive only; confirm target DB per `butlers-db-host-topology` memory before running)
- [ ] 1.2 Batched idempotent backfill script: `observed_at := COALESCE(last_seen, created_at)` where NULL
- [ ] 1.3 `relationship_assert_fact()`: accept optional `observed_at` (default `now()`); supersession carries per-row `observed_at`
- [ ] 1.4 Read-time staleness derivation helper (shared SQL expression / Python fn) returning `staleness_band`; unit tests for the COALESCE fallback chain and band edges
- [ ] 1.5 Guardrail test: no in-place `UPDATE` of `conf` anywhere (source-scan + DB-layer test)

## 2. Merge review backend (spec: relationship-merge-review)

- [ ] 2.1 `POST /api/relationship/entities/compare` — structural diff (a/b blocks over both stores; shared/divergent over identity store only, divergent gated on registry cardinality=single) with full provenance + staleness, owner-only authz
- [ ] 2.2 `relationship.merge_reviews` write paths: audit row written by `POST /entities/{id}/merge` itself regardless of entry path; dismissed row on dismissal
- [ ] 2.3 Queue derivation update: dismissed pairs suppressed from duplicate bucket until new `{predicate, shared_value}` evidence
- [ ] 2.4 Guardrail test: no-LLM source-scan over compare/merge handler paths

## 3. Lookup MCP tool (spec: relationship-entity-lookup)

- [ ] 3.1 `relationship_lookup(entity_id|entity_ref)` tool: deterministic ref resolution (search ranking), layered two-store fact payload with provenance + staleness, recency block, ambiguity candidates, structured miss
- [ ] 3.2 Read-only test: identical repeated calls leave DB byte-identical
- [ ] 3.3 Docstring ≤300-token test + in-session-only guardrail scan (no scheduled-task prompts feeding the tool)

## 4. Read endpoints (specs: dashboard-relationship delta)

- [ ] 4.1 `GET /entities/{id}/facts` drill: `predicate=`/`validity=`(default active)/`store=` filters, keyset pagination, provenance + staleness per row, owner-only authz (12a/12b)
- [ ] 4.2 `GET /entities/{id}/neighbours`: `rank=weight` + `per_predicate=N` + `remainder` counts
- [ ] 4.3 `GET /entities/{id}/activity`: `bins=daily&window=90d` (+`bins_only`); extend chronicler-boundary guardrail test to the binning path
- [ ] 4.4 `POST /entities/{id}/view-mark` + `GET /entities/{id}/delta-facts` (delta computed before mark moves; owner-only authz 12a/12b)
- [ ] 4.5 Core-dates server extraction: date-kind facts with next-occurrence on the detail payload (replace client-side string-matching)

## 5. Cross-butler invariants (specs: switchboard-identity delta, module-memory delta, relationship-entity-lifecycle)

- [ ] 5.1 Switchboard guardrail test: source-scan for `relationship_assert_fact` calls / write-DML (`INSERT`/`UPDATE`/`DELETE`) on `relationship.entity_facts` in switchboard code — must be empty (the mandated `resolve_contact_by_channel()` SELECT stays legal)
- [ ] 5.2 Connector guardrail test: no fact writes from `src/butlers/connectors/`
- [ ] 5.3 Memory-module boundary: reject/route identity-predicate content away from the memory-module `facts` table (writer-side check + test); fact-extraction skill doc updated to cite the boundary
- [ ] 5.4 Relationship MANIFESTO one-line amendment: confidence (assertion-time, immutable) vs staleness (read-time) axes

## 6. Frontend foundation (spec: dashboard-relationship delta)

- [ ] 6.1 Extract `EntityMark`, `Row`, `TierBadge`, `StateDot` to `frontend/src/components/ui/`; replace inline copies in Index + Hop; lint/review gate on new inline copies
- [ ] 6.2 Staleness + provenance display primitives (conf bar, staleness band treatment, src/verified marks) as shared components — two visually distinct axes

## 7. View depth — detail and curation (specs: dashboard-relationship delta, relationship-merge-review)

- [ ] 7.1 Workbench three-rail layout: left context rail (top relations, introduced-via, shares-identifiers hint), middle KPI strip + two-store sortable provenance grid, right action rail + confidence/staleness inspector, duplicate warning panel
- [ ] 7.2 Compare view UI: two-column structural diff, shared evidence highlighted, divergents grouped; merge (choose survivor) / dismiss commits; entry from queue card, workbench panel, bulk gutter
- [ ] 7.3 Editorial provenance on-demand reveal per fact row
- [ ] 7.4 90-day sparkline (custom SVG sticks, absent days 4% opacity) in detail hero
- [ ] 7.5 Delta-since-last-visit banner + row highlights; view-mark POST after delta render
- [ ] 7.6 Core-dates block (both modes) with next-occurrence, provenance affordance
- [ ] 7.7 Detail keyboard map (`k/j` siblings, `Esc`, `m` compare when evidenced)
- [ ] 7.8 Latest-interactions block: per-channel most-recent rows (read-through to existing interactions/message-thread endpoints) with staleness treatment and `src`

## 8. View depth — index, hop, columns, concentration, finder (spec: dashboard-relationship delta)

- [ ] 8.1 Index selection + bulk gutter (archive/forget with canned confirm; merge enabled only at exactly 2 → compare view); Index keyboard map
- [ ] 8.2 Queue evidence drill: duplicate card → compare; unidentified/stale cards → detail links; one commit button per card preserved
- [ ] 8.3 Hop: ranked truncated predicate groups (+N more inert), clickable breadcrumb trail + reset pill, keyboard map
- [ ] 8.4 Columns: ranked truncated groups, keyboard map (`↑↓→←`, Enter)
- [ ] 8.5 Concentration: weight bars, footer KPI strip, row drill to detail
- [ ] 8.6 Finder: preview pane (inert, ≤1 extra neighbours call), Tab-to-hop, keyboard footer
- [ ] 8.7 Toolbar search on Index wired to `/entities/search` (single search path with Cmd-K ranking)
- [ ] 8.8 Finder empty-query owner-pinned set: top-8 owner neighbours by direct-edge weight via the ranked `/neighbours` extension

## 9. Validation and close-out

- [ ] 9.1 e2e smoke: queue→compare→merge/dismiss flow; delta banner; workbench grid; finder Tab-to-hop (Playwright, extend existing entity smoke)
- [ ] 9.2 Full guardrail suite green: chronicler boundary (incl. binning), switchboard read-only, connectors no-write, no-LLM compare/merge, lookup in-session scan, conf-immutability, docstring budget
- [ ] 9.3 Spec-to-code reconciliation report (`docs/reports/entity-v3-lifecycle-and-depth.md`) + archive ceremony for this change
