## Why

The adopted `relationship-fact-effective-time` contract splits the uniqueness transition into two
schema acts. `rel_035` (merged in PR #4254) added the temporal columns and the occurrence index while
keeping `relationship.uq_ef_spo_active`, and the transition writer refuses temporal intent while that
index exists. The second act, symbolic gate G, drops the legacy index and thereby enables temporal and
repeated-period writes. The contract allows it "only after every Relationship instance is proven to
contain the complete compatible/fenced mutator inventory, and every old image is proven absent".

The first draft of this change answered that with a root-held Ed25519 signer, signed content-blind
receipts, an isolated test runtime and a Docker Compose lifecycle fence. Three weeks of work on it
produced no G: the signer was never adopted, the fence was Compose-only while dev runs on k3s, the
Kubernetes extension (`extend-relationship-cutover-kubernetes`) stayed unadopted, and the
fresh-database exemption (`bu-2z6jyb`) was never delivered.

On 2026-10-20 the owner answered `bu-4awqyt` with choice B: a plain hard cutover. Butlers is a
single-owner system and Relationship owns its schema, so every writer of `relationship.entity_facts`
ships in the Relationship release. The database itself can prove no old writer is present: G takes an
exclusive no-wait lock and refuses while any other client is connected, and the rollout stops every
database client before the migration, accepting whole-assistant downtime.

Actual `rel_036` is the existing meeting-debrief migration, not the cutover. It remains untouched.
G denotes a then-free revision after the actual then-current Relationship head; it reserves no number.

## What Changes

- Amend the `relationship-effective-time-cutover` source proposal to a plain hard cutover:
  - drop the signer, signed receipt, receipt verifier, wrapper-produced test receipt, isolated test
    runtime, Compose instance proof, lifecycle fence, gated-revision ceiling, receipt x-argument and
    fresh/disposable admission requirements;
  - keep the compatible-mutator inventory and require it clean, with the named real-PostgreSQL tests
    passing, at the commit that carries G;
  - keep the no-old-writer rule, enforced by G's lock-and-refuse check and by stopping every
    database client before the migration, and require a real-PostgreSQL test that G refuses while
    another client is connected;
  - make G an ordinary Relationship revision that fresh and CI databases traverse like any other;
  - keep the rollback boundary at the first temporal write, with no flattening escape;
  - state how this satisfies the adopted parent's old-images-absent precondition.
- Rewrite the operator notes `docs/operations/relationship-effective-time-cutover.md` as hard-cutover
  rollout notes.
- Archive `extend-relationship-cutover-kubernetes` unadopted; its deltas are not applied to
  `openspec/specs`.

## Capabilities

### New Capabilities

- `relationship-effective-time-cutover`: hard-cutover authorization, mutator inventory, lock-and-refuse
  check, ordinary G revision, rollback boundary, and ownership for the Relationship legacy-index drop.

### Modified Capabilities

None. The adopted `relationship-facts` bodies in `relationship-fact-effective-time` are referenced,
not restated or overwritten.

## Impact

- Specification and documentation only. No G code, migration execution, deployment, client stop or
  start, database write or temporal activation occurs in this change.
- `bu-h3b7t` retains implementation ownership of G and its tests. Rolling out G stays an owner act.
- `bu-2z6jyb` (fresh-database receipt exemption) is superseded: no receipt exists to exempt from.
  CLOSED `bu-ftd491` answer A governed that exemption and is moot under B. CLOSED `bu-ldcp5f`
  remains applied by PR #4317.
- Temporal writes remain refused `temporal_cutover_pending` until G is applied.

## Acceptance Status

This remains an unimplemented source proposal. The amended text conforms to the owner's recorded
`bu-4awqyt` answer B. Merging it implements nothing and authorizes no rollout.
