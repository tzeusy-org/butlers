# Tasks — relationship-memory-curation

Depends on `relational-edges-single-home` (single home, seeded registry, alias
map). Sequencing/prioritization is owned by `/th-projects` project-direction;
this list is the decomposition seed.

## Track A — Extraction emits edges + confidence gate

- [ ] A1 — Edit `roster/relationship/.agents/skills/fact-extraction/SKILL.md`:
  add the "standing relationship in prose → resolve-or-create entity + assert
  registry-relational edge" step, with the durable-vs-episodic discriminator.
  (spec: "Extraction emits structured edges from relational prose")
- [ ] A2 — Add the inferred-relationship confidence gate to the skill: inferred
  family relationships below the bar are proposed for confirmation, not written
  active; all inferred facts record confidence + provenance.
  (spec: "Inferred relationship facts pass a confidence gate")
- [ ] A3 — Tests: a prose fixture with a standing relationship yields an
  `assert_fact(object_kind='entity')` edge; an episodic fixture does not; a
  low-confidence inferred family fact is not written active.

## Track B — Backfill parked-write guard (bu-2ezvz)

- [ ] B1 — `src/butlers/scripts/backfill_edge_facts_to_entity_facts.py`: inspect
  the `AssertResult` outcome; retract the source only on a committed active write;
  on `pending_approval`, leave the source active and count it as "parked".
  (spec: "Re-home and backfill must not retract a parked write")
- [ ] B2 — Add a per-predicate `parked` counter to the summary; tests cover the
  parked-owner path and the committed-write path.

## Track C — Curation skill + schedule

- [ ] C1 — New `roster/relationship/.agents/skills/memory-curation/SKILL.md`: the
  four jobs, the autonomy boundary, the high-confidence criteria, reversibility,
  and the digest format. (spec: relationship-curation — all requirements)
- [ ] C2 — `[[butler.schedule]]` entry in `roster/relationship/butler.toml`:
  weekly `cron = "0 9 * * 1"`, `dispatch_mode="prompt"`, prompt invoking the skill
  and ending with an explicit `notify()` instruction.
  (spec: "Weekly curation pass")
- [ ] C3 — Verify the curator's required tools are available to the relationship
  butler session: `relationship_assert_fact`, `memory_entity_merge`/`entity_merge`,
  `contact_merge`, `memory_forget`, `entity_resolve`, `memory_search`, `notify`.

## Track D — Carry-over durable code (bu-kgh8g)

- [ ] D1 — Land the dev-only registry seeds + alias-map additions as code (this
  is `bu-kgh8g`, owned by `relational-edges-single-home`): migration seeding the
  triaged durable predicates, alias entries in both `_PREDICATE_ALIAS_MAP` copies.
  Referenced here as a dependency; track under that change, not this one.

## Verification

- [ ] `openspec validate relationship-memory-curation --strict` passes
- [ ] targeted `pytest` for the touched skill tests + backfill tests
- [ ] `ruff check` / `ruff format --check` on changed Python
- [ ] manual: dev curation dry-run produces a digest with proposals + auto-applied
  + expiring approvals, and the owner approval surface shows the parked edges
