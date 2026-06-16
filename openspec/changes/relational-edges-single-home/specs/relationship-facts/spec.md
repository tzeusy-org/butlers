# relationship-facts

## MODIFIED Requirements

### Requirement: Predicate catalog

The set of valid predicates SHALL live in `relationship.entity_predicate_registry` (table seeded by
Alembic migration). Predicates MUST be grouped into families:

- **Contact predicates** (`object_kind='literal'`): `has-email`, `has-phone`, `has-handle`,
  `has-address`, `has-birthday`, `has-website`.
- **Relational predicates** (`object_kind='entity'`): `knows`, `family-of`, `partner-of`,
  `parent-of`, `child-of`, `colleague-of`, `friend-of`, `co-attended`, `purchased-from`,
  `subscribed-to`, `visited`, `works-at`, `member-of` (set extensible). `works-at` and
  `member-of` are person→organization edges.
- **Override predicates** (`object_kind='literal'`, JSON): `dunbar_tier_override` (per
  RFC 0013 weight-at-query decision and Phase 1 Amendment 6).

Predicate names in the registry are **hyphenated** (`friend-of`, not `friend_of`). The set is
extended ONLY by adding seed rows to `relationship.entity_predicate_registry`; no predicate ID
MAY be hardcoded outside `entity-model.ts` (frontend) and the registry seed (backend).

#### Scenario: Unknown predicate is rejected by the central writer
- **WHEN** `relationship_assert_fact()` is called with `predicate='has-feet'` (not in
  `relationship.entity_predicate_registry`)
- **THEN** the writer MUST raise a validation error before any DB write
- **AND** no row MUST land in `relationship.entity_facts`

#### Scenario: Predicate IDs are not hardcoded in component tree
- **WHEN** ripgrep is run for known predicate string literals (e.g. `'has-email'`,
  `'knows'`) across `frontend/src/components/relationship/`,
  `frontend/src/pages/entities/`, and `roster/relationship/api/`
- **THEN** the only allowed matches MUST be inside `frontend/src/lib/entity-model.ts`
  (frontend) or seed data for `relationship.entity_predicate_registry` (backend)

#### Scenario: Person-to-organization edges are registered as relational
- **WHEN** `relationship.entity_predicate_registry` is queried for the relational family
- **THEN** `works-at` and `member-of` MUST be present with `object_kind='entity'`
- **AND** `relationship_assert_fact(predicate='works-at', object_kind='entity', ...)` MUST
  insert an active row in `relationship.entity_facts`

## ADDED Requirements

### Requirement: Single home for registry-relational edges

Every entity-to-entity edge whose meaning is a **registry-relational predicate** SHALL be
written to `relationship.entity_facts` through the central writer
`relationship_assert_fact(object_kind='entity')`, and SHALL NOT be stored as the canonical
representation in the memory module's `{schema}.facts` table. The relationship graph — the data
read by `/entities/concentration`, Dunbar tier-scoring, and entity-neighbor views — SHALL be
materialized from `relationship.entity_facts` only.

A relationship between two entities is a **registry-relational edge** when it corresponds to a
predicate in the relational family of `relationship.entity_predicate_registry` (kinship, social,
professional, membership, employment, co-attendance, transactional). An edge that references two
entities but is **episodic or coordination context** (e.g. `planned_dinner_with`,
`wake_coordination`) is **narrative**, is not part of the canonical relationship graph, and MAY
live in memory `{schema}.facts` (see `module-memory`).

#### Scenario: Relational edge lands in entity_facts, not memory
- **WHEN** a relationship between two tracked entities matching a registry-relational predicate
  (e.g. "Alice is Bob's sister", "Carol works at Acme") is extracted
- **THEN** an active row MUST be written to `relationship.entity_facts` via
  `relationship_assert_fact(object_kind='entity')` with the hyphenated registry predicate
- **AND** the canonical edge MUST NOT be stored in `relationship.facts`

#### Scenario: Concentration reads the graph from entity_facts
- **WHEN** registry-relational edges exist as active rows in `relationship.entity_facts`
- **THEN** `GET /api/relationship/entities/concentration?pred=<p>` MUST return those edges
  aggregated by weight
- **AND** the result MUST NOT depend on any read from `relationship.facts`

### Requirement: Fact-extraction skill routes relational edges to the central writer

The relationship butler's `fact-extraction` skill SHALL instruct the runtime to write
registry-relational edges through `relationship_assert_fact()` using hyphenated registry
predicate names, and SHALL reserve `memory_store_fact(object_entity_id=…)` for narrative
(non-registry) edges only. A contract test SHALL assert that every edge predicate named in the
skill is either a member of the relational registry or on an explicit narrative allowlist.

#### Scenario: Skill edge vocabulary stays a subset of the registry
- **WHEN** the contract test scans `roster/relationship/.agents/skills/fact-extraction/SKILL.md`
  for edge predicates routed to `relationship_assert_fact()`
- **THEN** each MUST resolve (directly or via the underscore→hyphen alias map) to a relational
  predicate in `relationship.entity_predicate_registry`
- **AND** any predicate not so resolvable MUST appear on the documented narrative allowlist or
  the test MUST fail

### Requirement: One-time backfill of memory edge-facts into entity_facts

A one-time backfill SHALL migrate existing memory edge-facts (`relationship.facts` rows with
`object_entity_id` set) into `relationship.entity_facts` where their predicate maps to a
registry-relational predicate, leaving genuinely narrative edges untouched. The backfill SHALL
default to dry-run, be idempotent, and report counts moved / retracted / left-narrative with no
silent truncation.

#### Scenario: Dry-run is the default and emits the mapping plan
- **WHEN** the backfill script is run without `--apply`
- **THEN** it MUST print the per-predicate mapping plan and counts
- **AND** it MUST NOT write to `relationship.entity_facts` or modify `relationship.facts`

#### Scenario: Mappable edge is re-homed exactly once
- **WHEN** the backfill is applied to a memory edge-fact whose predicate maps to a registry
  predicate (e.g. `child_of`→`child-of`)
- **THEN** an active row MUST be asserted in `relationship.entity_facts` via the central writer
- **AND** the source memory edge-fact MUST be retracted so the edge has a single home
- **AND** re-running the backfill MUST NOT create a duplicate active row

#### Scenario: Narrative edge is left in memory
- **WHEN** the backfill encounters a memory edge-fact whose predicate has no registry mapping
  (e.g. `planned_dinner_with`)
- **THEN** the row MUST be left unchanged in `relationship.facts`
- **AND** the summary MUST count it under "left narrative"

### Requirement: Deprecate and remove `relationship.quick_facts`

The legacy `relationship.quick_facts` key/value table SHALL be removed. Its only live use —
vCard ORG/TITLE — SHALL be routed to `public.contacts.company`/`job_title`. The table SHALL be
dropped only by a self-guarding migration that asserts zero rows before dropping.

#### Scenario: vCard ORG/TITLE round-trips via public.contacts
- **WHEN** a vCard with ORG and TITLE is imported and then exported
- **THEN** ORG MUST persist to and read from `public.contacts.company`
- **AND** TITLE MUST persist to and read from `public.contacts.job_title`
- **AND** no read or write MUST touch `relationship.quick_facts`

#### Scenario: Drop is gated on zero rows
- **WHEN** the deprecation migration runs
- **THEN** it MUST assert `relationship.quick_facts` has zero rows before issuing `DROP TABLE`
- **AND** if any row exists the migration MUST refuse and report rather than drop

## Source References
- Non-Negotiable Rule 5 (git-based config is the source of truth) — one canonical store per kind of fact
- RFC 0004 (identity and contact resolution)
- RFC 0006 (schema isolation)
- RFC 0013 (Dunbar weight-at-query)
