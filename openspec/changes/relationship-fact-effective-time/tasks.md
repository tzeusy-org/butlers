## 1. Draft contract

- [x] 1.1 Trace the complete baseline requirements, the current writer and migration, the original
      implementation bead, and all active `relationship-facts` deltas.
- [x] 1.2 Define exact columns, SQL types, constraints, bound semantics, precision vocabulary,
      normalization, compatibility, replay, correction, concurrency, approval, and reader behavior.
- [x] 1.3 Preserve every baseline clause and scenario name in both MODIFIED requirement bodies.
- [x] 1.4 Resolve explicit-null/omission semantics and the old/new writer index transition raised by
      exact-head review without enabling temporal effects in this draft.
- [x] 1.5 Resolve ordinary known-packet reassertion and inventory every production direct mutator,
      assigning occurrence-preserving behavior or a pre-write fence before temporal admission.

## 2. Draft verification

- [x] 2.1 Run strict named-change validation and the repository OpenSpec, overwrite, countability,
      citation, archive, duplicate-name, copy-inventory, and session-link guards on the exact draft.
- [ ] 2.2 Obtain independent exact-head semantic and PostgreSQL-schema review.
- [ ] 2.3 Record exact owner acceptance of the proposed contract. Draft authorship, review, and CI do
      not satisfy this task.

## 3. Future implementation owned by bu-h3b7t

- [ ] 3.1 Add a then-free expand migration with nullable temporal columns, checks, and the occurrence
      index while retaining `uq_ef_spo_active`; prove the deployed old-writer SQL still prepares and
      writes against that real PostgreSQL schema.
- [ ] 3.2 Deploy a transition writer using targetless conflict handling that canonicalizes the full
      presence/null matrix, preserves known packets on ordinary reassertion, freezes approval mode
      and base identity, and rejects temporal intent while the legacy index exists; prove omitted and
      explicit-null writes and approvals replay identically.
- [ ] 3.3 After proving exact old-writer absence, add a separate then-free cutover migration that
      removes the legacy index and enables temporal/repeated-period behavior only after the complete
      production mutator inventory is compatible or fenced, with the specified rollback refusal once
      temporal data exists.
- [ ] 3.4 Add the static production-DML inventory guard and implement the specified behavior/fence for
      owner bootstrap, entity merge, contact merge, SPO/hash lifecycle and verification, contact
      value edit, preferred-channel, entity forget, and explicit companion-entity cascades.
- [ ] 3.5 Add real-PostgreSQL scenarios for the actual old/new SQL transition; legacy rows;
      unknown/open/partial/coarse intervals; invalid packets; identical replay; correction to
      unknown and retry; ordinary known-packet provenance replacement; repeated periods; entity and
      contact merge collision/no-collision paths; ambiguous and exact lifecycle paths;
      preferred-channel fences; all-occurrence forget/cascade; and two truly concurrent CAS
      transactions whose loser rolls back fact, evidence, coverage, approval-context, and projection
      effects.
- [ ] 3.6 Preserve unchanged assertion-current read boundaries and execute the full Relationship
      migration chain through expand and cutover without reserving revision numbers in this draft.

## 4. Separately owned follow-ups

- [ ] 4.1 `bu-4ss0u` defines and implements predicate-cardinality and valid-period overlap
      enforcement over this representation.
- [ ] 4.2 `bu-1ypjo` defines and implements opt-in `as_of` MCP and REST reads. It must not retrofit an
      implicit effective-now filter onto existing readers.
