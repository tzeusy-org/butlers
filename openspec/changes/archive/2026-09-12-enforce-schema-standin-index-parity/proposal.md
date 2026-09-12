## Why

A hand-provisioned table stand-in exists so an integration test can have one
table from another butler's migration chain without running that chain. The
parity guard added by bu-r8opr diffs each stand-in's columns and CHECK
constraints against the table the real chain builds, so a widened column list
can no longer rot silently.

Indexes were left outside that diff and documented as outside it. That
documentation described a real hole. `approvals_013` creates a **unique**
partial index, `ux_pending_actions_active_deduplication_key`, and a unique
index is not decoration: it is what makes a second active action with the same
deduplication key fail. A stand-in without it produces a table that accepts
writes the real schema rejects — and the guard reports green while it does.
At the time this change was proposed, each of the four existing registered
stand-ins was missing at least one index its chain has; between them, thirteen.
This change also extends the registry to mechanically adoptable relationship
tables whose migration chain owns a non-default schema.

The same shape has already been paid for once. The column gap cost five of the
nine failures on PR #3853, none of which pointed at the DDL.

## What Changes

- A stand-in SHALL declare the indexes its migration chain creates, and the
  parity guard SHALL diff them against the real table's catalogue entries in
  both directions.
- Mechanically adoptable relationship tables SHALL use one shared stand-in
  definition, and a chain that owns a schema SHALL record the chain/schema pair
  used by the parity fixture to materialise the real table.
- The guard's index arm SHALL itself be covered by a test that fails when the
  arm is removed, so the arm cannot silently become a no-op — a guard nobody
  has watched go red is indistinguishable from one that cannot go red, which is
  the exact defect being closed.
- Foreign keys SHALL stay excluded, and the reason SHALL stay documented:
  `pending_actions` and `approval_rules` reference each other through a
  DEFERRABLE constraint, so mirroring foreign keys would leave neither table
  independently creatable, defeating the point of a stand-in.
- Triggers SHALL stay excluded for the same structural reason, now stated
  rather than implied.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `testing`: gains one additive requirement covering complete stand-in parity,
  including indexes, schema-owning chains, and the deliberate foreign-key and
  trigger exclusions. No existing requirement changes.

## Impact

- `src/butlers/testing/schema_standins.py`: `TableStandin` gains `indexes` and
  `chain_schemas`; `ddl()` emits the table plus its indexes as one
  `;`-separated script. All eight registered stand-ins declare their chain's
  indexes, and the relationship-owned definitions record their migration
  schema.
- `tests/config/test_schema_standin_parity.py`: diffs `pg_indexes`, migrates
  schema-owning chains under their declared `search_path`, and adds can-fail
  coverage for both index and chain/schema metadata drift.
- `tests/modules/conftest.py` and
  `roster/relationship/tests/test_entity_dedup_curation_job.py` drop the
  hand-written index DDL the stand-in now carries.
- Relationship test fixtures replace mechanically adoptable copies with the
  shared relationship stand-ins; historical migration fixtures retain explicit
  exemptions with their reasons.

## Deferred

- Trigger parity. Three of the four triggers on these tables
  (`approvals_008`/`009`/`011`) are dropped foreign keys reimplemented as
  plpgsql guards that read the *sibling* table unqualified, so mirroring them
  into a stand-in's own schema would either fail to create or validate against
  whichever table `search_path` reached first — a false green. They inherit the
  foreign-key exclusion. The one self-contained trigger, `approvals_001`'s
  append-only guard on `approval_events`, stays beside the `ddl()` call in
  `tests/modules/conftest.py`. Covering triggers honestly needs the trigger
  *function bodies* compared too, which is a separate contract from this one.
