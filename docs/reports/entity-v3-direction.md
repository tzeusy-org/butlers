# Direction Report — Entity v3 Lifecycle Semantics and View Depth

**Date:** 2026-06-12 · **Mode:** work decomposition with upstream brief (focused run; full baseline lives in the cited artifacts)
**Upstream input:** `docs/redesigns/2026-06-12-entity-brief-v3.md` (binding Section 0 + five Phase D amendments)
**Changeset:** `openspec/changes/entity-v3-lifecycle-and-depth/` (PR #2165) · **Beads:** epics `bu-89993` (backend), `bu-ehc1s` (frontend)

## Executive summary

Butlers' entity surface exists so the owner — directly, and through every butler — holds deep, provenance-backed knowledge of the people, vendors, companies, and places in their life. The v1/v2 redesign shipped the geography of that promise (six routes, the Dispatch language, a triple store) but not its substance: the lifecycle that turns ingested signals into trustworthy, queryable, aging knowledge was never specified, and the views render the population rather than the knowledge.

Alignment today is strong at the storage and matching layers (deterministic matching, central writer, provenance columns all shipped) and weak at the consumption layers: the biggest gap is that **nothing reads the knowledge** — provenance fields are fetched and never rendered, butlers can write facts but have no read contract, workbench mode is a stub, and no quick-refresh affordances exist. The biggest strength is that v3 requires almost no new machinery: every gap closes with deterministic reads over data that already exists.

Highest-priority next work, in order: (1) land the spec changeset (PR #2165) — it is the source of truth for everything else; (2) execute the backend epic `bu-89993`, whose ready frontier is the schema migration (`bu-mxxjy`) and the cross-butler guardrails (`bu-odlcq`); (3) execute the frontend epic `bu-ehc1s` once backend contracts exist.

## Phase outcomes and reconciliation reporting

| Phase | Tier | Passes | Outcome |
|---|---|---|---|
| 1 — Doctrine alignment | verify | 1 | All seven feature groups aligned with amendments; canonical-store question adjudicated to layering (identity triples in `relationship.entity_facts`, narrative in the memory-module `facts` table) |
| 2 — Changeset authoring | change | 5 (R1–R5; ceiling 6) | R1: 14 findings · R2: 10 · R3: 11 · R4: 4 · R5: **pass**. 39 findings fixed; `openspec validate --strict` green |
| 3 — Beads graph | verify | 1 (+ mechanical: cycle check, mandate coverage 34/34, spec-link coverage) | **Pass**; 3 low findings, 2 fixed (conflict-prevention edges, recon report note), 1 accepted (deliberate FE-behind-BE serialization) |

Notable adjudications made during reconciliation (all recorded in `design.md` / the deltas):
- The brief's Phase D drift-3 proposal (memory store canonical) was **deliberately superseded** by the layering decision — grounded in the standing `relationship-facts` schema-boundary requirement and the shipped switchboard resolution path.
- The brief's amendment 4 ("switchboard reads `public.*` only") was **corrected against the standing spec**: switchboard's mandated read of `relationship.entity_facts` via `resolve_contact_by_channel()` stays; the invariant is *never writes facts*.
- Confidence decay was rejected and replaced with immutable `conf` + read-time staleness, because merge conflict-resolution (`entities.py:834-837`) keys on `conf`.

## Work plan (generated graph)

**Backend epic `bu-89993`** — `bu-mxxjy` (migration + backfill) → `bu-4mh9a` (writer + staleness + conf guardrail) → {`bu-9wcxm` compare/merge-review, `bu-vqy9j` relationship_lookup, `bu-tzvm6` drill/ranking, `bu-bjvny` binning/delta/core-dates}; `bu-odlcq` (guardrails, parallel-safe); terminal `bu-6ivjj` (gen-1 recon + report).

**Frontend epic `bu-ehc1s`** (blocked by `bu-89993`) — `bu-ovq7t` (primitives) → {`bu-ly48x` workbench, `bu-pkmr8` compare-UI/queue/gutter, `bu-19u8r` quick-refresh, `bu-hks7e` hop/columns/concentration, `bu-rru9g` finder/toolbar} → `bu-8qfok` (e2e) → terminal `bu-5pm9n` (gen-1 recon + report).

Every bead cites its spec requirement paths; recon beads close with `/opsx:sync` + change archive.

## Do not do yet

| Item | Reason | Revisit when |
|---|---|---|
| Social-map refresh | Explicitly deferred (design Non-Goals); standing spec freezes it | After v3 ships; file as its own small change |
| Predicate-catalog authoring UI | Registry exists; UX undefined | When a new predicate is actually needed at runtime |
| Fact-store consolidation migration | Layering specified instead; migration is churn without user value | If layering proves leaky in practice (recon reports will show it) |
| Columns "+N more" side sheet | Bundle defers it; counts ship inert | Owner asks for it after using ranked columns |
| Any LLM-assisted matching/summarization on this surface | Binding §0 rejection; Phase D intent-conflict-red | Only via explicit Phase D re-entry |

---

## Conclusion

**Real direction**: Butlers is becoming an owner-scoped knowledge system whose entity layer is the contextual memory every butler consults before acting — deterministic, provenance-backed, and aging honestly.

**Work on next**: (1) merge PR #2165; (2) dispatch `bu-89993` starting at `bu-mxxjy` + `bu-odlcq`; (3) dispatch `bu-ehc1s` when the backend recon closes.

**Stop pretending**: the entity pages "show what butlers know" — until the frontend epic lands, they show rows while hiding the provenance, recency, and relationships that constitute the knowing.
