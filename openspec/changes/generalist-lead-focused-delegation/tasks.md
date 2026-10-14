## 1. Contract

- [x] 1.1 Record owner adoption of the generalist-lead doctrine (2026-10-14).
- [x] 1.2 Draft RFC 0038 with the authorization broker, hard cutover plan and owner decisions.
- [ ] 1.3 Owner signoff on RFC 0038 and its owner decisions; author MODIFIED/REMOVED deltas for the baseline capabilities listed in proposal.md.
- [ ] 1.4 Derive the implementation epic and beads from the signed contract.

## 2. Dark build (merged to main, not wired to ingress or schedules)

- [ ] 2.1 Catalog authorization metadata for every LLM-presentable tool, with a CI check.
- [ ] 2.2 Goal ledger migrations: goals, invocations, effects, budgets.
- [ ] 2.3 Goal admission with origin ceilings and obligation declarations.
- [ ] 2.4 Goal runner, session sandbox profiles and runtime adapter proofs.
- [ ] 2.5 Broker endpoint: per-invocation listing, call checks, exposure, approvals, effect ledger, signed assertions.
- [ ] 2.6 Domain-side assertion verification in the core tool wrapper (enabled by the cutover release).
- [ ] 2.7 Goal tools, delegation, budgets, Stop, recovery and escalation.
- [ ] 2.8 RFC 0027 native deferred discovery on the broker endpoint.

## 3. Cutover gate (dev namespace, release candidate)

- [ ] 3.1 All goal-orchestration and capability-authorization scenarios pass.
- [ ] 3.2 Adversarial suite: zero unauthorized effects.
- [ ] 3.3 Replay evaluation meets or beats recorded outcomes; model tiers and default budgets set from it.
- [ ] 3.4 Stop, lease-loss, runner-crash and escalation drills pass.

## 4. Switch

- [ ] 4.1 One release routes all ingress and obligations through admission, deletes classification, route dispatch, per-butler spawning and cross-butler delegation, and enables domain verification.
- [ ] 4.2 Apply baseline deltas, archive this change, update RFC statuses, topology, operational docs, `CLAUDE.md` and the Switchboard manifesto.
