# relational-edges-single-home

## Why

The relationship graph has **two homes**, and entity-to-entity edges are landing
in the one that the graph-shaped consumers cannot read.

`relationship.entity_facts` is declared the *canonical RDF registry* for both
contact and relational predicates (`relationship-facts` spec, Requirement:
Relationship entity facts triple store) — it is registry-validated, weighted
(Dunbar), provenance-tracked (`relationship_assert_fact()` is the sole central
writer), and is the substrate the `/entities/concentration` page, Dunbar
tier-scoring, and graph traversal all read.

But the relationship butler's `fact-extraction` skill routes **every** edge —
`works_at`, `friend_of`, `sibling_of`, `child_of`, `member_of` — through
`memory_store_fact(object_entity_id=…)` into the memory module's
`relationship.facts` table instead (skill §"Canonical fact-store boundary" and
§"Extract and Store Edge-Facts"). The boundary clause sends contact predicates
and "any future *contact* predicate" to `entity_facts`, but lumps relational
edges into "non-registry edge-facts → memory facts" — so registry-relational
predicates (`friend-of`, `parent-of`, `child-of`, `colleague-of`) fall through
the crack. The skill's underscore vocabulary (`friend_of`) does not literally
match the registry's hyphenated names (`friend-of`), which is how it self-
classifies as "non-registry".

Verified on the live dev database (2026-06-16):

- `relationship.entity_facts`: **0** relational rows of any validity (879
  contact, 14 state). The concentration page renders empty for all 11 relational
  predicates.
- `relationship.facts`: **62** edge-facts (`object_entity_id` set) —
  `works_at` (22), `member_of` (5), `child_of` (3), `parent_of` (3), plus 25+
  one-off narrative predicates (`planned_dinner_with`, `wake_coordination`,
  `social_exchange_with`). The relationship graph data exists; it is in the wrong
  store under a free-form vocabulary that cannot be aggregated.

This is a doctrine-correcting change. `entity_facts` already *is* the canonical
relationship registry; this change makes the write path obey that contract,
gives the memory store a sharp, enforced exclusion, and backfills the graph from
data already captured.

A second, smaller cleanup rides along: `relationship.quick_facts` (a legacy
per-contact `key`/`value` KV table, **0 rows**, superseded by `entity_facts`)
is still wired into vCard ORG/TITLE import/export and a backfill script. It is
deprecated and removed; `public.contacts` already carries `company`/`job_title`
columns to absorb its only live use.

## What Changes

- **MODIFIED (`relationship-facts`) — predicate catalog.** Add `works-at` and
  `member-of` to the relational predicate family (`object_kind='entity'`,
  person→organization). Reaffirm the catalog is extensible via registry seed
  only. Define the canonical-vs-narrative discriminator for entity-to-entity
  edges.
- **NEW (`relationship-facts`) — single home for registry-relational edges.**
  Any edge whose meaning is a registry-relational predicate MUST be written to
  `relationship.entity_facts` through `relationship_assert_fact(object_kind=
  'entity')`. The memory `{schema}.facts` store MUST NOT be the home for
  registry-relational edges, and the relationship graph (concentration, Dunbar,
  neighbors) MUST be materialized from `entity_facts` only.
- **MODIFIED (`module-memory`) — memory edge-fact scope.** The memory store's
  `object_entity_id` edge-facts are for **non-registry, narrative** relationships
  only (episodic/coordination context such as `planned_dinner_with`). Asserting a
  registry-relational predicate (or a known underscore alias of one) via
  `memory_store_fact()` MUST be rejected with a `ValueError` directing the caller
  to `relationship_assert_fact()`, mirroring the existing identity-contact
  carve-out.
- **NEW (`relationship-facts`) — fact-extraction skill contract.** The
  relationship butler's `fact-extraction` skill MUST route registry-relational
  edges through `relationship_assert_fact()` with hyphenated registry predicate
  names, and a contract test MUST assert the skill's edge vocabulary is a subset
  of the relational registry (or explicitly classified narrative).
- **NEW (`relationship-facts`) — one-time backfill.** A guarded, idempotent,
  dry-run-default backfill maps the existing memory edge-facts onto registry
  predicates where they map (`child_of`→`child-of`, `parent_of`→`parent-of`,
  `friend*`→`friend-of`, `works_at`→`works-at`, `member_of`→`member-of`),
  re-asserts them via the central writer, and retracts the migrated memory copies
  to avoid dual presence. Unmappable narrative edges are left untouched in memory.
- **NEW (`relationship-facts`) — deprecate `relationship.quick_facts`.** Migrate
  vCard ORG/TITLE to `public.contacts.company`/`job_title`, drop the
  `quick_facts` read/write helpers and backfill branch, and drop the table in a
  guarded migration (snapshot + zero-row assertion before `DROP`).

## Impact

- **Specs:** `relationship-facts` (MODIFIED + ADDED), `module-memory` (MODIFIED).
- **Code:**
  - `roster/relationship/migrations/` — registry seed (`works-at`, `member-of`);
    guarded `quick_facts` drop.
  - `roster/relationship/.agents/skills/fact-extraction/SKILL.md` — boundary
    rewrite + edge-fact routing to `relationship_assert_fact()`.
  - `src/butlers/modules/memory/` (`__init__.py` / `storage.py`) — reject
    registry-relational predicates in `memory_store_fact()`.
  - `roster/relationship/tools/relationship_assert_fact.py` — alias map for
    underscore→hyphen relational predicate names (optional ingest convenience).
  - `roster/relationship/tools/vcard.py`, `src/butlers/scripts/backfill_facts.py`
    — drop `quick_facts` usage; route ORG/TITLE to `public.contacts`.
  - New backfill script under `src/butlers/scripts/` (dry-run default).
- **Data:** 62 memory edge-facts re-homed into `entity_facts` where mappable;
  `relationship.quick_facts` dropped (0 rows).
- **User-visible:** `/entities/concentration` populates from real data; Dunbar
  tier-scoring and entity-neighbor views begin reflecting relational edges.
- **No breaking API contract change.** All endpoints already read `entity_facts`;
  this fills it. Memory `{schema}.facts` narrative reads are unaffected.

## Source References

- Non-Negotiable Rule 5 (git-based config is the source of truth) — applied by
  analogy: one canonical store per kind of fact; no silent second home.
- `relationship-facts` spec (Requirement: Relationship entity facts triple store;
  Requirement: Predicate catalog; Requirement: Central writer).
- `module-memory` spec (Requirement: Storage layer CRUD operations).
- RFC 0004 (identity and contact resolution); RFC 0006 (schema isolation);
  RFC 0013 (Dunbar weight-at-query).
