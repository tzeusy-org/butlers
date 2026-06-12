# Entity v3 — Lifecycle Semantics and View Depth

## Why

The v1/v2 entity redesign shipped the routes and visual language but not the depth: workbench mode is a stub, provenance fields are fetched but never rendered (violating the existing `dashboard-relationship` "Provenance contract" requirement, spec.md:975), no merge-*review* flow exists despite the queue surfacing duplicate candidates (the bare merge endpoint ships without any compare step), and the entity data lifecycle — how ingested signals become entities, how observations match existing entities, how facts are looked up and age — has no first-class specification. Entities are the core value surface of Butlers (owner quick-refresh + butler contextual lookup, per `docs/redesigns/2026-06-12-entity-brief-v3.md` §0, binding); without defined lifecycle semantics the views have nothing deep to render and butlers have nothing reliable to query.

## What Changes

- **New lifecycle capability**: ingest → match → assert → look up → age semantics as normative spec — deterministic matching rules, queue bucket derivation, supersession, read-time staleness bands with `observed_at` fallback precedence, and the canonical fact-store layering (`relationship.entity_facts` = identity/contact/relational triples; memory-module `facts` = narrative facts/episodes). Note: this layering deliberately supersedes the brief's Phase D drift-3 reconciliation proposal (which suggested the memory-module table as canonical); the deviation is grounded in the standing `relationship-facts` schema-boundary requirement and recorded in design.md D1.
- **New butler-facing lookup contract**: `relationship_lookup` MCP tool — read-only, in-session-only (no new spawn triggers or schedules may exist to feed it), docstring ≤300 tokens. Codifies brief Phase D amendment 1.
- **New merge-review capability**: `POST /api/relationship/entities/compare` structural diff (no model-generated scoring/ranking/prose — guardrail-tested), `relationship.merge_reviews` audit table, single-pair review UX from queue, workbench, Index gutter, and detail `m`. Free-form bulk merge remains rejected.
- **`relationship.entity_facts` schema delta**: additive `observed_at TIMESTAMPTZ NULL` + `metadata JSONB NULL` columns with backfill; `conf` declared immutable assertion-time certainty (merge conflict-resolution depends on it); staleness is computed at read time, never stored — **no confidence decay** (brief Phase D amendment 3).
- **Switchboard invariant**: switchboard's access to `relationship.entity_facts` is read-only channel resolution via `resolve_contact_by_channel()` (already mandated by `relationship-facts` spec); it never writes facts — assertion happens only inside routed domain-butler sessions. Guardrail source-scan test. (Amendment 4, corrected against spec :143.)
- **Memory-module boundary**: identity-contact triples are never stored in the memory-module `facts` table; narrative facts/episodes never in `entity_facts`. Resolves the two-fact-stores ambiguity (brief open question 11).
- **View-depth completion** (extends existing `dashboard-relationship` requirements): Workbench actual 3-rail build-out (raw triples view, confidence/staleness inspector, duplicate warning panel), 90-day activity sparkline via binned `/activity` (chronicler boundary stays MCP-only), provenance rendering on detail/workbench/concentration, concentration weight bars + footer KPIs + row drill, hop/columns weight ranking + per-predicate top-N via `/neighbours` extension, finder preview pane + Tab-to-hop + empty-query owner-pinned set, index bulk-select gutter, Index toolbar search wired to the search endpoint, queue evidence drill, delta-since-last-visit (`entity_view_marks` + view-mark/delta endpoints), latest-interactions-per-channel quick-refresh block, keyboard maps on index/hop/columns/detail.

Non-goals (binding rejections, brief §0): no LLM calls per page render; no LLM-driven matching even in background; no free-form bulk merge; no card layouts; no generated-prose affordances ("summarize the merge", "narrate the delta", "explain the score").

## Capabilities

### New Capabilities

- `relationship-entity-lifecycle`: normative semantics for the entity data lifecycle — ingest (who creates entities/facts, with what provenance), match (deterministic duplicate/unidentified/stale derivation), assert (central writer, idempotency, supersession, immutable confidence), look up (frontend search + switchboard resolution + butler MCP lookup layering), age (staleness bands, `observed_at` fallback, backfill). Declares the canonical fact-store layering.
- `relationship-entity-lookup`: the `relationship_lookup` MCP tool contract — request/response shape, provenance+recency payload, read-only and in-session-only constraints, cost gates, docstring budget.
- `relationship-merge-review`: single-pair merge-review — compare endpoint contract (structural diff only), `relationship.merge_reviews` audit table, review outcomes (merged / dismissed), entry points (queue card, workbench panel/hint, Index gutter at exactly-2, detail `m`), no-LLM guardrail.

### Modified Capabilities

- `relationship-facts`: additive `observed_at` + `metadata` columns on `relationship.entity_facts`; `conf` immutability requirement; read-time staleness derivation; explicit layering statement vs the memory-module `facts` table.
- `dashboard-relationship`: deepen existing entity-view requirements — Workbench mode becomes a real layout requirement (currently a toggle-only requirement, spec.md:772), provenance contract extended from "API carries origin" to "UI renders origin", concentration/hop/columns/finder/index depth requirements, delta-since-last-visit endpoints, keyboard maps.
- `switchboard-identity`: add the never-writes-`entity_facts` invariant + guardrail test requirement.
- `module-memory`: add the store-boundary requirement (no identity-contact triples in memory `facts`; narrative facts stay out of `entity_facts`).

## Impact

- **Backend**: `roster/relationship/api/router.py` (compare, view-mark/delta-facts, facts drill, `/activity` binning param, `/neighbours` ranking params), new MCP tool in relationship toolset, Alembic migrations for `observed_at`/`metadata`/`entity_view_marks`/`merge_reviews` (relationship schema only; no `public.*` changes), backfill script, guardrail tests (switchboard source-scan, no-LLM source-scan, staleness fallback).
- **Frontend**: `frontend/src/pages/EntityDetailPage.tsx` (workbench build-out, sparkline, provenance, delta banner, k/j), `frontend/src/components/relationship/{EntitiesIndexPage,HopPage,ColumnsPage,ConcentrationPage}.tsx`, `frontend/src/components/layout/EntityFinder.tsx`, component extraction (EntityMark/Row/TierBadge/StateDot).
- **Cross-butler**: chronicler untouched in code (aggregation stays MCP); switchboard gains an invariant test, no behavior change; memory module gains a boundary statement, no behavior change.
- **Docs**: relationship MANIFESTO one-line amendment (confidence vs staleness axes); brief `docs/redesigns/2026-06-12-entity-brief-v3.md` is the binding intent source.
