## 1. Implementation
- [x] 1.1 core_334: lane, deliver_after, claim columns, timed dedup index; test DDL mirror.
- [x] 1.2 Shared all-or-none claim in the digest and urgent cycles; claim-guarded settle and failure release; stale digest claim release.
- [x] 1.3 Dark timed sub-cycle with window, holds, premise, budget/lane cap and ambiguous-send handling; job registered, unscheduled.
- [x] 1.4 `propose_insight_candidate` lane/deliver_after validation and dark refusal.
- [x] 1.5 RFC 0011 Amendment 8.

## 2. Verification
- [ ] 2.1 Real-PostgreSQL timed-lane and concurrency tests green in CI (Docker unavailable locally).
- [ ] 2.2 Home departure producer and premise probe (bu-jv1moa.8 PR-B).
- [ ] 2.3 Owner-approved activation: flag on and schedule added.
