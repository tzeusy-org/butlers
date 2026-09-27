# Tasks — relational-edges-single-home

This change amends `relationship-facts` and `module-memory` and is carried by one
beads epic (`relational-edges-single-home`). Dependency order below; the skill
rewrite and the memory-writer guard MUST land together (with the contract test)
so the boundary cannot silently re-diverge.

## Track A — Registry + writer (foundation)

- [x] A1 — Alembic migration: seed `works-at` and `member-of` into
  `relationship.entity_predicate_registry` (relational family, `object_kind='entity'`).
  (spec: Predicate catalog → "Person-to-organization edges are registered as relational")
- [x] A2 — Add the underscore→hyphen alias map for relational predicates in
  `relationship_assert_fact.py` (ingest convenience; registry stays hyphenated).
- [x] A3 — Tests: registry seed present; `assert_fact(predicate='works-at',
  object_kind='entity')` inserts an active row; alias map resolves underscores.

## Track B — Boundary enforcement (skill + memory guard + contract test) — land together

- [x] B1 — Rewrite `roster/relationship/.agents/skills/fact-extraction/SKILL.md`:
  route registry-relational edges through `relationship_assert_fact()` (hyphenated
  names); reserve `memory_store_fact(object_entity_id=…)` for narrative edges; state
  the canonical-vs-narrative discriminator explicitly.
  (spec: "Fact-extraction skill routes relational edges to the central writer")
- [x] B2 — Guard `memory_store_fact()` in `src/butlers/modules/memory/`
  (`__init__.py`/`storage.py`): reject registry-relational predicates and known
  underscore aliases with a `ValueError` pointing to `relationship_assert_fact()`.
  (spec: module-memory "Registry-relational edges are out of scope")
- [x] B3 — Contract test: every edge predicate in the skill resolves to a relational
  registry predicate (via alias map) or is on the documented narrative allowlist.
  (spec: "Skill edge vocabulary stays a subset of the registry")
- [x] B4 — Tests for B2: registry-relational predicate rejected; narrative edge
  (`planned_dinner_with`) still stored and visible via `memory_entity_neighbors`.

## Transferred landed slice — B5/B6 only

PR #3728 landed B5/B6 before this carrier could complete. They are no longer
tasks or delta authority in this change: their observed canonical contract and
test mapping live in `openspec/specs/module-memory/spec.md` and
`landed-b5-b6-transfer.md`. Tracks A-E themselves landed in June 2026 (epic
bu-5vpyh; PRs #2392, #2395, #2398, #2404, #2411, #2413) and were ticked from that
evidence on 2026-09-27 (bu-60pwv6.3).

## Track C — Backfill (blocked by A1, B2)

- [x] C1 — New `src/butlers/scripts/` backfill: read `relationship.facts` edge-facts,
  classify via alias map, re-assert mappable edges via the central writer, retract
  migrated memory copies, leave narrative untouched. Dry-run default; idempotent;
  per-predicate count summary (no silent truncation).
  (spec: "One-time backfill of memory edge-facts into entity_facts")
- [x] C2 — Tests: dry-run writes nothing; apply re-homes a mappable edge exactly once
  and retracts the source; re-run is a no-op; narrative edge left in place.
- [x] C3 — Run the backfill against the live dev DB (dry-run → review mapping plan →
  apply); record counts moved / retracted / left-narrative in the bead.

## Track D — quick_facts deprecation (independent; parallel with A–C)

- [x] D1 — Route vCard ORG/TITLE in `roster/relationship/tools/vcard.py` to
  `public.contacts.company`/`job_title`; remove `quick_facts` read/write helpers if
  unused elsewhere. (spec: "vCard ORG/TITLE round-trips via public.contacts")
- [x] D2 — Remove the `_backfill_rel_quick_facts` branch from
  `src/butlers/scripts/backfill_facts.py`.
- [x] D3 — Self-guarding Alembic migration: assert `relationship.quick_facts` is empty,
  then `DROP TABLE`; refuse + report if non-empty. (spec: "Drop is gated on zero rows")
- [x] D4 — Update vCard round-trip tests to assert `public.contacts` is the source and
  `quick_facts` is untouched.

## Track E — Verification

- [x] E1 — Targeted pytest for touched areas (registry, memory guard, backfill, vcard);
  ruff check/format on touched files.
- [x] E2 — Manual: confirm `/entities/concentration` populates after backfill (the
  original symptom) on the dev stack.
- [x] E3 — `openspec validate relational-edges-single-home --strict` passes; archive
  the change after merge.
