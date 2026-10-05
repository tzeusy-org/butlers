## 1. Slice 1: table, selection job, answer tools

- [x] 1.1 Migration `rel_036` adds `relationship.meeting_debriefs`.
- [x] 1.2 `butlers.jobs.meeting_debrief` selection with owner-RSVP, solo, transparent, posture and idempotence rules.
- [x] 1.3 `meeting_debrief_pending` / `meeting_debrief_answer` tools and the `meeting-debrief` skill.
- [x] 1.4 `create_commitment(sphere=...)` metadata.

## 2. Slice 2: batched prompt and back-off

- [x] 2.1 One batched insight per run, `prompted_at` only on acceptance.
- [x] 2.2 Weekly cadence after 3 consecutive unanswered prompts, announced once.

## 3. Deferred

- [ ] 3.1 Dashboard Debrief card with a pinned reply.
- [ ] 3.2 "Work commitments" filter on the condition ledger.
- [ ] 3.3 Sphere derived from endpoint custody.

## 4. Exactly-once answer repair (bu-q7vx1q.68)

- [x] 4.1 Retain official old-source migrated PostgreSQL distinct-set, none/captured and first-write fault counterexamples with genuine positives.
- [ ] 4.2 Compose actual create_commitment and shared reconciliation on one owning connection; lock/recheck debrief and commit the whole answer before acknowledgment.
- [ ] 4.3 Preserve answered state against stale unaskable-job expiry with a current-state predicate and pending-expiry positive proof.
- [ ] 4.4 Execute connection-aware/default compatibility, eligible graph rollback, actual conditional premise enqueue/outer rollback and size-one-pool controls without changing policy.
- [ ] 4.5 Preserve full contract/scenario/inventory parity, update relationship Implementation Notes, measure test growth and obtain exact-source hosted and protected terminal receipts.

These unchecked repair tasks do not erase the original implemented tool/job slice, certify its
historical sequential tests as race proof, or complete the separate posture/free-text/deferred work.

Task 4.1 evidence: unchanged-production test-only head `220cf3eb29b2dcfd911e38d85420c51172c35c32`,
[CI 37286612106](https://github.com/tzeusy-org/butlers/actions/runs/37286612106),
affected job `111687162335`: 23 owning cases, 10 causal assertion failures, 13 passing controls,
zero setup errors/skips. Both answer races accepted twice, stale expiry overwrote both terminal
states, a later invalid action left the first write, and all five real-write faults leaked effects.
Winner-only/retry checks after the earlier failing race assertions were unreached on this baseline.
The remaining repair/verification tasks require corrected-source evidence; this baseline is not
implementation completion or a whole-change archive.
