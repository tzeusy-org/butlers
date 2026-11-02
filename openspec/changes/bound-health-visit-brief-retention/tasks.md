## 1. Contract (this change)

- [x] 1.1 Inventory the digest fields and consumers, and cite each field's classification and retention authority.
- [x] 1.2 Specify the digest classification and the disabled-until-adopted pruning mechanism with its residual receipt.
- [x] 1.3 Record the owner retention-window decision (options A to D, recommendation A).

## 2. Owner decision (gate)

- [ ] 2.1 Owner adopts a window (A, B or C) or accepts indefinite retention (D).

## 3. Implementation (held until 2.1 adopts A, B or C)

- [ ] 3.1 Prune phase in `health_visit_brief._run_locked` under the run lock, batched, fence-aware and idempotent.
- [ ] 3.2 Job result `pruned` and `prune_failed` with a content-free WARNING.
- [ ] 3.3 Real-Postgres cases on `test_visit_brief`: prune to receipt, terminal flags kept, repeat no-op, overlap sends once, disabled leaves digest unchanged.
- [ ] 3.4 Update `roster/health/AGENTS.md` and the `health_005` docstring with the adopted window.
