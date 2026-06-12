# Design — Entity v3 Lifecycle Semantics and View Depth

## Context

The entity surface shipped in v1/v2 (closed epics `bu-lh4ol`, `bu-ao6uh`, `bu-uhjxr`, `bu-m8gb6`) with six routes, the Dispatch design language, and a triple store (`relationship.entity_facts`, spec `relationship-facts`). The v3 audit (`docs/redesigns/2026-06-12-entity-brief-v3.md`, binding Section 0 + five Phase D amendments) found: workbench is a stub (`frontend/src/pages/EntityDetailPage.tsx:2075` renders the generic "overview" archetype), provenance fields are returned by the API but unrendered (violating `dashboard-relationship` spec.md:975), matching is deterministic but has no merge-review surface, confidence is hardcoded `1.0` with no semantics (`src/butlers/modules/memory/storage.py:767`), butlers can write facts (`relationship_assert_fact`) but cannot read them programmatically, and no recency/delta machinery exists.

Constraints (binding): no LLM per page render; no LLM matching even in background; single-pair merge review only; no cards; deterministic everything on this surface; chronicler data only via MCP; schema isolation (cross-butler = MCP via switchboard).

## Goals / Non-Goals

**Goals**
- Normative lifecycle semantics (ingest → match → assert → look up → age) that every endpoint, tool, and view traces to.
- One unambiguous fact-store layering decision.
- Butler-facing read contract (`relationship_lookup`).
- Owner quick-refresh affordances: core dates, latest interactions, delta-since-last-visit, staleness signals.
- Finish the designed-but-unbuilt view depth (workbench, sparkline, keyboard, provenance rendering, merge review, finder preview).

**Non-Goals**
- Social-map refresh (deferred; tracked as follow-up).
- Predicate-catalog runtime extensibility UI (registry exists; authoring UX deferred).
- Columns "+N more" side sheet (counts shipped; sheet deferred per bundle prompts/03 §3.3).
- Any consolidation migration of the two fact stores (boundary is specified instead).
- Multi-user/tenant semantics (owner-only, v1 doctrine).
- Secondary keyboard chords from the bundle recipes (Index `n`, detail `e`/`Shift+Backspace`, workbench `t`/`?`, hop `1..9`) — deferred as polish; the specced maps cover the navigation spine.

## Decisions

### D1 — Fact-store layering: `relationship.entity_facts` = identity, memory-module `facts` = narrative

`relationship.entity_facts` is canonical for identity-contact and relational predicates (already declared, `relationship-facts` spec.md §"triple store" and §"Schema boundary": "ONE `relationship.entity_facts` table (NOT `memory.facts`)"; switchboard resolves against it; the v2 contacts migration landed rows into it). The memory-module `facts` table is canonical for narrative facts, edge-facts, and episodes (permanence/embedding/consolidation lifecycle, `entity-identity` spec.md §Dual-mode facts; merge conflict-resolution reads it, `src/butlers/modules/memory/tools/entities.py:834-837`).

*Alternatives rejected*: (a) consolidate into one table — different lifecycles (identity triples have no permanence/embedding; narrative facts have no predicate registry), large migration churn for zero user value now; (b) declare memory `facts` canonical for everything — contradicts `relationship-facts` §Schema boundary, the v2 migration, and switchboard resolution. **Note:** option (b) was the brief's Phase D drift-3 reconciliation proposal; D1 deliberately supersedes it (accepted deviation, grounded in the standing spec and shipped resolution path). Registry-predicate relationships always live in `entity_facts`; memory edge-facts cover non-registry narrative relationship records (`entity-identity` §Dual-mode facts). The memory-module table is named by identity, not schema prefix — it is the unqualified `facts` table in the mounting butler's schema (`relationship` in production).

*Consequences*: provenance display, compare, and `relationship_lookup` read **both stores with declared layering** (identity facts ranked above narrative for identity questions); merge coalesces contact triples from `entity_facts` and applies higher-conf-wins to narrative facts in memory `facts`.

### D2 — `conf` is immutable; staleness is a separate, read-time axis

`conf` stays assertion-time certainty, never mutated after write. Staleness is computed at read time: `staleness_days = now() - COALESCE(observed_at, last_seen, created_at)`; bands `fresh` ≤ 30d, `aging` ≤ 180d, `stale` > 180d (the existing 365-day queue bucket criterion is unchanged and derives from the same inputs). No stored decay.

*Alternative rejected*: stored confidence decay — silently flips merge winners (higher-conf-wins at `entities.py:834-837` would prefer a fresh low-quality extraction over a decayed owner-stated fact) and contradicts the explicit retract+replace correction model (`module-memory` spec, correction-driven retraction).

### D3 — `observed_at` additive column with deterministic backfill

Add `observed_at TIMESTAMPTZ NULL` + `metadata JSONB NULL` to `relationship.entity_facts`. Backfill once: `observed_at := COALESCE(last_seen, created_at)`. `last_seen` preferred because it reflects actual observation; `created_at` is assertion time. New writes stamp `observed_at` explicitly; the central writer defaults it to `now()` when the caller omits it.

### D4 — `relationship_lookup` MCP tool: read-only, in-session-only

Request: `entity_id` OR `entity_ref` (name/alias string, resolved with the same deterministic ranking as `/entities/search`). Response: entity header (canonical_name, type, aliases, tier, state) + active facts from both stores per D1, each with `{predicate, object, src, conf, verified, primary, observed_at, staleness_band}` + recency summary. Constraints carried into the spec as requirements: callable only from already-running sessions; **no cron/schedule/spawn trigger may exist whose purpose is feeding it**; docstring ≤ 300 tokens (it enters every mounting butler's tool inventory); guardrail scan for `relationship_lookup` in scheduled-task prompts.

*Alternative rejected*: a REST endpoint other butlers poll — violates MCP-only inter-butler doctrine.

### D5 — Merge review is a structural diff, server-computed, audit-logged

`POST /api/relationship/entities/compare {entity_a, entity_b}` returns per-store fact lists for both entities plus `shared` (identical predicate+object pairs — the duplicate evidence) and `divergent` (same predicate, different object). No scoring, no ranking, no recommendation text — enforced by a source-scan guardrail test (no model/spawner imports in the compare/merge code paths). Review outcomes persist to `relationship.merge_reviews (entity_a, entity_b, shared_facts, divergent_facts, outcome merged|dismissed, reviewed_at NOT NULL)` — rows written at commit time only. Dismissed pairs suppress the queue's duplicate-candidate card for that pair (re-raised only on new shared evidence).

### D6 — Delta-since-last-visit via `relationship.entity_view_marks`

One row per entity (owner-only system): `POST /entities/{id}/view-mark` upserts `marked_at`; `GET /entities/{id}/delta-facts` returns facts (both stores) changed since the mark, per the per-store timestamp expressions in the `dashboard-relationship` delta. Both endpoints owner-only. Rendered as a deterministic banner + row highlights; no generated narration.

### D7 — Sparkline data from `/activity` binning; chronicler stays behind MCP

Extend `GET /entities/{id}/activity` with `bins=daily&window=90d`; the relationship router keeps calling chronicler MCP tools (existing aggregator, `roster/relationship/api/router.py:6284`) and bins server-side. The existing no-chronicler-SQL guardrail test extends to the binning path. If MCP volume becomes a problem, the sanctioned escape is an RFC'd read-only view (RFC 0010), not quiet SQL. Rendering: custom SVG sticks per the design language (recharts rejected — chartjunk-free 90-stick bar is ~30 lines and honors "no axes, absent days at 4% opacity").

### D8 — `/neighbours` ranking server-side

Add `rank=weight` and `per_predicate=N` params returning top-N per predicate plus `remainder` counts. Hop and Columns consume the same extension (Columns keeps its chained per-column calls; each call is now bounded). Client-side ranking rejected: unbounded payloads on high-degree entities.

### D9 — Component extraction before depth work

`EntityMark`, `Row`, `TierBadge`, `StateDot` extracted to `frontend/src/components/ui/` first; workbench/provenance/queue work builds on them. Prevents a third copy of the inlined mark (already duplicated at `EntitiesIndexPage.tsx:473` and `HopPage.tsx:40`).

## Risks / Trade-offs

- [Backfill on a large `entity_facts` table locks rows] → additive nullable columns + batched `UPDATE ... WHERE observed_at IS NULL LIMIT n` loop; no table rewrite.
- [Two-store reads make compare/lookup slower] → both queries are per-entity, indexed by subject; acceptable for owner-scale data; measure before optimizing.
- [`relationship_lookup` becomes a spawn magnet over time] → requirement-level prohibition + source-scan guardrail in CI; re-entry to cost review required to lift.
- [Dismissed duplicate pairs hide real duplicates] → dismissal is pair-scoped and evidence-keyed; new shared values re-raise the card.
- [Keyboard maps collide with global shortcuts (Cmd-K, `/`)] → view-local handlers attach to focused list containers only; Finder retains global priority.
- [90-day MCP binning is slow for hot entities] → cached per-entity for the session via TanStack Query staleTime; server keeps existing aggregator timeout envelope.

## Migration Plan

1. Alembic (relationship chain): add `observed_at`, `metadata` to `relationship.entity_facts`; create `relationship.entity_view_marks`, `relationship.merge_reviews`. All additive — rollback = drop columns/tables.
2. Batched backfill script `observed_at := COALESCE(last_seen, created_at)` (operator-run, idempotent).
3. Central writer stamps `observed_at` on new asserts (default `now()`).
4. Endpoints + MCP tool land behind the existing owner-only authz (Amendment 12a/b precedent).
5. Frontend ships per-view; no flag needed (additive UI on existing routes).
6. Confirm target DB before running migrations: the live system is `butlers-db-dev` / `.env.dev` ("prod"/"dev" naming is reversed — see repo memory `butlers-db-host-topology`).

## Open Questions

- OQ1 (resolved in spec): the owner-pinned Finder empty-state set is direct-edge weight top-8 (`dashboard-relationship` delta, "Finder empty-query state"), matching bundle prompts/07 §7.1.
- OQ2: Detail `k/j` sibling scope when arriving from Hop/Columns — spec proposes "most recent list scope, Index default" with scope recorded in router state; cheap to revisit.
- OQ3 (resolved in spec): the facts drill exposes `superseded` rows behind the `validity=` param, default `active` (`dashboard-relationship` delta, "Facts drill endpoint").
