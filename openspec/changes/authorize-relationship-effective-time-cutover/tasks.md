## 1. Draft contract

- [x] 1.1 Trace the adopted two-stage uniqueness transition, the merged `rel_035` expand stage and
      transition writer, the mutator inventory guard, and every automatic migration path.
- [x] 1.2 Amend the contract to the owner's `bu-4awqyt` answer B: drop the signer, receipt, test
      runtime, fence, gated ceiling and fresh-database admission; keep the mutator inventory and
      the no-old-writer rule, enforced by lock-and-refuse plus stopping every database client.
- [x] 1.3 Reconcile the amended text with the adopted parent's old-images-absent precondition.
- [x] 1.4 Rewrite `docs/operations/relationship-effective-time-cutover.md` as hard-cutover rollout
      notes, and archive `extend-relationship-cutover-kubernetes` unadopted.

## 2. Draft verification

- [x] 2.1 Run strict OpenSpec validation and the overwrite, requirement-trace, countability,
      em-dash and session-link guards on the exact draft.

## 3. Future implementation owned by bu-h3b7t

- [ ] 3.1 Allocate then-free G after the actual current Relationship head; meeting-debrief `rel_036`
      stays untouched. Implement the lock-and-refuse check and index-state checks before DDL, and
      the offline refusal.
- [ ] 3.2 Add real-PostgreSQL tests: G refuses `active_writer` with index and revision unchanged
      while a second client, logged in as a different role, is connected; G refuses
      `db_activity_unverifiable` when the migrating role lacks `pg_read_all_stats`; G refuses
      `lock_unavailable` under lock contention; G
      applies with no other client; the downgrade recreates the index before the first temporal
      write and refuses after it.
- [ ] 3.3 Keep `tests/contracts/test_entity_facts_mutator_inventory.py` clean and the named
      `relationship-fact-effective-time` task 3.5 scenarios passing at the commit carrying G.
- [ ] 3.4 Complete the rollout notes against the merged G.

## 4. Owner act

- [ ] 4.1 The owner rolls out the release carrying G: stop every database client, run the migration
      alone, start only that release. Source delivery, tests and CI do not perform it.
