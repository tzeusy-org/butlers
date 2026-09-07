## 1. Draft contract

- [x] 1.1 Trace the complete baseline requirements, the current writer and migration, the original
      implementation bead, and all active `relationship-facts` deltas.
- [x] 1.2 Define exact columns, SQL types, constraints, bound semantics, precision vocabulary,
      normalization, compatibility, replay, correction, concurrency, approval, and reader behavior.
- [x] 1.3 Preserve every baseline clause and scenario name in both MODIFIED requirement bodies.

## 2. Draft verification

- [x] 2.1 Run strict named-change validation and the repository OpenSpec, overwrite, countability,
      citation, archive, duplicate-name, copy-inventory, and session-link guards on the exact draft.
- [ ] 2.2 Obtain independent exact-head semantic and PostgreSQL-schema review.
- [ ] 2.3 Record exact owner acceptance of the proposed contract. Draft authorship, review, and CI do
      not satisfy this task.

## 3. Future implementation owned by bu-h3b7t

- [ ] 3.1 Add the then-free Relationship migration with nullable temporal columns, checks, and
      occurrence-scoped active uniqueness; prove upgrade, idempotent replay, fail-closed downgrade,
      and full chain execution against real PostgreSQL.
- [ ] 3.2 Extend the central writer and MCP wrapper with normalization, explicit occurrence identity,
      compare-and-swap correction, approval replay, evidence carry-forward, and atomic rollback.
- [ ] 3.3 Add real-PostgreSQL scenarios for legacy rows, unknown/open/partial/coarse intervals,
      invalid packets, identical replay, repeated periods, correction, concurrent correction, and
      unchanged assertion-current read boundaries.

## 4. Separately owned follow-ups

- [ ] 4.1 `bu-4ss0u` defines and implements predicate-cardinality and valid-period overlap
      enforcement over this representation.
- [ ] 4.2 `bu-1ypjo` defines and implements opt-in `as_of` MCP and REST reads. It must not retrofit an
      implicit effective-now filter onto existing readers.
