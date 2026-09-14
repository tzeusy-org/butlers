## 1. Migration serialization and Health repair

- [x] 1.1 Rebase the Health repair behind the current core head and verify a
  single Alembic core head.
- [x] 1.2 Keep the historical Health reclassification/categorized catalog
  purge guarded, idempotent, and limited to the four clinical predicates.
- [x] 1.3 Extend migration regression coverage for the serialized revision and
  the preserved measurement boundary.

## 2. Server-held local read enforcement

- [x] 2.1 Pass the module-held read policy into every public local retrieval
  closure without exposing a caller authority argument.
- [x] 2.2 Apply the allowed-sensitivity predicate to the atomic retrieval
  mutation so denied UUIDs have no observable reference side effect.
- [x] 2.3 Add unit and real-Postgres regression coverage for denied and
  authorized direct retrieval.

## 3. Context budget discipline

- [x] 3.1 Reserve fixed context text and the Profile Facts withheld receipt
  before fitting variable fact lines.
- [x] 3.2 Add saturated and small-budget regressions proving no section or
  total-budget overflow and no excluded-content exposure.

## 4. Verification and review closure

- [x] 4.1 Validate the OpenSpec change strictly and run targeted memory,
  Health, and migration tests plus relevant static guards.
- [x] 4.2 Rewrite PR commit-message PII trailers, push the exact rebased head,
  and close the two review threads with compliant terminal replies.
- [ ] 4.3 Obtain fresh independent exact-head alignment review and hosted CI;
  do not merge in this review lane.
