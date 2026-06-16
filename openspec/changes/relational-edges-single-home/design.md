# Design — relational-edges-single-home

## Context

Two fact stores exist by design and both should survive — they are
complementary, not redundant:

- **`relationship.entity_facts`** — the structured identity + relationship graph.
  Registry-validated predicates, single central writer, immutable confidence,
  supersession, weights, owner carve-out. No embeddings, no decay. Built for
  exact lookup and weighted aggregation (concentration, Dunbar, neighbors,
  ingress routing).
- **`{schema}.facts`** (memory module, per butler) — semantic/narrative memory.
  pgvector embeddings, decay/tiering, free-form auto-registered predicates,
  episodes. Built for recall and context injection.

The defect is not "too many stores" — it is an **unowned overlap**: entity-to-
entity edges can be expressed in both, and the extraction skill picks the wrong
one. The fix assigns the overlap a single owner and enforces the boundary on
both sides.

## The discriminator

The contested question is "which edges are structural (→ entity_facts) vs
narrative (→ memory)". The rule:

> An edge belongs in `entity_facts` iff its meaning is a **registry-relational
> predicate** — a durable, standing relationship type: kinship (`family-of`,
> `parent-of`, `child-of`, `partner-of`), social (`knows`, `friend-of`),
> professional (`colleague-of`, `works-at`, `member-of`), and the existing
> structural edges (`co-attended`, `purchased-from`, `subscribed-to`,
> `visited`). Everything else that happens to reference two entities —
> episodic coordination, one-off events (`planned_dinner_with`,
> `wake_coordination`, `social_exchange_with`) — is **narrative** and stays in
> memory `{schema}.facts`, where decay is appropriate and aggregation is not
> expected.

Memory edge-facts remain legal for narrative relationships (they still carry
`object_entity_id` so `memory_entity_neighbors` works), but they MUST NOT use a
registry-relational predicate name. The canonical relationship graph is read
**only** from `entity_facts`.

### Why entity_facts wins the overlap (alternatives rejected)

- **Repoint concentration/Dunbar at memory facts.** Rejected: you would have to
  rebuild predicate validation, weights, provenance, and supersession on top of
  a decaying, free-form store — i.e. reinvent `entity_facts`. The 25+ one-off
  predicates already in the 62 edges show the free-form path cannot support a
  weighted balance-sheet.
- **Merge the two tables.** Rejected: their schemas and access patterns are
  fundamentally opposed (decay+embeddings+narrative vs validated+weighted+
  structural). Merging forces one side to compromise. Two stores with a sharp
  boundary is the cheaper, clearer architecture.
- **Let decay apply to relationships.** Rejected: "Alice is Bob's sister" must
  not fade like a recall score — the same reasoning that already keeps
  identity-contact data out of memory.

## Predicate registry additions

Add to `relationship.entity_predicate_registry` (relational family,
`object_kind='entity'`):

| Predicate | Direction | Rationale |
|---|---|---|
| `works-at` | person → organization | 22 of 62 existing edges; durable employment edge; wanted by concentration/graph |
| `member-of` | person → organization | 5 existing edges; standing membership |

Underscore→hyphen alias map (ingest convenience; the registry stays hyphenated):
`works_at→works-at`, `member_of→member-of`, `friend_of→friend-of`,
`child_of→child-of`, `parent_of→parent-of`, `colleague_of→colleague-of`,
`family_of→family-of`, `partner_of→partner-of`. `sibling_of`/`married_to`
have no current registry entry — map `sibling_of`/`married_to` to `family-of`
and `partner-of` respectively (closest standing relationship), or leave
unmapped and narrative if semantics must be preserved (decided per-edge during
backfill review, logged).

## Backfill (one-time, dry-run default, idempotent)

New script under `src/butlers/scripts/` mirroring the safety posture of
`backfill_facts.py`:

1. Read memory edge-facts: `SELECT … FROM relationship.facts WHERE
   object_entity_id IS NOT NULL AND validity='active'`.
2. Classify each predicate via the alias map → (mappable registry predicate |
   narrative, leave).
3. For mappable rows: `relationship_assert_fact(subject=entity_id,
   predicate=<hyphen>, object=object_entity_id::text, object_kind='entity',
   src='backfill', conf=…, last_seen=…)`. Idempotent via the central writer's
   `(subject,predicate,object)` active-unique contract.
4. Retract the migrated memory copy (`memory_forget` / set `validity`) so the
   edge has exactly one home. Narrative rows are untouched.
5. `--dry-run` (default) prints the mapping plan and per-predicate counts; a
   summary logs how many edges moved, retracted, and were left narrative — no
   silent truncation.

## Enforcement points (so the two never re-diverge)

- **Skill rewrite** (`fact-extraction/SKILL.md`): the edge-fact section routes
  registry-relational edges through `relationship_assert_fact()` with hyphenated
  names; narrative edges keep `memory_store_fact(object_entity_id=…)`. The
  boundary block names the discriminator explicitly.
- **Memory writer guard** (`memory_store_fact`): reject a registry-relational
  predicate or known underscore alias with a `ValueError` pointing to
  `relationship_assert_fact()` (same shape as the existing identity-contact
  rejection).
- **Contract test**: every edge predicate used in the skill is a subset of the
  relational registry (or on an explicit narrative allowlist). This is the
  guard that would have caught the original drift.

## quick_facts deprecation

`relationship.quick_facts` (0 rows) is still read/written by `vcard.py` (ORG →
`company`, TITLE → `job_title`) and a `backfill_facts.py` branch. `public.contacts`
already has `company`/`job_title` columns.

1. Route vCard ORG/TITLE to `public.contacts.company`/`job_title`.
2. Remove the `quick_facts` backfill branch and the `fact_list`/`fact_set`
   helpers if they have no other callers.
3. Guarded migration: snapshot row count, assert `= 0`, then `DROP TABLE
   relationship.quick_facts` (refuse and report if non-zero — self-guarding per
   the project's destructive-drop convention).

## Risks & mitigations

- **Mis-mapping a narrative edge into the structural graph.** Mitigated by
  dry-run review of the mapping plan before apply, and the conservative default
  (leave-as-narrative when ambiguous, logged).
- **Skill change without writer guard re-introduces drift.** Both ship together;
  the contract test fails CI if they diverge.
- **vCard regression on ORG/TITLE.** Covered by vcard round-trip tests updated
  to assert `public.contacts` is the source.
