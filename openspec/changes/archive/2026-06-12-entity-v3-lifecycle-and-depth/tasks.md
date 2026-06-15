# Tasks — entity-v3-lifecycle-and-depth

Backend groups (1–5) block frontend groups (6–8); group 9 closes the change. Spec traceability is noted per group.

## 1. Schema and lifecycle foundation (specs: relationship-facts delta, relationship-entity-lifecycle)

- [x] 1.1 Alembic migration (relationship chain): add `observed_at TIMESTAMPTZ NULL` + `metadata JSONB NULL` to `relationship.entity_facts`; add `cardinality` to `relationship.entity_predicate_registry` (seed: single for has-birthday/dunbar_tier_override, multi otherwise); create `relationship.entity_view_marks` and `relationship.merge_reviews` (additive only; confirm target DB per `butlers-db-host-topology` memory before running) — **PR #2171** (bu-mxxjy)
- [x] 1.2 Batched idempotent backfill script: `observed_at := COALESCE(last_seen, created_at)` where NULL — **PR #2171** (bu-mxxjy: `scripts/backfill_entity_fact_observed_at.py`)
- [x] 1.3 `relationship_assert_fact()`: accept optional `observed_at` (default `now()`); supersession carries per-row `observed_at` — **PR #2174** (bu-4mh9a)
- [x] 1.4 Read-time staleness derivation helper (shared SQL expression / Python fn) returning `staleness_band`; unit tests for the COALESCE fallback chain and band edges — **PR #2174** (bu-4mh9a)
- [x] 1.5 Guardrail test: no in-place `UPDATE` of `conf` anywhere (source-scan + DB-layer test) — **PR #2174** (bu-4mh9a: `test_conf_immutability_guardrail.py`)

## 2. Merge review backend (spec: relationship-merge-review)

- [x] 2.1 `POST /api/relationship/entities/compare` — structural diff (a/b blocks over both stores; shared/divergent over identity store only, divergent gated on registry cardinality=single) with full provenance + staleness, owner-only authz — **PR #2187** (bu-9wcxm)
- [x] 2.2 `relationship.merge_reviews` write paths: audit row written by `POST /entities/{id}/merge` itself regardless of entry path; dismissed row on dismissal — **PR #2187** (bu-9wcxm) + **PR #2223** (bu-csvop: MCP session-side merge tools write merge_reviews rows)
- [x] 2.3 Queue derivation update: dismissed pairs suppressed from duplicate bucket until new `{predicate, shared_value}` evidence — **PR #2187** (bu-9wcxm)
- [x] 2.4 Guardrail test: no-LLM source-scan over compare/merge handler paths — **PR #2195** (bu-odlcq) + **PR #2213** (bu-awv4f: widened to queue derivation + tool-layer merge impls)

## 3. Lookup MCP tool (spec: relationship-entity-lookup)

- [x] 3.1 `relationship_lookup(entity_id|entity_ref)` tool: deterministic ref resolution (search ranking), layered two-store fact payload with provenance + staleness, recency block, ambiguity candidates, structured miss — **PR #2179** (bu-vqy9j)
- [x] 3.2 Read-only test: identical repeated calls leave DB byte-identical — **PR #2179** (bu-vqy9j)
- [x] 3.3 Docstring ≤300-token test + in-session-only guardrail scan (no scheduled-task prompts feeding the tool) — **PR #2179** (bu-vqy9j) + **PR #2213** (bu-awv4f: widened scan to SKILL.md skill bodies)

## 4. Read endpoints (specs: dashboard-relationship delta)

- [x] 4.1 `GET /entities/{id}/facts` drill: `predicate=`/`validity=`(default active)/`store=` filters, keyset pagination, provenance + staleness per row, owner-only authz (12a/12b) — **PR #2180** (bu-tzvm6) + **PR #2184** (bu-ekad9: FE rewire)
- [x] 4.2 `GET /entities/{id}/neighbours`: `rank=weight` + `per_predicate=N` + `remainder` counts — **PR #2180** (bu-tzvm6) + **PR #2184** (bu-ekad9: FE rewire)
- [x] 4.3 `GET /entities/{id}/activity`: `bins=daily&window=90d` (+`bins_only`); extend chronicler-boundary guardrail test to the binning path — **PR #2183** (bu-bjvny)
- [x] 4.4 `POST /entities/{id}/view-mark` + `GET /entities/{id}/delta-facts` (delta computed before mark moves; owner-only authz 12a/12b) — **PR #2183** (bu-bjvny)
- [x] 4.5 Core-dates server extraction: date-kind facts with next-occurrence on the detail payload (replace client-side string-matching) — **PR #2183** (bu-bjvny) + **PR #2230** (bu-rag77: registry-driven date predicates)

## 5. Cross-butler invariants (specs: switchboard-identity delta, module-memory delta, relationship-entity-lifecycle)

- [x] 5.1 Switchboard guardrail test: source-scan for `relationship_assert_fact` calls / write-DML (`INSERT`/`UPDATE`/`DELETE`) on `relationship.entity_facts` in switchboard code — must be empty (the mandated `resolve_contact_by_channel()` SELECT stays legal) — **PR #2195** (bu-odlcq: `test_switchboard_no_fact_writes.py`)
- [x] 5.2 Connector guardrail test: no fact writes from `src/butlers/connectors/` — **PR #2195** (bu-odlcq: `test_connector_no_fact_writes.py`)
- [x] 5.3 Memory-module boundary: reject/route identity-predicate content away from the memory-module `facts` table (writer-side check + test); fact-extraction skill doc updated to cite the boundary — **PR #2195** (bu-odlcq)
- [x] 5.4 Relationship MANIFESTO one-line amendment: confidence (assertion-time, immutable) vs staleness (read-time) axes — **PR #2195** (bu-odlcq: `roster/relationship/MANIFESTO.md`)

## 6. Frontend foundation (spec: dashboard-relationship delta)

- [x] 6.1 Extract `EntityMark`, `Row`, `TierBadge`, `StateDot` to `frontend/src/components/ui/`; replace inline copies in Index + Hop; lint/review gate on new inline copies — **PR #2229** (bu-ovq7t)
- [x] 6.2 Staleness + provenance display primitives (conf bar, staleness band treatment, src/verified marks) as shared components — two visually distinct axes — **PR #2229** (bu-ovq7t)

## 7. View depth — detail and curation (specs: dashboard-relationship delta, relationship-merge-review)

- [x] 7.1 Workbench three-rail layout: left context rail (top relations, introduced-via, shares-identifiers hint), middle KPI strip + two-store sortable provenance grid, right action rail + confidence/staleness inspector, duplicate warning panel — **PR #2231** (bu-ly48x)
- [x] 7.2 Compare view UI: two-column structural diff, shared evidence highlighted, divergents grouped; merge (choose survivor) / dismiss commits; entry from queue card, workbench panel, bulk gutter — **PR #2198** (bu-b2qg8) + **PR #2236** (bu-pkmr8: queue evidence drill + bulk gutter entry)
- [x] 7.3 Editorial provenance on-demand reveal per fact row — **PR #2234** (bu-19u8r)
- [x] 7.4 90-day sparkline (custom SVG sticks, absent days 4% opacity) in detail hero — **PR #2188** (bu-xzh76)
- [x] 7.5 Delta-since-last-visit banner + row highlights; view-mark POST after delta render — **PR #2188** (bu-xzh76) + **PR #2239** (bu-ehc1s: detail fact-row delta highlight)
- [x] 7.6 Core-dates block (both modes) with next-occurrence, provenance affordance — **PR #2188** (bu-xzh76)
- [x] 7.7 Detail keyboard map (`k/j` siblings, `Esc`, `m` compare when evidenced) — **PR #2231** (bu-ly48x)
- [x] 7.8 Latest-interactions block: per-channel most-recent rows (read-through to existing interactions/message-thread endpoints) with staleness treatment and `src` — **PR #2234** (bu-19u8r)

## 8. View depth — index, hop, columns, concentration, finder (spec: dashboard-relationship delta)

- [x] 8.1 Index selection + bulk gutter (archive/forget with canned confirm; merge enabled only at exactly 2 → compare view); Index keyboard map — **PR #2236** (bu-pkmr8) + **PR #2239** (bu-ehc1s: 2px-border focus treatment)
- [x] 8.2 Queue evidence drill: duplicate card → compare; unidentified/stale cards → detail links; one commit button per card preserved — **PR #2236** (bu-pkmr8)
- [x] 8.3 Hop: ranked truncated predicate groups (+N more inert), clickable breadcrumb trail + reset pill, keyboard map — **PR #2232** (bu-hks7e)
- [x] 8.4 Columns: ranked truncated groups, keyboard map (`↑↓→←`, Enter) — **PR #2232** (bu-hks7e)
- [x] 8.5 Concentration: weight bars, footer KPI strip, row drill to detail — **PR #2232** (bu-hks7e) + **PR #2239** (bu-ehc1s: src/verified provenance marks + staleness dim)
- [x] 8.6 Finder: preview pane (inert, ≤1 extra neighbours call), Tab-to-hop, keyboard footer — **PR #2233** (bu-rru9g)
- [x] 8.7 Toolbar search on Index wired to `/entities/search` (single search path with Cmd-K ranking) — **PR #2233** (bu-rru9g)
- [x] 8.8 Finder empty-query owner-pinned set: top-8 owner neighbours by direct-edge weight via the ranked `/neighbours` extension — **PR #2233** (bu-rru9g)

## 9. Validation and close-out

- [x] 9.1 e2e smoke: queue→compare→merge/dismiss flow; delta banner; workbench grid; finder Tab-to-hop (Playwright, extend existing entity smoke) — **PR #2241** (bu-8qfok)
- [x] 9.2 Full guardrail suite green: chronicler boundary (incl. binning), switchboard read-only, connectors no-write, no-LLM compare/merge, lookup in-session scan, conf-immutability, docstring budget — **PR #2195** (bu-odlcq) + **PR #2213** (bu-awv4f: widened lookup + no-LLM scans)
- [x] 9.3 Spec-to-code reconciliation report (`about/audits/2026-06-13-entity-v3-spec-to-code.md`) + archive ceremony for this change — **PR #2203** (bu-6ivjj: reconciliation report); archive: **bu-lvzf9** (this change)

---

*Reconciled 2026-06-15 by bu-lvzf9. All 42 tasks confirmed shipped across PRs #2165–#2341. Genuine residuals (not part of these tasks) are tracked as sibling beads under epic bu-hg3cj: confidence calibration (bu-8j0ir), first_seen field (bu-t7dmi), editorial hero first-seen line (bu-llshp), low-severity polish (bu-oz2bd).*
