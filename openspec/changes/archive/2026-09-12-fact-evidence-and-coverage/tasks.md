## 1. Schema

- [x] 1.1 Add `relationship.fact_evidence` with an append-only BEFORE UPDATE
  trigger, a `(fact_id, kind, ref)` unique index, and 512-character CHECK bounds
  on `ref`/`note`.
- [x] 1.2 Add `assert_origin`/`assert_session_id`/`assert_action_id` to
  `relationship.entity_facts` with an origin CHECK.
- [x] 1.3 Add `relationship.fact_coverage` keyed on `(subject, predicate, src)`.
- [x] 1.4 Add `relationship.fact_approval_context` and repair in-flight parked
  `relationship_assert_fact` rows: move `src`/`observed_at` out of `tool_args`
  and stamp `approval_action_id`.
- [x] 1.5 Grant the relationship role DML on all three tables; grant the
  connector role SELECT on coverage only, never on the evidence ledger.

## 2. Evidence packet

- [x] 2.1 Validate a packet before any write: typed references only, at most 32
  items, `ref`/`note` under the character cap.
- [x] 2.2 Persist the packet on the caller's connection so it commits with the
  fact; absorb a re-cited `(kind, ref)` instead of growing the ledger.
- [x] 2.3 Copy evidence forward on supersession tagged `carried_from`, without
  touching the superseded row's own rows.
- [x] 2.4 Record a non-UUID runtime session id as unknown rather than failing
  the write.

## 3. Coverage receipts

- [x] 3.1 Compose `target availability + active values + receipts` into exactly
  one state; no receipts composes to `unknown`.
- [x] 3.2 Write a `present` receipt in the same transaction as every successful
  assert.
- [x] 3.3 Reject an out-of-order receipt replay from rewinding coverage.
- [x] 3.4 Report a merged-away or missing subject as `unavailable`.

## 4. Approved writes

- [x] 4.1 Verify `approval_action_id` against `pending_actions`: same tool,
  executable status, exact identity quadruple.
- [x] 4.2 Take `src`, `observed_at`, evidence, and session from the parked row,
  never from the dispatch caller.
- [x] 4.3 Skip the proposal-time gates on an approved replay so the write lands
  instead of re-parking.
- [x] 4.4 Keep `src` and `observed_at` off the MCP tool signature entirely.

## 5. Reads

- [x] 5.1 Add `relationship_fact_evidence`, `relationship_predicate_coverage`,
  and `relationship_record_coverage` MCP tools.
- [x] 5.2 Return `fact_id` from `relationship_lookup` identity facts so a caller
  can ask the ledger why a fact is believed.
