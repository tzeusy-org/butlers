## 1. Index parity

- [x] 1.1 Demonstrate the gap red first: diff `pg_indexes` in the parity guard
  before any stand-in declares an index, and record the thirteen missing
  indexes it names across the four stand-ins.
- [x] 1.2 Give `TableStandin` an `indexes` field and emit it from `ddl()` as
  one `;`-separated script both asyncpg and psycopg2 execute in a single call.
- [x] 1.3 Declare every chain index on all four registered stand-ins, and
  confirm the guard goes green.

## 2. Guarding the guard

- [x] 2.1 Add `test_the_index_diff_can_fail`, which diffs a deliberately
  blinded copy of `PENDING_ACTIONS` and asserts the missing unique partial
  index is reported.
- [x] 2.2 Verify that test fails when the index arm is removed from the diff.

## 3. Exclusions and cleanup

- [x] 3.1 Restate the foreign-key exclusion and add the trigger exclusion, each
  with its structural reason, in the `schema_standins` module docstring.
- [x] 3.2 Delete the hand-written index DDL the stand-in now carries from
  `tests/modules/conftest.py` and
  `roster/relationship/tests/test_entity_dedup_curation_job.py`.
